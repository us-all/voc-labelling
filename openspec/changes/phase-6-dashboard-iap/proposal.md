# Proposal: Phase 6 — 대시보드 IAP 인증 적용

## Intent

VOC 대시보드 (`voc-dashboard` Cloud Run Service) 를 회사 도메인
사용자에게 **브라우저 클릭만으로 접근 가능**하게 만든다. 현재는 토큰 헤더가
필요해서 브라우저 그냥 클릭 시 403 Forbidden.

## 현재 상태

- URL: `https://voc-dashboard-fzln77pgnq-du.a.run.app`
- 인증: `--no-allow-unauthenticated` + IAM `domain:us-all.co.kr roles/run.invoker`
- 문제: 브라우저는 IAM 토큰 헤더 자동 첨부 안 함 → 403

## 옵션

### 옵션 A: Cloud Run 직접 IAP (권장)
신기능. Load Balancer 없이 Cloud Run Service 에 IAP 직접 활성화.

```bash
gcloud services enable iap.googleapis.com
gcloud iap oauth-brands create \
    --application_title="VOC Dashboard" \
    --support_email=gayunlee11@us-all.co.kr
gcloud beta run services update voc-dashboard \
    --region=asia-northeast3 --iap
gcloud iap web add-iam-policy-binding \
    --resource-type=cloud-run \
    --service=voc-dashboard \
    --region=asia-northeast3 \
    --member=domain:us-all.co.kr \
    --role=roles/iap.httpsResourceAccessor
```

### 옵션 B: Load Balancer + IAP (전통)
- HTTPS Load Balancer + Cloud Run NEG + IAP 백엔드 등록
- 더 복잡 (LB 설정, 도메인/SSL)
- 옵션 A 안 되면 fallback

### 옵션 C: 임시 — Public 으로 열기
`--allow-unauthenticated` (회사 정책으로 차단됐을 가능성)

## 결정 필요

- 옵션 A 시도해서 안 되면 옵션 B
- OAuth Brand 는 조직 최초 1회만 — 다른 팀이 이미 만들었을 가능성

## 작업 시간

- 옵션 A: 30분 (gcloud 명령 4~5개)
- 옵션 B: 1~2시간 (LB 세팅)

## 임시 우회 (지금 본인 테스트)

```bash
gcloud run services proxy voc-dashboard --region=asia-northeast3
# → http://localhost:8080 에서 자동 인증
```

## 영향 없음

- 대시보드 코드 변경 없음
- 다른 Cloud Run Job 무관
- BigQuery/Bedrock 무관
