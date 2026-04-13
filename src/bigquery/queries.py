"""주간 리포트를 위한 BigQuery 쿼리 모듈"""
from typing import List, Dict, Any
from datetime import datetime, timedelta
from .client import BigQueryClient


class WeeklyDataQuery:
    """주간 데이터 조회를 위한 쿼리 클래스"""

    def __init__(self, client: BigQueryClient):
        """
        WeeklyDataQuery 초기화

        Args:
            client: BigQueryClient 인스턴스
        """
        self.client = client
        self.project_id = client.project_id
        self.dataset_id = "us_plus_next"

    @staticmethod
    def _kst_to_utc(date_str: str) -> str:
        """
        KST 날짜(YYYY-MM-DD)를 UTC 타임스탬프로 변환
        KST 자정 = UTC 전날 15:00

        Args:
            date_str: KST 날짜 (YYYY-MM-DD)

        Returns:
            UTC 타임스탬프 (YYYY-MM-DDTHH:MM:SS.000Z)
        """
        date = datetime.strptime(date_str, '%Y-%m-%d')
        # KST 자정 = UTC -9시간 = 전날 15:00
        utc_time = date - timedelta(hours=9)
        return utc_time.strftime('%Y-%m-%dT%H:%M:%S.000Z')

    def get_weekly_letters(
        self,
        start_date: str,
        end_date: str
    ) -> List[Dict[str, Any]]:
        """
        주간 편지글 조회

        Args:
            start_date: 시작 날짜 (YYYY-MM-DD, KST 기준)
            end_date: 종료 날짜 (YYYY-MM-DD, KST 기준, exclusive)

        Returns:
            편지글 데이터 리스트
        """
        start_utc = self._kst_to_utc(start_date)
        end_utc = self._kst_to_utc(end_date)

        query = f"""
        SELECT
            _id,
            message,
            masterId,
            userId,
            type,
            createdAt,
            isBlock,
            viewType
        FROM `{self.project_id}.{self.dataset_id}.usermastermessages`
        WHERE
            createdAt >= '{start_utc}'
            AND createdAt < '{end_utc}'
            AND type = 'LETTER'
        ORDER BY createdAt DESC
        """

        return self.client.execute_query(query)

    def get_weekly_posts(
        self,
        start_date: str,
        end_date: str
    ) -> List[Dict[str, Any]]:
        """
        주간 게시글 조회

        Args:
            start_date: 시작 날짜 (YYYY-MM-DD, KST 기준)
            end_date: 종료 날짜 (YYYY-MM-DD, KST 기준, exclusive)

        Returns:
            게시글 데이터 리스트
        """
        start_utc = self._kst_to_utc(start_date)
        end_utc = self._kst_to_utc(end_date)

        query = f"""
        SELECT
            _id,
            title,
            body,
            textBody,
            userId,
            postBoardId,
            createdAt,
            likeCount,
            replyCount,
            isBlock,
            deleted
        FROM `{self.project_id}.{self.dataset_id}.posts`
        WHERE
            createdAt >= '{start_utc}'
            AND createdAt < '{end_utc}'
            AND deleted = 'false'
        ORDER BY createdAt DESC
        """

        return self.client.execute_query(query)

    def get_weekly_data(
        self,
        start_date: str,
        end_date: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        주간 편지글과 게시글 모두 조회

        Args:
            start_date: 시작 날짜 (YYYY-MM-DD)
            end_date: 종료 날짜 (YYYY-MM-DD)

        Returns:
            {'letters': [...], 'posts': [...]} 형태의 데이터
        """
        letters = self.get_weekly_letters(start_date, end_date)
        posts = self.get_weekly_posts(start_date, end_date)

        return {
            'letters': letters,
            'posts': posts
        }

    @staticmethod
    def get_last_week_range() -> tuple[str, str]:
        """
        지난 주의 날짜 범위 반환 (월요일 ~ 일요일)

        Returns:
            (start_date, end_date) 튜플 (YYYY-MM-DD 형식)
        """
        today = datetime.now()
        # 지난 주 월요일
        last_monday = today - timedelta(days=today.weekday() + 7)
        # 지난 주 일요일
        last_sunday = last_monday + timedelta(days=6)

        start_date = last_monday.strftime('%Y-%m-%d')
        end_date = (last_sunday + timedelta(days=1)).strftime('%Y-%m-%d')

        return start_date, end_date

    @staticmethod
    def get_previous_week_range() -> tuple[str, str]:
        """
        전전주(2주 전)의 날짜 범위 반환 (월요일 ~ 일요일)

        Returns:
            (start_date, end_date) 튜플 (YYYY-MM-DD 형식)
        """
        today = datetime.now()
        # 2주 전 월요일
        prev_monday = today - timedelta(days=today.weekday() + 14)
        # 2주 전 일요일
        prev_sunday = prev_monday + timedelta(days=6)

        start_date = prev_monday.strftime('%Y-%m-%d')
        end_date = (prev_sunday + timedelta(days=1)).strftime('%Y-%m-%d')

        return start_date, end_date

    def get_master_info(self) -> Dict[str, Dict[str, Any]]:
        """
        마스터 정보 조회 (ID -> 이름 매핑)
        postBoardId도 포함하여 게시판 -> 마스터 매핑 제공

        Returns:
            {
                "id": {
                    "name": str,
                    "displayName": str,
                    "clubName": str
                }
            }
        """
        # 마스터 정보 조회
        master_query = f"""
        SELECT
            _id as masterId,
            name,
            displayName,
            clubName
        FROM `{self.project_id}.{self.dataset_id}.masters`
        WHERE deleted = 'false'
        """

        masters = self.client.execute_query(master_query)

        # ID를 키로 하는 딕셔너리로 변환
        master_dict = {}
        for master in masters:
            master_id = master.get('masterId')
            if master_id:
                master_info = {
                    'name': master.get('name', ''),
                    'displayName': master.get('displayName', ''),
                    'clubName': master.get('clubName', '')
                }
                master_dict[master_id] = master_info

        # 게시판 -> 마스터 매핑 추가
        board_query = f"""
        SELECT
            _id as boardId,
            masterId,
            name as boardName
        FROM `{self.project_id}.{self.dataset_id}.postboards`
        """

        boards = self.client.execute_query(board_query)

        # postBoardId를 키로 추가 (masterId 정보 복사)
        for board in boards:
            board_id = board.get('boardId')
            master_id = board.get('masterId')
            if board_id and master_id and master_id in master_dict:
                master_dict[board_id] = master_dict[master_id].copy()

        return master_dict
