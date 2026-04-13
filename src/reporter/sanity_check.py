"""주간 리포트 발행 전 데이터 건전성 체크.

의도:
- 원본 데이터 적재 실패(예: us_plus_new → us_plus_next 전환 누락)를 자동 감지
- 전주 대비 급감/평일 0건 등 이상 패턴을 발견하면 발행을 중단하거나 경고

판정:
- ok: 정상
- warn: 의심 패턴 있지만 자동 진행 (로그 남김)
- fail: 발행 중단 (--force 로만 우회)
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Any
from collections import defaultdict


# 임계치 (운영 안정화 후 환경변수로 분리 가능)
MIN_TOTAL_ITEMS = 100               # 총 건수 하한 (편지 + 게시글)
SHARP_DROP_RATIO = 0.5              # 전주 대비 50%+ 감소
MASTER_ZERO_PREV_THRESHOLD = 30     # 전주 활성 마스터 (이만큼 이상이면 비교 대상)


@dataclass
class Anomaly:
    code: str       # 'low_total' | 'weekday_zero' | 'sharp_drop' | 'master_zero'
    severity: str   # 'warn' | 'fail'
    message: str
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass
class SanityResult:
    status: str             # 'ok' | 'warn' | 'fail'
    anomalies: List[Anomaly]
    should_continue: bool   # fail이면 False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "should_continue": self.should_continue,
            "anomalies": [a.to_dict() for a in self.anomalies],
        }


def _kst_date_of(item: Dict[str, Any]) -> str:
    """item의 createdAt(UTC ISO)을 KST YYYY-MM-DD 로 변환."""
    created = item.get("createdAt", "")
    if not created:
        return ""
    try:
        utc_dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        return (utc_dt + timedelta(hours=9)).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _master_counts(items: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    for it in items:
        name = it.get("masterName", "Unknown")
        counts[name] += 1
    return dict(counts)


def check_data_health(
    letters: List[Dict[str, Any]],
    posts: List[Dict[str, Any]],
    prev_letters: List[Dict[str, Any]],
    prev_posts: List[Dict[str, Any]],
    start_date: str,
    end_date: str,
) -> SanityResult:
    """주간 데이터 건전성 검사."""
    anomalies: List[Anomaly] = []

    total = len(letters) + len(posts)
    prev_total = len(prev_letters) + len(prev_posts)

    # 1) 총 건수 하한
    if total < MIN_TOTAL_ITEMS:
        anomalies.append(Anomaly(
            code="low_total",
            severity="fail",
            message=f"총 건수 {total}건 — 임계치({MIN_TOTAL_ITEMS}) 미만. 원본 적재 실패 의심.",
            detail={"total": total, "letters": len(letters), "posts": len(posts)},
        ))

    # 2) 전주 대비 급감 (전주가 충분히 클 때만 의미 있음)
    if prev_total >= MIN_TOTAL_ITEMS and total < prev_total * (1 - SHARP_DROP_RATIO):
        drop_pct = (1 - total / prev_total) * 100
        anomalies.append(Anomaly(
            code="sharp_drop",
            severity="warn",
            message=f"전주 대비 {drop_pct:.1f}% 감소 (이번 {total} / 전주 {prev_total})",
            detail={"this_week": total, "prev_week": prev_total, "drop_pct": drop_pct},
        ))

    # 3) 평일에 0건인 날 (편지 + 게시글 합계)
    by_date: Dict[str, int] = defaultdict(int)
    for it in letters + posts:
        d = _kst_date_of(it)
        if d:
            by_date[d] += 1

    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    cur = start
    weekday_zero_dates: List[str] = []
    while cur < end:
        ds = cur.strftime("%Y-%m-%d")
        # 평일만 (월=0 ... 금=4)
        if cur.weekday() < 5 and by_date.get(ds, 0) == 0:
            weekday_zero_dates.append(ds)
        cur += timedelta(days=1)

    if weekday_zero_dates:
        anomalies.append(Anomaly(
            code="weekday_zero",
            severity="fail",
            message=f"평일 0건 발생 ({len(weekday_zero_dates)}일): {', '.join(weekday_zero_dates)}",
            detail={"dates": weekday_zero_dates, "by_date": dict(by_date)},
        ))

    # 4) 전주 활성 마스터인데 이번 주 0건
    prev_master_counts = _master_counts(prev_letters + prev_posts)
    this_master_counts = _master_counts(letters + posts)

    if len(prev_master_counts) >= MASTER_ZERO_PREV_THRESHOLD:
        gone_masters: List[str] = []
        for master, prev_n in prev_master_counts.items():
            if prev_n >= 5 and this_master_counts.get(master, 0) == 0 and master != "Unknown":
                gone_masters.append(master)
        if gone_masters:
            anomalies.append(Anomaly(
                code="master_zero",
                severity="warn",
                message=f"전주 활성 마스터 중 {len(gone_masters)}명 이번 주 0건",
                detail={"masters": gone_masters[:20]},  # 상위 20명만
            ))

    # 종합 판정
    has_fail = any(a.severity == "fail" for a in anomalies)
    has_warn = any(a.severity == "warn" for a in anomalies)
    if has_fail:
        status = "fail"
    elif has_warn:
        status = "warn"
    else:
        status = "ok"

    return SanityResult(
        status=status,
        anomalies=anomalies,
        should_continue=not has_fail,
    )
