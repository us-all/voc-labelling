"""주간 리포트 생성 메인 스크립트"""
import sys
import os
from datetime import datetime
from typing import List, Dict, Any
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.bigquery.client import BigQueryClient
from src.bigquery.queries import WeeklyDataQuery
from src.reporter.analytics import WeeklyAnalytics
from src.reporter.report_generator import ReportGenerator
from src.reporter.tone_reviewer import review_report
from src.reporter.feedback_clusterer import cluster_feedbacks, enrich_master_stats_with_clusters


def load_from_voc_labelled(client, start_date, end_date):
    """voc_labelled.letters_posts에서 이미 분류된 데이터 조회 (id 기준 dedup)"""
    query = f"""
    SELECT id, source_type, master_id, master_name, user_id,
           content, created_at, topic, subtag, sentiment, summary, tags,
           confidence, pipeline_date
    FROM (
      SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY classified_at DESC) as rn
      FROM `{client.project_id}.voc_labelled.letters_posts`
      WHERE pipeline_date >= '{start_date}' AND pipeline_date < '{end_date}'
    )
    WHERE rn = 1
    """
    results = client.execute_query(query)

    letters = []
    posts = []
    for r in results:
        item = dict(r)
        # 기존 analytics와 호환되도록 필드 매핑
        item['masterName'] = item.get('master_name', 'Unknown')
        item['masterId'] = item.get('master_id', '')
        if item.get('source_type') == 'letter':
            item['message'] = item.get('content', '')
            letters.append(item)
        else:
            item['textBody'] = item.get('content', '')
            posts.append(item)

    return letters, posts


def load_previous_week_counts_only(client, start_date, end_date):
    """전주 데이터 — us_plus_next 원본에서 count만 조회 (분류 없이)

    전주 비교용이라 classification은 불필요, masterName/Id만 있으면 됨.
    voc_labelled에 업로드 안 되어 있어도 동작함.
    """
    query = WeeklyDataQuery(client)
    master_info = query.get_master_info()

    letters = query.get_weekly_letters(start_date, end_date)
    for item in letters:
        mid = item.get("masterId")
        if mid and mid in master_info:
            item["masterName"] = master_info[mid]["displayName"]
            item["masterClubName"] = master_info[mid]["clubName"]
        else:
            item["masterName"] = "Unknown"
            item["masterClubName"] = "Unknown"

    posts = query.get_weekly_posts(start_date, end_date)
    board_query_sql = f"""
    SELECT _id as boardId, masterId
    FROM `{client.project_id}.us_plus_next.postboards`
    """
    board_to_master = {b["boardId"]: b["masterId"] for b in client.execute_query(board_query_sql)}
    for item in posts:
        actual_master_id = board_to_master.get(item.get("postBoardId"), "")
        if actual_master_id and actual_master_id in master_info:
            item["masterName"] = master_info[actual_master_id]["displayName"]
            item["masterClubName"] = master_info[actual_master_id]["clubName"]
        else:
            item["masterName"] = "Unknown"
            item["masterClubName"] = "Unknown"

    return letters, posts


def main():
    """주간 리포트 생성 메인 프로세스"""

    print("=" * 60)
    print("📊 주간 리포트 자동 생성 시스템 (voc_labelled)")
    print("=" * 60)
    print()

    # BigQuery 클라이언트 초기화
    client = BigQueryClient()

    # 날짜 범위 계산
    start_date, end_date = WeeklyDataQuery.get_last_week_range()
    print(f"📅 대상 기간: {start_date} ~ {end_date}")
    print()

    # 1. voc_labelled에서 이번 주 데이터 조회
    print("1️⃣  voc_labelled 데이터 조회")
    print("-" * 60)

    classified_letters, classified_posts = load_from_voc_labelled(client, start_date, end_date)

    print(f"✓ 편지글 {len(classified_letters)}건 조회")
    print(f"✓ 게시글 {len(classified_posts)}건 조회")

    if not classified_letters and not classified_posts:
        print("  ❌ 데이터가 없어 리포트를 생성할 수 없습니다.")
        return

    print()

    # 2. 전주 데이터 로드 (전주 비교)
    print("2️⃣  전주 데이터 로드")
    print("-" * 60)

    prev_start, prev_end = WeeklyDataQuery.get_previous_week_range()
    print(f"📅 전주 기간: {prev_start} ~ {prev_end}")

    # 전주는 count만 필요하므로 us_plus_next 원본에서 직접 조회 (분류 불필요)
    previous_letters, previous_posts = load_previous_week_counts_only(client, prev_start, prev_end)

    if previous_letters or previous_posts:
        print(f"✓ 전주 데이터 로드 (count only): 편지 {len(previous_letters)}건, 게시글 {len(previous_posts)}건")
    else:
        print(f"❌ 전주 데이터 없음")
        previous_letters = None
        previous_posts = None

    print()

    # 3. 통계 분석
    print("3️⃣  통계 분석 (전주 비교)")
    print("-" * 60)

    analytics = WeeklyAnalytics()
    stats = analytics.analyze_weekly_data(
        classified_letters,
        classified_posts,
        previous_letters=previous_letters,
        previous_posts=previous_posts
    )

    total = stats["total_stats"]["this_week"]
    print(f"✓ 전체 통계: 편지 {total['letters']}건, 게시글 {total['posts']}건")

    category_stats = stats["category_stats"]
    print(f"✓ 토픽별 통계:")
    for topic, count in sorted(category_stats.items(), key=lambda x: x[1], reverse=True):
        print(f"  - {topic}: {count}건")

    tag_stats = stats.get("tag_stats", {})
    if tag_stats:
        print(f"✓ 상위 태그:")
        for tag, count in sorted(tag_stats.items(), key=lambda x: x[1], reverse=True)[:10]:
            print(f"  - {tag}: {count}건")

    sentiment_stats = stats.get("sentiment_stats", {})
    if sentiment_stats:
        print(f"✓ 감정 분포:")
        for sentiment, count in sorted(sentiment_stats.items(), key=lambda x: x[1], reverse=True):
            print(f"  - {sentiment}: {count}건")

    master_stats = stats["master_stats"]
    print(f"✓ 마스터별 통계: {len(master_stats)}개 마스터")

    feedbacks = stats.get("service_feedbacks", [])
    print(f"✓ 대응 필요 건: {len(feedbacks)}건")

    print()

    # 3-1. 피드백 클러스터링 (주요 내용 인사이트 향상용)
    print("3️⃣ -1 피드백 벡터 클러스터링")
    print("-" * 60)
    if feedbacks:
        enriched, cluster_info = cluster_feedbacks(feedbacks)
        stats["service_feedbacks"] = enriched
        stats["feedback_clusters"] = cluster_info
        # master_stats의 피드백 콘텐츠에 클러스터 라벨 주입
        enrich_master_stats_with_clusters(stats)
        multi_clusters = [c for c in cluster_info.values() if c["size"] >= 2]
        print(f"✓ 총 {len(cluster_info)}개 클러스터 (2건+: {len(multi_clusters)}개)")
        for c in sorted(multi_clusters, key=lambda x: -x["size"])[:5]:
            print(f"  - [{c['size']}건] {c['label'][:70]}")
    else:
        print("  피드백 없음 - 클러스터링 생략")
    print()

    # 4. 리포트 생성
    print("4️⃣  리포트 생성")
    print("-" * 60)

    # 출력 디렉토리 설정
    output_dir = os.getenv("REPORT_OUTPUT_DIR", "./reports")
    os.makedirs(output_dir, exist_ok=True)

    # 파일명 생성 (YYYY-MM-DD 형식)
    output_filename = f"weekly_report_{start_date}.md"
    output_path = os.path.join(output_dir, output_filename)

    generator = ReportGenerator()

    print("리포트 생성 중...")
    report = generator.generate_report(
        stats,
        start_date,
        end_date,
        output_path=output_path
    )

    print(f"✓ 리포트 생성 완료")
    print(f"✓ 저장 위치: {output_path}")
    print()

    # 4-1. 톤 검수 (부서 귀책/평가 표현 자동 교정)
    print("4️⃣ -1 톤 검수 (부서 귀책/평가 표현)")
    print("-" * 60)
    fixed_report, review_stats = review_report(report)
    print(f"✓ 검수 완료: {review_stats['fixed_sections']}/{review_stats['total_sections']} 섹션 수정, {review_stats['total_issues']}건 교정")

    if review_stats["total_issues"] > 0:
        # 교정된 리포트로 덮어쓰기
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(fixed_report)
        report = fixed_report
        print(f"✓ 교정된 리포트 저장 완료")

        # 교정 내역 저장
        review_log_path = output_path.replace(".md", "_tone_review.json")
        import json as _json
        with open(review_log_path, "w", encoding="utf-8") as f:
            _json.dump(review_stats, f, ensure_ascii=False, indent=2)
        print(f"✓ 교정 내역: {review_log_path}")
    print()

    # 5. 완료
    print("=" * 60)
    print("✅ 주간 리포트 생성 완료!")
    print("=" * 60)
    print()

    # 리포트 미리보기 (처음 30줄)
    print("📄 리포트 미리보기:")
    print("-" * 60)
    lines = report.split('\n')
    for line in lines[:30]:
        print(line)

    if len(lines) > 30:
        print("\n... (전체 내용은 생성된 파일을 확인하세요)")


if __name__ == "__main__":
    main()
