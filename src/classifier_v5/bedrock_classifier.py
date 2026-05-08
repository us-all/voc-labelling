"""BedrockClassifier v5 — Bedrock Sonnet으로 편지글/게시글 4분류

v4 BedrockClassifier와 동일 인터페이스. v5 프롬프트 + subtag 추가.
"""
import json
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3

from .bedrock_prompt import BEDROCK_V5_SYSTEM_PROMPT
from .classifier import V5_TOPICS, V5_SUBTAGS, _load_subtags

logger = logging.getLogger(__name__)

MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
V5_SENTIMENTS = ["긍정", "부정", "중립"]
MODEL_PRICING_PER_MILLION_TOKENS = {
    "haiku": {"input": 1.0, "output": 5.0},
    "sonnet": {"input": 3.0, "output": 15.0},
}


def _pricing_for_model(model_id: str) -> dict:
    """Return input/output USD rates per million tokens for a Claude model."""
    normalized = (model_id or "").lower()
    for model_family, pricing in MODEL_PRICING_PER_MILLION_TOKENS.items():
        if model_family in normalized:
            return pricing
    return MODEL_PRICING_PER_MILLION_TOKENS["haiku"]


def _parse_json_response(raw: str) -> dict:
    """LLM 응답에서 JSON 추출. 다양한 형태의 응답 처리."""
    import re
    # 코드펜스 제거
    if "```" in raw:
        parts = raw.split("```")
        for part in parts[1:]:
            cleaned = part.lstrip("json").strip()
            if cleaned.startswith("{"):
                raw = cleaned
                break

    raw = raw.strip()

    # { ... } 추출
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        json_str = raw[start:end]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            # 제어문자 제거 후 재시도
            json_str = re.sub(r'[\x00-\x1f\x7f]', ' ', json_str)
            # 작은따옴표 → 큰따옴표
            json_str = json_str.replace("'", '"')
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                # 마지막 시도: 정규식으로 핵심 필드만 추출
                topic_m = re.search(r'"topic"\s*:\s*"([^"]+)"', json_str)
                subtag_m = re.search(r'"subtag"\s*:\s*"([^"]+)"', json_str)
                sentiment_m = re.search(r'"sentiment"\s*:\s*"([^"]+)"', json_str)
                summary_m = re.search(r'"summary"\s*:\s*"([^"]*)"', json_str)
                return {
                    "topic": topic_m.group(1) if topic_m else "시장·투자",
                    "subtag": subtag_m.group(1) if subtag_m else "기타",
                    "sentiment": sentiment_m.group(1) if sentiment_m else "중립",
                    "summary": summary_m.group(1) if summary_m else "",
                    "tags": [],
                    "confidence": 0.5,
                }

    return json.loads(raw)


class BedrockV5Classifier:
    """Bedrock Sonnet으로 편지글/게시글 4분류 + subtag + tags + summary"""

    def __init__(self, region="us-west-2", model_id=None, max_workers=5):
        self.bedrock = boto3.client("bedrock-runtime", region_name=region)
        self.model_id = model_id or MODEL_ID
        self.max_workers = max_workers
        self._input_tokens = 0
        self._output_tokens = 0
        self._errors = 0
        self._total = 0
        self._subtags = _load_subtags()

    def classify_single(self, text: str) -> dict:
        """단일 건 분류 → {topic, subtag, sentiment, tags, summary, confidence}"""
        if not text or len(text.strip()) < 2:
            return {
                "topic": "일상", "subtag": "일상",
                "sentiment": "중립", "tags": [],
                "summary": "", "confidence": 1.0,
            }

        for attempt in range(3):
            try:
                resp = self.bedrock.invoke_model(
                    modelId=self.model_id,
                    body=json.dumps({
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 350,
                        "system": BEDROCK_V5_SYSTEM_PROMPT,
                        "messages": [{"role": "user", "content": text[:500]}],
                    }),
                )
                result = json.loads(resp["body"].read())
                self._input_tokens += result.get("usage", {}).get("input_tokens", 0)
                self._output_tokens += result.get("usage", {}).get("output_tokens", 0)

                raw_text = result["content"][0]["text"].strip()
                if not raw_text:
                    # 빈 응답 → retry 없이 fallback
                    self._errors += 1
                    return {
                        "topic": "시장·투자", "subtag": "기타",
                        "sentiment": "중립", "tags": [],
                        "summary": "", "confidence": 0.0,
                    }

                parsed = _parse_json_response(raw_text)

                topic = parsed.get("topic", "시장·투자")
                if topic not in V5_TOPICS:
                    topic = "시장·투자"

                subtag = parsed.get("subtag", "기타")
                valid_subtags = self._subtags.get(topic, ["기타"])
                if subtag not in valid_subtags:
                    subtag = "기타"

                sentiment = parsed.get("sentiment", "중립")
                if sentiment not in V5_SENTIMENTS:
                    sentiment = "중립"

                tags = parsed.get("tags", [])
                if not isinstance(tags, list):
                    tags = []

                return {
                    "topic": topic,
                    "subtag": subtag,
                    "sentiment": sentiment,
                    "tags": tags[:4],
                    "summary": str(parsed.get("summary", ""))[:200],
                    "confidence": max(0.0, min(1.0, float(parsed.get("confidence", 0.5)))),
                }

            except Exception as e:
                if "Throttl" in str(e) and attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                self._errors += 1
                logger.warning(f"분류 실패 (attempt {attempt+1}): {e}")
                return {
                    "topic": "시장·투자", "subtag": "기타",
                    "sentiment": "중립", "tags": [],
                    "summary": "", "confidence": 0.0,
                }

    def classify_batch(self, items, content_field="message"):
        """ThreadPoolExecutor 병렬 분류."""
        self._total = len(items)
        start_time = time.time()
        done_count = 0

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_map = {}
            for i, item in enumerate(items):
                text = item.get(content_field, "") if isinstance(item, dict) else str(item)
                future = executor.submit(self.classify_single, text)
                future_map[future] = i

            for future in as_completed(future_map):
                idx = future_map[future]
                classification = future.result()
                if isinstance(items[idx], dict):
                    items[idx]["classification"] = classification
                done_count += 1
                if done_count % 100 == 0 or done_count == len(items):
                    elapsed = time.time() - start_time
                    rate = done_count / elapsed if elapsed > 0 else 0
                    logger.info(f"  {done_count}/{len(items)} ({rate:.1f}건/초)")

        return items

    def get_cost_report(self) -> dict:
        """비용 리포트"""
        pricing = _pricing_for_model(self.model_id)
        input_cost = self._input_tokens * pricing["input"] / 1_000_000
        output_cost = self._output_tokens * pricing["output"] / 1_000_000
        return {
            "model": self.model_id,
            "total_items": self._total,
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "cost_usd": round(input_cost + output_cost, 4),
            "errors": self._errors,
        }
