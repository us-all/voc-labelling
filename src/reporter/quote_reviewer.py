"""주간 리포트 인용구 검수 — 발행 전 부적절한 원문 인용을 자동 제거.

문제:
- 인사이트 LLM이 1차 프롬프트 가이드를 따랐어도 욕설/인신공격/사기 단정 등
  외부 공유 부적절 인용구를 선택할 수 있음 (예: 주갤/가족 인용 사고)

동작:
1. 마크다운에서 `> _"..."_` 패턴 추출
2. 각 인용구를 LLM(Bedrock Haiku)에 일괄 평가 요청 → keep/remove
3. remove 판정된 인용구 라인 제거 + 검수 결과 저장
4. 본문 분석 텍스트는 그대로 둠 (인용구만 사라짐 → 부서 입장에서는 "이런 의견들이 있었다"는 서술만 남음)

비용: 인용구 30~50개 → Haiku 호출 1~2회, 약 $0.01 미만
"""
import json
import logging
import re
from typing import List, Dict, Any, Tuple

import boto3

logger = logging.getLogger(__name__)

MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# 마크다운 인용구 패턴: `> _"..."_`
QUOTE_PATTERN = re.compile(r'^(\s*)>\s*_"(.+?)"_\s*$', re.MULTILINE)

# 한 번에 평가할 인용구 묶음 크기
BATCH_SIZE = 20


REVIEW_SYSTEM_PROMPT = """당신은 금융 교육 플랫폼 주간 리포트의 인용구 검수자입니다.

리포트는 회사 내부 부서(운영팀/CS팀/제품팀)와 마스터 본인에게 공유됩니다.
이용자 원문 인용구가 외부 공유에 부적절한지 판정하세요.

## REMOVE 판정 (제거 대상)
- 욕설/비속어: "사기꾼", "사이코 새기", "씨발", "좆", "병신", "ㅄ" 등
- 인신공격: 학력/가족/사생활/외모/출신 비하
  · 예: "주갤 출신", "가족까지 팔아먹지만", "여호와의 증인들이십니까"
- 사기/범죄 단정: "100% 사기임", "사이코", "범죄자"
- 비꼬는 ㅋㅋ/ㅎㅎ 3회 이상 반복

## KEEP 판정 (유지 대상)
- 비판/문제 제기 자체는 OK (표현이 정중하다면)
- 신뢰도 의문, 해명 요구, 투명성 요청
- 부정적 감정 표현 (실망/아쉬움/혼란)
- 짧은 ㅋㅋ 1~2회는 OK

## 응답 형식
JSON 배열만 출력. 다른 텍스트 금지.
[{"i": 0, "verdict": "remove", "reason": "사유 한 줄"}, {"i": 1, "verdict": "keep"}, ...]

verdict는 "keep" 또는 "remove" 중 하나."""


def extract_quotes(report_md: str) -> List[Dict[str, Any]]:
    """마크다운에서 인용구를 위치 정보와 함께 추출.

    Returns:
        [{"line_no": int, "indent": str, "text": str, "raw": str}, ...]
    """
    quotes = []
    for m in QUOTE_PATTERN.finditer(report_md):
        line_no = report_md[:m.start()].count("\n")
        quotes.append({
            "line_no": line_no,
            "indent": m.group(1),
            "text": m.group(2),
            "raw": m.group(0),
        })
    return quotes


def _call_llm(quotes_batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """LLM에 인용구 묶음을 보내 verdict 받기."""
    user_msg = "다음 인용구들을 평가하세요:\n\n"
    for i, q in enumerate(quotes_batch):
        user_msg += f'{i}. "{q["text"]}"\n'

    bedrock = boto3.client("bedrock-runtime", region_name="us-west-2")
    try:
        resp = bedrock.invoke_model(
            modelId=MODEL_ID,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 2000,
                "system": REVIEW_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_msg}],
            }),
        )
        result = json.loads(resp["body"].read())
        raw = result["content"][0]["text"].strip()

        # JSON 배열 추출
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
        return []
    except Exception as e:
        logger.warning(f"인용구 검수 LLM 호출 실패: {e}")
        return []


def review_quotes_in_report(report_md: str) -> Tuple[str, Dict[str, Any]]:
    """리포트 전체 인용구 검수 후 부적절한 인용구 제거.

    Returns:
        (cleaned_report_md, audit_dict)

    audit_dict 구조:
        {
            "total_quotes": int,
            "removed_count": int,
            "removed": [{"text": str, "reason": str, "line_no": int}, ...]
        }
    """
    quotes = extract_quotes(report_md)
    if not quotes:
        return report_md, {"total_quotes": 0, "removed_count": 0, "removed": []}

    # 배치 단위로 LLM 평가
    verdicts: List[Dict[str, Any]] = []
    for i in range(0, len(quotes), BATCH_SIZE):
        batch = quotes[i:i + BATCH_SIZE]
        batch_verdicts = _call_llm(batch)
        # 인덱스를 글로벌로 변환
        for v in batch_verdicts:
            if "i" in v:
                v["i"] = v["i"] + i
        verdicts.extend(batch_verdicts)

    # remove 판정된 인용구 모음
    to_remove: Dict[int, str] = {}  # global_idx → reason
    for v in verdicts:
        if v.get("verdict") == "remove":
            idx = v.get("i")
            if idx is not None and 0 <= idx < len(quotes):
                to_remove[idx] = v.get("reason", "부적절")

    # raw string 단위 치환 (line_no 기반은 indent 매칭이 어려워서 raw로)
    cleaned = report_md
    removed_log = []
    for idx, reason in to_remove.items():
        q = quotes[idx]
        # raw 라인 통째로 제거 (앞뒤 공백 라인 정리는 마지막에)
        cleaned = cleaned.replace(q["raw"] + "\n", "", 1)
        # 혹시 끝줄이라 \n 이 없는 경우 대비
        cleaned = cleaned.replace(q["raw"], "", 1)
        removed_log.append({
            "text": q["text"],
            "reason": reason,
            "line_no": q["line_no"],
        })

    # 인용구 제거로 생긴 연속 빈 줄 정리 (3개 이상 → 2개)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    audit = {
        "total_quotes": len(quotes),
        "removed_count": len(removed_log),
        "removed": removed_log,
    }
    return cleaned, audit
