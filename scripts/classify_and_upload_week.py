"""특정 주간 데이터 분류 + BigQuery 업로드

사용:
    python scripts/classify_and_upload_week.py 2026-03-30 2026-04-06

흐름:
1. BigQuery 원본 조회 (편지 + 게시글)
2. v5 Bedrock Haiku 분류
3. 날짜별로 그룹핑 → voc_labelled.letters_posts에 업로드
4. 로컬 캐시도 저장
"""
import sys
import os
import json
import time
import logging
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.bigquery.client import BigQueryClient
from src.bigquery.queries import WeeklyDataQuery
from src.bigquery.writer import BigQueryWriter
from src.classifier_v5.bedrock_classifier import BedrockV5Classifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)


def fetch_data(start_date, end_date):
    client = BigQueryClient()
    query = WeeklyDataQuery(client)

    logger.info(f"BigQuery 조회: {start_date} ~ {end_date}")

    # 마스터 정보
    master_info = query.get_master_info()

    # 편지
    letters = query.get_weekly_letters(start_date, end_date)
    for item in letters:
        mid = item.get("masterId")
        if mid and mid in master_info:
            item["masterName"] = master_info[mid]["displayName"]
            item["masterClubName"] = master_info[mid]["clubName"]
        else:
            item["masterName"] = "Unknown"
            item["masterClubName"] = "Unknown"

    # 게시글
    posts = query.get_weekly_posts(start_date, end_date)
    board_query = f"""
    SELECT _id as boardId, masterId
    FROM `{client.project_id}.us_plus_next.postboards`
    """
    board_to_master = {b["boardId"]: b["masterId"] for b in client.execute_query(board_query)}

    for item in posts:
        board_id = item.get("postBoardId")
        actual_master_id = board_to_master.get(board_id, board_id)
        if actual_master_id and actual_master_id in master_info:
            item["masterName"] = master_info[actual_master_id]["displayName"]
            item["masterClubName"] = master_info[actual_master_id]["clubName"]
            item["masterId"] = actual_master_id
        else:
            item["masterName"] = "Unknown"
            item["masterClubName"] = "Unknown"

    logger.info(f"조회: 편지 {len(letters)}건 + 게시글 {len(posts)}건")
    return letters, posts, client


def classify_batch(letters, posts):
    classifier = BedrockV5Classifier(max_workers=30)

    all_items = []
    for item in letters:
        all_items.append({
            "_id": item.get("_id", ""),
            "source_type": "letter",
            "masterId": item.get("masterId", ""),
            "masterName": item.get("masterName", ""),
            "masterClubName": item.get("masterClubName", ""),
            "userId": item.get("userId", ""),
            "message": item.get("message", ""),  # classifier용
            "text": item.get("message", ""),  # 캐시용
            "createdAt": str(item.get("createdAt", ""))[:19],
        })
    for item in posts:
        content = item.get("textBody", "") or item.get("body", "")
        all_items.append({
            "_id": item.get("_id", ""),
            "source_type": "post",
            "masterId": item.get("masterId", ""),
            "masterName": item.get("masterName", ""),
            "masterClubName": item.get("masterClubName", ""),
            "userId": item.get("userId", ""),
            "textBody": content,  # classifier용
            "text": content,  # 캐시용
            "createdAt": str(item.get("createdAt", ""))[:19],
        })

    logger.info(f"분류 시작: {len(all_items)}건 (workers=30)")
    start = time.time()
    done = 0

    with ThreadPoolExecutor(max_workers=30) as executor:
        futures = {}
        for i, item in enumerate(all_items):
            text = item.get("message") or item.get("textBody", "")
            futures[executor.submit(classifier.classify_single, text)] = i

        for future in as_completed(futures):
            idx = futures[future]
            result = future.result()
            # classification dict 구조로 저장 (writer 호환)
            all_items[idx]["classification"] = result
            # 캐시용 평탄화
            all_items[idx].update(result)
            done += 1
            if done % 500 == 0 or done == len(all_items):
                rate = done / (time.time() - start)
                logger.info(f"  {done}/{len(all_items)} ({rate:.1f}건/초)")

    cost = classifier.get_cost_report()
    logger.info(f"분류 완료: ${cost['cost_usd']}, 에러 {cost['errors']}")
    return all_items


def upload_to_bigquery(items, client):
    writer = BigQueryWriter(client.client)

    # pipeline_date 별로 그룹핑 (createdAt 기준)
    by_date = defaultdict(list)
    for item in items:
        created = item.get("createdAt", "")[:10]
        if created:
            by_date[created].append(item)

    logger.info(f"BigQuery 업로드 시작: {len(by_date)}개 날짜")
    total = 0
    for date_str in sorted(by_date.keys()):
        day_items = by_date[date_str]
        count = writer.write_letters_posts(
            day_items,
            pipeline_date=date_str,
            classifier_model="bedrock-haiku-4.5",
        )
        logger.info(f"  {date_str}: {count}건 업로드")
        total += count

    logger.info(f"BigQuery 업로드 완료: {total}건")
    return total


def main():
    if len(sys.argv) < 3:
        print("사용: python classify_and_upload_week.py <start> <end>")
        print("예: python classify_and_upload_week.py 2026-03-30 2026-04-06")
        sys.exit(1)

    start_date = sys.argv[1]
    end_date = sys.argv[2]

    # 1. BigQuery 원본 조회
    letters, posts, bq_client = fetch_data(start_date, end_date)

    if not letters and not posts:
        logger.error("데이터 없음")
        return

    # 2. v5 분류
    items = classify_batch(letters, posts)

    # 3. 로컬 캐시 저장
    os.makedirs("exports", exist_ok=True)
    cache_path = f"exports/classified_{start_date}_{end_date}.json"
    with open(cache_path, "w") as f:
        json.dump(items, f, ensure_ascii=False)
    logger.info(f"로컬 캐시 저장: {cache_path}")

    # 4. BigQuery 업로드
    upload_to_bigquery(items, bq_client)

    print("\n완료!")
    print(f"  기간: {start_date} ~ {end_date}")
    print(f"  건수: {len(items)}건")
    print(f"  캐시: {cache_path}")


if __name__ == "__main__":
    main()
