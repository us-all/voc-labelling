#!/bin/bash
# VOC 파이프라인 GCP 인프라 최초 1회 세팅
# 사용: bash deploy/setup_infrastructure.sh
#
# 수행:
#   1. Artifact Registry 저장소 생성
#   2. 서비스 계정 생성 + BigQuery/Secret Manager 권한 부여
#   3. Secret Manager 시크릿 "껍데기" 생성 (값은 별도 명령으로 주입)
#   4. Cloud Build / Cloud Run / Scheduler API 활성화

set -euo pipefail

# ── 환경변수 (프로젝트마다 조정) ────────────────────────────
PROJECT_ID="${PROJECT_ID:-us-service-data}"
REGION="${REGION:-asia-northeast3}"
REPO_NAME="voc-pipeline"
SA_NAME="voc-pipeline-sa"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "=== VOC 파이프라인 인프라 세팅 ==="
echo "프로젝트: ${PROJECT_ID}"
echo "리전: ${REGION}"
echo

gcloud config set project "${PROJECT_ID}"

# 1. API 활성화
echo ">> Google API 활성화"
gcloud services enable \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    run.googleapis.com \
    cloudscheduler.googleapis.com \
    secretmanager.googleapis.com \
    bigquery.googleapis.com

# 2. Artifact Registry 저장소
echo ">> Artifact Registry 저장소"
gcloud artifacts repositories create "${REPO_NAME}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="VOC 파이프라인 컨테이너 이미지" \
    || echo "  (이미 존재 — 건너뜀)"

# 3. 서비스 계정
echo ">> 서비스 계정"
gcloud iam service-accounts create "${SA_NAME}" \
    --display-name="VOC Pipeline Service Account" \
    || echo "  (이미 존재 — 건너뜀)"

# 4. IAM 바인딩 — BigQuery 읽기/쓰기, Secret Manager 접근, Cloud Run 실행
echo ">> IAM 권한 부여"
for role in \
    roles/bigquery.dataEditor \
    roles/bigquery.jobUser \
    roles/secretmanager.secretAccessor \
    roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${SA_EMAIL}" \
      --role="${role}" \
      --condition=None \
      >/dev/null
  echo "  + ${role}"
done

# 5. Secret Manager 시크릿 껍데기 (값은 deploy/create_secrets.sh 참고)
echo ">> Secret Manager 껍데기 생성"
for secret in \
    anthropic-api-key \
    aws-access-key-id \
    aws-secret-access-key \
    aws-region \
    notion-api-key \
    notion-database-id \
    slack-bot-token \
    slack-channel-id; do
  gcloud secrets create "${secret}" --replication-policy=automatic \
      >/dev/null 2>&1 && echo "  + ${secret}" || echo "  (${secret} 이미 존재)"
done

echo
echo "=== 완료 ==="
echo "다음 단계:"
echo "  1. bash deploy/create_secrets.sh   # 시크릿 값 주입 (.env에서 자동 추출)"
echo "  2. gcloud builds submit --config=deploy/cloudbuild.yaml ."
echo "  3. bash deploy/deploy_jobs.sh"
