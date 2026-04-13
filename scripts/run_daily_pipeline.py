"""일간 VOC 분류 파이프라인

매일 전날 데이터를 BigQuery에서 조회 → 분류 → voc_labelled에 저장 → Slack 알림

사용법:
    python scripts/run_daily_pipeline.py                # 어제 데이터
    python scripts/run_daily_pipeline.py --date 2026-03-22  # 특정 날짜
    python scripts/run_daily_pipeline.py --dry-run       # 테스트 (5건만)
"""
import sys
import os
import re
import json
import time
import logging
import argparse
from datetime import datetime, timedelta
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from src.bigquery.client import BigQueryClient
from src.bigquery.queries import WeeklyDataQuery
from src.bigquery.writer import BigQueryWriter
from src.classifier_v5.bedrock_classifier import BedrockV5Classifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# 서비스 공지 필터링 키워드
FILTER_KEYWORDS = [
    "운영 안내", "공지사항", "서비스 점검", "시스템 점검",
    "업데이트 안내", "이벤트 안내", "서비스 안내",
]


def filter_service_notices(items):
    """서비스 공지 필터링"""
    filtered = []
    for item in items:
        text = item.get("message") or item.get("textBody") or item.get("body", "")
        if any(kw in text[:100] for kw in FILTER_KEYWORDS):
            continue
        filtered.append(item)
    return filtered


def enrich_master_info(items, master_info, field="masterId"):
    """마스터 이름 매핑"""
    for item in items:
        mid = item.get(field, "")
        if field == "postBoardId":
            mid = item.get("postBoardId", "")
        if mid in master_info:
            info = master_info[mid]
            item["masterName"] = info.get("displayName") or info.get("name", "Unknown")
            item["masterClubName"] = info.get("clubName", "")
    return items


def send_daily_summary(items, channel_items, pipeline_date, cost_usd):
    """매일 분류 완료 시 요약 메시지 — 부정 급증이 없어도 항상 발송.

    역할:
    1. "오늘 일간 파이프라인이 정상 완료됨" 시그널 (없으면 사람이 인지해야 함)
    2. 요약 정보 (건수/감정 분포/토픽 Top) — 매일 보는 화면
    3. 대시보드 URL (DASHBOARD_URL 환경변수)
    """
    try:
        from src.integrations.slack_client import SlackNotifier
        slack = SlackNotifier()
    except Exception:
        logger.warning("Slack 연동 실패 — 일일 요약 건너뜀")
        return

    lp_total = len(items)
    letter_n = sum(1 for it in items if "message" in it)
    post_n = lp_total - letter_n
    ch_total = len(channel_items) if channel_items else 0

    # 편지/게시글 감정 분포
    sentiment_counts = {"긍정": 0, "중립": 0, "부정": 0}
    topic_counts = {}
    for it in items:
        cls = it.get("classification", {})
        s = cls.get("sentiment") or "중립"
        if s in sentiment_counts:
            sentiment_counts[s] += 1
        t = cls.get("topic") or "기타"
        topic_counts[t] = topic_counts.get(t, 0) + 1

    sent_pct = {k: (v / lp_total * 100 if lp_total else 0) for k, v in sentiment_counts.items()}

    # 부정 급증 마스터 (5건+ & 30%+)
    master_stats = {}
    for item in items:
        master = re.sub(r"\d+$", "", item.get("masterName", "Unknown")).strip()
        cls = item.get("classification", {})
        master_stats.setdefault(master, {"total": 0, "neg": 0})
        master_stats[master]["total"] += 1
        if cls.get("sentiment") == "부정":
            master_stats[master]["neg"] += 1
    neg_alerts = []
    for master, st in master_stats.items():
        if st["total"] >= 5 and st["neg"] / st["total"] >= 0.3:
            neg_alerts.append((master, st["neg"], st["total"], st["neg"] / st["total"] * 100))
    neg_alerts.sort(key=lambda x: -x[3])

    # 메시지 조립
    parts = [f"📊 *VOC 일간 요약 — {pipeline_date}*", ""]
    parts.append(f"*편지/게시글*: {lp_total}건 (편지 {letter_n}, 게시글 {post_n})")
    if ch_total:
        parts.append(f"*채널톡*: {ch_total:,}건")
    parts.append("")

    if lp_total:
        parts.append("*감정 분포*")
        parts.append(f"🟢 긍정 {sentiment_counts['긍정']}건 ({sent_pct['긍정']:.0f}%)")
        parts.append(f"⚪ 중립 {sentiment_counts['중립']}건 ({sent_pct['중립']:.0f}%)")
        parts.append(f"🔴 부정 {sentiment_counts['부정']}건 ({sent_pct['부정']:.0f}%)")
        parts.append("")

    if topic_counts:
        top3 = sorted(topic_counts.items(), key=lambda x: -x[1])[:4]
        parts.append("*토픽 Top*")
        for t, n in top3:
            parts.append(f"• {t} {n}건")
        parts.append("")

    if neg_alerts:
        parts.append(f"⚠️ *부정 급증 마스터* ({len(neg_alerts)}명)")
        for master, neg, total, pct in neg_alerts[:5]:
            parts.append(f"• *{master}*: {neg}/{total}건 ({pct:.0f}%)")
        if len(neg_alerts) > 5:
            parts.append(f"• ... 외 {len(neg_alerts) - 5}명")
        parts.append("")

    dashboard_url = os.getenv("DASHBOARD_URL", "")
    if dashboard_url:
        parts.append(f"📊 대시보드: {dashboard_url}")

    parts.append(f"_(분류 비용 ${cost_usd:.2f})_")

    msg = "\n".join(parts)
    try:
        slack._send_message(msg)
        logger.info("일일 요약 Slack 발송 완료")
    except Exception as e:
        logger.warning(f"일일 요약 Slack 발송 실패: {e}")


def send_failure_alert(error: Exception, pipeline_date: str, phase: str = ""):
    """파이프라인 실패 시 Slack 즉시 알림 — 다음 날 주간 리포트가 막히지 않도록 빠른 인지."""
    try:
        from src.integrations.slack_client import SlackNotifier
        slack = SlackNotifier()
    except Exception:
        logger.warning("Slack 연동 실패 — 실패 알림 건너뜀")
        return

    import traceback
    tb = traceback.format_exc()
    # 가장 최근 traceback 라인 5줄만 (메시지가 너무 길어지지 않게)
    tb_tail = "\n".join(tb.splitlines()[-8:])

    parts = [
        f"🛑 *VOC 일간 파이프라인 실패 — {pipeline_date}*",
        "",
        f"*단계*: {phase or '미상'}",
        f"*에러*: `{type(error).__name__}: {str(error)[:300]}`",
        "",
        "```",
        tb_tail,
        "```",
        "",
        "_Cloud Run 로그 확인 필요. 이번 주 주간 리포트가 막힐 수 있음 (sanity check fail)._",
    ]
    try:
        slack._send_message("\n".join(parts))
        logger.info("실패 알림 Slack 발송 완료")
    except Exception as e:
        logger.warning(f"실패 알림 Slack 발송도 실패: {e}")


def send_slack_alert(items, pipeline_date):
    """부정 급증 시 Slack 알림 (별도 채널/스레드 — 일일 요약과 분리)"""
    try:
        from src.integrations.slack_client import SlackNotifier
        slack = SlackNotifier()
    except Exception:
        logger.warning("Slack 연동 실패 — 알림 건너뜀")
        return

    # 마스터별 부정률 계산
    master_stats = {}
    for item in items:
        master = re.sub(r"\d+$", "", item.get("masterName", "Unknown")).strip()
        cls = item.get("classification", {})
        if master not in master_stats:
            master_stats[master] = {"total": 0, "neg": 0}
        master_stats[master]["total"] += 1
        if cls.get("sentiment") == "부정":
            master_stats[master]["neg"] += 1

    # 부정률 30% 이상 + 5건 이상인 마스터 감지
    alerts = []
    for master, stats in master_stats.items():
        if stats["total"] >= 5:
            neg_r = stats["neg"] / stats["total"] * 100
            if neg_r >= 30:
                alerts.append(f"• *{master}*: 부정 {stats['neg']}건/{stats['total']}건 ({neg_r:.0f}%)")

    if alerts:
        msg = f"🚨 *[VOC 부정 감지] {pipeline_date}*\n\n" + "\n".join(alerts)
        try:
            slack._send_message(msg)
            logger.info(f"Slack 알림 발송: {len(alerts)}건")
        except Exception as e:
            logger.warning(f"Slack 발송 실패: {e}")
    else:
        logger.info("부정 급증 없음 — 알림 없음")


def main():
    parser = argparse.ArgumentParser(description="일간 VOC 분류 파이프라인")
    parser.add_argument("--date", help="대상 날짜 (YYYY-MM-DD, 기본: 어제)")
    parser.add_argument("--dry-run", action="store_true", help="테스트 모드 (5건만)")
    parser.add_argument("--skip-channel", action="store_true", help="채널톡 건너뛰기")
    parser.add_argument("--skip-slack", action="store_true", help="Slack 알림 건너뛰기")
    parser.add_argument("--workers", type=int, default=5, help="병렬 워커 수")
    args = parser.parse_args()

    # 날짜 설정
    if args.date:
        target_date = args.date
    else:
        target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    next_date = (datetime.strptime(target_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

    logger.info(f"=== VOC 일간 파이프라인 시작 ===")
    logger.info(f"대상 날짜: {target_date}")
    pipeline_start = time.time()

    # 현재 단계 추적 (실패 시 알림에 포함)
    current_phase = ["init"]

    try:
        _run_pipeline(args, target_date, next_date, current_phase, pipeline_start)
    except Exception as e:
        logger.exception(f"파이프라인 실패 (단계: {current_phase[0]})")
        if not args.skip_slack:
            send_failure_alert(e, target_date, current_phase[0])
        raise   # Cloud Run 이 실패로 인지하도록 재발생


def _run_pipeline(args, target_date, next_date, current_phase, pipeline_start):
    # ── Phase 1: BigQuery 데이터 조회 ──
    current_phase[0] = "Phase 1: BigQuery 조회"
    logger.info("Phase 1: BigQuery 조회")
    bq_client = BigQueryClient()
    query = WeeklyDataQuery(bq_client)
    master_info = query.get_master_info()

    data = query.get_weekly_data(target_date, next_date)
    letters = data["letters"]
    posts = data["posts"]

    # 마스터 정보 매핑
    letters = enrich_master_info(letters, master_info, "masterId")
    posts = enrich_master_info(posts, master_info, "postBoardId")

    # 서비스 공지 필터링
    letters = filter_service_notices(letters)
    posts = filter_service_notices(posts)

    logger.info(f"  편지 {len(letters)}건, 게시글 {len(posts)}건")

    if args.dry_run:
        letters = letters[:3]
        posts = posts[:2]
        logger.info(f"  [DRY RUN] 편지 {len(letters)}건, 게시글 {len(posts)}건으로 제한")

    # ── Phase 2: 편지글/게시글 분류 (Bedrock Sonnet) ──
    current_phase[0] = "Phase 2: 편지/게시글 분류"
    logger.info("Phase 2: 편지글/게시글 분류 (Bedrock Sonnet v5 4분류)")
    classifier = BedrockV5Classifier(max_workers=args.workers)

    all_items = []
    if letters:
        logger.info(f"  편지 {len(letters)}건 분류 중...")
        classifier.classify_batch(letters, content_field="message")
        all_items.extend(letters)

    if posts:
        logger.info(f"  게시글 {len(posts)}건 분류 중...")
        classifier.classify_batch(posts, content_field="textBody")
        all_items.extend(posts)

    cost = classifier.get_cost_report()
    logger.info(f"  분류 완료: {cost['total_items']}건, 에러 {cost['errors']}건, ${cost['cost_usd']}")

    # ── Phase 3: 채널톡 분류 (KcELECTRA + Bedrock) ──
    channel_items = []
    if not args.skip_channel:
        current_phase[0] = "Phase 3: 채널톡 분류"
        logger.info("Phase 3: 채널톡 분류 (KcELECTRA + Bedrock)")
        try:
            from src.classifier_v4.bedrock_two_depth import BedrockTwoDepthClassifier
            ch_classifier = BedrockTwoDepthClassifier()

            # 채널톡 데이터 조회
            try:
                from src.bigquery.channel_queries import ChannelQueryService
                ch_query = ChannelQueryService(bq_client)
                ch_data = ch_query.get_daily_chats(target_date, next_date)

                from src.bigquery.channel_preprocessor import build_chat_items
                channel_items = build_chat_items(ch_data)
                logger.info(f"  채널톡 {len(channel_items)}건 조회")

                if args.dry_run:
                    channel_items = channel_items[:3]

                channel_items = ch_classifier.classify_batch(channel_items)
                logger.info(f"  채널톡 분류 완료: {len(channel_items)}건")
            except ImportError:
                logger.warning("  채널톡 쿼리 모듈 없음 — 건너뜀")
            except Exception as e:
                logger.warning(f"  채널톡 처리 실패: {e}")
        except Exception as e:
            logger.warning(f"  KcELECTRA 로드 실패: {e}")

    # ── Phase 4: BigQuery 저장 ──
    current_phase[0] = "Phase 4: BigQuery 저장"
    logger.info("Phase 4: BigQuery voc_labelled 저장")
    writer = BigQueryWriter(bq_client.client)

    lp_count = writer.write_letters_posts(all_items, target_date)
    ch_count = 0
    if channel_items:
        ch_count = writer.write_channel_talk(channel_items, target_date)

    # ── Phase 5: Slack 일일 요약 (부정 급증 정보 포함) ──
    if not args.skip_slack and all_items:
        current_phase[0] = "Phase 5: Slack 일일 요약"
        logger.info("Phase 5: Slack 일일 요약")
        send_daily_summary(all_items, channel_items, target_date, cost.get("cost_usd", 0.0))

    # ── 완료 ──
    elapsed = time.time() - pipeline_start
    logger.info(f"\n=== 파이프라인 완료 ===")
    logger.info(f"대상 날짜: {target_date}")
    logger.info(f"편지/게시글: {lp_count}건 저장")
    logger.info(f"채널톡: {ch_count}건 저장")
    logger.info(f"소요 시간: {elapsed:.1f}초 ({elapsed/60:.1f}분)")
    logger.info(f"분류 비용: ${cost['cost_usd']}")


if __name__ == "__main__":
    main()
