# Tasks: Phase 4 — VOC 파이프라인 운영 전환

## 완료된 작업 (2026-04-13)

### 데이터 인프라
- [x] BigQuery 데이터셋 `us_plus_new` → `us_plus_next` 이전 (모든 쿼리 갱신, 7개 파일)
- [x] `voc_labelled.letters_posts` 4/6~4/12 백필 (2641건, KST 일자별 멱등 upsert)

### 일간 파이프라인 (이미 존재, 재확인)
- [x] `run_daily_pipeline.py` 검토: Bedrock v5 분류 + voc_labelled 적재 + 부정 급증 Slack
- [x] 채널톡 KcELECTRA 활성화 (모델 489MB, 메모리 4Gi, channel_io.messages 적재 정상 확인)

### 주간 파이프라인 (재구조)
- [x] `generate_weekly_report_v5.py` 를 voc_labelled 읽기 전용으로 변경 (Phase 2 분류 단계 삭제)
- [x] `load_from_voc_labelled` / `count_from_voc_labelled` 헬퍼 추가
- [x] 분류 결과 JSON 저장 + voc_labelled 저장 로직 (백필 시 사용했으나 정상 운영에선 불필요)
- [x] Notion/Slack/엑셀 업로드 통합 (`--no-upload` 옵션 포함)

### 안전장치
- [x] `src/reporter/sanity_check.py` — 룰 기반 데이터 건전성 (총 건수/평일 0건/전주 대비 감소/마스터 0건)
- [x] `src/utils/run_logger.py` — `logs/weekly_report_{date}/` 에 pipeline.log + anomalies.json + cost.json
- [x] 인사이트 프롬프트에 부적절 인용구 회피 가이드 추가 (`_generate_master_insight`, `_generate_key_issues`)
- [x] `src/reporter/quote_reviewer.py` — Bedrock Haiku 인용구 검수 LLM 레이어
- [x] v5 스크립트 Phase 5-2 에 인용구 검수 통합 + `quote_audit.json` 저장

### GCP 배포 자산
- [x] `Dockerfile` 정리 (ENTRYPOINT 유연화 — 일간/주간 동일 이미지)
- [x] `deploy/cloudbuild.yaml` (E2_HIGHCPU_8, Artifact Registry 푸시)
- [x] `deploy/setup_infrastructure.sh` (API/저장소/SA/IAM/Secret 껍데기)
- [x] `deploy/create_secrets.sh` (.env → Secret Manager 자동 주입)
- [x] `deploy/deploy_jobs.sh` (voc-daily 매일 08:00, voc-weekly 매주 월 09:00 KST)
- [x] `deploy/deploy_dashboard.sh` (Cloud Run Service)
- [x] `deploy/README.md` (전체 배포 가이드 + 운영/트러블슈팅)

### 대시보드
- [x] `dashboard/voc_dashboard.py` BigQuery 직접 연결 (10분 캐시)
- [x] 동적 기간 선택 (최근 7/14/30일 / 커스텀)
- [x] 클러스터링 데이터 없으면 개발팀 뷰 자동 비활성
- [x] `dashboard/Dockerfile` + `dashboard/requirements.txt` (Streamlit 슬림 이미지)

### 보조 스크립트
- [x] `scripts/upload_existing_report.py` — 재분류 없이 기존 마크다운 Notion/Slack 재업로드 (사고 복구용)
- [x] `excel_exporter.py` — datetime tz-aware 처리 (BigQuery TIMESTAMP 호환)

## 사용자가 직접 실행해야 할 작업

### 최초 1회 GCP 세팅
- [ ] `bash deploy/setup_infrastructure.sh`
- [ ] `bash deploy/create_secrets.sh` (.env 채워진 상태에서)
- [ ] `gcloud builds submit --config=deploy/cloudbuild.yaml .`
- [ ] `bash deploy/deploy_jobs.sh`
- [ ] `bash deploy/deploy_dashboard.sh`
- [ ] 수동 실행 테스트: `gcloud run jobs execute voc-daily --region=asia-northeast3 --wait`

### 운영 시작 후 (1~2주)
- [ ] 일간이 매일 정상 동작하는지 voc_labelled 일자별 건수 확인
- [ ] 주간 리포트가 Notion/Slack 정상 발행되는지 첫 월요일 모니터링
- [ ] 대시보드 URL 동작 확인 + 회사 도메인에 IAP 적용

### 정리
- [ ] 이전 발행된 잘못된 Notion 페이지 3개 수동 삭제 (`...95439`, `...e278e4` 등)
- [ ] `generate_weekly_report.py` 삭제 검토 (v5 가 동일 역할 + 더 안전)
- [ ] `generate_custom_week_report.py` 삭제 검토 (구 VectorClassifier — 사고 원인이었음)

## 다음 단계 (별도 phase)

- [x] **Phase 4-1**: 일간 완료 Slack 모니터링 메시지 (대시보드 URL + 부정 요약)
- [x] **Phase 4-2**: 일간 실패 Slack 알림 (sanity check fail 시 주간이 막힘)
- [ ] **Phase 5**: `cluster_tech_issues.py` voc_labelled 읽기로 전환 (분기 분석 시점에)
