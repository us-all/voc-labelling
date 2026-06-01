"""주간 리포트 생성 — voc_labelled 기반 (일간 파이프라인 결과 재사용)

전제: 일간 파이프라인(`run_daily_pipeline.py`)이 매일 voc_labelled에 분류 결과를
적재해둔다. 주간 스크립트는 해당 기간을 읽어 통계 + 리포트 생성만 수행.

voc_labelled에 데이터가 없으면 sanity check 에서 fail → 발행 중단.
원본에서 재분류가 필요한 경우는 별도 백필 스크립트로 처리한다.

사용법:
    python scripts/generate_weekly_report_v5.py                    # 지난 주
    python scripts/generate_weekly_report_v5.py --start 2026-04-06 --end 2026-04-13
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


def load_from_voc_labelled(bq_client, start_date, end_date):
    """voc_labelled.letters_posts 에서 분류된 데이터 조회 (id 기준 dedup)."""
    query_sql = f"""
    SELECT id, source_type, master_id, master_name, user_id,
           content, created_at, topic, subtag, sentiment, summary, tags,
           confidence, pipeline_date
    FROM (
      SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY classified_at DESC) as rn
      FROM `{bq_client.project_id}.voc_labelled.letters_posts`
      WHERE pipeline_date >= '{start_date}' AND pipeline_date < '{end_date}'
    )
    WHERE rn = 1
    """
    results = bq_client.execute_query(query_sql)

    letters, posts = [], []
    for r in results:
        item = dict(r)
        # analytics 호환 필드 매핑
        item["masterName"] = item.get("master_name", "Unknown")
        item["masterId"] = item.get("master_id", "")
        item["createdAt"] = item.get("created_at", "")
        item["_id"] = item.get("id", "")
        if item.get("source_type") == "letter":
            item["message"] = item.get("content", "")
            letters.append(item)
        else:
            item["textBody"] = item.get("content", "")
            posts.append(item)
    return letters, posts


def count_from_voc_labelled(bq_client, start_date, end_date):
    """전주 건수만 필요할 때 count-only 조회."""
    query_sql = f"""
    SELECT source_type, master_name, COUNT(*) as cnt
    FROM (
      SELECT source_type, master_name,
             ROW_NUMBER() OVER (PARTITION BY id ORDER BY classified_at DESC) as rn
      FROM `{bq_client.project_id}.voc_labelled.letters_posts`
      WHERE pipeline_date >= '{start_date}' AND pipeline_date < '{end_date}'
    )
    WHERE rn = 1
    GROUP BY source_type, master_name
    """
    letters, posts = [], []
    for r in bq_client.execute_query(query_sql):
        stub = {"masterName": r["master_name"] or "Unknown", "createdAt": ""}
        if r["source_type"] == "letter":
            letters.extend([stub] * r["cnt"])
        else:
            posts.extend([stub] * r["cnt"])
    return letters, posts


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

    # 1. voc_labelled 에서 이번 주 분류 데이터 조회
    print(f"\n  Phase 1: voc_labelled 조회 (일간 파이프라인 결과)")
    letters, posts = load_from_voc_labelled(bq_client, start_date, end_date)
    print(f"    편지 {len(letters)}건, 게시글 {len(posts)}건")
    run_logger.log(f"Phase 1: 편지 {len(letters)}건, 게시글 {len(posts)}건", also_print=False)

    # 1-1. 전주 건수 (전주 대비 감소율 sanity check 용)
    from datetime import datetime, timedelta
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    prev_start = (start_dt - timedelta(days=7)).strftime("%Y-%m-%d")
    prev_end = start_date
    print(f"\n  Phase 1-1: 전주 건수 조회 ({prev_start} ~ {prev_end})")
    prev_letters, prev_posts = count_from_voc_labelled(bq_client, prev_start, prev_end)
    print(f"    전주: 편지 {len(prev_letters)}건, 게시글 {len(prev_posts)}건")

    # 1-2. 데이터 건전성 체크 (일간 파이프라인 실패 감지)
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
            print(f"\n  🛑 건전성 체크 실패 — 발행 중단.")
            print(f"     일간 파이프라인 완료 여부 확인 필요. 강제 진행은 --force 사용.")
            print(f"     로그: {run_logger.run_dir}/")
            return

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

    # 비용: 분류 없음. 톤/인용구 검수 Haiku 호출만 (~$0.05)
    run_logger.save_cost({"classification": 0.0, "note": "분류는 일간 파이프라인에서 수행"})
    print(f"\n  저장: {output_path}")
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
            week_label = SlackNotifier.get_week_label(start_date)
            page_title = f"[{week_label}] 이용자 반응 리포트 ({start_formatted} ~ {end_formatted})"
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
