FROM python:3.10-slim

WORKDIR /app

# 시스템 의존성
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && \
    rm -rf /var/lib/apt/lists/*

# Python 의존성
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt boto3

# 소스 코드
COPY src/ src/
COPY scripts/ scripts/
COPY dashboard/ dashboard/

# KcELECTRA 모델
COPY models/channel_3class/kcelectra-base-v2022-v9-boost/final_model/ models/channel_3class/kcelectra-base-v2022-v9-boost/final_model/

# BigQuery 서비스 계정 키 (런타임에 환경변수로 주입 권장)
# COPY accountKey.json .

EXPOSE 8502

# Cloud Run Jobs 에서 args 오버라이드로 실행 대상 선택:
#   daily  : ["scripts/run_daily_pipeline.py"]
#   weekly : ["scripts/generate_weekly_report_v5.py"]
ENTRYPOINT ["python"]
CMD ["scripts/run_daily_pipeline.py"]
