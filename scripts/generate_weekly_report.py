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


def load_from_voc_labelled(client, start_date, end_date):
    """voc_labelled.letters_posts에서 이미 분류된 데이터 조회"""
    query = f"""
    SELECT id, source_type, master_id, master_name, user_id,
           content, created_at, topic, sentiment, summary, tags,
           confidence, pipeline_date
    FROM `{client.project_id}.voc_labelled.letters_posts`
    WHERE pipeline_date >= '{start_date}' AND pipeline_date < '{end_date}'
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

    previous_letters, previous_posts = load_from_voc_labelled(client, prev_start, prev_end)

    if previous_letters or previous_posts:
        print(f"✓ 전주 데이터 로드: 편지 {len(previous_letters)}건, 게시글 {len(previous_posts)}건")
    else:
        print(f"❌ 전주 데이터 없음 (첫 실행 또는 전주 데이터 미생성)")
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
