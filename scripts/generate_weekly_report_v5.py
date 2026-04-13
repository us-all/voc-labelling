"""주간 리포트 생성 (v5 4분류) — 원본 조회 → v5 분류 → 리포트

voc_labelled에 분류 데이터가 없는 기간도 처리 가능.
BigQuery 원본 → Haiku v5 분류 → 통계 분석 → 리포트 생성.

사용법:
    python scripts/generate_weekly_report_v5.py                    # 지난 주
    python scripts/generate_weekly_report_v5.py --start 2026-03-23 --end 2026-03-30
"""
import sys
import os
import argparse
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from dotenv import load_dotenv
load_dotenv()

from src.bigquery.client import BigQueryClient
from src.bigquery.queries import WeeklyDataQuery
from src.bigquery.writer import BigQueryWriter
from src.classifier_v5.classifier import V5Classifier
from src.classifier_v5.bedrock_classifier import BedrockV5Classifier
from src.reporter.analytics import WeeklyAnalytics
from src.reporter.report_generator import ReportGenerator
from src.reporter.feedback_clusterer import cluster_feedbacks, enrich_master_stats_with_clusters
from src.reporter.tone_reviewer import review_report
from src.reporter.quote_reviewer import review_quotes_in_report
from src.reporter.sanity_check import check_data_health
from src.integrations.notion_client import NotionReportClient
from src.integrations.slack_client import SlackNotifier
from src.utils.excel_exporter import export_to_excel
from src.utils.run_logger import RunLogger


def classify_items(items, content_field, classifier):
    """v5 분류 결과를 item에 직접 매핑 (analytics 호환)"""
    if not items:
        return items

    classifier.classify_batch(items, content_field=content_field)

    # classification 결과를 item 최상위 필드로 복사 (analytics 호환)
    for item in items:
        cls = item.get("classification", {})
        item["topic"] = cls.get("topic", "")
        item["subtag"] = cls.get("subtag", "")
        item["sentiment"] = cls.get("sentiment", "")
        item["summary"] = cls.get("summary", "")
        item["tags"] = cls.get("tags", [])
        item["confidence"] = cls.get("confidence", 0.0)

    return items


def main():
    parser = argparse.ArgumentParser(description="주간 리포트 생성 (v5)")
    parser.add_argument("--start", help="시작일 (YYYY-MM-DD)")
    parser.add_argument("--end", help="종료일 (YYYY-MM-DD)")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--bedrock", action="store_true", help="Bedrock Haiku 사용 (Anthropic API 대신)")
    parser.add_argument("--no-upload", action="store_true", help="Notion/Slack 업로드 건너뛰기")
    parser.add_argument("--force", action="store_true", help="건전성 체크 fail이어도 강제 진행")
    args = parser.parse_args()

    print("=" * 60)
    print("  주간 리포트 생성 (v5 4분류)")
    print("=" * 60)

    # BigQuery 클라이언트
    bq_client = BigQueryClient()
    query = WeeklyDataQuery(bq_client)

    # 날짜 범위
    if args.start and args.end:
        start_date, end_date = args.start, args.end
    else:
        start_date, end_date = WeeklyDataQuery.get_last_week_range()

    print(f"\n  대상 기간: {start_date} ~ {end_date}")

    run_logger = RunLogger(start_date)
    run_logger.log(f"=== 주간 리포트 시작 ({start_date} ~ {end_date}) ===", also_print=False)

    # 1. 원본 데이터 조회
    print(f"\n  Phase 1: BigQuery 원본 조회")
    master_info = query.get_master_info()
    data = query.get_weekly_data(start_date, end_date)
    letters = data["letters"]
    posts = data["posts"]

    # 마스터 정보 매핑
    for item in letters:
        mid = item.get("masterId", "")
        if mid in master_info:
            item["masterName"] = master_info[mid].get("displayName") or master_info[mid].get("name", "Unknown")
    for item in posts:
        mid = item.get("postBoardId", "")
        if mid in master_info:
            item["masterName"] = master_info[mid].get("displayName") or master_info[mid].get("name", "Unknown")

    print(f"    편지 {len(letters)}건, 게시글 {len(posts)}건")
    run_logger.log(f"Phase 1: 편지 {len(letters)}건, 게시글 {len(posts)}건", also_print=False)

    # 1-1. 전주 데이터 조회 (sanity check 입력용 — 분류 전에 가져옴)
    from datetime import datetime, timedelta
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    prev_start = (start_dt - timedelta(days=7)).strftime("%Y-%m-%d")
    prev_end = start_date
    print(f"\n  Phase 1-1: 전주 건수 조회 ({prev_start} ~ {prev_end})")
    prev_data = query.get_weekly_data(prev_start, prev_end)
    prev_letters = prev_data["letters"]
    prev_posts = prev_data["posts"]
    for item in prev_letters:
        mid = item.get("masterId", "")
        item["masterName"] = master_info.get(mid, {}).get("displayName", "Unknown")
    board_query_sql = f"SELECT _id as boardId, masterId FROM `{bq_client.project_id}.us_plus_next.postboards`"
    board_to_master = {b["boardId"]: b["masterId"] for b in bq_client.execute_query(board_query_sql)}
    for item in prev_posts:
        bid = item.get("postBoardId", "")
        mid = board_to_master.get(bid, bid)
        item["masterName"] = master_info.get(mid, {}).get("displayName", "Unknown")
    print(f"    전주: 편지 {len(prev_letters)}건, 게시글 {len(prev_posts)}건")

    # 1-2. 데이터 건전성 체크 ($21 분류 전에 잡아냄)
    print(f"\n  Phase 1-2: 데이터 건전성 체크")
    sanity = check_data_health(letters, posts, prev_letters, prev_posts, start_date, end_date)
    run_logger.save_anomalies(sanity.to_dict())
    print(f"    상태: {sanity.status.upper()} ({len(sanity.anomalies)}개 이상)")
    for a in sanity.anomalies:
        print(f"    [{a.severity}] {a.code}: {a.message}")
    run_logger.log(f"Phase 1-2: sanity={sanity.status}, anomalies={len(sanity.anomalies)}", also_print=False)

    if not sanity.should_continue:
        if args.force:
            print(f"\n  ⚠️  fail 이지만 --force 로 강제 진행")
            run_logger.log("fail 무시 (--force)", also_print=False)
        else:
            print(f"\n  🛑 건전성 체크 실패 — 발행 중단. 강제 진행은 --force 사용.")
            print(f"     로그: {run_logger.run_dir}/")
            return

    # 2. v5 분류
    print(f"\n  Phase 2: v5 4분류 (Bedrock Haiku)")
    bedrock_workers = min(args.workers, 30)  # Bedrock Haiku는 30까지 OK
    classifier = BedrockV5Classifier(model_id="us.anthropic.claude-3-5-haiku-20241022-v1:0", max_workers=bedrock_workers)
    start_time = time.time()

    classify_items(letters, "message", classifier)
    classify_items(posts, "textBody", classifier)

    cost = classifier.get_cost_report()
    elapsed = time.time() - start_time
    print(f"    분류 완료: {cost['total_items']}건, {elapsed:.1f}초, ${cost['cost_usd']}")

    # 2-1. 분류 결과 JSON 저장 (로컬 캐시)
    import json
    data_dir = os.path.join(os.path.dirname(__file__), "..", "data", "classified_data")
    os.makedirs(data_dir, exist_ok=True)
    classified_path = os.path.join(data_dir, f"v5_{start_date}.json")
    with open(classified_path, "w", encoding="utf-8") as f:
        json.dump({"letters": letters, "posts": posts}, f, ensure_ascii=False, default=str)
    print(f"    로컬 저장: {classified_path}")

    # 2-2. BigQuery voc_labelled.letters_posts 저장 (일간 파이프라인과 동일 테이블)
    # pipeline_date는 각 item의 KST 생성일 기준으로 그룹핑 후 day별 upsert
    print(f"\n  Phase 2-2: BigQuery voc_labelled 저장")
    writer = BigQueryWriter(bq_client.client)
    all_items = letters + posts

    from collections import defaultdict
    from datetime import datetime as _dt, timedelta as _td
    items_by_date = defaultdict(list)
    for item in all_items:
        created = item.get("createdAt", "")
        if not created:
            continue
        # createdAt은 UTC ISO 문자열 → KST 날짜로 변환
        try:
            if isinstance(created, str):
                utc_dt = _dt.fromisoformat(created.replace("Z", "+00:00"))
            else:
                utc_dt = created
            kst_date = (utc_dt + _td(hours=9)).strftime("%Y-%m-%d")
            items_by_date[kst_date].append(item)
        except Exception:
            pass

    total_written = 0
    for pipeline_date, day_items in sorted(items_by_date.items()):
        n = writer.write_letters_posts(day_items, pipeline_date, classifier_model="bedrock-haiku-3.5-v5")
        total_written += n
        print(f"    {pipeline_date}: {n}건")
    print(f"    총 {total_written}건 저장 완료")

    # (전주 데이터는 Phase 1-1 에서 이미 로드됨 — 건전성 체크용으로 미리 가져옴)
    if not prev_letters and not prev_posts:
        prev_letters = None
        prev_posts = None

    # 4. 통계 분석
    print(f"\n  Phase 4: 통계 분석")
    analytics = WeeklyAnalytics()
    stats = analytics.analyze_weekly_data(
        letters, posts,
        previous_letters=prev_letters,
        previous_posts=prev_posts,
    )

    total = stats["total_stats"]["this_week"]
    print(f"    전체: 편지 {total['letters']}건, 게시글 {total['posts']}건")

    category_stats = stats["category_stats"]
    print(f"    토픽별:")
    for topic, count in sorted(category_stats.items(), key=lambda x: x[1], reverse=True):
        print(f"      {topic}: {count}건")

    feedbacks = stats.get("service_feedbacks", [])
    print(f"    피드백: {len(feedbacks)}건")

    # 4-1. 피드백 클러스터링
    print(f"\n  Phase 4-1: 피드백 클러스터링")
    if feedbacks:
        enriched, cluster_info = cluster_feedbacks(feedbacks)
        stats["service_feedbacks"] = enriched
        stats["feedback_clusters"] = cluster_info
        enrich_master_stats_with_clusters(stats)
        multi = [c for c in cluster_info.values() if c["size"] >= 2]
        print(f"    {len(cluster_info)}개 클러스터 (2건+: {len(multi)}개)")
    else:
        print(f"    피드백 없음")

    # 5. 리포트 생성
    print(f"\n  Phase 5: 리포트 생성")
    output_dir = os.getenv("REPORT_OUTPUT_DIR", "./reports")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"weekly_report_v5_{start_date}.md")

    generator = ReportGenerator()
    report = generator.generate_report(stats, start_date, end_date, output_path=output_path)

    # 5-1. 톤 검수
    print(f"\n  Phase 5-1: 톤 검수")
    fixed_report, review_stats = review_report(report)
    print(f"    {review_stats['fixed_sections']}/{review_stats['total_sections']} 섹션 수정, {review_stats['total_issues']}건 교정")
    if review_stats["total_issues"] > 0:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(fixed_report)
        report = fixed_report

    # 5-2. 인용구 검수 (욕설/인신공격/사기 단정 자동 제거)
    print(f"\n  Phase 5-2: 인용구 검수")
    cleaned_report, quote_audit = review_quotes_in_report(report)
    print(f"    인용구 {quote_audit['total_quotes']}개 / 제거 {quote_audit['removed_count']}개")
    for r in quote_audit["removed"]:
        print(f"    [REMOVED] \"{r['text'][:60]}{'...' if len(r['text']) > 60 else ''}\"")
        print(f"              사유: {r['reason']}")
    # 감사 로그 저장
    import json as _json
    with open(run_logger.path_for("quote_audit.json"), "w", encoding="utf-8") as f:
        _json.dump(quote_audit, f, ensure_ascii=False, indent=2)
    if quote_audit["removed_count"] > 0:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(cleaned_report)
        report = cleaned_report

    total_cost = classifier.get_cost_report()
    run_logger.save_cost(total_cost)
    print(f"\n  저장: {output_path}")
    print(f"  총 비용: ${total_cost['cost_usd']}")
    print(f"  실행 로그: {run_logger.run_dir}/")

    # Phase 6: 엑셀 생성
    print(f"\n  Phase 6: 엑셀 파일 생성")
    excel_path = os.path.join(output_dir, f"weekly_data_{start_date}.xlsx")
    export_to_excel(letters, posts, excel_path)
    print(f"    저장: {excel_path}")

    # Phase 7: Notion + Slack 업로드
    if args.no_upload:
        print(f"\n  Phase 7: 업로드 건너뜀 (--no-upload)")
    else:
        print(f"\n  Phase 7: Notion 업로드")
        notion_url = None
        try:
            from datetime import datetime, timedelta
            notion_client = NotionReportClient()
            start_formatted = datetime.strptime(start_date, '%Y-%m-%d').strftime('%Y.%m.%d')
            end_dt = datetime.strptime(end_date, '%Y-%m-%d')
            end_formatted = (end_dt - timedelta(days=1)).strftime('%m.%d')
            page_title = f"이용자 반응 리포트 ({start_formatted} ~ {end_formatted})"
            page_info = notion_client.create_report_page(
                title=page_title,
                markdown_content=report,
                start_date=start_date,
                end_date=end_date,
            )
            notion_url = page_info["url"]
            print(f"    Notion URL: {notion_url}")
        except Exception as e:
            print(f"    ⚠️  Notion 업로드 실패: {e}")

        print(f"\n  Phase 8: Slack 알림")
        if notion_url:
            try:
                slack_client = SlackNotifier()
                week_label = SlackNotifier.get_week_label(start_date)
                result = slack_client.send_report_notification(
                    week_label=week_label,
                    start_date=start_date,
                    end_date=end_date,
                    notion_url=notion_url,
                )
                if result.get("ok"):
                    print(f"    Slack 알림 전송 완료")
                    message_ts = result.get("message_ts")
                    if message_ts and os.path.exists(excel_path):
                        file_result = slack_client.upload_file_to_thread(
                            file_path=excel_path,
                            thread_ts=message_ts,
                            title=f"원본 데이터 ({start_date})",
                            comment="📎 라벨링된 원본 데이터 파일입니다.",
                        )
                        if file_result.get("ok"):
                            print(f"    엑셀 파일 업로드 완료")
                        else:
                            print(f"    ⚠️  엑셀 업로드 실패: {file_result.get('error')}")
                else:
                    print(f"    ⚠️  Slack 알림 실패: {result.get('error')}")
            except Exception as e:
                print(f"    ⚠️  Slack 알림 실패: {e}")
        else:
            print(f"    Notion URL 없음 — Slack 알림 건너뜀")

    print(f"\n{'='*60}")
    print(f"  완료!")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
