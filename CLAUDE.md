# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

주간 리포트 자동 생성 시스템 - 금융 콘텐츠 플랫폼의 편지글/게시글을 분석하여 인사이트 리포트 생성

### Key Concepts

- **마스터 (Masters)**: 투자 교육 커뮤니티를 운영하는 금융 콘텐츠 크리에이터
- **편지글 (Letters)**: 구독자가 마스터에게 보내는 개인 메시지
- **게시글 (Posts)**: 각 마스터 커뮤니티 게시판의 게시글
- **오피셜클럽**: 각 마스터가 운영하는 개별 커뮤니티

## Commands

```bash
# 의존성 설치
pip install -r requirements.txt

# 일간 분류 (어제 KST 일자) — 정상 운영에서는 Cloud Run Job 자동 실행
python scripts/run_daily_pipeline.py --skip-channel
python scripts/run_daily_pipeline.py --date 2026-04-10 --skip-channel  # 백필

# 주간 리포트 (지난 주 voc_labelled 읽기) — 정상 운영에서는 Cloud Run Job 자동 실행
python scripts/generate_weekly_report_v5.py
python scripts/generate_weekly_report_v5.py --start 2026-04-06 --end 2026-04-13
python scripts/generate_weekly_report_v5.py --no-upload   # 로컬 검증만
python scripts/generate_weekly_report_v5.py --force        # sanity check 무시

# 기존 마크다운 리포트 재업로드 (사고 복구용 — 재분류 없음)
python scripts/upload_existing_report.py \
    --report reports/weekly_report_v5_2026-04-06.md \
    --start 2026-04-06 --end 2026-04-13 \
    --excel reports/weekly_data_2026-04-06.xlsx

# 대시보드 (로컬 개발)
streamlit run dashboard/voc_dashboard.py

# 개별 테스트
python scripts/test_classifier.py      # 분류기 테스트
python scripts/explore_bigquery.py     # BigQuery 스키마 탐색
```

GCP 자동 운영 배포는 `deploy/README.md` 참조.

## Architecture

운영 데이터 흐름 (Phase 4 — `openspec/changes/phase-4-operational-deployment/proposal.md`):

```
us_plus_next        ──→  voc-daily Cloud Run Job   ──→  voc-weekly Cloud Run Job  ──→  Notion / Slack
(원본 BigQuery)         (매일 08:00 KST, Bedrock Haiku)   (매주 월 09:00 KST)
                              ↓                              ↑ 읽기 전용
                       voc_labelled.letters_posts ──────────┘
                       (DATE 파티션, id 기준 dedup)
                              ↓
                       voc-dashboard Cloud Run Service (Streamlit, BQ 직접)
```

```
src/
├── bigquery/           # BigQuery 연동
│   ├── client.py       # BigQueryClient
│   ├── queries.py      # WeeklyDataQuery (us_plus_next)
│   └── writer.py       # BigQueryWriter (voc_labelled.letters_posts upsert)
├── classifier_v5/      # 운영 분류기 (Bedrock Haiku, 4분류)
│   └── bedrock_classifier.py   # BedrockV5Classifier
├── classifier_v4/      # 채널톡 KcELECTRA (초기 운영 스코프 제외)
├── reporter/           # 리포트 + 안전장치
│   ├── analytics.py            # WeeklyAnalytics — 통계 분석
│   ├── report_generator.py     # ReportGenerator — 마크다운 (인사이트 LLM 가이드 포함)
│   ├── feedback_clusterer.py   # 피드백 클러스터링 (Bedrock Titan 임베딩)
│   ├── sanity_check.py         # 룰 기반 데이터 건전성 (적재 실패 감지)
│   ├── tone_reviewer.py        # 부서 귀책 표현 자동 교정
│   └── quote_reviewer.py       # 욕설/인신공격 인용구 자동 제거 (LLM)
├── integrations/       # 외부 시스템
│   ├── notion_client.py
│   └── slack_client.py
├── utils/
│   ├── excel_exporter.py       # 엑셀 내보내기 (datetime tz-aware 처리)
│   └── run_logger.py           # logs/weekly_report_{date}/ 단계별 로그
└── (legacy: classifier/, vectorstore/, storage/ — 구버전, 운영 미사용)
```

### Pipeline Flow (운영)

**일간 (`run_daily_pipeline.py`)**
1. `WeeklyDataQuery.get_weekly_data(어제, 오늘)` — 원본 조회
2. `BedrockV5Classifier.classify_batch()` — 4분류 + 감정 + 요약 + 태그
3. `BigQueryWriter.write_letters_posts()` — voc_labelled 멱등 upsert
4. 마스터별 부정 30%+ 시 Slack 알림

**주간 (`generate_weekly_report_v5.py`)**
1. `load_from_voc_labelled(start, end)` — 분류된 데이터 읽기 (재분류 없음)
2. `count_from_voc_labelled(prev)` — 전주 건수만
3. `check_data_health()` — sanity check (fail이면 중단)
4. `WeeklyAnalytics.analyze_weekly_data()` — 통계
5. `cluster_feedbacks()` — 피드백 클러스터링
6. `ReportGenerator.generate_report()` — 마크다운 + 인사이트 LLM (인용구 가이드)
7. `review_report()` — 톤 검수
8. `review_quotes_in_report()` — 인용구 검수 (욕설/인신공격 제거)
9. `export_to_excel()` + `NotionReportClient.create_report_page()` + `SlackNotifier`

### Classification Categories (v5 4분류)

- **시장·투자**: 종목/포트폴리오/시장 전망
- **마스터 반응**: 마스터에 대한 평가/피드백 (감사/비판/신뢰/불신)
- **일상**: 인사/안부/잡담
- **피드백**: 운영/CS/서비스 개선 요청 (대응 필요)

## Environment Setup

```bash
# .env 파일 생성 (.env.example 참고)
ANTHROPIC_API_KEY=your_api_key
GOOGLE_APPLICATION_CREDENTIALS=./accountKey.json
BIGQUERY_PROJECT_ID=your_project
REPORT_OUTPUT_DIR=./reports
```

## Data Storage

- `data/classified_data/`: 분류된 주간 데이터 (JSON)
- `data/stats/`: 주간 통계 요약
- `chroma_db/`: ChromaDB 벡터 저장소
- `reports/`: 생성된 마크다운 리포트

## Report Format

`example.md` 형식을 따름:
- 핵심 요약: 전체 편지/게시글 통계 + 전주 대비 증감
- 마스터별 상세: 통계 테이블, 주요 내용, 서비스 피드백, 체크 포인트
- 직접 인용은 이탤릭 (_"인용문"_)
- 개선 권고는 화살표 표시 (_→ 권고사항_)

### /note 기본 설정
- **category**: `voc 데이터 라벨링`
- **trigger**: `coding`
- 이 프로젝트에서 `/note` 실행 시 위 category를 기본값으로 사용한다 (F.F.md 추론 생략)
