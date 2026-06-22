"""주간 리포트 생성 모듈"""
import os
import json
import re
from typing import Dict, Any, List, Tuple
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import boto3
from dotenv import load_dotenv
from src.utils.text_utils import clean_text

load_dotenv()


class ReportGenerator:
    """주간 리포트 생성기"""

    def __init__(self):
        """
        ReportGenerator 초기화 (Bedrock 클라이언트)
        """
        self.bedrock = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-west-2"))
        self.model_id = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        # 인사이트 생성 건전성 추적 (LLM 장애 시 silent fallback 감지용 — fail-loud)
        self.insight_fallback_masters = []   # fallback으로 떨어진 마스터명
        self.key_issues_failed = False       # 핵심 이슈 생성 실패 여부
        self.active_master_count = 0         # 인사이트 생성 대상 마스터 수

    def _call_llm(self, prompt: str, max_tokens: int = 2000) -> str:
        """Bedrock Claude API 호출"""
        resp = self.bedrock.invoke_model(
            modelId=self.model_id,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }),
        )
        result = json.loads(resp["body"].read())
        return result["content"][0]["text"].strip()

    def generate_report(
        self,
        stats: Dict[str, Any],
        start_date: str,
        end_date: str,
        output_path: str = None
    ) -> str:
        """
        주간 리포트 생성

        Args:
            stats: 통계 분석 결과
            start_date: 시작 날짜 (YYYY-MM-DD)
            end_date: 종료 날짜 (YYYY-MM-DD)
            output_path: 저장 경로 (선택, 지정하지 않으면 저장하지 않음)

        Returns:
            생성된 마크다운 리포트
        """
        # 건전성 카운터 리셋 (재실행 시 누적 방지)
        self.insight_fallback_masters = []
        self.key_issues_failed = False
        self.active_master_count = 0

        # 리포트 헤더 생성
        report = self._generate_header(start_date, end_date, stats)

        # 핵심 요약 생성
        report += self._generate_summary(stats)

        # 마스터별 상세 생성 (플랫폼/서비스 피드백, 체크포인트 포함)
        report += self._generate_master_details(stats)

        # 파일로 저장
        if output_path:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(report)

        return report

    def _generate_header(
        self,
        start_date: str,
        end_date: str,
        stats: Dict[str, Any]
    ) -> str:
        """리포트 헤더 생성"""
        from datetime import timedelta
        # 날짜 포맷 변환 (YYYY-MM-DD -> MM.DD)
        # end_date는 exclusive이므로 하루 빼서 표시
        start_formatted = datetime.strptime(start_date, '%Y-%m-%d').strftime('%m.%d')
        end_dt = datetime.strptime(end_date, '%Y-%m-%d') - timedelta(days=1)
        end_formatted = end_dt.strftime('%m.%d')

        return f"""# 📌 이번 주 이용자 반응 리포트 ({start_formatted} ~ {end_formatted})

(편지 + 게시글 기준)

---

"""

    def _generate_top3_ranking(self, stats: Dict[str, Any]) -> str:
        """편지 top3, 게시글 top3 비중을 한 줄 요약으로 표시"""
        master_stats = stats["master_stats"]
        total = stats["total_stats"]["this_week"]

        # 편지 top3
        letter_ranking = sorted(
            [(name, data["this_week"]["letters"]) for name, data in master_stats.items()
             if data["this_week"]["letters"] > 0],
            key=lambda x: x[1], reverse=True
        )[:3]

        # 게시글 top3
        post_ranking = sorted(
            [(name, data["this_week"]["posts"]) for name, data in master_stats.items()
             if data["this_week"]["posts"] > 0],
            key=lambda x: x[1], reverse=True
        )[:3]

        result = ""

        if letter_ranking and total['letters'] > 0:
            names = "·".join([name for name, _ in letter_ranking])
            total_pct = sum(count for _, count in letter_ranking) / total['letters'] * 100
            result += f"편지 Top3({names}) 비중: **약 {total_pct:.0f}%**\n\n"

        if post_ranking and total['posts'] > 0:
            names = "·".join([name for name, _ in post_ranking])
            total_pct = sum(count for _, count in post_ranking) / total['posts'] * 100
            result += f"게시글 Top3({names}) 비중: **약 {total_pct:.0f}%**\n\n"

        return result

    def _generate_summary(self, stats: Dict[str, Any]) -> str:
        """핵심 요약 생성"""
        total = stats["total_stats"]
        this_week = total["this_week"]
        last_week = total["last_week"]
        change = total["change"]

        summary = f"""# 0. 핵심 요약

| 구분             | 이번 주 | 전주 | 증감 |
| ---------------- | ------- | ---- | ---- |
| 전체 편지 건수   | {this_week['letters']} | {last_week['letters']} | {self._format_change(change['letters'])} |
| 전체 게시글 건수 | {this_week['posts']} | {last_week['posts']} | {self._format_change(change['posts'])} |
| 전체 총합        | {this_week['total']} | {last_week['total']} | {self._format_change(change['total'])} |

### 핵심 이슈

"""

        # LLM API로 핵심 이슈 생성
        key_issues = self._generate_key_issues(stats)
        summary += f"{key_issues}\n"

        summary += f"""(총합: 편지 {this_week['letters']}건 / 게시글 {this_week['posts']}건)

"""

        # Top 3 랭킹 추가
        summary += self._generate_top3_ranking(stats)

        summary += """---

"""
        return summary

    def _generate_key_issues(self, stats: Dict[str, Any]) -> str:
        """LLM API를 사용한 핵심 이슈 3개 생성 (태그 분포 + 요약 샘플 기반, 마스터 비특정)"""
        total = stats["total_stats"]
        master_stats = stats["master_stats"]

        # 태그 분포 (stats["tag_stats"]에서 읽기)
        tag_stats = stats.get("tag_stats", {})
        tag_lines = [f"- {tag}: {cnt}건" for tag, cnt in sorted(tag_stats.items(), key=lambda x: x[1], reverse=True)[:15]]
        tag_section = "\n".join(tag_lines) if tag_lines else "태그 데이터 없음"

        # 마스터별 원문 콘텐츠 수집 (부정/긍정 분리, 마스터명 제거)
        neg_samples = []
        pos_samples = []
        for master_name, data in sorted(
            master_stats.items(),
            key=lambda x: x[1]["this_week"]["total"],
            reverse=True
        )[:8]:
            for c in data.get("contents", []):
                content = c.get("content", "")[:250]
                tags = c.get("tags", [])
                tag_str = ",".join(tags[:2]) if tags else ""
                sentiment = c.get("sentiment", "")
                if not content:
                    continue
                if sentiment == "부정":
                    neg_samples.append(f"[{tag_str}] {content}")
                elif sentiment == "긍정":
                    pos_samples.append(f"[{tag_str}] {content}")

        neg_str = "\n".join(neg_samples[:25])
        pos_str = "\n".join(pos_samples[:15])

        # 감정 분포
        sentiment_stats = stats.get("sentiment_stats", {})
        sentiment_section = ", ".join([f"{s} {cnt}건" for s, cnt in sentiment_stats.items()])

        prompt = f"""다음은 금융 투자 커뮤니티 플랫폼의 이번 주 이용자 반응 데이터입니다.

[전체 규모] 총 {total['this_week']['total']}건 ({sentiment_section})

[세부 태그 분포 Top 15]
{tag_section}

[부정 반응 원문 샘플]
{neg_str}

[긍정 반응 원문 샘플]
{pos_str}

위 데이터를 바탕으로 핵심 이슈 3개를 작성해주세요.

## 작성 원칙
- 원문 샘플에서 반복 등장하는 구체적 키워드(종목명, 이슈명, 사건명)를 포함하여 작성
- 태그 분포는 주제 파악용 참고 자료일 뿐, "응원 139건", "감사 123건" 같은 태그 통계를 본문에 언급하지 말 것
- 카테고리명이나 태그명을 그대로 쓰지 말 것 (예: "포트폴리오 태그가 183건" 금지)
- 특정 마스터 이름을 절대 언급하지 말 것. "일부 커뮤니티", "특정 클럽" 등으로 표현
- 여러 클럽에 걸쳐 나타나는 전반적인 추세와 경향을 중심으로 작성
- 데이터에 실제로 나온 내용만 언급. 추측이나 해석 금지
- "커뮤니티 활성화", "소통 강화", "정서적 유대", "비관적 인식이 확산" 등 추상적·미사여구 표현 금지
- 각 이슈는 제목 + 2-3문장 설명으로 구성. 구체적 종목명, 사건, 수치를 포함할 것
- ⚠️ 톤 주의: "강한 불신", "강력한 요구", "납득하지 않는 태도", "고조", "팽배" 등 자극적·공격적 표현 금지. 중립적·완만한 톤으로 작성. (예: "불만이 고조" → "아쉬움을 표현", "강한 불신" → "신뢰도에 대한 의문")
- ⚠️ 원문 샘플에 욕설/인신공격/사기 단정 표현이 있어도 본문 서술에 인용·차용하지 말 것. 사실 관계만 추출하여 정제된 표현으로 서술
- ~합니다체로 작성

## 형식
반드시 아래 형식으로 작성:

**1. 구체적 이슈 제목**

2-3문장 설명. 구체적 맥락과 데이터에 기반한 팩트 중심.

**2. 구체적 이슈 제목**

2-3문장 설명.

**3. 구체적 이슈 제목**

2-3문장 설명."""

        try:
            return self._call_llm(prompt, max_tokens=1500)
        except Exception as e:
            print(f"⚠️  핵심 이슈 생성 실패: {str(e)}")
            self.key_issues_failed = True
            return f"- 이번 주 전체 이용자 반응 규모는 총 {total['this_week']['total']}건입니다."

    def _generate_master_details(self, stats: Dict[str, Any]) -> str:
        """마스터별 상세 리포트 생성 (LLM API 병렬 호출)"""
        master_stats = stats["master_stats"]

        # 총 건수로 정렬
        sorted_masters = sorted(
            master_stats.items(),
            key=lambda x: x[1]["this_week"]["total"],
            reverse=True
        )

        # 5건 이상 마스터 vs 기타 마스터 분리
        MIN_THRESHOLD = 5
        active_masters = [
            (name, data) for name, data in sorted_masters
            if data["this_week"]["total"] >= MIN_THRESHOLD
        ]
        minor_masters = [
            (name, data) for name, data in sorted_masters
            if data["this_week"]["total"] < MIN_THRESHOLD
        ]
        self.active_master_count = len(active_masters)

        # LLM API 병렬 호출로 인사이트 생성
        insights = {}
        max_workers = min(5, len(active_masters))  # 최대 5개 동시 호출
        print(f"  마스터 인사이트 생성 중... ({len(active_masters)}명, 병렬 {max_workers}개)")
        if minor_masters:
            minor_names = [name for name, _ in minor_masters]
            minor_total = sum(d["this_week"]["total"] for _, d in minor_masters)
            print(f"  ⚠️  기타 마스터 {len(minor_masters)}명 ({minor_total}건, 5건 미만): {', '.join(minor_names)}")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_master = {
                executor.submit(
                    self._generate_master_insight,
                    name,
                    data,
                    data["this_week"]["total"] < 20  # 20건 미만이면 축약 모드
                ): name
                for name, data in active_masters
            }
            for future in as_completed(future_to_master):
                master_name = future_to_master[future]
                try:
                    insights[master_name] = future.result()
                except Exception as e:
                    print(f"  ⚠️ {master_name} 인사이트 생성 실패: {e}")
                    if master_name not in self.insight_fallback_masters:
                        self.insight_fallback_masters.append(master_name)
                    insights[master_name] = self._generate_fallback_insight(
                        dict(active_masters).get(master_name, {})
                    )

        # 정렬 순서대로 리포트 조합
        details = "# 1. 오피셜클럽별 상세\n\n"

        for i, (master_group_name, data) in enumerate(active_masters, 1):
            this_week = data["this_week"]
            last_week = data["last_week"]
            change_data = data["change"]

            master_name = master_group_name
            club_names = data.get("club_names", set())

            if len(club_names) > 1:
                clubs_suffix = f" _({'+'.join(sorted(club_names))} 합산)_"
            else:
                clubs_suffix = ""

            insight = insights.get(master_name, self._generate_fallback_insight(data))

            details += f"""## {i}. {master_name}{clubs_suffix}

> {insight['summary']}

| 구분   | 이번 주 | 전주 | 증감 |
| ------ | ------- | ---- | ---- |
| 편지   | {this_week['letters']} | {last_week['letters']} | {self._format_change(change_data.get('letters', 0))} |
| 게시글 | {this_week['posts']} | {last_week['posts']} | {self._format_change(change_data.get('posts', 0))} |
| 총합   | {this_week['total']} | {last_week['total']} | {self._format_change(change_data.get('total', 0))} |

■ 주요 내용
{insight['main_content']}

■ 서비스 피드백

{insight['service_feedback']}

---

"""

        # 기타 마스터 섹션 추가
        if minor_masters:
            minor_names = [name for name, _ in minor_masters]
            minor_total = sum(d["this_week"]["total"] for _, d in minor_masters)
            details += f"""## 기타 마스터

> 편지+게시글 총합 {MIN_THRESHOLD}건 미만으로 분석 대상에서 제외하였습니다.

- 해당 마스터: {', '.join(minor_names)} (총 {minor_total}건)

---

"""

        return details

    def _generate_master_insight(self, master_name: str, data: Dict[str, Any], compact: bool = False) -> Dict[str, str]:
        """LLM API로 마스터별 상세 인사이트 생성 (compact=True: 20건 미만 축약 모드)"""
        contents = data.get("contents", [])
        tags = data.get("tags", {})
        change = data.get("change", {})

        # 콘텐츠가 없으면 기본값 반환
        if not contents:
            return {
                "summary": "반응 데이터가 부족하여 상세 분석이 어렵습니다.",
                "main_content": "- 분석할 콘텐츠가 없습니다.",
                "service_feedback": "- 서비스 피드백이 없습니다.",
            }

        # 콘텐츠를 부정/긍정/대응필요로 분리
        negative_contents = []
        positive_contents = []
        neutral_contents = []
        feedback_contents = []  # topic in ("피드백", "대응 필요")

        for c in contents:
            topic = c.get("topic", "")
            sentiment = c.get("sentiment", "")
            # 원문 우선 사용 (summary는 LLM 요약이라 구체성 떨어짐)
            text = c.get("content", "") or c.get("summary", "")

            if topic in ("피드백", "대응 필요"):
                feedback_contents.append(text)
            elif sentiment == "부정":
                negative_contents.append(f"[부정] {text}")
            elif sentiment == "긍정":
                positive_contents.append(f"[긍정] {text}")
            else:
                neutral_contents.append(f"[중립] {text}")

        # 일반 콘텐츠 (긍정 + 중립 + 부정, 최대 30개)
        general_contents = positive_contents[:15] + neutral_contents[:10] + negative_contents[:5]
        general_str = "\n".join(general_contents[:30])

        # 부정 콘텐츠 별도 표시
        negative_str = "\n".join([f"- {nc}" for nc in negative_contents[:10]]) if negative_contents else "없음"

        # 서비스 피드백 (대응 필요 건)
        feedback_str = "\n".join([f"- {fb}" for fb in feedback_contents]) if feedback_contents else "없음"

        # 태그 분포
        tag_stats_str = "\n".join([f"- {tag}: {cnt}건" for tag, cnt in sorted(tags.items(), key=lambda x: x[1], reverse=True)]) if tags else "태그 데이터 없음"

        prompt = f"""다음은 금융 투자 커뮤니티 "{master_name}" 마스터의 이번 주 이용자 반응 데이터입니다.

[통계]
- 편지: {data['this_week']['letters']}건 (전주 대비 {change.get('letters', 0):+d})
- 게시글: {data['this_week']['posts']}건 (전주 대비 {change.get('posts', 0):+d})

[태그 분포]
{tag_stats_str}

[긍정/중립 콘텐츠 샘플]
{general_str}

[부정 콘텐츠]
{negative_str}

[대응 필요 건 (서비스 피드백)]
{feedback_str}

위 데이터를 분석하여 다음 3가지를 작성해주세요.

## 작성 원칙
- 데이터에 실제로 나온 내용만 언급할 것. 추측이나 해석을 넣지 말 것.
- "커뮤니티 활성화", "소통 강화", "긍정적 분위기" 등 추상적·미사여구 표현 금지.
- ⚠️ 톤 주의: "강한 불신", "강력한 요구", "고조", "팽배", "극심한 불만" 등 자극적 표현 금지. 중립적·완만한 톤으로 작성. (예: "불만이 고조" → "아쉬움을 표현하는 의견이 확인됩니다")
- ⚠️ 부서/담당자 귀책 표현 금지: "운영진 대응 미흡", "관리 부재" 등. 현상 자체만 서술.
- 테마 제목은 실제 데이터 내용을 반영하는 구체적인 제목으로 작성 (예: "수익인증 요구 및 투명성 논란", "3기 오프라인 전우회 참여 후기")
- 인용문은 원문 그대로 사용. 요약하거나 다듬지 말 것.
- 건수를 추정하거나 표기하지 말 것. 테마 제목에 건수를 넣지 말 것.
- ⚠️ **인용구 선택 가이드** — 부서/마스터에게 외부 공유되는 리포트이므로 다음과 같은 인용구는 **절대 선택하지 말 것**:
  · 욕설/비속어 (사기꾼, 새기, 시발, 좆 등)
  · 인신공격 (학력/가족/사생활/외모 비하 — "주갤 출신", "가족까지 팔아먹지만", "여호와의 증인" 등)
  · 사기/범죄 단정 ("100% 사기임", "사이코" 등)
  · 비꼬는 ㅋㅋ/ㅎㅎ 반복 (3회 이상)
  · 같은 뜻을 더 점잖게 표현한 다른 인용문이 있다면 그것을 선택할 것 (예: "계좌 공개 안한다 ㅋㅋ" 보다 "계좌 공개 요청드립니다"가 우선)
  · 비판/문제 제기 자체는 OK. 표현 방식이 문제일 뿐임

1. **summary**: 한 줄 요약. 데이터에 기반한 팩트 중심으로 작성. (예: "포트폴리오 구성과 종목 관련 질문이 중심인 주간입니다.")

2. **main_content**: 주요 내용을 테마별로 정리 ({('2개 테마' if compact else '2-4개 테마')})
   - 서비스 피드백/불편사항/제보 관련 내용은 여기에 넣지 말 것. service_feedback에서 별도로 다룸.
   - 비슷한 내용끼리 묶어서 테마로 구성
   - 각 테마마다 2-3문장으로 충분히 설명하고, 맥락과 배경을 포함할 것
   - 대표 인용문은 가장 핵심적인 원문을 그대로 사용 (긴 인용 OK)
   - 데이터에 나온 내용을 충실히 반영하되, 다양한 주제를 빠짐없이 커버할 것
   - 테마 제목은 구체적으로 (예: "이란-미국 전쟁과 예측 신뢰성 논쟁", "커뮤니티 내 갈등 심화")
   반드시 아래 형식의 문자열로 작성해주세요. 각 테마 사이에 반드시 빈 줄(\\n\\n)을 넣어주세요:

**1. 구체적 테마 제목**\\n\\n테마에 대한 2-3문장 상세 설명. 어떤 맥락에서 이런 반응이 나왔는지, 이용자들의 입장은 어떤지 구체적으로 서술.\\n\\n> _"대표적인 원문 인용 (가능하면 2-3문장 길이)"_\\n\\n\\n**2. 구체적 테마 제목**\\n\\n테마에 대한 2-3문장 상세 설명.\\n\\n> _"대표적인 원문 인용"_


3. **service_feedback**: 서비스 피드백 (하나의 문자열로 작성)
   - 서비스 관련 내용이 없으면 "- 서비스 관련 피드백 없음"으로 작성
   - 있으면 전체 내용을 한 문장으로 요약하고, 대표 인용문 1개만 첨부
   - 담당자들이 실시간으로 처리한 내용이므로 추이만 간결하게 보여주면 됨
   - 형식 예시: "VOD 업로드 지연, 결제 오류 등 서비스 관련 피드백이 N건 접수되었습니다.\\n\\n> _\\"인용문\\"_"

## 응답 형식
반드시 JSON 객체로 응답하되, 모든 값은 문자열(string)이어야 합니다. 리스트나 배열을 사용하지 마세요.
{{"summary": "문자열", "main_content": "문자열", "service_feedback": "문자열"}}"""

        try:
            response_text = self._call_llm(prompt, max_tokens=3000)

            # JSON 파싱 (작은따옴표, 줄바꿈, 코드펜스 등 다양한 형태 처리)
            cleaned = response_text.strip()
            if "```" in cleaned:
                parts = cleaned.split("```")
                for part in parts[1:]:
                    p = part.lstrip("json").strip()
                    if p.startswith("{"):
                        cleaned = p
                        break

            json_match = re.search(r'\{[\s\S]*\}', cleaned)
            if json_match:
                json_str = json_match.group()
                result = None
                # 시도 1: 그대로
                try:
                    result = json.loads(json_str)
                except json.JSONDecodeError:
                    pass
                # 시도 2: 작은따옴표 → 큰따옴표
                if result is None:
                    try:
                        fixed = json_str.replace("'", '"')
                        result = json.loads(fixed)
                    except json.JSONDecodeError:
                        pass
                # 시도 3: 줄바꿈 이스케이프
                if result is None:
                    try:
                        fixed = json_str.replace('\n', '\\n').replace('\r', '\\r')
                        fixed = fixed.replace('\\\\n', '\\n').replace('\\\\r', '\\r')
                        result = json.loads(fixed)
                    except json.JSONDecodeError:
                        pass
                # 시도 4: 필드별 추출 — 다음 키를 경계로 사용
                if result is None:
                    def _extract_between(text, key, next_keys):
                        pat = rf'["\']?{key}["\']?\s*:\s*["\']'
                        m = re.search(pat, text)
                        if not m:
                            return None
                        start = m.end()
                        end = len(text)
                        for nk in next_keys:
                            nm = re.search(rf'["\']?\s*,?\s*["\']?{nk}["\']?\s*:', text[start:])
                            if nm:
                                end = min(end, start + nm.start())
                        value = text[start:end].strip()
                        # JSON 잔여물 제거
                        value = re.sub(r'[\s"\']*[,\}\]]+\s*$', '', value)
                        value = value.strip().rstrip('",\'').strip()
                        return value

                    result = {
                        "summary": _extract_between(json_str, "summary", ["main_content", "service_feedback"]) or "분석 결과 없음",
                        "main_content": _extract_between(json_str, "main_content", ["service_feedback"]) or "- 분석 결과 없음",
                        "service_feedback": _extract_between(json_str, "service_feedback", []) or "- 서비스 피드백 없음",
                    }

                def _to_markdown(value):
                    """리스트/딕셔너리를 마크다운 문자열로 변환"""
                    if isinstance(value, str):
                        value = value.replace('\\n', '\n').replace('\\r', '')
                        value = value.replace('\\"', '"').replace("\\'", "'")
                        # 줄 끝 백슬래시 제거
                        value = re.sub(r'\\\s*$', '', value, flags=re.MULTILINE)
                        return value
                    if isinstance(value, list):
                        parts = []
                        for item in value:
                            if isinstance(item, dict):
                                title = item.get('title', '')
                                summary = item.get('summary', '')
                                quote = item.get('quote', '')
                                parts.append(f"**{title}**\n\n{summary}\n\n> {quote}\n")
                            else:
                                parts.append(str(item))
                        return "\n".join(parts)
                    return str(value)

                main_content = _to_markdown(result.get("main_content", "- 분석 결과 없음"))
                main_content = re.sub(r'\n(\*\*\d+\.)', r'\n\n\1', main_content)

                return {
                    "summary": _to_markdown(result.get("summary", "분석 결과 없음")),
                    "main_content": main_content,
                    "service_feedback": _to_markdown(result.get("service_feedback", "- 서비스 피드백 없음")),
                }

        except Exception as e:
            print(f"⚠️  {master_name} 인사이트 생성 실패: {str(e)}")
            self.insight_fallback_masters.append(master_name)

        # 실패 시 기본값 반환
        return self._generate_fallback_insight(data)

    def _generate_fallback_insight(self, data: Dict[str, Any]) -> Dict[str, str]:
        """API 실패 시 기본 인사이트 생성 (라벨링 데이터 활용)"""
        tags = data.get("tags", {})
        contents = data.get("contents", [])
        change = data.get("change", {})

        # 가장 많은 태그
        top_tag = max(tags.items(), key=lambda x: x[1])[0] if tags else "미분류"

        # 증감 트렌드
        if change.get("total", 0) > 0:
            trend = "증가"
        elif change.get("total", 0) < 0:
            trend = "감소"
        else:
            trend = "유지"

        summary = f"전체 규모는 {trend}했으며, {top_tag} 중심의 주간입니다."

        # 태그별로 콘텐츠 그룹핑
        tag_contents = {}
        for c in contents:
            topic = c.get("topic", "")
            if topic != "대응 필요":
                content_tags = c.get("tags", [])
                for tag in content_tags:
                    if tag not in tag_contents:
                        tag_contents[tag] = []
                    tag_contents[tag].append(c.get("content", ""))

        # 주요 내용을 테마 형식으로
        main_parts = []
        theme_num = 1
        for tag, cnt in sorted(tags.items(), key=lambda x: x[1], reverse=True):
            if cnt < 3:  # 3건 미만은 스킵
                continue
            if theme_num > 3:  # 최대 3개 테마
                break

            main_parts.append(f"**{theme_num}. {tag} ({cnt}건)**\n")

            # 해당 태그의 대표 인용문
            tag_texts = tag_contents.get(tag, [])
            if tag_texts:
                sample = tag_texts[0][:150]
                main_parts.append(f"> _\"{sample}{'...' if len(tag_texts[0]) > 150 else ''}\"_\n\n")

            theme_num += 1

        main_content = "\n".join(main_parts) if main_parts else "- 분석 데이터 부족"

        # 서비스 피드백 (대응 필요 건)
        feedback_items = []
        for c in contents:
            topic = c.get("topic", "")
            text = c.get("content", "")
            if topic in ("피드백", "대응 필요") and text:
                feedback_items.append(text)

        if feedback_items:
            representative = feedback_items[0]
            sample = representative[:120]
            quote = f"{sample}{'...' if len(representative) > 120 else ''}"
            service_feedback = f"대응 필요 건이 {len(feedback_items)}건 접수되었습니다.\n\n> _\"{quote}\"_"
        else:
            service_feedback = "- 서비스 관련 피드백 없음"

        return {
            "summary": summary,
            "main_content": main_content,
            "service_feedback": service_feedback,
        }

    def _generate_master_summary(self, master_data: Dict[str, Any]) -> str:
        """마스터별 요약 문구 생성"""
        tags = master_data.get("tags", {})
        top_tag = max(tags.items(), key=lambda x: x[1])[0] if tags else "없음"

        change_total = master_data["change"]["total"]

        if change_total > 0:
            trend = "증가"
        elif change_total < 0:
            trend = "감소"
        else:
            trend = "유지"

        return f"{trend} 추세이며, {top_tag} 중심의 주간입니다."

    def _generate_service_feedback_summary(self, stats: Dict[str, Any]) -> str:
        """서비스 피드백 요약 생성"""
        feedbacks = stats.get("service_feedbacks", [])

        if not feedbacks:
            return "# 2. 서비스 피드백\n\n서비스 피드백이 없습니다.\n\n---\n\n"

        summary = f"# 2. 서비스 피드백\n\n총 {len(feedbacks)}건의 서비스 피드백이 접수되었습니다.\n\n"

        for i, feedback in enumerate(feedbacks[:10], 1):
            # clean_text는 analytics에서 이미 적용됨
            content = feedback['content']

            summary += f"""### {i}. {feedback.get('title', '피드백')}

{content}

**분류 이유**: {feedback['reason']}

---

"""

        return summary

    def _format_change(self, value: int) -> str:
        """증감 값 포맷팅"""
        if value > 0:
            return f"+{value}"
        elif value < 0:
            return str(value)
        else:
            return "±0"
