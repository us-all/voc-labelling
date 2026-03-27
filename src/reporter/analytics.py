"""주간 리포트를 위한 통계 분석 모듈"""
import re
from typing import List, Dict, Any
from collections import defaultdict, Counter
from src.utils.text_utils import clean_text


class WeeklyAnalytics:
    """주간 데이터 통계 분석"""

    def __init__(self):
        """WeeklyAnalytics 초기화"""
        pass

    def _get_master_group_name(self, master_name: str) -> str:
        """
        마스터 이름에서 숫자를 제거하여 그룹명 반환
        예: 서재형2 -> 서재형, 서재형3 -> 서재형
        """
        if not master_name:
            return "Unknown"
        # 이름 끝의 숫자 제거 (예: 서재형2 -> 서재형)
        return re.sub(r'\d+$', '', master_name).strip()

    def analyze_weekly_data(
        self,
        letters: List[Dict[str, Any]],
        posts: List[Dict[str, Any]],
        previous_letters: List[Dict[str, Any]] = None,
        previous_posts: List[Dict[str, Any]] = None,
        prev_week_counts: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        주간 데이터 통계 분석

        Args:
            letters: 이번 주 편지글
            posts: 이번 주 게시글
            previous_letters: 전주 편지글 (선택)
            previous_posts: 전주 게시글 (선택)
            prev_week_counts: 전주 카운트 데이터 (선택) - {"letters": {masterId: count}, "posts": {masterId: count}}

        Returns:
            통계 분석 결과
        """
        # 전체 통계
        total_stats = self._calculate_total_stats(
            letters, posts,
            previous_letters, previous_posts,
            prev_week_counts
        )

        # 마스터별 통계
        master_stats = self._calculate_master_stats(
            letters, posts,
            previous_letters, previous_posts,
            prev_week_counts
        )

        # 카테고리별 통계 (topic 기반)
        category_stats = self._calculate_category_stats(letters, posts)

        # 서비스 피드백 추출 (topic == "대응 필요")
        service_feedbacks = self._extract_service_feedbacks(letters, posts)

        # 태그 분포
        tag_stats = self._calculate_tag_stats(letters, posts)

        # 감정 분포
        sentiment_stats = self._calculate_sentiment_stats(letters, posts)

        return {
            "total_stats": total_stats,
            "master_stats": master_stats,
            "category_stats": category_stats,
            "service_feedbacks": service_feedbacks,
            "tag_stats": tag_stats,
            "sentiment_stats": sentiment_stats,
        }

    def _calculate_tag_stats(
        self,
        letters: List[Dict[str, Any]],
        posts: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """전체 태그 분포 계산"""
        tag_counts = Counter()
        for item in letters + posts:
            tags = item.get("tags", [])
            if isinstance(tags, list):
                for tag in tags:
                    tag_counts[tag] += 1
        return dict(tag_counts)

    def _calculate_sentiment_stats(
        self,
        letters: List[Dict[str, Any]],
        posts: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """전체 감정 분포 계산"""
        sentiment_counts = Counter()
        for item in letters + posts:
            sentiment = item.get("sentiment", "미분류")
            sentiment_counts[sentiment] += 1
        return dict(sentiment_counts)

    def _calculate_total_stats(
        self,
        letters: List[Dict[str, Any]],
        posts: List[Dict[str, Any]],
        previous_letters: List[Dict[str, Any]] = None,
        previous_posts: List[Dict[str, Any]] = None,
        prev_week_counts: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        전체 통계 계산

        Returns:
            {
                "this_week": {"letters": int, "posts": int, "total": int},
                "last_week": {"letters": int, "posts": int, "total": int},
                "change": {"letters": int, "posts": int, "total": int}
            }
        """
        this_week = {
            "letters": len(letters),
            "posts": len(posts),
            "total": len(letters) + len(posts)
        }

        # prev_week_counts가 있으면 우선 사용
        if prev_week_counts:
            prev_letters_total = sum(prev_week_counts.get("letters", {}).values())
            prev_posts_total = sum(prev_week_counts.get("posts", {}).values())
            last_week = {
                "letters": prev_letters_total,
                "posts": prev_posts_total,
                "total": prev_letters_total + prev_posts_total
            }
        else:
            last_week = {
                "letters": len(previous_letters) if previous_letters else 0,
                "posts": len(previous_posts) if previous_posts else 0,
                "total": (len(previous_letters) if previous_letters else 0) +
                         (len(previous_posts) if previous_posts else 0)
            }

        change = {
            "letters": this_week["letters"] - last_week["letters"],
            "posts": this_week["posts"] - last_week["posts"],
            "total": this_week["total"] - last_week["total"]
        }

        return {
            "this_week": this_week,
            "last_week": last_week,
            "change": change
        }

    def _calculate_master_stats(
        self,
        letters: List[Dict[str, Any]],
        posts: List[Dict[str, Any]],
        previous_letters: List[Dict[str, Any]] = None,
        previous_posts: List[Dict[str, Any]] = None,
        prev_week_counts: Dict[str, Any] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        마스터별 통계 계산 (동일 마스터의 여러 클럽 합산)

        Returns:
            {
                "master_group_name": {
                    "this_week": {"letters": int, "posts": int, "total": int},
                    "last_week": {"letters": int, "posts": int, "total": int},
                    "change": {"letters": int, "posts": int, "total": int},
                    "tags": Counter,
                    "contents": [...],
                    "club_names": set()
                }
            }
        """
        master_stats = defaultdict(lambda: {
            "this_week": {"letters": 0, "posts": 0, "total": 0},
            "last_week": {"letters": 0, "posts": 0, "total": 0},
            "tags": Counter(),
            "contents": [],
            "club_names": set()
        })

        # 이번 주 데이터 집계
        for letter in letters:
            # masterName에서 숫자를 제거해 그룹화
            master_name = letter.get("masterName", "Unknown")
            master_group = self._get_master_group_name(master_name)

            master_stats[master_group]["this_week"]["letters"] += 1
            master_stats[master_group]["this_week"]["total"] += 1

            # 클럽명 수집
            club_name = letter.get("masterClubName", "")
            if club_name:
                master_stats[master_group]["club_names"].add(club_name)

            # 태그 집계
            item_tags = letter.get("tags", [])
            if isinstance(item_tags, list):
                for tag in item_tags:
                    master_stats[master_group]["tags"][tag] += 1

            # 콘텐츠 저장
            topic = letter.get("topic", "")
            sentiment = letter.get("sentiment", "")
            summary = letter.get("summary", "")
            master_stats[master_group]["contents"].append({
                "type": "letter",
                "content": clean_text(letter.get("message", ""), 150),
                "topic": topic,
                "sentiment": sentiment,
                "summary": summary,
                "tags": item_tags if isinstance(item_tags, list) else [],
                "createdAt": letter.get("createdAt", letter.get("created_at", "")),
                "masterName": master_name,
                "masterClubName": club_name
            })

        for post in posts:
            # masterName에서 숫자를 제거해 그룹화
            master_name = post.get("masterName", "Unknown")
            master_group = self._get_master_group_name(master_name)

            master_stats[master_group]["this_week"]["posts"] += 1
            master_stats[master_group]["this_week"]["total"] += 1

            # 클럽명 수집
            club_name = post.get("masterClubName", "")
            if club_name:
                master_stats[master_group]["club_names"].add(club_name)

            # 태그 집계
            item_tags = post.get("tags", [])
            if isinstance(item_tags, list):
                for tag in item_tags:
                    master_stats[master_group]["tags"][tag] += 1

            # 콘텐츠 저장
            content = post.get("textBody") or post.get("body", "")
            topic = post.get("topic", "")
            sentiment = post.get("sentiment", "")
            summary = post.get("summary", "")
            master_stats[master_group]["contents"].append({
                "type": "post",
                "content": clean_text(content, 150),
                "topic": topic,
                "sentiment": sentiment,
                "summary": summary,
                "tags": item_tags if isinstance(item_tags, list) else [],
                "title": post.get("title", ""),
                "createdAt": post.get("createdAt", post.get("created_at", "")),
                "masterName": master_name,
                "masterClubName": club_name
            })

        # 전주 데이터 집계
        # prev_week_counts 사용 (masterId -> masterName 매핑 필요)
        if prev_week_counts:
            # masterId -> masterName 매핑 생성
            master_id_to_name = {}
            for letter in letters:
                master_id = letter.get("masterId", "") or letter.get("master_id", "")
                master_name = letter.get("masterName", "Unknown")
                if master_id:
                    master_id_to_name[master_id] = self._get_master_group_name(master_name)
            for post in posts:
                master_id = post.get("postBoardId", "") or post.get("masterId", "") or post.get("master_id", "")
                master_name = post.get("masterName", "Unknown")
                if master_id:
                    master_id_to_name[master_id] = self._get_master_group_name(master_name)

            # 전주 편지 카운트 적용
            for master_id, count in prev_week_counts.get("letters", {}).items():
                master_group = master_id_to_name.get(master_id)
                if master_group:
                    master_stats[master_group]["last_week"]["letters"] += count
                    master_stats[master_group]["last_week"]["total"] += count

            # 전주 게시글 카운트 적용
            for master_id, count in prev_week_counts.get("posts", {}).items():
                master_group = master_id_to_name.get(master_id)
                if master_group:
                    master_stats[master_group]["last_week"]["posts"] += count
                    master_stats[master_group]["last_week"]["total"] += count

        elif previous_letters or previous_posts:
            if previous_letters:
                for letter in previous_letters:
                    master_name = letter.get("masterName", "Unknown")
                    master_group = self._get_master_group_name(master_name)
                    master_stats[master_group]["last_week"]["letters"] += 1
                    master_stats[master_group]["last_week"]["total"] += 1

                    # 클럽명 수집
                    club_name = letter.get("masterClubName", "")
                    if club_name:
                        master_stats[master_group]["club_names"].add(club_name)

            if previous_posts:
                for post in previous_posts:
                    master_name = post.get("masterName", "Unknown")
                    master_group = self._get_master_group_name(master_name)
                    master_stats[master_group]["last_week"]["posts"] += 1
                    master_stats[master_group]["last_week"]["total"] += 1

                    # 클럽명 수집
                    club_name = post.get("masterClubName", "")
                    if club_name:
                        master_stats[master_group]["club_names"].add(club_name)

        # 증감 계산
        for master_group in master_stats:
            master_stats[master_group]["change"] = {
                "letters": (master_stats[master_group]["this_week"]["letters"] -
                           master_stats[master_group]["last_week"]["letters"]),
                "posts": (master_stats[master_group]["this_week"]["posts"] -
                         master_stats[master_group]["last_week"]["posts"]),
                "total": (master_stats[master_group]["this_week"]["total"] -
                         master_stats[master_group]["last_week"]["total"])
            }

        return dict(master_stats)

    def _calculate_category_stats(
        self,
        letters: List[Dict[str, Any]],
        posts: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """
        카테고리별 통계 계산 (topic 필드 기반)

        Returns:
            {"대응 필요": int, "콘텐츠·투자": int, ...}
        """
        category_counts = Counter()

        for letter in letters:
            topic = letter.get("topic", "미분류")
            category_counts[topic] += 1

        for post in posts:
            topic = post.get("topic", "미분류")
            category_counts[topic] += 1

        return dict(category_counts)

    def _extract_service_feedbacks(
        self,
        letters: List[Dict[str, Any]],
        posts: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        서비스 관련 피드백 추출 (topic == "대응 필요")

        Returns:
            [{"content": str, "reason": str, "masterId": str}, ...]
        """
        feedbacks = []

        for letter in letters:
            topic = letter.get("topic", "")
            if topic == "대응 필요":
                feedbacks.append({
                    "type": "letter",
                    "content": clean_text(letter.get("message", ""), 200),
                    "reason": letter.get("summary", ""),
                    "masterId": letter.get("masterId", "") or letter.get("master_id", "unknown"),
                    "createdAt": letter.get("createdAt", letter.get("created_at", "")),
                    "sentiment": letter.get("sentiment", ""),
                    "tags": letter.get("tags", []),
                })

        for post in posts:
            topic = post.get("topic", "")
            if topic == "대응 필요":
                content = post.get("textBody") or post.get("body", "")
                feedbacks.append({
                    "type": "post",
                    "title": post.get("title", ""),
                    "content": clean_text(content, 200),
                    "reason": post.get("summary", ""),
                    "masterId": post.get("postBoardId", "") or post.get("masterId", "") or post.get("master_id", "unknown"),
                    "createdAt": post.get("createdAt", post.get("created_at", "")),
                    "sentiment": post.get("sentiment", ""),
                    "tags": post.get("tags", []),
                })

        return feedbacks

    def get_top_contents_by_category(
        self,
        letters: List[Dict[str, Any]],
        posts: List[Dict[str, Any]],
        category: str,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        특정 카테고리(topic)의 상위 콘텐츠 추출

        Args:
            letters: 편지글 리스트
            posts: 게시글 리스트
            category: topic명
            limit: 반환할 최대 개수

        Returns:
            해당 카테고리의 콘텐츠 리스트
        """
        contents = []

        for letter in letters:
            if letter.get("topic") == category:
                contents.append({
                    "type": "letter",
                    "content": clean_text(letter.get("message", ""), 200),
                    "masterId": letter.get("masterId", "") or letter.get("master_id", ""),
                    "createdAt": letter.get("createdAt", letter.get("created_at", ""))
                })

        for post in posts:
            if post.get("topic") == category:
                content_text = post.get("textBody") or post.get("body", "")
                contents.append({
                    "type": "post",
                    "title": post.get("title", ""),
                    "content": clean_text(content_text, 200),
                    "masterId": post.get("postBoardId", "") or post.get("master_id", ""),
                    "createdAt": post.get("createdAt", post.get("created_at", "")),
                    "likeCount": post.get("likeCount", 0),
                    "replyCount": post.get("replyCount", 0)
                })

        # 게시글의 경우 좋아요+댓글 수로 정렬
        contents_sorted = sorted(
            contents,
            key=lambda x: (x.get("likeCount", 0) + x.get("replyCount", 0)),
            reverse=True
        )

        return contents_sorted[:limit]
