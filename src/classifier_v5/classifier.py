"""V5 Classifier — 4분류 (피드백/마스터 반응/시장·투자/일상)

v4 UnifiedClassifier와 동일 인터페이스. 프롬프트만 v5로 교체.
피드백 내부 서브그룹핑은 태그로 처리.
subtag 목록은 data/config/v5_subtags.json에서 로드 (코드 수정 없이 승격 가능).
"""
import json
import os
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

from .prompt import V5_SYSTEM_PROMPT

V5_TOPICS = ["피드백", "마스터 반응", "시장·투자", "일상"]
V5_SENTIMENTS = ["긍정", "부정", "중립"]

# subtag 목록: JSON 설정 파일에서 로드
_SUBTAG_CONFIG = Path(__file__).resolve().parents[2] / "data" / "config" / "v5_subtags.json"

def _load_subtags() -> dict:
    """v5_subtags.json에서 subtag 목록 로드. 파일이 없으면 fail-loud.

    과거에 컨테이너 빌드에서 data/ 가 .dockerignore 로 제외되며 이 파일이
    누락된 적이 있다 (4/12~4/26 사고). 폴백으로 silent 하게 진행하면
    valid subtag 목록이 ["기타"] 하나뿐이라 모든 분류가 "기타"로 강제된다.
    검증 깨짐을 즉시 인지하도록 명시적 RuntimeError 로 잡 자체를 실패시킨다.
    """
    if not _SUBTAG_CONFIG.exists():
        raise RuntimeError(
            f"subtag 설정 파일 누락: {_SUBTAG_CONFIG}. "
            "Docker 이미지에 data/config/v5_subtags.json 이 포함됐는지 확인 필요."
        )
    with open(_SUBTAG_CONFIG, encoding="utf-8") as f:
        return json.load(f)

# 모듈 import 자체는 성공시켜야 일간 파이프라인의 try/except 가 동작하고
# Slack 실패 알림이 발송된다. 실제 검증은 BedrockV5Classifier.__init__ 에서
# _load_subtags() 를 호출하면서 fail-loud 하게 잡힌다.
try:
    V5_SUBTAGS = _load_subtags()
except RuntimeError:
    V5_SUBTAGS = {}


def _parse_json_response(raw: str) -> dict:
    """LLM 응답에서 JSON 추출."""
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    if raw.startswith("{{") and raw.endswith("}}"):
        raw = raw[1:-1]
    return json.loads(raw)


class V5Classifier:
    """Single LLM call로 topic + sentiment + tags + summary 추출 (4분류)"""

    def __init__(self, model="claude-haiku-4-5-20251001", max_workers=10):
        self.client = Anthropic()
        self.model = model
        self.max_workers = max_workers
        self._input_tokens = 0
        self._output_tokens = 0
        self._errors = 0
        self._total = 0

    def classify_single(self, text: str) -> dict:
        """단일 건 분류 → {topic, sentiment, tags, summary, confidence}"""
        if not text or len(text.strip()) < 2:
            return {
                "topic": "일상",
                "subtag": "일상",
                "sentiment": "중립",
                "tags": [],
                "summary": "",
                "confidence": 1.0,
            }

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=350,
                system=V5_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": text[:500]}],
                timeout=30.0,
            )

            self._input_tokens += response.usage.input_tokens
            self._output_tokens += response.usage.output_tokens

            result = _parse_json_response(response.content[0].text.strip())

            topic = result.get("topic", "시장·투자")
            if topic not in V5_TOPICS:
                topic = "시장·투자"

            sentiment = result.get("sentiment", "중립")
            if sentiment not in V5_SENTIMENTS:
                sentiment = "중립"

            tags = result.get("tags", [])
            if not isinstance(tags, list):
                tags = []

            subtag = result.get("subtag", "기타")
            valid_subtags = V5_SUBTAGS.get(topic, ["기타"])
            if subtag not in valid_subtags:
                subtag = "기타"

            return {
                "topic": topic,
                "subtag": subtag,
                "sentiment": sentiment,
                "tags": tags[:4],
                "summary": str(result.get("summary", ""))[:200],
                "confidence": max(0.0, min(1.0, float(result.get("confidence", 0.5)))),
            }

        except Exception as e:
            self._errors += 1
            return {
                "topic": "시장·투자",
                "subtag": "기타",
                "sentiment": "중립",
                "tags": [],
                "summary": "",
                "confidence": 0.0,
                "error": str(e)[:80],
            }

    def classify_batch(self, items, content_field="message"):
        """ThreadPoolExecutor 병렬 분류."""
        results = [None] * len(items)
        start_time = time.time()
        self._total = len(items)

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_map = {}
            for i, item in enumerate(items):
                text = item.get(content_field, "") if isinstance(item, dict) else str(item)
                future = executor.submit(self.classify_single, text)
                future_map[future] = i

            done_count = 0
            for future in as_completed(future_map):
                idx = future_map[future]
                classification = future.result()
                if isinstance(items[idx], dict):
                    items[idx]["classification"] = classification
                results[idx] = classification
                done_count += 1
                if done_count % 50 == 0 or done_count == len(items):
                    elapsed = time.time() - start_time
                    rate = done_count / elapsed if elapsed > 0 else 0
                    print(f"    {done_count}/{len(items)} 완료 ({rate:.1f}건/초)")

        return items

    def reclassify_others(self, items, content_field="text"):
        """subtag='기타'인 항목들을 재분류.

        1차: 텍스트를 다시 읽고 기존 subtag 목록에서 가장 가까운 것 선택 (강제)
        2차: 그래도 안 맞으면 새 subtag 후보를 제안

        Returns: (reclassified_items, new_subtag_candidates)
        """
        others = [(i, item) for i, item in enumerate(items) if
                  isinstance(item, dict) and
                  item.get("classification", {}).get("subtag") == "기타"]

        if not others:
            return items, []

        print(f"  기타 재분류: {len(others)}건")

        RECLASSIFY_PROMPT = """이 텍스트의 topic은 "{topic}"입니다.

아래 subtag 목록에서 **반드시 하나를 선택**하세요. "기타"는 선택할 수 없습니다.
정확히 맞는 것이 없더라도 **가장 가까운 것**을 선택하세요.

subtag 목록: {subtags}

어떤 것과도 전혀 관련이 없다면, 새로운 subtag 이름을 제안하세요.

응답: JSON만 출력
{{"subtag": "선택한subtag", "is_new": false, "reason": "선택 이유"}}
또는
{{"subtag": "새로운subtag명", "is_new": true, "reason": "기존 목록에 없는 이유"}}"""

        new_candidates = []

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_map = {}
            for idx, item in others:
                topic = item["classification"]["topic"]
                subtags = [s for s in V5_SUBTAGS.get(topic, []) if s != "기타"]
                prompt = RECLASSIFY_PROMPT.format(topic=topic, subtags=", ".join(subtags))
                text = item.get(content_field, "")[:500]

                future = executor.submit(
                    self._reclassify_single, prompt, text
                )
                future_map[future] = idx

            for future in as_completed(future_map):
                idx = future_map[future]
                result = future.result()
                topic = items[idx]["classification"]["topic"]
                valid = V5_SUBTAGS.get(topic, ["기타"])

                if result.get("is_new"):
                    # 새 subtag 후보 기록, 일단 기타 유지
                    new_candidates.append({
                        "topic": topic,
                        "suggested_subtag": result["subtag"],
                        "reason": result.get("reason", ""),
                        "text_preview": items[idx].get(content_field, "")[:100],
                    })
                elif result["subtag"] in valid:
                    items[idx]["classification"]["subtag"] = result["subtag"]
                # else: 유효하지 않은 응답 → 기타 유지

        reclassified = sum(
            1 for idx, _ in others
            if items[idx]["classification"]["subtag"] != "기타"
        )
        print(f"    재분류 성공: {reclassified}건, 새 후보: {len(new_candidates)}건")

        # 새 후보가 있으면 리뷰 큐에 자동 추가
        if new_candidates:
            try:
                from scripts.review_subtags import append_to_queue
                append_to_queue(new_candidates)
            except ImportError:
                # 스크립트를 직접 import 못 하는 환경에선 파일 직접 저장
                queue_path = Path(__file__).resolve().parents[2] / "data" / "config" / "subtag_review_queue.json"
                queue = []
                if queue_path.exists():
                    with open(queue_path, encoding="utf-8") as f:
                        queue = json.load(f)
                from datetime import datetime
                for c in new_candidates:
                    c["queued_at"] = datetime.now().isoformat()
                    c["status"] = "pending"
                    queue.append(c)
                with open(queue_path, "w", encoding="utf-8") as f:
                    json.dump(queue, f, ensure_ascii=False, indent=2)
                print(f"    리뷰 큐에 {len(new_candidates)}건 추가됨")

        return items, new_candidates

    def _reclassify_single(self, system_prompt: str, text: str) -> dict:
        """기타 재분류 단건 호출."""
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                system=system_prompt,
                messages=[{"role": "user", "content": text}],
                timeout=30.0,
            )
            self._input_tokens += response.usage.input_tokens
            self._output_tokens += response.usage.output_tokens
            return _parse_json_response(response.content[0].text.strip())
        except Exception:
            return {"subtag": "기타", "is_new": False, "reason": "error"}

    def get_cost_report(self) -> dict:
        """비용 추적 리포트"""
        input_cost = self._input_tokens * 0.80 / 1_000_000
        output_cost = self._output_tokens * 4.00 / 1_000_000
        return {
            "model": self.model,
            "total_items": self._total,
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "cost_usd": round(input_cost + output_cost, 4),
            "errors": self._errors,
        }
