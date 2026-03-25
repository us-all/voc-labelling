"""주간 리포트 생성 모듈"""
import os
from typing import Dict, Any, List, Tuple
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI
from dotenv import load_dotenv
from src.utils.text_utils import clean_text

load_dotenv()

# LLM 모델 설정
DEFAULT_MODEL = "gpt-4o-mini"


class ReportGenerator:
    """주간 리포트 생성기"""

    def __init__(self, api_key: str = None, model: str = None):
        """
        ReportGenerator 초기화

        Args:
            api_key: OpenAI API 키
            model: 사용할 모델명 (기본: gpt-4o-mini)
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("CALLME_OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY 또는 CALLME_OPENAI_API_KEY가 설정되지 않았습니다.")

        self.model = model or DEFAULT_MODEL
        self.client = OpenAI(api_key=self.api_key)

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

### 분류 기준

| 카테고리 | 설명 |
| -------- | ---- |
| 감사·후기 | 마스터에 대한 피드백 (긍정적/부정적 포함) |
| 질문·토론 | 포트폴리오, 종목, 투자 전략에 대한 질문 및 토론 |
| 정보성 글 | 투자 경험 공유, 종목 분석, 뉴스/정보 공유 |
| 일상·공감 | 안부, 축하, 가입인사, 일상 이야기, 공감 표현 |
| 서비스 피드백 | 플랫폼/서비스 기능 문의, 일반적인 서비스 관련 질문 |
| 서비스 불편사항 | 플랫폼 오류, 버그, 장애 등 기술적 문제 제보 |
| 서비스 제보/건의 | 서비스 개선 제안, 신규 기능 요청 |

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
        """LLM API를 사용한 핵심 이슈 3개 생성 (특정 마스터 비특정)"""
        total = stats["total_stats"]
        category_stats = stats["category_stats"]
        master_stats = stats["master_stats"]

        # 마스터별 주요 콘텐츠 샘플 수집 (마스터명 제거)
        all_contents_sample = []
        for master_name, data in sorted(
            master_stats.items(),
            key=lambda x: x[1]["this_week"]["total"],
            reverse=True
        )[:8]:  # 상위 8개 마스터만
            for c in data.get("contents", [])[:10]:
                text = c.get("content", "")
                cat = c.get("category", "")
                if text:
                    all_contents_sample.append(f"[{cat}] {text}")

        contents_str = "\n".join(all_contents_sample[:50])

        prompt = f"""다음은 금융 투자 커뮤니티 플랫폼의 이번 주 이용자 반응 데이터입니다.

[전체 통계]
- 이번 주: 편지 {total['this_week']['letters']}건, 게시글 {total['this_week']['posts']}건
- 전주: 편지 {total['last_week']['letters']}건, 게시글 {total['last_week']['posts']}건

[카테고리별 통계]
{chr(10).join([f"- {cat}: {count}건" for cat, count in category_stats.items()])}

[이용자 반응 샘플]
{contents_str}

위 데이터를 바탕으로 핵심 이슈 3개를 작성해주세요.

## 작성 원칙
- 특정 마스터 이름을 절대 언급하지 말 것. "일부 커뮤니티", "특정 클럽" 등으로 표현
- 여러 클럽에 걸쳐 나타나는 전반적인 추세와 경향을 중심으로 작성
- 데이터에 실제로 나온 내용만 언급. 추측이나 해석 금지
- "커뮤니티 활성화", "소통 강화" 등 추상적·미사여구 표현 금지
- 각 이슈는 제목 + 2-3문장 설명으로 구성

## 형식
반드시 아래 형식으로 작성:

**1. 구체적 이슈 제목**

2-3문장 설명. 구체적 맥락과 데이터에 기반한 팩트 중심.

**2. 구체적 이슈 제목**

2-3문장 설명.

**3. 구체적 이슈 제목**

2-3문장 설명."""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=1500,
                temperature=0.3,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            return response.choices[0].message.content.strip()

        except Exception as e:
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
            if 0 < data["this_week"]["total"] < MIN_THRESHOLD
        ]

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
| 편지   | {this_week['letters']} | {last_week['letters']} | {self._format_change(change_data['letters'])} |
| 게시글 | {this_week['posts']} | {last_week['posts']} | {self._format_change(change_data['posts'])} |
| 총합   | {this_week['total']} | {last_week['total']} | {self._format_change(change_data['total'])} |

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
        categories = data.get("categories", {})
        change = data.get("change", {})

        # 콘텐츠가 없으면 기본값 반환
        if not contents:
            return {
                "summary": "반응 데이터가 부족하여 상세 분석이 어렵습니다.",
                "main_content": "- 분석할 콘텐츠가 없습니다.",
                "service_feedback": "- 서비스 피드백이 없습니다.",
            }

        # 일반 콘텐츠와 서비스 관련 콘텐츠 분리
        general_contents = []
        feedback_contents = []
        complaint_contents = []
        suggestion_contents = []
        for c in contents:
            cat = c.get("category", "미분류")
            text = c.get("content", "")
            if cat == "서비스 피드백":
                feedback_contents.append(text)
            elif cat in ("불편사항", "서비스 불편사항"):
                complaint_contents.append(text)
            elif cat == "서비스 제보/건의":
                suggestion_contents.append(text)
            else:
                general_contents.append(f"[{cat}] {text}")

        # 일반 콘텐츠 (최대 30개)
        general_str = "\n".join(general_contents[:30])

        # 서비스 피드백 + 불편사항 + 제보/건의 합쳐서 전달
        all_feedback = []
        if feedback_contents:
            all_feedback.extend([f"[서비스 피드백] {fb}" for fb in feedback_contents])
        if complaint_contents:
            all_feedback.extend([f"[서비스 불편사항] {cp}" for cp in complaint_contents])
        if suggestion_contents:
            all_feedback.extend([f"[서비스 제보/건의] {sg}" for sg in suggestion_contents])
        feedback_str = "\n".join([f"- {fb}" for fb in all_feedback]) if all_feedback else "없음"

        # 카테고리 통계
        cat_stats = "\n".join([f"- {cat}: {cnt}건" for cat, cnt in categories.items()])

        prompt = f"""다음은 금융 투자 커뮤니티 "{master_name}" 마스터의 이번 주 이용자 반응 데이터입니다.

[통계]
- 편지: {data['this_week']['letters']}건 (전주 대비 {change.get('letters', 0):+d})
- 게시글: {data['this_week']['posts']}건 (전주 대비 {change.get('posts', 0):+d})

[카테고리별 분류]
{cat_stats}

[일반 콘텐츠 샘플]
{general_str}

[서비스 피드백 및 불편사항]
{feedback_str}

위 데이터를 분석하여 다음 3가지를 작성해주세요.

## 작성 원칙
- 데이터에 실제로 나온 내용만 언급할 것. 추측이나 해석을 넣지 말 것.
- "커뮤니티 활성화", "소통 강화", "긍정적 분위기" 등 추상적·미사여구 표현 금지.
- 테마 제목은 실제 데이터 내용을 반영하는 구체적인 제목으로 작성 (예: "수익인증 요구 및 투명성 논란", "3기 오프라인 전우회 참여 후기")
- 인용문은 원문 그대로 사용. 요약하거나 다듬지 말 것.
- 건수를 추정하거나 표기하지 말 것. 테마 제목에 건수를 넣지 말 것.

1. **summary**: 한 줄 요약. 데이터에 기반한 팩트 중심으로 작성. (예: "편지 수는 감소했으나, 포트폴리오 구성과 종목 관련 질문이 중심인 주간입니다.")

2. **main_content**: 주요 내용을 테마별로 정리 ({('2개 테마' if compact else '2-4개 테마')})
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
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=3000,
                temperature=0.3,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            response_text = response.choices[0].message.content.strip()

            # JSON 파싱
            import json
            import re

            # JSON 블록 추출
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                json_str = json_match.group()
                try:
                    result = json.loads(json_str)
                except json.JSONDecodeError:
                    # 이스케이프 안 된 줄바꿈/따옴표 정리 후 재시도
                    json_str = json_str.replace('\n', '\\n').replace('\r', '\\r')
                    # 이중 이스케이프 방지
                    json_str = json_str.replace('\\\\n', '\\n').replace('\\\\r', '\\r')
                    result = json.loads(json_str)

                def _to_markdown(value):
                    """리스트/딕셔너리를 마크다운 문자열로 변환"""
                    if isinstance(value, str):
                        return value
                    if isinstance(value, list):
                        parts = []
                        for item in value:
                            if isinstance(item, dict):
                                # {'title': ..., 'summary': ..., 'quote': ...} 형태
                                title = item.get('title', '')
                                summary = item.get('summary', '')
                                quote = item.get('quote', '')
                                parts.append(f"**{title}**\n\n{summary}\n\n> {quote}\n")
                            else:
                                parts.append(str(item))
                        return "\n".join(parts)
                    return str(value)

                main_content = _to_markdown(result.get("main_content", "- 분석 결과 없음"))
                # 번호 항목(**N.) 사이에 빈 줄이 확실히 들어가도록 후처리
                main_content = re.sub(r'\n(\*\*\d+\.)', r'\n\n\1', main_content)

                return {
                    "summary": _to_markdown(result.get("summary", "분석 결과 없음")),
                    "main_content": main_content,
                    "service_feedback": _to_markdown(result.get("service_feedback", "- 서비스 피드백 없음")),
                }

        except Exception as e:
            print(f"⚠️  {master_name} 인사이트 생성 실패: {str(e)}")

        # 실패 시 기본값 반환
        return self._generate_fallback_insight(data)

    def _generate_fallback_insight(self, data: Dict[str, Any]) -> Dict[str, str]:
        """API 실패 시 기본 인사이트 생성 (라벨링 데이터 활용)"""
        categories = data.get("categories", {})
        contents = data.get("contents", [])
        change = data.get("change", {})

        # 가장 많은 카테고리
        top_category = max(categories.items(), key=lambda x: x[1])[0] if categories else "미분류"

        # 증감 트렌드
        if change.get("total", 0) > 0:
            trend = "증가"
        elif change.get("total", 0) < 0:
            trend = "감소"
        else:
            trend = "유지"

        summary = f"전체 규모는 {trend}했으며, {top_category} 중심의 주간입니다."

        # 카테고리별로 콘텐츠 그룹핑
        service_categories = ["서비스 피드백", "서비스 불편사항", "서비스 제보/건의", "불편사항"]
        category_contents = {}
        for c in contents:
            cat = c.get("category", "미분류")
            if cat not in service_categories:
                if cat not in category_contents:
                    category_contents[cat] = []
                category_contents[cat].append(c.get("content", ""))

        # 주요 내용을 테마 형식으로
        main_parts = []
        theme_num = 1
        for cat, cnt in sorted(categories.items(), key=lambda x: x[1], reverse=True):
            if cat in service_categories:
                continue
            if cnt < 3:  # 3건 미만은 스킵
                continue
            if theme_num > 3:  # 최대 3개 테마
                break

            main_parts.append(f"**{theme_num}. {cat} ({cnt}건)**\n")

            # 해당 카테고리의 대표 인용문
            cat_texts = category_contents.get(cat, [])
            if cat_texts:
                sample = cat_texts[0][:150]
                main_parts.append(f"> _\"{sample}{'...' if len(cat_texts[0]) > 150 else ''}\"_\n\n")

            theme_num += 1

        main_content = "\n".join(main_parts) if main_parts else "- 분석 데이터 부족"

        # 서비스 피드백, 불편사항, 제보/건의 추출
        feedback_items = []
        complaint_items = []
        suggestion_items = []
        for c in contents:
            cat = c.get("category", "")
            text = c.get("content", "")
            if cat == "서비스 피드백" and text:
                feedback_items.append(text)
            elif cat in ("불편사항", "서비스 불편사항") and text:
                complaint_items.append(text)
            elif cat == "서비스 제보/건의" and text:
                suggestion_items.append(text)

        # 서비스 피드백: 한 문장 요약 + 인용구 1개
        total_feedback = len(complaint_items) + len(suggestion_items) + len(feedback_items)
        if total_feedback > 0:
            # 대표 인용문 1개 선택 (불편사항 우선)
            representative = (complaint_items or suggestion_items or feedback_items)[0]
            sample = representative[:120]
            quote = f"{sample}{'...' if len(representative) > 120 else ''}"

            # 피드백 유형 요약
            types = []
            if complaint_items:
                types.append("불편사항")
            if suggestion_items:
                types.append("건의사항")
            if feedback_items:
                types.append("일반 피드백")
            type_str = ", ".join(types)

            service_feedback = f"{type_str} 등 서비스 관련 피드백이 {total_feedback}건 접수되었습니다.\n\n> _\"{quote}\"_"
        else:
            service_feedback = "- 서비스 관련 피드백 없음"

        return {
            "summary": summary,
            "main_content": main_content,
            "service_feedback": service_feedback,
        }

    def _generate_master_summary(self, master_data: Dict[str, Any]) -> str:
        """마스터별 요약 문구 생성"""
        categories = master_data["categories"]
        top_category = max(categories.items(), key=lambda x: x[1])[0] if categories else "없음"

        change_total = master_data["change"]["total"]

        if change_total > 0:
            trend = "증가"
        elif change_total < 0:
            trend = "감소"
        else:
            trend = "유지"

        return f"{trend} 추세이며, {top_category} 중심의 주간입니다."

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
