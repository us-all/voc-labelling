"""Golden set 270건에 2-depth 서브태그 부여

사용법: python scripts/add_subtags_to_golden.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.classifier_v4.two_depth_classifier import TwoDepthClassifier, strip_workflow_buttons

GOLDEN_PATH = Path("data/channel_io/golden/golden_multilabel_270.json")
OUTPUT_PATH = Path("data/channel_io/golden/golden_multilabel_270_with_subtags.json")


def main():
    # Golden set 로드
    with open(GOLDEN_PATH, encoding="utf-8") as f:
        items = json.load(f)

    print(f"Golden set: {len(items)}건")

    # 2-depth 분류기 초기화
    classifier = TwoDepthClassifier(
        model_dir="models/channel_3class/kcelectra-base-v2022-v9-boost/final_model",
        llm_model="claude-haiku-4-5-20251001",
        confidence_threshold=0.9,
        subtag_all=True,  # 모든 건에 서브태그 부여
    )

    # 분류 실행
    for i, item in enumerate(items):
        text = item.get("text", "")
        route = item.get("route", "")

        # abandoned는 분류 스킵
        if route == "abandoned":
            item["subtags"] = []
            item["kcelectra_topic"] = None
            item["kcelectra_confidence"] = None
            continue

        # 2-depth 분류
        result = classifier.classify(text)

        item["kcelectra_topic"] = result["topic"]
        item["kcelectra_confidence"] = result["confidence"]
        item["kcelectra_source"] = result.get("source", "kcelectra")
        item["subtag"] = result.get("subtag", "기타")

        # 기존 topics (multi-label)의 각 topic에 대해서도 서브태그 부여
        subtags = []
        for topic in item.get("topics", []):
            if topic in ["기타"]:
                subtags.append({"topic": topic, "subtag": "기타"})
                continue
            # topic 이름 매핑 (golden set의 topics 형식 → classifier 형식)
            mapped_topic = topic
            if topic in ["결제·환불", "구독·멤버십"]:
                mapped_topic = "결제·구독"
            st = classifier.classify_subtag(text, mapped_topic)
            subtags.append({"topic": topic, "subtag": st})

        item["subtags"] = subtags

        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(items)} 완료 | [{result.get('source','?')}] {result['topic']} > {result.get('subtag','?')}")

    # 저장
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    print(f"\n저장: {OUTPUT_PATH}")

    # 요약 통계
    subtag_counts = {}
    for item in items:
        for st in item.get("subtags", []):
            key = f"{st['topic']} > {st['subtag']}"
            subtag_counts[key] = subtag_counts.get(key, 0) + 1

    print("\n서브태그 분포:")
    for key, cnt in sorted(subtag_counts.items(), key=lambda x: -x[1]):
        print(f"  {key}: {cnt}건")


if __name__ == "__main__":
    main()
