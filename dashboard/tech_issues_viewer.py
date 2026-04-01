"""기술 이슈 클러스터링 뷰어

실행: streamlit run dashboard/tech_issues_viewer.py
"""
import json
from pathlib import Path
from collections import Counter

import streamlit as st
import pandas as pd

st.set_page_config(page_title="기술 이슈 클러스터링", layout="wide")

# ── 데이터 로드 ──
EXPORTS_DIR = Path("exports")

@st.cache_data
def load_excel(path):
    tasks = pd.read_excel(path, sheet_name="Jira Tasks")
    detail = pd.read_excel(path, sheet_name="전체 상세")
    stats = pd.read_excel(path, sheet_name="통계")
    return tasks, detail, stats


# 파일 선택
excel_files = sorted(EXPORTS_DIR.glob("tech_issues_clustered_*.xlsx"), reverse=True)
if not excel_files:
    st.error("클러스터링 엑셀 파일이 없습니다. 먼저 cluster_tech_issues.py를 실행하세요.")
    st.stop()

selected = st.sidebar.selectbox(
    "데이터 선택",
    excel_files,
    format_func=lambda x: x.stem.replace("tech_issues_clustered_", ""),
)

tasks_df, detail_df, stats_df = load_excel(selected)

# ── 헤더 ──
st.title("🔧 기술 이슈 클러스터링")

col1, col2, col3 = st.columns(3)
col1.metric("전체 이슈", f"{len(detail_df)}건")
col2.metric("클러스터 (Jira 태스크)", f"{len(tasks_df)}개")
col3.metric("2건 이상 클러스터", f"{len(tasks_df[tasks_df['건수'] >= 2])}개")

st.divider()

# ── 필터 ──
st.sidebar.divider()
st.sidebar.subheader("필터")

min_count = st.sidebar.slider("최소 건수", 1, int(tasks_df['건수'].max()), 1)
filtered_tasks = tasks_df[tasks_df['건수'] >= min_count]

# ── 클러스터 목록 ──
st.subheader(f"클러스터 목록 ({len(filtered_tasks)}개)")

for _, row in filtered_tasks.iterrows():
    cluster_id = row['클러스터']
    count = row['건수']
    desc = row['대표 증상']
    communities = row.get('영향 커뮤니티', '')
    date_range = row.get('날짜 범위', '')
    sentiment = row.get('감성 분포', '')

    # 건수에 따라 색상
    if count >= 5:
        badge = "🔴"
    elif count >= 3:
        badge = "🟡"
    else:
        badge = "🟢"

    with st.expander(f"{badge} {cluster_id} — {desc} ({count}건)", expanded=(count >= 5)):
        cols = st.columns([1, 1, 1])
        cols[0].write(f"**커뮤니티:** {communities}")
        cols[1].write(f"**기간:** {date_range}")
        cols[2].write(f"**감성:** {sentiment}")

        # 이 클러스터의 상세 항목
        cluster_items = detail_df[detail_df['클러스터'] == cluster_id]

        if not cluster_items.empty:
            for _, item in cluster_items.iterrows():
                st.markdown(f"""
<div style="background: #f8f9fa; padding: 10px; border-radius: 5px; margin: 5px 0; border-left: 3px solid {'#dc3545' if item.get('감성') == '부정' else '#6c757d'};">
<small><b>{item.get('출처', '')}</b> | {item.get('마스터', '')} | {item.get('작성일', '')}</small><br>
<b>{item.get('증상 서술', '')}</b><br>
<span style="color: #666;">{str(item.get('원문', ''))[:200]}</span>
</div>
""", unsafe_allow_html=True)

        # Jira 제목 제안
        jira_title = row.get('Jira 제목 제안', '')
        if jira_title:
            st.code(jira_title, language=None)

st.divider()

# ── 전체 테이블 ──
st.subheader("전체 데이터 테이블")

tab1, tab2 = st.tabs(["클러스터 요약", "전체 상세"])

with tab1:
    st.dataframe(
        filtered_tasks[['클러스터', '건수', '대표 증상', '영향 커뮤니티', '날짜 범위', 'Jira 제목 제안']],
        use_container_width=True,
        height=400,
    )

with tab2:
    st.dataframe(
        detail_df[['클러스터', '증상 서술', 'subtag', '마스터', '감성', '원문', '작성일']],
        use_container_width=True,
        height=500,
    )
