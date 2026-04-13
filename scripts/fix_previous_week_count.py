"""전주 count만 us_plus_next 원본에서 직접 조회 → 로컬 파일 저장

주간 리포트는 이 파일을 참조할 수 있음 (선택).
"""
import sys, os, json
sys.path.insert(0, "/Users/gygygygy/Documents/ai/letter-post-weekly-report")

from src.bigquery.client import BigQueryClient
from src.bigquery.queries import WeeklyDataQuery

# 지난주(이번 리포트 전주) = 3/23 ~ 3/30
start = "2026-03-23"
end = "2026-03-30"

client = BigQueryClient()
query = WeeklyDataQuery(client)

master_info = query.get_master_info()

letters = query.get_weekly_letters(start, end)
for item in letters:
    mid = item.get("masterId")
    if mid in master_info:
        item["masterName"] = master_info[mid]["displayName"]
        item["masterClubName"] = master_info[mid]["clubName"]
    else:
        item["masterName"] = "Unknown"

posts = query.get_weekly_posts(start, end)
board_query = f"""
SELECT _id as boardId, masterId
FROM `{client.project_id}.us_plus_next.postboards`
"""
board_to_master = {b["boardId"]: b["masterId"] for b in client.execute_query(board_query)}
for item in posts:
    actual_master_id = board_to_master.get(item.get("postBoardId"), "")
    if actual_master_id in master_info:
        item["masterName"] = master_info[actual_master_id]["displayName"]
        item["masterClubName"] = master_info[actual_master_id]["clubName"]
    else:
        item["masterName"] = "Unknown"

print(f"전주({start}~{end}): 편지 {len(letters)}건, 게시글 {len(posts)}건")

# 마스터별 집계
from collections import Counter
letter_counts = Counter(l["masterName"] for l in letters)
post_counts = Counter(p["masterName"] for p in posts)
print("\n편지 Top 5:")
for m, c in letter_counts.most_common(5):
    print(f"  {m}: {c}")
