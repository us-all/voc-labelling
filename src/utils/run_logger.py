"""주간 리포트 실행 로그 — 단계별 로그 + 이상 감지 결과 + 비용을 한 디렉토리에 모음.

구조:
  logs/weekly_report_{start_date}/
    pipeline.log    — 전체 실행 로그 (텍스트)
    anomalies.json  — sanity_check 결과
    cost.json       — 분류 비용

사용:
  logger = RunLogger("2026-04-06")
  logger.log("Phase 1 시작")
  logger.save_anomalies(sanity_result.to_dict())
  logger.save_cost({"cost_usd": 21.4, "items": 2641})
"""
import json
import os
from datetime import datetime
from typing import Any, Dict


class RunLogger:
    def __init__(self, start_date: str, base_dir: str = "logs"):
        self.run_dir = os.path.join(base_dir, f"weekly_report_{start_date}")
        os.makedirs(self.run_dir, exist_ok=True)
        self.log_path = os.path.join(self.run_dir, "pipeline.log")

    def log(self, message: str, also_print: bool = True) -> None:
        """단계 로그 한 줄 기록 (콘솔에도 출력)."""
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if also_print:
            print(message)

    def save_anomalies(self, anomalies: Dict[str, Any]) -> None:
        path = os.path.join(self.run_dir, "anomalies.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(anomalies, f, ensure_ascii=False, indent=2)

    def save_cost(self, cost: Dict[str, Any]) -> None:
        path = os.path.join(self.run_dir, "cost.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cost, f, ensure_ascii=False, indent=2)

    def path_for(self, name: str) -> str:
        return os.path.join(self.run_dir, name)
