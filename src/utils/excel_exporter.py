"""엑셀 파일 내보내기 유틸리티"""
import os
from typing import List, Dict, Any
from datetime import datetime
import pandas as pd


def export_to_excel(
    letters: List[Dict[str, Any]],
    posts: List[Dict[str, Any]],
    output_path: str
) -> str:
    """
    분류된 데이터를 엑셀 파일로 내보내기

    Args:
        letters: 분류된 편지글 리스트
        posts: 분류된 게시글 리스트
        output_path: 출력 파일 경로

    Returns:
        생성된 엑셀 파일 경로
    """
    # 편지글 데이터 변환
    letter_rows = []
    for letter in letters:
        cls = letter.get("classification", {})
        letter_rows.append({
            "마스터": letter.get("masterName", "Unknown"),
            "클럽": letter.get("masterClubName", ""),
            "내용": letter.get("message", ""),
            "라벨": cls.get("category", "") or letter.get("topic", "미분류"),
            "세부분류": cls.get("subtag", "") or letter.get("subtag", ""),
            "날짜": _format_date(letter.get("createdAt", "")),
            "차단": "Y" if letter.get("isBlock") == "true" else "N"
        })

    # 게시글 데이터 변환
    post_rows = []
    for post in posts:
        content = post.get("textBody") or post.get("body", "")
        cls = post.get("classification", {})
        post_rows.append({
            "마스터": post.get("masterName", "Unknown"),
            "클럽": post.get("masterClubName", ""),
            "제목": post.get("title", ""),
            "내용": content,
            "라벨": cls.get("category", "") or post.get("topic", "미분류"),
            "세부분류": cls.get("subtag", "") or post.get("subtag", ""),
            "날짜": _format_date(post.get("createdAt", "")),
            "차단": "Y" if post.get("isBlock") == "true" else "N"
        })

    # DataFrame 생성
    df_letters = pd.DataFrame(letter_rows)
    df_posts = pd.DataFrame(post_rows)

    # 디렉토리 생성
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 엑셀 파일로 저장 (시트 분리)
    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        if not df_letters.empty:
            df_letters.to_excel(writer, sheet_name='편지글', index=False)
            _set_column_widths(writer.sheets['편지글'], df_letters)
        if not df_posts.empty:
            df_posts.to_excel(writer, sheet_name='게시글', index=False)
            _set_column_widths(writer.sheets['게시글'], df_posts)

    return output_path


def _set_column_widths(worksheet, df: pd.DataFrame):
    """열 너비 설정"""
    # 기본 열 너비 설정
    column_widths = {
        '마스터': 15,
        '클럽': 20,
        '제목': 40,
        '내용': 80,
        '라벨': 15,
        '세부분류': 15,
        '날짜': 18,
        '차단': 8
    }

    for i, col in enumerate(df.columns):
        col_letter = chr(65 + i)  # A, B, C, ...
        width = column_widths.get(col, 15)
        worksheet.column_dimensions[col_letter].width = width


def _format_date(date_str) -> str:
    """날짜 포맷 변환 (str 또는 datetime 모두 처리)"""
    if not date_str:
        return ""
    # datetime/Timestamp 객체는 문자열로 변환 (tz 제거)
    if hasattr(date_str, "strftime"):
        # KST 변환이 필요하면 호출부에서 처리. 여기선 단순 포맷.
        try:
            return date_str.astimezone().strftime("%Y-%m-%d %H:%M")
        except Exception:
            return date_str.strftime("%Y-%m-%d %H:%M")
    try:
        if "T" in date_str:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d %H:%M")
        return date_str
    except Exception:
        return str(date_str)
