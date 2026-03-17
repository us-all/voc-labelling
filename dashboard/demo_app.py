"""VOC Intelligence Demo — 분류 데이터 활용 데모

실행: streamlit run dashboard/demo_app.py
"""
import json
import re
import sys
import os
from pathlib import Path
from collections import Counter
from datetime import datetime

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 상수 ──────────────────────────────────────────────────────────────
TOPICS = ["콘텐츠 반응", "투자 이야기", "서비스 이슈", "커뮤니티 소통"]
SENTIMENTS = ["긍정", "부정", "중립"]
SENTIMENT_COLORS = {"긍정": "#2ecc71", "부정": "#e74c3c", "중립": "#95a5a6"}
TOPIC_COLORS = {
    "콘텐츠 반응": "#3498db",
    "투자 이야기": "#e67e22",
    "서비스 이슈": "#e74c3c",
    "커뮤니티 소통": "#9b59b6",
}
CHANNEL_TOPICS = ["결제·환불", "구독·멤버십", "콘텐츠·수강", "기술·오류", "기타"]
CHANNEL_TOPIC_COLORS = {
    "결제·환불": "#e74c3c",
    "구독·멤버십": "#e67e22",
    "콘텐츠·수강": "#3498db",
    "기술·오류": "#9b59b6",
    "기타": "#95a5a6",
}

DATA_DIR = Path("data")
TWO_AXIS_DIR = DATA_DIR / "classified_data_two_axis"
CHANNEL_DIR = DATA_DIR / "channel_io" / "golden"

# ── 데이터 로드 ──────────────────────────────────────────────────────

@st.cache_data
def load_two_axis_data() -> list[dict]:
    """2축 분류 데이터 로드 (detail_tags 있는 것만)"""
    items = []
    for f in sorted(TWO_AXIS_DIR.glob("*.json")):
        with open(f, encoding="utf-8") as fh:
            data = json.load(fh)
        week = f.stem
        for letter in data.get("letters", []):
            if "detail_tags" not in letter:
                continue
            letter["_type"] = "편지"
            letter["_week"] = week
            items.append(letter)
        for post in data.get("posts", []):
            if "detail_tags" not in post:
                continue
            post["_type"] = "게시글"
            post["_week"] = week
            items.append(post)
    return items


@st.cache_data
def load_channel_data() -> list[dict]:
    """채널톡 분류 데이터 로드"""
    path = CHANNEL_DIR / "golden_multilabel_270.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def items_to_dataframe(items: list[dict]) -> pd.DataFrame:
    """편지/게시글 아이템을 DataFrame으로 변환"""
    rows = []
    for item in items:
        cls = item.get("classification", {})
        dt = item.get("detail_tags", {})
        master = re.sub(r"\d+$", "", item.get("masterName", "Unknown")).strip()
        content = item.get("message") or item.get("textBody") or item.get("body", "")

        rows.append({
            "유형": item["_type"],
            "주차": item["_week"],
            "마스터": master,
            "클럽": item.get("masterClubName", ""),
            "주제": cls.get("topic", "미분류"),
            "감성": cls.get("sentiment", "미분류"),
            "내용": content,
            "summary": dt.get("summary", ""),
            "category_tags": dt.get("category_tags", []),
            "free_tags": dt.get("free_tags", []),
            "날짜": item.get("createdAt", ""),
        })
    return pd.DataFrame(rows)


def channel_to_dataframe(items: list[dict]) -> pd.DataFrame:
    """채널톡 아이템을 DataFrame으로 변환"""
    rows = []
    for item in items:
        topics = item.get("topics", [])
        rows.append({
            "chatId": item.get("chatId", ""),
            "내용": item.get("text", ""),
            "route": item.get("route", ""),
            "topics": topics,
            "topic_primary": topics[0] if topics else "없음",
        })
    return pd.DataFrame(rows)


# ── 헬퍼 함수 ────────────────────────────────────────────────────────

def _generate_mini_report(df: pd.DataFrame, df_channel: pd.DataFrame) -> str:
    """분류 데이터 기반 미니 리포트 생성 (LLM 없이)"""
    total = len(df)
    letters = len(df[df["유형"] == "편지"])
    posts = len(df[df["유형"] == "게시글"])

    master_stats = []
    for master in df["마스터"].unique():
        m_df = df[df["마스터"] == master]
        t = len(m_df)
        if t < 3:
            continue
        n = len(m_df[m_df["감성"] == "부정"])
        nr = round(n / t * 100, 1) if t else 0
        ftags = Counter()
        for tags in m_df[m_df["감성"] == "부정"]["free_tags"]:
            for tag in tags:
                ftags[tag] += 1
        top_issue = ftags.most_common(1)[0][0] if ftags else "-"
        master_stats.append((master, t, nr, top_issue))

    master_stats.sort(key=lambda x: x[1], reverse=True)

    svc = df[df["주제"] == "서비스 이슈"]
    svc_tags = Counter()
    for tags in svc["category_tags"]:
        for t in tags:
            svc_tags[t] += 1

    risk_masters = [(m, t, nr, issue) for m, t, nr, issue in master_stats if nr >= 20]

    report = f"""
## 핵심 요약

| 구분 | 건수 |
|------|------|
| 편지 | {letters}건 |
| 게시글 | {posts}건 |
| 전체 | {total}건 |
| 채널톡 CS | {len(df_channel)}건 |

"""
    if risk_masters:
        report += "### ⚠️ 위험 신호\n\n"
        for m, t, nr, issue in risk_masters:
            report += f"- **{m}**: 부정 {nr}% ({t}건 중) — {issue}\n"
        report += "\n"

    report += "### 마스터별 통계\n\n"
    report += "| 마스터 | 총건수 | 부정비율 | 주요 부정 이슈 |\n"
    report += "|--------|--------|----------|----------------|\n"
    for m, t, nr, issue in master_stats[:10]:
        status = "🔴" if nr >= 25 else "🟡" if nr >= 15 else "🟢"
        report += f"| {m} | {t} | {status} {nr}% | {issue} |\n"

    if svc_tags:
        report += "\n### 서비스 이슈 요약\n\n"
        for tag, cnt in svc_tags.most_common(5):
            report += f"- {tag}: {cnt}건\n"

    return report


def _generate_agent_response(query: str, df: pd.DataFrame, df_channel: pd.DataFrame) -> str:
    """AI Agent 응답 생성 — Claude API 호출"""
    try:
        import anthropic
        client = anthropic.Anthropic()
    except Exception:
        return _generate_fallback_response(query, df, df_channel)

    context = _build_data_context(query, df, df_channel)

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{"role": "user", "content": query}],
            system=f"""당신은 VOC 데이터 분석 AI 에이전트입니다.
사용자의 질문에 분류된 VOC 데이터를 기반으로 정확하고 구조화된 인사이트를 제공합니다.

## 사용 가능한 데이터

{context}

## 응답 규칙
- 구체적인 건수와 비율을 포함
- 마스터명, 태그 등 실제 데이터 인용
- 원문 인용 시 이탤릭 사용
- 액션 포인트/권장 사항 포함
- 마크다운 형식으로 구조화""",
        )
        return response.content[0].text
    except Exception:
        return _generate_fallback_response(query, df, df_channel)


def _build_data_context(query: str, df: pd.DataFrame, df_channel: pd.DataFrame) -> str:
    """쿼리에 맞는 데이터 컨텍스트 생성"""
    context_parts = []

    total = len(df)
    context_parts.append(f"### 전체 통계\n- 편지+게시글: {total}건")
    context_parts.append(f"- 채널톡 CS: {len(df_channel)}건")

    sent_dist = df["감성"].value_counts().to_dict()
    context_parts.append(f"- 감성: 긍정 {sent_dist.get('긍정',0)}건, 부정 {sent_dist.get('부정',0)}건, 중립 {sent_dist.get('중립',0)}건")

    context_parts.append("\n### 마스터별 통계")
    for master in df["마스터"].unique():
        m_df = df[df["마스터"] == master]
        t = len(m_df)
        if t < 3:
            continue
        n = len(m_df[m_df["감성"] == "부정"])
        nr = round(n / t * 100, 1) if t else 0

        tags = Counter()
        for tlist in m_df["category_tags"]:
            for tag in tlist:
                tags[tag] += 1
        top_tags = [f"{tag}({cnt})" for tag, cnt in tags.most_common(3)]

        neg_ftags = Counter()
        for tlist in m_df[m_df["감성"] == "부정"]["free_tags"]:
            for tag in tlist:
                neg_ftags[tag] += 1
        top_neg = [f'"{tag}"' for tag, _ in neg_ftags.most_common(3)]

        context_parts.append(f"- **{master}**: {t}건, 부정 {nr}%, 태그: {', '.join(top_tags)}, 부정키워드: {', '.join(top_neg)}")

    context_parts.append("\n### 주제별 통계")
    for topic in TOPICS:
        t_df = df[df["주제"] == topic]
        t_total = len(t_df)
        t_neg = len(t_df[t_df["감성"] == "부정"])
        context_parts.append(f"- {topic}: {t_total}건 (부정 {t_neg}건)")

    svc_df = df[df["주제"] == "서비스 이슈"]
    if not svc_df.empty:
        context_parts.append(f"\n### 서비스 이슈 상세 ({len(svc_df)}건)")
        for _, row in svc_df.head(10).iterrows():
            context_parts.append(f"- [{row['마스터']}] {row['summary']} (태그: {', '.join(row['category_tags'])})")

    if not df_channel.empty:
        context_parts.append("\n### 채널톡 CS 통계")
        ch_topics = Counter()
        for topics in df_channel["topics"]:
            for t in topics:
                ch_topics[t] += 1
        for t, cnt in ch_topics.most_common():
            context_parts.append(f"- {t}: {cnt}건")

    keywords = [w for w in query.split() if len(w) > 1]
    if keywords:
        mask = pd.Series(False, index=df.index)
        for kw in keywords:
            mask = mask | df["내용"].str.contains(kw, case=False, na=False)
            mask = mask | df["summary"].str.contains(kw, case=False, na=False)
            mask = mask | df["마스터"].str.contains(kw, case=False, na=False)
        relevant = df[mask]

        if not relevant.empty and len(relevant) < len(df):
            context_parts.append(f"\n### 쿼리 관련 원문 샘플 ({len(relevant)}건 중 상위 10건)")
            for _, row in relevant.head(10).iterrows():
                context_parts.append(f"- [{row['마스터']}|{row['주제']}|{row['감성']}] {row['summary']}")
                context_parts.append(f"  원문: \"{row['내용'][:150]}\"")

    return "\n".join(context_parts)


def _generate_fallback_response(query: str, df: pd.DataFrame, df_channel: pd.DataFrame) -> str:
    """API 호출 실패 시 규칙 기반 응답"""
    if "요약" in query or "핵심" in query:
        total = len(df)
        neg = len(df[df["감성"] == "부정"])
        neg_ratio = round(neg / total * 100, 1)

        risk = []
        for master in df["마스터"].unique():
            m_df = df[df["마스터"] == master]
            t = len(m_df)
            if t < 5:
                continue
            n = len(m_df[m_df["감성"] == "부정"])
            nr = round(n / t * 100, 1)
            if nr >= 15:
                risk.append((master, t, nr))
        risk.sort(key=lambda x: x[2], reverse=True)

        svc = df[df["주제"] == "서비스 이슈"]
        svc_tags = Counter()
        for tags in svc["category_tags"]:
            for t in tags:
                svc_tags[t] += 1

        result = f"""📊 **전체 VOC 핵심 요약** ({df['주차'].unique()[0]} 주차)

**전체**: {total}건 (편지 {len(df[df['유형']=='편지'])}건 + 게시글 {len(df[df['유형']=='게시글'])}건)
**전체 부정 비율**: {neg_ratio}%

"""
        if risk:
            result += "**⚠️ 위험 마스터:**\n"
            for m, t, nr in risk[:3]:
                neg_ftags = Counter()
                for tlist in df[(df["마스터"]==m) & (df["감성"]=="부정")]["free_tags"]:
                    for tag in tlist:
                        neg_ftags[tag] += 1
                top = neg_ftags.most_common(2)
                issues = ", ".join([f'"{t}"' for t, _ in top])
                result += f"- **{m}**: 부정 {nr}% — {issues}\n"

        if svc_tags:
            result += f"\n**서비스 이슈** ({len(svc)}건):\n"
            for tag, cnt in svc_tags.most_common(3):
                result += f"- {tag}: {cnt}건\n"

        return result

    for master in df["마스터"].unique():
        if master in query:
            m_df = df[df["마스터"] == master]
            t = len(m_df)
            n = len(m_df[m_df["감성"] == "부정"])
            p = len(m_df[m_df["감성"] == "긍정"])

            neg_tags = Counter()
            for tags in m_df[m_df["감성"] == "부정"]["free_tags"]:
                for tag in tags:
                    neg_tags[tag] += 1

            result = f"""📊 **{master}** 분석

총 {t}건 | 긍정 {p}건({round(p/t*100,1)}%) | 부정 {n}건({round(n/t*100,1)}%)

"""
            if neg_tags:
                result += "**부정 주요 키워드:**\n"
                for tag, cnt in neg_tags.most_common(5):
                    result += f"- \"{tag}\" ({cnt}건)\n"

            neg_items = m_df[m_df["감성"] == "부정"].head(3)
            if not neg_items.empty:
                result += "\n**부정 원문 샘플:**\n"
                for _, row in neg_items.iterrows():
                    result += f"\n> _{row['내용'][:200]}_\n"

            return result

    return "해당 질문에 대한 분석 결과를 생성하려면 API 키가 필요합니다. ANTHROPIC_API_KEY 환경변수를 설정해주세요."


# ── 페이지 설정 ──────────────────────────────────────────────────────

st.set_page_config(
    page_title="VOC Intelligence Demo",
    page_icon="🔍",
    layout="wide",
)

# 데이터 로드
items = load_two_axis_data()
df = items_to_dataframe(items)
channel_items = load_channel_data()
df_channel = channel_to_dataframe(channel_items)

if df.empty:
    st.error("2축 분류 데이터가 없습니다. data/classified_data_two_axis/ 를 확인하세요.")
    st.stop()

# ── 메인 타이틀 ──────────────────────────────────────────────────────

st.title("🔍 VOC Intelligence Demo")
st.caption("분류된 VOC 데이터를 활용한 인사이트 추출 · 시각화 · 분석 데모")

# ── 탭 구성 ──────────────────────────────────────────────────────────

tab_overview, tab_ops, tab_content, tab_service, tab_agent, tab_report, tab_catalog, tab_explorer = st.tabs([
    "📊 전사 개요",
    "🏢 운영/사업팀",
    "📝 콘텐츠팀",
    "🔧 서비스/개발팀",
    "🤖 AI 인사이트",
    "📋 리포트 예시",
    "📚 분석 가능 범위",
    "🔎 원문 탐색",
])


# ═════════════════════════════════════════════════════════════════════
# 탭 1: 전사 개요
# ═════════════════════════════════════════════════════════════════════

with tab_overview:
    st.header("전사 VOC 개요")

    # 상단 메트릭
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("전체 VOC", f"{len(df)}건")
    col2.metric("편지", f"{len(df[df['유형']=='편지'])}건")
    col3.metric("게시글", f"{len(df[df['유형']=='게시글'])}건")
    col4.metric("채널톡 CS", f"{len(df_channel)}건")

    st.divider()

    c1, c2 = st.columns(2)

    with c1:
        # Topic × Sentiment 히트맵
        st.subheader("Topic × Sentiment")
        cross = pd.crosstab(df["주제"], df["감성"])
        for s in SENTIMENTS:
            if s not in cross.columns:
                cross[s] = 0
        cross = cross[SENTIMENTS]

        fig_hm = px.imshow(
            cross, text_auto=True, color_continuous_scale="RdYlGn_r",
            labels=dict(x="감성", y="주제", color="건수"),
        )
        fig_hm.update_layout(height=350)
        st.plotly_chart(fig_hm, use_container_width=True)

    with c2:
        # 마스터별 부정 비율 Top 10
        st.subheader("마스터별 부정 비율")
        master_stats = []
        for master in df["마스터"].unique():
            m_df = df[df["마스터"] == master]
            total = len(m_df)
            if total < 5:
                continue
            neg = len(m_df[m_df["감성"] == "부정"])
            master_stats.append({
                "마스터": master,
                "총건수": total,
                "부정": neg,
                "부정비율": round(neg / total * 100, 1),
            })
        df_master = pd.DataFrame(master_stats).sort_values("부정비율", ascending=True)

        fig_neg = px.bar(
            df_master, x="부정비율", y="마스터", orientation="h",
            color="부정비율", color_continuous_scale="RdYlGn_r",
            text="부정비율",
        )
        fig_neg.update_layout(height=350, yaxis_title="", coloraxis_showscale=False)
        fig_neg.update_traces(texttemplate="%{text}%", textposition="outside")
        st.plotly_chart(fig_neg, use_container_width=True)

    # 주요 category_tags 분포
    st.subheader("주요 이슈 태그 (category_tags)")
    all_tags = Counter()
    for tags in df["category_tags"]:
        for t in tags:
            all_tags[t] += 1

    tag_df = pd.DataFrame(all_tags.most_common(15), columns=["태그", "건수"])
    fig_tags = px.bar(tag_df, x="건수", y="태그", orientation="h",
                      color_discrete_sequence=["#3498db"])
    fig_tags.update_layout(height=450, yaxis=dict(autorange="reversed"), yaxis_title="")
    st.plotly_chart(fig_tags, use_container_width=True)


# ═════════════════════════════════════════════════════════════════════
# 탭 2: 운영/사업팀
# ═════════════════════════════════════════════════════════════════════

with tab_ops:
    st.header("운영/사업팀 뷰")
    st.caption("마스터별 여론 파악 · 위험신호 감지 · 운영 피드백 큐")

    # 마스터 선택
    masters = sorted(df["마스터"].unique(), key=lambda m: len(df[df["마스터"]==m]), reverse=True)
    selected_master = st.selectbox("마스터 선택", ["전체"] + masters, key="ops_master")

    if selected_master == "전체":
        m_df = df
    else:
        m_df = df[df["마스터"] == selected_master]

    # 헬스 스코어
    col1, col2, col3, col4 = st.columns(4)
    total = len(m_df)
    pos = len(m_df[m_df["감성"] == "긍정"])
    neg = len(m_df[m_df["감성"] == "부정"])
    neu = len(m_df[m_df["감성"] == "중립"])
    neg_ratio = round(neg / total * 100, 1) if total > 0 else 0

    col1.metric("총 건수", f"{total}건")
    col2.metric("긍정", f"{pos}건 ({round(pos/total*100,1) if total else 0}%)")
    col3.metric("부정", f"{neg}건 ({neg_ratio}%)")
    status = "🔴 위험" if neg_ratio >= 25 else "🟡 주의" if neg_ratio >= 15 else "🟢 정상"
    col4.metric("상태", status)

    st.divider()

    c1, c2 = st.columns(2)

    with c1:
        # 주제별 감성 분포
        st.subheader("주제별 감성 분포")
        topic_sent = m_df.groupby(["주제", "감성"]).size().reset_index(name="건수")
        fig = px.bar(topic_sent, x="주제", y="건수", color="감성",
                     color_discrete_map=SENTIMENT_COLORS, barmode="stack")
        fig.update_layout(height=350)
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        # 부정 의견 category_tags
        st.subheader("부정 의견 주요 태그")
        neg_df = m_df[m_df["감성"] == "부정"]
        neg_tags = Counter()
        for tags in neg_df["category_tags"]:
            for t in tags:
                neg_tags[t] += 1
        if neg_tags:
            ntag_df = pd.DataFrame(neg_tags.most_common(10), columns=["태그", "건수"])
            fig_nt = px.bar(ntag_df, x="건수", y="태그", orientation="h",
                            color_discrete_sequence=["#e74c3c"])
            fig_nt.update_layout(height=350, yaxis=dict(autorange="reversed"), yaxis_title="")
            st.plotly_chart(fig_nt, use_container_width=True)
        else:
            st.success("부정 의견 없음")

    # 부정 의견 샘플
    if not neg_df.empty:
        st.subheader("부정 의견 원문 (최근)")
        for _, row in neg_df.head(5).iterrows():
            tags_str = ", ".join(row["category_tags"]) if row["category_tags"] else ""
            st.markdown(f"""
> **[{row['주제']}]** {tags_str}
> {row['summary']}
>
> _{row['내용'][:200]}..._
""")
            st.divider()


# ═════════════════════════════════════════════════════════════════════
# 탭 3: 콘텐츠팀
# ═════════════════════════════════════════════════════════════════════

with tab_content:
    st.header("콘텐츠팀 뷰")
    st.caption("콘텐츠 반응 분석 · 부정 패턴 · 개선 포인트")

    # 콘텐츠 반응 필터
    content_df = df[df["주제"] == "콘텐츠 반응"]

    col1, col2, col3 = st.columns(3)
    ct = len(content_df)
    cp = len(content_df[content_df["감성"] == "긍정"])
    cn = len(content_df[content_df["감성"] == "부정"])
    col1.metric("콘텐츠 반응 전체", f"{ct}건")
    col2.metric("긍정", f"{cp}건 ({round(cp/ct*100,1) if ct else 0}%)")
    col3.metric("부정", f"{cn}건 ({round(cn/ct*100,1) if ct else 0}%)")

    st.divider()

    c1, c2 = st.columns(2)

    with c1:
        # 마스터별 콘텐츠 감성
        st.subheader("마스터별 콘텐츠 반응")
        cm = content_df.groupby(["마스터", "감성"]).size().reset_index(name="건수")
        m_order = content_df.groupby("마스터").size().reset_index(name="t").sort_values("t", ascending=False)["마스터"].tolist()
        fig_cm = px.bar(cm, x="마스터", y="건수", color="감성",
                        color_discrete_map=SENTIMENT_COLORS, barmode="stack",
                        category_orders={"마스터": m_order})
        fig_cm.update_layout(height=350, xaxis_tickangle=-45)
        st.plotly_chart(fig_cm, use_container_width=True)

    with c2:
        # 콘텐츠 관련 태그 분포
        st.subheader("콘텐츠 관련 태그")
        c_tags = Counter()
        for tags in content_df["category_tags"]:
            for t in tags:
                c_tags[t] += 1
        if c_tags:
            ctag_df = pd.DataFrame(c_tags.most_common(10), columns=["태그", "건수"])
            fig_ct = px.bar(ctag_df, x="건수", y="태그", orientation="h",
                            color_discrete_sequence=["#3498db"])
            fig_ct.update_layout(height=350, yaxis=dict(autorange="reversed"), yaxis_title="")
            st.plotly_chart(fig_ct, use_container_width=True)

    # 콘텐츠 부정 패턴
    st.subheader("콘텐츠 부정 패턴 분석")
    content_neg = content_df[content_df["감성"] == "부정"]

    if not content_neg.empty:
        # 마스터별 부정 건수
        neg_by_master = content_neg.groupby("마스터").size().reset_index(name="부정_건수")
        neg_by_master = neg_by_master.sort_values("부정_건수", ascending=False)

        for _, row in neg_by_master.iterrows():
            m_neg = content_neg[content_neg["마스터"] == row["마스터"]]
            ftags = Counter()
            for tags in m_neg["free_tags"]:
                for t in tags:
                    ftags[t] += 1
            top_ftags = ", ".join([f'"{t}"' for t, _ in ftags.most_common(3)])

            with st.expander(f"**{row['마스터']}** — 부정 {row['부정_건수']}건 | {top_ftags}"):
                for _, item in m_neg.head(3).iterrows():
                    st.markdown(f"- **{item['summary']}**")
                    st.markdown(f"  _{item['내용'][:150]}..._")
    else:
        st.success("콘텐츠 부정 의견 없음")


# ═════════════════════════════════════════════════════════════════════
# 탭 4: 서비스/개발팀
# ═════════════════════════════════════════════════════════════════════

with tab_service:
    st.header("서비스/개발팀 뷰")
    st.caption("채널톡 + 편지글 서비스 이슈 통합 · 교차 감지")

    c1, c2 = st.columns(2)

    with c1:
        st.subheader("채널톡 CS 분류")
        if not df_channel.empty:
            # Route 분포
            route_counts = df_channel["route"].value_counts().reset_index()
            route_counts.columns = ["route", "건수"]
            fig_route = px.pie(route_counts, values="건수", names="route",
                              title="처리 경로 (route)")
            fig_route.update_layout(height=300)
            st.plotly_chart(fig_route, use_container_width=True)

            # Topic 분포
            topic_counts = Counter()
            for topics in df_channel["topics"]:
                for t in topics:
                    topic_counts[t] += 1
            tc_df = pd.DataFrame(topic_counts.most_common(), columns=["topic", "건수"])
            fig_tc = px.bar(tc_df, x="건수", y="topic", orientation="h",
                           color="topic", color_discrete_map=CHANNEL_TOPIC_COLORS)
            fig_tc.update_layout(height=300, yaxis_title="", showlegend=False,
                               title="채널톡 이슈 분류 (multi-label)")
            st.plotly_chart(fig_tc, use_container_width=True)
        else:
            st.warning("채널톡 데이터 없음")

    with c2:
        st.subheader("편지글/게시글 서비스 이슈")
        svc_df = df[df["주제"] == "서비스 이슈"]
        svc_tags = Counter()
        for tags in svc_df["category_tags"]:
            for t in tags:
                svc_tags[t] += 1

        if svc_tags:
            stag_df = pd.DataFrame(svc_tags.most_common(10), columns=["태그", "건수"])
            fig_st = px.bar(stag_df, x="건수", y="태그", orientation="h",
                           color_discrete_sequence=["#e74c3c"])
            fig_st.update_layout(height=300, yaxis=dict(autorange="reversed"), yaxis_title="",
                               title="서비스 이슈 상세 태그")
            st.plotly_chart(fig_st, use_container_width=True)
        else:
            st.info("서비스 이슈 없음")

        # 서비스 이슈 원문
        if not svc_df.empty:
            st.markdown(f"**서비스 이슈 {len(svc_df)}건**")
            for _, row in svc_df.head(5).iterrows():
                tags_str = ", ".join(row["category_tags"])
                st.markdown(f"- [{row['마스터']}] **{row['summary']}** ({tags_str})")

    # 교차 감지
    st.divider()
    st.subheader("⚠️ 채널톡 × 편지글 교차 감지")

    # 매핑 정의
    cross_map = {
        "결제·환불": ["결제/환불/구독", "가격/프로모션 정책"],
        "구독·멤버십": ["결제/환불/구독", "가격/프로모션 정책"],
        "콘텐츠·수강": ["콘텐츠 접근 문제", "강의/수업 피드백", "온보딩/접근성"],
        "기술·오류": ["앱/기능 오류", "기타 서비스"],
    }

    for ch_topic, letter_tags in cross_map.items():
        # 채널톡 건수
        ch_count = sum(1 for topics in df_channel["topics"] for t in topics if t == ch_topic)
        # 편지글 건수
        letter_count = 0
        for tags in svc_df["category_tags"]:
            if any(t in letter_tags for t in tags):
                letter_count += 1

        if ch_count > 0 or letter_count > 0:
            total = ch_count + letter_count
            icon = "🔴" if total >= 15 else "🟡" if total >= 5 else "🟢"
            st.markdown(f"{icon} **{ch_topic}** — 채널톡 {ch_count}건 + 편지글 {letter_count}건 = **총 {total}건**")


# ═════════════════════════════════════════════════════════════════════
# 탭 5: AI 인사이트
# ═════════════════════════════════════════════════════════════════════

with tab_agent:
    st.header("🤖 AI 인사이트")
    st.caption("자연어로 질문하면 분류 데이터를 기반으로 인사이트를 추출합니다")

    # 예시 프롬프트 버튼
    st.markdown("**예시 질문** (클릭하면 자동 입력)")
    example_cols = st.columns(3)
    examples = [
        "이번 주 전체 VOC 핵심 3줄 요약",
        "부정 비율 높은 마스터 Top3 원인 분석",
        "서비스 이슈 중 반복 패턴 찾아줘",
        "콘텐츠 반응 부정 패턴 분석",
        "온보딩 관련 문의 전체 마스터 걸쳐서 분석",
        "채널톡과 편지글에서 동시에 나오는 이슈",
    ]

    for i, ex in enumerate(examples):
        col = example_cols[i % 3]
        if col.button(ex, key=f"ex_{i}", use_container_width=True):
            st.session_state["agent_input"] = ex

    st.divider()

    # Slack Bot 미리보기 안내
    st.info("💡 **Slack Bot으로도 동일하게 사용 가능합니다**\n\n"
            "`/voc 미과장 부정 여론 요약` → 이 AI Agent와 동일한 파이프라인으로 응답\n\n"
            "`/voc 서비스이슈 이번주` → 채널톡+편지글 통합 분석")

    # 채팅 인터페이스
    if "agent_messages" not in st.session_state:
        st.session_state["agent_messages"] = []

    # 이전 메시지 표시
    for msg in st.session_state["agent_messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # 입력
    default_input = st.session_state.pop("agent_input", None)
    user_input = st.chat_input("질문을 입력하세요...", key="agent_chat")

    if default_input and not user_input:
        user_input = default_input

    if user_input:
        st.session_state["agent_messages"].append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        # AI 응답 생성
        with st.chat_message("assistant"):
            with st.spinner("분류 데이터 분석 중..."):
                response = _generate_agent_response(user_input, df, df_channel)
            st.markdown(response)
            st.session_state["agent_messages"].append({"role": "assistant", "content": response})


# ═════════════════════════════════════════════════════════════════════
# 탭 6: 리포트 예시
# ═════════════════════════════════════════════════════════════════════

with tab_report:
    st.header("📋 리포트 예시")
    st.caption("분류 파이프라인 기반으로 자동 생성된 주간 리포트 예시")

    st.info("💡 **기존 방식**: LLM이 2-3K건 원문 전수 읽기 → 분류 + 요약 (느리고 비쌈)\n\n"
            "**개선 방식**: 파이프라인이 미리 분류/태깅 → LLM은 요약만 (5배 빠르고 70% 저렴)")

    # example.md 로드
    example_path = Path("src/example.md")
    if example_path.exists():
        with open(example_path, encoding="utf-8") as f:
            report_content = f.read()

        with st.expander("📄 전체 리포트 보기 (example.md 형식)", expanded=False):
            st.markdown(report_content)
    else:
        st.warning("example.md 파일이 없습니다.")

    st.divider()

    # 파이프라인 기반 미니 리포트 생성
    st.subheader("파이프라인 기반 자동 생성 예시")
    st.markdown(_generate_mini_report(df, df_channel))


# ═════════════════════════════════════════════════════════════════════
# 탭 7: 분석 가능 범위
# ═════════════════════════════════════════════════════════════════════

with tab_catalog:
    st.header("📚 이런 분석이 가능합니다")
    st.caption("분류 데이터로 지원 가능한 분석 유형 카탈로그")

    analysis_categories = {
        "📊 현황 파악": [
            ("전사 VOC 통계", "편지/게시글/채널톡 건수, 전주 대비 증감", "대시보드, 리포트"),
            ("마스터별 여론 현황", "마스터별 긍/부정 비율, 주제 분포", "대시보드, AI Agent"),
            ("카테고리별 분포", "28개 category_tag별 건수 분포", "대시보드"),
        ],
        "🚨 위험 감지": [
            ("부정 급증 감지", "마스터별 부정 비율이 전주 대비 10%p 이상 증가", "자동 알림, 대시보드"),
            ("서비스 장애 감지", "채널톡 특정 topic 건수 2배 이상 급증", "자동 알림"),
            ("교차 이슈 감지", "채널톡+편지글에서 동일 이슈 동시 급증", "대시보드, 자동 알림"),
            ("커뮤니티 분위기 악화", "커뮤니티 소통 부정 비율 추이", "대시보드"),
        ],
        "📈 트렌드 분석": [
            ("감성 추이", "주차별 긍/부정/중립 비율 변화", "대시보드"),
            ("투자 여론 트렌드", "투자 담론 감성과 증시 연동 분석", "대시보드, AI Agent"),
            ("이슈 태그 추이", "특정 category_tag의 주차별 건수 변화", "대시보드"),
            ("마스터 헬스 추이", "마스터별 부정 비율 시계열", "대시보드"),
        ],
        "🔍 심층 분석": [
            ("부정 패턴 분석", "부정 의견의 category_tag + free_tag 클러스터링", "AI Agent"),
            ("상품 런칭 반응", "특정 기간 + 자유태그로 상품 반응 추적", "AI Agent"),
            ("온보딩 문제 횡단 분석", "마스터 횡단 동일 유형 문의 집계", "AI Agent"),
            ("콘텐츠 품질 피드백", "콘텐츠 반응 부정의 세부 원인 분석", "AI Agent, 대시보드"),
        ],
        "📋 리포트 & 배포": [
            ("전사 주간 리포트", "example.md 형식 자동 생성", "자동 생성"),
            ("부서별 맞춤 리포트", "운영/콘텐츠/서비스 각각 필터된 리포트", "자동 생성"),
            ("경영진 핵심 브리핑", "위험신호 + 액션포인트 3줄 요약", "Slack 자동 게시"),
            ("원본 데이터 추출", "조건별 필터링 후 CSV/Excel 다운로드", "대시보드"),
        ],
        "💬 자연어 질의 (AI Agent / Slack Bot)": [
            ("마스터별 질의", '"미과장 이번주 부정 여론 요약"', "AI Agent, Slack"),
            ("주제별 질의", '"서비스 이슈 중 결제 관련만"', "AI Agent, Slack"),
            ("비교 질의", '"이번주 vs 전주 부정 변화"', "AI Agent, Slack"),
            ("원문 조회", '"수익인증 관련 원문 보여줘"', "AI Agent, Slack"),
            ("교차 질의", '"채널톡이랑 편지에서 겹치는 이슈"', "AI Agent"),
        ],
    }

    for category, items in analysis_categories.items():
        st.subheader(category)
        for name, desc, channel in items:
            st.markdown(f"- **{name}** — {desc}  \n  `전달 수단: {channel}`")
        st.divider()


# ═════════════════════════════════════════════════════════════════════
# 탭 8: 원문 탐색
# ═════════════════════════════════════════════════════════════════════

with tab_explorer:
    st.header("🔎 원문 탐색")
    st.caption("조건별 필터링으로 원본 데이터 조회 · CSV 다운로드")

    # 필터
    fc1, fc2, fc3, fc4 = st.columns(4)
    with fc1:
        f_master = st.multiselect("마스터", sorted(df["마스터"].unique()), key="exp_master")
    with fc2:
        f_topic = st.multiselect("주제", TOPICS, key="exp_topic")
    with fc3:
        f_sent = st.multiselect("감성", SENTIMENTS, key="exp_sent")
    with fc4:
        f_search = st.text_input("키워드 검색", key="exp_search")

    # 태그 필터
    all_ctags = sorted(set(t for tags in df["category_tags"] for t in tags))
    f_tags = st.multiselect("category_tag 필터", all_ctags, key="exp_tags")

    # 필터 적용
    filtered = df.copy()
    if f_master:
        filtered = filtered[filtered["마스터"].isin(f_master)]
    if f_topic:
        filtered = filtered[filtered["주제"].isin(f_topic)]
    if f_sent:
        filtered = filtered[filtered["감성"].isin(f_sent)]
    if f_search:
        mask = filtered["내용"].str.contains(f_search, case=False, na=False)
        mask = mask | filtered["summary"].str.contains(f_search, case=False, na=False)
        filtered = filtered[mask]
    if f_tags:
        filtered = filtered[filtered["category_tags"].apply(
            lambda tags: any(t in f_tags for t in tags)
        )]

    st.caption(f"검색 결과: {len(filtered)}건")

    # 표시
    display = filtered[["유형", "마스터", "주제", "감성", "summary", "내용"]].copy()
    display["내용"] = display["내용"].str[:300]

    st.dataframe(display, use_container_width=True, hide_index=True, height=500)

    # CSV 다운로드
    csv = filtered.drop(columns=["category_tags", "free_tags"]).to_csv(index=False).encode("utf-8-sig")
    st.download_button("📥 CSV 다운로드", csv, file_name="voc_filtered.csv", mime="text/csv")(df: pd.DataFrame, df_channel: pd.DataFrame) -> str:
    """분류 데이터 기반 미니 리포트 생성 (LLM 없이)"""
    total = len(df)
    letters = len(df[df["유형"] == "편지"])
    posts = len(df[df["유형"] == "게시글"])

    # 마스터별 통계
    master_stats = []
    for master in df["마스터"].unique():
        m_df = df[df["마스터"] == master]
        t = len(m_df)
        if t < 3:
            continue
        n = len(m_df[m_df["감성"] == "부정"])
        nr = round(n / t * 100, 1) if t else 0
        # 주요 free_tags
        ftags = Counter()
        for tags in m_df[m_df["감성"] == "부정"]["free_tags"]:
            for tag in tags:
                ftags[tag] += 1
        top_issue = ftags.most_common(1)[0][0] if ftags else "-"
        master_stats.append((master, t, nr, top_issue))

    master_stats.sort(key=lambda x: x[1], reverse=True)

    # 서비스 이슈
    svc = df[df["주제"] == "서비스 이슈"]
    svc_tags = Counter()
    for tags in svc["category_tags"]:
        for t in tags:
            svc_tags[t] += 1

    # 위험 마스터
    risk_masters = [(m, t, nr, issue) for m, t, nr, issue in master_stats if nr >= 20]

    report = f"""
## 핵심 요약

| 구분 | 건수 |
|------|------|
| 편지 | {letters}건 |
| 게시글 | {posts}건 |
| 전체 | {total}건 |
| 채널톡 CS | {len(df_channel)}건 |

"""

    if risk_masters:
        report += "### ⚠️ 위험 신호\n\n"
        for m, t, nr, issue in risk_masters:
            report += f"- **{m}**: 부정 {nr}% ({t}건 중) — {issue}\n"
        report += "\n"

    report += "### 마스터별 통계\n\n"
    report += "| 마스터 | 총건수 | 부정비율 | 주요 부정 이슈 |\n"
    report += "|--------|--------|----------|----------------|\n"
    for m, t, nr, issue in master_stats[:10]:
        status = "🔴" if nr >= 25 else "🟡" if nr >= 15 else "🟢"
        report += f"| {m} | {t} | {status} {nr}% | {issue} |\n"

    if svc_tags:
        report += "\n### 서비스 이슈 요약\n\n"
        for tag, cnt in svc_tags.most_common(5):
            report += f"- {tag}: {cnt}건\n"

    return report


def _generate_agent_response(query: str, df: pd.DataFrame, df_channel: pd.DataFrame) -> str:
    """AI Agent 응답 생성 — Claude API 호출"""
    try:
        import anthropic
        client = anthropic.Anthropic()
    except Exception:
        return _generate_fallback_response(query, df, df_channel)

    # 분류 데이터 요약 컨텍스트 생성
    context = _build_data_context(query, df, df_channel)

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{"role": "user", "content": query}],
            system=f"""당신은 VOC 데이터 분석 AI 에이전트입니다.
사용자의 질문에 분류된 VOC 데이터를 기반으로 정확하고 구조화된 인사이트를 제공합니다.

## 사용 가능한 데이터

{context}

## 응답 규칙
- 구체적인 건수와 비율을 포함
- 마스터명, 태그 등 실제 데이터 인용
- 원문 인용 시 이탤릭 사용
- 액션 포인트/권장 사항 포함
- 마크다운 형식으로 구조화""",
        )
        return response.content[0].text
    except Exception as e:
        return _generate_fallback_response(query, df, df_channel)


def _build_data_context(query: str, df: pd.DataFrame, df_channel: pd.DataFrame) -> str:
    """쿼리에 맞는 데이터 컨텍스트 생성"""
    context_parts = []

    # 기본 통계
    total = len(df)
    context_parts.append(f"### 전체 통계\n- 편지+게시글: {total}건")
    context_parts.append(f"- 채널톡 CS: {len(df_channel)}건")

    # 감성 분포
    sent_dist = df["감성"].value_counts().to_dict()
    context_parts.append(f"- 감성: 긍정 {sent_dist.get('긍정',0)}건, 부정 {sent_dist.get('부정',0)}건, 중립 {sent_dist.get('중립',0)}건")

    # 마스터별 통계
    context_parts.append("\n### 마스터별 통계")
    for master in df["마스터"].unique():
        m_df = df[df["마스터"] == master]
        t = len(m_df)
        if t < 3:
            continue
        n = len(m_df[m_df["감성"] == "부정"])
        nr = round(n / t * 100, 1) if t else 0

        # 주요 태그
        tags = Counter()
        for tlist in m_df["category_tags"]:
            for tag in tlist:
                tags[tag] += 1
        top_tags = [f"{tag}({cnt})" for tag, cnt in tags.most_common(3)]

        # 부정 free_tags
        neg_ftags = Counter()
        for tlist in m_df[m_df["감성"] == "부정"]["free_tags"]:
            for tag in tlist:
                neg_ftags[tag] += 1
        top_neg = [f'"{tag}"' for tag, _ in neg_ftags.most_common(3)]

        context_parts.append(f"- **{master}**: {t}건, 부정 {nr}%, 태그: {', '.join(top_tags)}, 부정키워드: {', '.join(top_neg)}")

    # 주제별 통계
    context_parts.append("\n### 주제별 통계")
    for topic in TOPICS:
        t_df = df[df["주제"] == topic]
        t_total = len(t_df)
        t_neg = len(t_df[t_df["감성"] == "부정"])
        context_parts.append(f"- {topic}: {t_total}건 (부정 {t_neg}건)")

    # 서비스 이슈 상세
    svc_df = df[df["주제"] == "서비스 이슈"]
    if not svc_df.empty:
        context_parts.append(f"\n### 서비스 이슈 상세 ({len(svc_df)}건)")
        for _, row in svc_df.head(10).iterrows():
            context_parts.append(f"- [{row['마스터']}] {row['summary']} (태그: {', '.join(row['category_tags'])})")

    # 채널톡 통계
    if not df_channel.empty:
        context_parts.append("\n### 채널톡 CS 통계")
        ch_topics = Counter()
        for topics in df_channel["topics"]:
            for t in topics:
                ch_topics[t] += 1
        for t, cnt in ch_topics.most_common():
            context_parts.append(f"- {t}: {cnt}건")

    # 쿼리 관련 원문 샘플
    keywords = [w for w in query.split() if len(w) > 1]
    relevant = df.copy()
    if keywords:
        mask = pd.Series(False, index=df.index)
        for kw in keywords:
            mask = mask | df["내용"].str.contains(kw, case=False, na=False)
            mask = mask | df["summary"].str.contains(kw, case=False, na=False)
            mask = mask | df["마스터"].str.contains(kw, case=False, na=False)
        relevant = df[mask]

    if not relevant.empty and len(relevant) < len(df):
        context_parts.append(f"\n### 쿼리 관련 원문 샘플 ({len(relevant)}건 중 상위 10건)")
        for _, row in relevant.head(10).iterrows():
            context_parts.append(f"- [{row['마스터']}|{row['주제']}|{row['감성']}] {row['summary']}")
            context_parts.append(f"  원문: \"{row['내용'][:150]}\"")

    return "\n".join(context_parts)


def _generate_fallback_response(query: str, df: pd.DataFrame, df_channel: pd.DataFrame) -> str:
    """API 호출 실패 시 규칙 기반 응답"""
    query_lower = query.lower()

    # 전체 요약
    if "요약" in query or "핵심" in query:
        total = len(df)
        neg = len(df[df["감성"] == "부정"])
        neg_ratio = round(neg / total * 100, 1)

        # 부정 비율 높은 마스터
        risk = []
        for master in df["마스터"].unique():
            m_df = df[df["마스터"] == master]
            t = len(m_df)
            if t < 5:
                continue
            n = len(m_df[m_df["감성"] == "부정"])
            nr = round(n / t * 100, 1)
            if nr >= 15:
                risk.append((master, t, nr))
        risk.sort(key=lambda x: x[2], reverse=True)

        # 서비스 이슈
        svc = df[df["주제"] == "서비스 이슈"]
        svc_tags = Counter()
        for tags in svc["category_tags"]:
            for t in tags:
                svc_tags[t] += 1

        result = f"""📊 **전체 VOC 핵심 요약** ({df['주차'].unique()[0]} 주차)

**전체**: {total}건 (편지 {len(df[df['유형']=='편지'])}건 + 게시글 {len(df[df['유형']=='게시글'])}건)
**전체 부정 비율**: {neg_ratio}%

"""
        if risk:
            result += "**⚠️ 위험 마스터:**\n"
            for m, t, nr in risk[:3]:
                neg_ftags = Counter()
                for tlist in df[(df["마스터"]==m) & (df["감성"]=="부정")]["free_tags"]:
                    for tag in tlist:
                        neg_ftags[tag] += 1
                top = neg_ftags.most_common(2)
                issues = ", ".join([f'"{t}"' for t, _ in top])
                result += f"- **{m}**: 부정 {nr}% — {issues}\n"

        if svc_tags:
            result += f"\n**서비스 이슈** ({len(svc)}건):\n"
            for tag, cnt in svc_tags.most_common(3):
                result += f"- {tag}: {cnt}건\n"

        return result

    # 마스터별
    for master in df["마스터"].unique():
        if master in query:
            m_df = df[df["마스터"] == master]
            t = len(m_df)
            n = len(m_df[m_df["감성"] == "부정"])
            p = len(m_df[m_df["감성"] == "긍정"])

            neg_tags = Counter()
            for tags in m_df[m_df["감성"] == "부정"]["free_tags"]:
                for tag in tags:
                    neg_tags[tag] += 1

            result = f"""📊 **{master}** 분석

총 {t}건 | 긍정 {p}건({round(p/t*100,1)}%) | 부정 {n}건({round(n/t*100,1)}%)

"""
            if neg_tags:
                result += "**부정 주요 키워드:**\n"
                for tag, cnt in neg_tags.most_common(5):
                    result += f"- \"{tag}\" ({cnt}건)\n"

            # 부정 원문 샘플
            neg_items = m_df[m_df["감성"] == "부정"].head(3)
            if not neg_items.empty:
                result += "\n**부정 원문 샘플:**\n"
                for _, row in neg_items.iterrows():
                    result += f"\n> _{row['내용'][:200]}_\n"

            return result

    return "해당 질문에 대한 분석 결과를 생성하려면 API 키가 필요합니다. ANTHROPIC_API_KEY 환경변수를 설정해주세요."
