#!/bin/bash
# Cloud Run Jobs 생성/업데이트 + Cloud Scheduler cron 등록
# 사용: bash deploy/deploy_jobs.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-us-service-data}"
REGION="${REGION:-asia-northeast3}"
REPO_NAME="voc-pipeline"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/voc-pipeline:latest"
SA_EMAIL="voc-pipeline-sa@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud config set project "${PROJECT_ID}"

# 공통 시크릿 바인딩 (env var = secret 이름)
SECRETS="ANTHROPIC_API_KEY=anthropic-api-key:latest,\
AWS_ACCESS_KEY_ID=aws-access-key-id:latest,\
AWS_SECRET_ACCESS_KEY=aws-secret-access-key:latest,\
AWS_REGION=aws-region:latest,\
NOTION_API_KEY=notion-api-key:latest,\
NOTION_DATABASE_ID=notion-database-id:latest,\
SLACK_BOT_TOKEN=slack-bot-token:latest,\
SLACK_CHANNEL_ID=slack-channel-id:latest"

COMMON_ENV="BIGQUERY_PROJECT_ID=${PROJECT_ID},PYTHONUNBUFFERED=1"

# ── Job 1: 일간 파이프라인 ──────────────────────────
echo ">> voc-daily Cloud Run Job 생성/업데이트"
gcloud run jobs deploy voc-daily \
    --image="${IMAGE}" \
    --region="${REGION}" \
    --service-account="${SA_EMAIL}" \
    --command=python \
    --args="scripts/run_daily_pipeline.py,--skip-channel" \
    --set-env-vars="${COMMON_ENV}" \
    --set-secrets="${SECRETS}" \
    --task-timeout=1800 \
    --memory=2Gi \
    --cpu=2 \
    --max-retries=1

# ── Job 2: 주간 리포트 ──────────────────────────────
echo ">> voc-weekly Cloud Run Job 생성/업데이트"
gcloud run jobs deploy voc-weekly \
    --image="${IMAGE}" \
    --region="${REGION}" \
    --service-account="${SA_EMAIL}" \
    --command=python \
    --args="scripts/generate_weekly_report_v5.py" \
    --set-env-vars="${COMMON_ENV}" \
    --set-secrets="${SECRETS}" \
    --task-timeout=3600 \
    --memory=4Gi \
    --cpu=2 \
    --max-retries=0

# ── Scheduler: 일간 (매일 08:00 KST = UTC 23:00 전날) ─
echo ">> voc-daily-schedule 생성/업데이트"
gcloud scheduler jobs create http voc-daily-schedule \
    --location="${REGION}" \
    --schedule="0 8 * * *" \
    --time-zone="Asia/Seoul" \
    --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/voc-daily:run" \
    --http-method=POST \
    --oauth-service-account-email="${SA_EMAIL}" \
    --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform" \
    2>/dev/null || \
gcloud scheduler jobs update http voc-daily-schedule \
    --location="${REGION}" \
    --schedule="0 8 * * *" \
    --time-zone="Asia/Seoul"

# ── Scheduler: 주간 (매주 월요일 09:00 KST) ──────────
echo ">> voc-weekly-schedule 생성/업데이트"
gcloud scheduler jobs create http voc-weekly-schedule \
    --location="${REGION}" \
    --schedule="0 9 * * 1" \
    --time-zone="Asia/Seoul" \
    --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/voc-weekly:run" \
    --http-method=POST \
    --oauth-service-account-email="${SA_EMAIL}" \
    --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform" \
    2>/dev/null || \
gcloud scheduler jobs update http voc-weekly-schedule \
    --location="${REGION}" \
    --schedule="0 9 * * 1" \
    --time-zone="Asia/Seoul"

# Scheduler → Run Jobs 호출용 권한
echo ">> Scheduler IAM 바인딩"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/run.invoker" \
    --condition=None \
    >/dev/null

echo
echo "=== 배포 완료 ==="
echo
echo "수동 실행 테스트:"
echo "  gcloud run jobs execute voc-daily --region=${REGION} --wait"
echo "  gcloud run jobs execute voc-weekly --region=${REGION} --wait"
echo
echo "로그 확인:"
echo "  gcloud beta run jobs executions list --job=voc-daily --region=${REGION}"
