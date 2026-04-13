# VOC 파이프라인 GCP 운영 배포

Cloud Run Jobs + Cloud Scheduler 기반 운영 세팅.

## 스코프

- ✅ **일간 파이프라인** (`run_daily_pipeline.py --skip-channel`) — 편지글/게시글 분류 → voc_labelled
- ✅ **주간 리포트** (`generate_weekly_report_v5.py`) — 분류 → 리포트 → Notion/Slack
- ⏭️ **채널톡 KcELECTRA 분류** — 이미지 포함 (Dockerfile)은 되어있으나 초기 배포 스코프 제외
  - 필요 시 `--skip-channel` 제거 + 메모리 상향으로 활성화 가능

## 아키텍처

```
Cloud Scheduler  →  Cloud Run Jobs  →  BigQuery (voc_labelled)
                       │                 ↓
                       ↓          [주간 리포트만]
                  Secret Manager      Notion API
                  (AWS/Notion/Slack)  Slack API
```

## 최초 1회 세팅

### 전제 조건
- `gcloud` CLI 설치 + 프로젝트 권한 (Owner 또는 Editor + Secret Admin)
- 로컬 `.env` 파일이 채워져 있을 것
- Docker 로그인 필요 시: `gcloud auth configure-docker asia-northeast3-docker.pkg.dev`

### 단계

```bash
# 1. 인프라 (API/저장소/서비스 계정/IAM/시크릿 껍데기)
bash deploy/setup_infrastructure.sh

# 2. 시크릿 값 주입 (.env → Secret Manager)
bash deploy/create_secrets.sh

# 3. 이미지 빌드 + Artifact Registry 푸시
gcloud builds submit --config=deploy/cloudbuild.yaml .

# 4. Cloud Run Jobs + Scheduler 생성
bash deploy/deploy_jobs.sh
```

## 운영 명령

### 수동 실행 (테스트/백필)
```bash
# 일간 — 어제 데이터
gcloud run jobs execute voc-daily --region=asia-northeast3 --wait

# 일간 — 특정 날짜
gcloud run jobs execute voc-daily --region=asia-northeast3 --wait \
    --args="scripts/run_daily_pipeline.py,--skip-channel,--date,2026-04-10"

# 주간 — 지난 주
gcloud run jobs execute voc-weekly --region=asia-northeast3 --wait

# 주간 — 특정 주
gcloud run jobs execute voc-weekly --region=asia-northeast3 --wait \
    --args="scripts/generate_weekly_report_v5.py,--start,2026-04-06,--end,2026-04-13"
```

### 로그 확인
```bash
# 최근 실행 목록
gcloud beta run jobs executions list --job=voc-daily --region=asia-northeast3

# 특정 실행의 로그
gcloud beta run jobs executions logs <EXECUTION_ID> --region=asia-northeast3
```

### 스케줄 관리
```bash
# 현재 등록된 스케줄
gcloud scheduler jobs list --location=asia-northeast3

# 일시 중지 / 재개
gcloud scheduler jobs pause voc-daily-schedule --location=asia-northeast3
gcloud scheduler jobs resume voc-daily-schedule --location=asia-northeast3

# 즉시 트리거 (Scheduler 테스트)
gcloud scheduler jobs run voc-daily-schedule --location=asia-northeast3
```

### 이미지 업데이트 (코드 변경 시)
```bash
gcloud builds submit --config=deploy/cloudbuild.yaml .
# 재배포 불필요 — :latest 태그를 Job 이 자동 사용
```

## 스케줄

| Job | Cron (KST) | 용도 |
|-----|-----------|------|
| `voc-daily-schedule` | 매일 08:00 | 전날 편지/게시글 분류 → voc_labelled |
| `voc-weekly-schedule` | 매주 월 09:00 | 지난 주 리포트 생성 + Notion/Slack 발행 |

주간 리포트는 월요일 오전에 실행되므로, **일간 파이프라인이 일요일까지 완주되어 있어야 함** (주간은 voc_labelled 읽기 전제). 일간이 실패하면 sanity check 가 "총 건수 부족/평일 0건" 으로 중단시킴 → 별도 알림 필요.

## 비용 참고

- **일간**: 500건/일 기준 Bedrock Haiku ≈ $3/일 → 월 $90 (유일한 분류 비용)
- **주간**: voc_labelled 재사용 — 톤/인용구 검수 Haiku 호출만 ≈ $0.05/주 → 무시 수준
- **Cloud Run**: 실행 시간 기준 (일간 <5분, 주간 <5분) → 월 $1 이하
- **Cloud Scheduler**: 3회/월 무료 초과분 $0.10 → 무시 수준

합계: 약 **월 $90**. 분류는 일간에서 1회만 수행 → 주간은 읽기 전용.

## 보안

- 서비스 계정 `voc-pipeline-sa@` 최소 권한:
  - `bigquery.dataEditor` + `bigquery.jobUser`
  - `secretmanager.secretAccessor`
  - `run.invoker` (Scheduler → Run Jobs 용)
  - `logging.logWriter`
- `.env`, `accountKey.json` 은 컨테이너에 **포함 안 됨** (`.dockerignore`)
- 모든 시크릿은 Secret Manager 관리 (버전 이력 있음)

## 트러블슈팅

### "permission denied" on BigQuery
서비스 계정에 `bigquery.dataEditor` 권한 있는지 확인:
```bash
gcloud projects get-iam-policy us-service-data \
    --flatten="bindings[].members" \
    --filter="bindings.members:voc-pipeline-sa"
```

### Bedrock 호출 실패
Secret Manager 의 AWS 자격 증명 확인:
```bash
gcloud secrets versions access latest --secret=aws-access-key-id
gcloud secrets versions access latest --secret=aws-region
```

### 이미지 빌드 OOM
`cloudbuild.yaml` 의 `machineType: E2_HIGHCPU_8` 사용 중. KcELECTRA 모델 제외하려면 `Dockerfile` 의 모델 COPY 라인 주석 처리.

### Scheduler 가 Job 을 호출 못함
IAM 정책 확인 — 서비스 계정에 `roles/run.invoker` 필요 (`deploy_jobs.sh` 마지막 단계에서 자동 부여).

## 다음 단계 (TODO)

- [ ] 채널톡 KcELECTRA 활성화 (메모리 증설 + `--skip-channel` 제거)
- [ ] 일간 파이프라인 완료 시 Slack 모니터링 메시지 (대시보드 URL + 부정 이슈 요약)
- [ ] 일간 파이프라인 실패 시 Slack 알림 (sanity check fail 시 주간이 막히므로)
- [ ] 대시보드 Cloud Run 배포 (별도 서비스 — BigQuery 직접 연결)
