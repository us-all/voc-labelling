"""Golden set 270건에 규칙 기반 서브태그 부여 (API 없이)

키워드 매칭으로 서브태그 부여. LLM fallback 없이 동작.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

GOLDEN_PATH = Path("data/channel_io/golden/golden_multilabel_270.json")
OUTPUT_PATH = Path("data/channel_io/golden/golden_multilabel_270_with_subtags.json")

# 서브태그별 키워드 규칙 (subtag_prompt.py 기반)
SUBTAG_RULES = {
    "결제·환불": {
        "환불": ["환불", "결제 취소", "카드 취소", "돈 돌려", "취소해주세요"],
        "자동결제항의": ["자동결제", "자동 결제", "신청 안했는데", "왜 결제", "의사 없이", "자동으로 결제"],
        "결제확인": ["결제 됐나", "입금 확인", "결제 내역", "결제가 안", "결제 확인"],
    },
    "구독·멤버십": {
        "구독해지": ["해지", "구독 취소", "탈퇴", "구독취소", "더이상 결제", "구독 중단"],
        "상품변경": ["변경", "1개월에서", "6개월로", "상품 바꾸", "다른 상품", "업그레이드"],
        "카드변경": ["카드 변경", "카드 분실", "다른 카드", "결제 카드"],
        "신규가입": ["가입", "신청 방법", "어떻게 구독", "결제 방법", "구독하고 싶"],
        "결제확인": ["결제 됐나", "입금 확인", "결제 내역", "재결제"],
    },
    "콘텐츠·수강": {
        "수강방법": ["어떻게 수강", "어떻게 들", "어떻게 보", "수강 방법", "줌 어떻게", "시청 방법", "라이브 어떻게", "어디서 보", "어디서 들"],
        "오프라인참석": ["오프라인", "장소", "참석", "현장", "세미나"],
        "동반참석": ["동반", "같이", "지인", "와이프", "남편"],
        "불참통보": ["불참", "못 갈", "취소합니다", "참석 취소"],
        "녹화본요청": ["녹화", "다시보기", "리플레이", "다시 볼"],
        "자료요청": ["자료", "교재", "책 배송", "PDF", "다운로드"],
        "콘텐츠질문": ["종목", "포트폴리오", "투자", "주식", "수업 내용"],
        "서비스피드백": ["기대와 달라", "불만", "개선", "시간대 변경"],
    },
    "기술·오류": {
        "로그인불가": ["로그인", "비밀번호", "계정", "가입 안된"],
        "앱오류": ["앱이 안", "설치", "업데이트", "크래시", "앱 오류"],
        "콘텐츠접근차단": ["안 보여", "안보여", "멤버십 전용", "시청이 안", "접근이 안", "볼 수 없"],
        "결제연동": ["결제했는데", "입금했는데", "반영이 안"],
        "영상재생오류": ["재생", "끊김", "오디오", "영상이 안", "소리가 안"],
        "기능건의": ["자막", "검색 기능", "다크 모드", "프린트", "기능 추가"],
        "계정변경": ["번호 변경", "번호 바뀌", "아이디 변경", "카카오에서"],
    },
}


def classify_subtag(text: str, topic: str) -> str:
    """키워드 기반 서브태그 분류"""
    rules = SUBTAG_RULES.get(topic, {})
    text_lower = text.lower()

    for subtag, keywords in rules.items():
        for kw in keywords:
            if kw in text_lower:
                return subtag

    return "기타"


def main():
    with open(GOLDEN_PATH, encoding="utf-8") as f:
        items = json.load(f)

    print(f"Golden set: {len(items)}건")

    for item in items:
        text = item.get("text", "")
        route = item.get("route", "")

        if route == "abandoned":
            item["subtags"] = []
            continue

        # 각 topic에 대해 서브태그 부여
        subtags = []
        for topic in item.get("topics", []):
            st = classify_subtag(text, topic)
            subtags.append({"topic": topic, "subtag": st})

        item["subtags"] = subtags

    # 저장
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    print(f"저장: {OUTPUT_PATH}")

    # 통계
    subtag_counts = {}
    for item in items:
        for st in item.get("subtags", []):
            key = f"{st['topic']} > {st['subtag']}"
            subtag_counts[key] = subtag_counts.get(key, 0) + 1

    print("\n서브태그 분포:")
    for key, cnt in sorted(subtag_counts.items(), key=lambda x: -x[1]):
        print(f"  {key}: {cnt}건")

    # 기타 비율
    total = sum(subtag_counts.values())
    etc = sum(v for k, v in subtag_counts.items() if k.endswith("> 기타"))
    print(f"\n기타 비율: {etc}/{total} ({round(etc/total*100,1)}%)")


if __name__ == "__main__":
    main()
