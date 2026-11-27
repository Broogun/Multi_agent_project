"""생활 패턴 → 필요한 요금제 스펙 → 검색어 (뼈대).

흐름 (docs/architecture/service-improvement-plan.md, 요금제 점검 시나리오 ③):
    ① 버튼 질문 답(UsageAnswer) 또는 통신사 앱 실측 사용량
    ② estimate_monthly_gb: 월 필요 데이터 범위를 **코드가** 계산 (LLM 이 숫자를 만들지 않는다)
    ③ to_spec: 사용 유형 규칙으로 요금제 스펙(PlanSpec)으로 바꾼다
    ④ build_search_request: 개인정보 없는 검색어 + 허용 도메인 (검색 호출은 하지 않는다)

지키는 것 (docs/06-search-policy.md):
- 검색어에는 스펙만 넣는다. 예산(지금 통신비일 수 있는 재무 정보)·통신사·결합·나이는 넣지 않는다.
  예산은 스펙에 남겨 화면에서 결과를 거를 때만 쓴다.
- 사용량을 추정할 수 없으면 검색 요청을 만들지 않고 정보가 더 필요하다고 돌려준다.
- 검색 결과의 가격으로 절감액을 계산하지 않는다. 이 모듈은 검색 **요청**만 만든다.
- 추정은 범위와 가정을 같이 돌려준다. 화면에서 사용자가 가정을 고치면 다시 계산한다.

⚠️ USAGE_RATES 와 ALLOWED_DOMAINS 의 값은 **확인 전 예시**다 (verified=False).
   출처를 확인해 채우기 전에는 결과에 '참고값 미확인' 주의 문구가 붙는다.
"""
from __future__ import annotations

import math
from typing import Literal, TypedDict

from ..agent.tools import PII

Activity = Literal["video", "music", "sns_web", "game", "navi"]
OutsideBand = Literal["le30m", "1h", "2to3h", "gt3h"]
WifiBand = Literal["mostly", "half", "rarely"]

ACTIVITY_LABEL: dict[str, str] = {"video": "영상", "music": "음악", "sns_web": "SNS·웹", "game": "게임", "navi": "내비"}

# 활동별 시간당 데이터 사용량 (GB/시간, 낮게~높게). ⚠️ 예시값. 출처 확인 후 바꾸고 verified 를 True 로.
USAGE_RATES: dict[str, dict] = {
    "video": {"gb_per_hour": (0.5, 1.5), "source": None, "verified": False},     # 화질에 따라 크게 다르다
    "music": {"gb_per_hour": (0.04, 0.15), "source": None, "verified": False},
    "sns_web": {"gb_per_hour": (0.1, 0.5), "source": None, "verified": False},   # 짧은 영상이 많으면 높아진다
    "game": {"gb_per_hour": (0.03, 0.1), "source": None, "verified": False},
    "navi": {"gb_per_hour": (0.005, 0.02), "source": None, "verified": False},
}

# 버튼 답 → 하루 밖에서 쓰는 시간 (시간). 실험용 초기값.
OUTSIDE_HOURS: dict[str, float] = {"le30m": 0.5, "1h": 1.0, "2to3h": 2.5, "gt3h": 4.0}
# 와이파이를 거의 못 쓰면 실내 사용도 모바일 데이터로 나간다고 본다. 실험용 초기값.
WIFI_FACTOR: dict[str, float] = {"mostly": 1.0, "half": 1.2, "rarely": 1.5}
DAYS = 30
MARGIN = 1.2            # 여유분
MEASURED_MARGIN = 1.1   # 실측값 여유분

# 소진 후 속도 하한 (Mbps). 이 활동을 하면 이 속도는 있어야 체감이 버틴다고 보는 초기 규칙값.
THROTTLE_MIN_MBPS: dict[str, int] = {"video": 3, "music": 1}
UNLIMITED_FROM_GB = 100     # 이보다 많이 필요하면 GB 대신 '데이터 무제한' 으로 찾는다. 초기 규칙값

# 통신 검색 허용 도메인. ⚠️ 실제 주소·약관 확인 전 (verified=False).
ALLOWED_DOMAINS: list[dict] = [
    {"domain": "smartchoice.or.kr", "name": "스마트초이스", "kind": "공공", "verified": False},
    {"domain": "sktelecom.com", "name": "SKT", "kind": "통신사", "verified": False},
    {"domain": "kt.com", "name": "KT", "kind": "통신사", "verified": False},
    {"domain": "lguplus.com", "name": "LG U+", "kind": "통신사", "verified": False},
    # 알뜰폰허브: 도메인 확인 후 추가
]


class UsageAnswer(TypedDict, total=False):
    activities: list[str]           # 밖에서 주로 하는 것 (여러 개)
    outside: str                    # OutsideBand
    wifi: str                       # WifiBand
    tethering: bool
    heavy_calls: bool
    budget_monthly: int | None      # 원. 거래 데이터의 지금 통신비나 사용자 입력
    measured_gb_monthly: float | None   # 통신사 앱 실측 (최근 3개월 평균). 있으면 추정보다 우선
    family_bundle: bool             # 가족결합 중이면 결합 해제 비용을 먼저 확인해야 한다


class GbEstimate(TypedDict):
    low: float
    high: float
    kind: Literal["실측", "추정", "추정 불가"]
    calc: str
    caveats: list[str]


class PlanSpec(TypedDict):
    data_gb_min: int
    throttle_mbps_min: int | None
    unlimited_calls: bool
    tethering: bool
    budget_max: int | None
    profile: str                    # 화면에 보일 사용 유형 ("밖에서 영상 위주")
    assumptions: list[str]          # 화면에 같이 보여 줄 가정 (사용자가 고칠 수 있게)
    caveats: list[str]


class SearchRequest(TypedDict):
    status: Literal["ok", "needs_info"]
    query: str | None
    allowed_domains: list[str]
    spec: PlanSpec


def _round1(x: float) -> float:
    return math.floor(x * 10 + 0.5) / 10


def estimate_monthly_gb(a: UsageAnswer) -> GbEstimate:
    """월 필요 데이터 범위. 실측이 있으면 그 값을, 없으면 활동·시간·와이파이로 추정한다."""
    measured = a.get("measured_gb_monthly")
    if measured is not None and measured >= 0:
        v = _round1(measured * MEASURED_MARGIN)
        return {"low": _round1(measured), "high": v, "kind": "실측",
                "calc": f"실측 {measured:g}GB × 여유 {MEASURED_MARGIN} = {v:g}GB", "caveats": []}

    acts = [x for x in a.get("activities", []) if x in USAGE_RATES]
    if not acts:
        return {"low": 0.0, "high": 0.0, "kind": "추정 불가", "calc": "활동 답 없음",
                "caveats": ["활동을 고르지 않아 추정할 수 없다. 실측 사용량을 넣거나 질문에 답해야 한다."]}
    hours = OUTSIDE_HOURS.get(a.get("outside", "1h"), 1.0)
    wifi = WIFI_FACTOR.get(a.get("wifi", "mostly"), 1.0)
    per = hours / len(acts)                     # 고른 활동에 시간을 똑같이 나눈다 (단순화)
    lo = sum(USAGE_RATES[x]["gb_per_hour"][0] for x in acts) * per * DAYS * wifi * MARGIN
    hi = sum(USAGE_RATES[x]["gb_per_hour"][1] for x in acts) * per * DAYS * wifi * MARGIN
    caveats = []
    if not all(USAGE_RATES[x]["verified"] for x in acts):
        caveats.append("시간당 사용량 참고값이 아직 확인되지 않았다 (예시값)")
    calc = (f"하루 {hours:g}시간 ÷ 활동 {len(acts)}개 × 시간당 사용량 × {DAYS}일 × 와이파이 {wifi:g} × 여유 {MARGIN}"
            f" = 월 {_round1(lo):g}~{_round1(hi):g}GB")
    return {"low": _round1(lo), "high": _round1(hi), "kind": "추정", "calc": calc, "caveats": caveats}


def _profile(acts: list[str], kind: str) -> str:
    if kind == "추정 불가":
        return "정보 부족"
    if not acts:
        return "실측 사용량 기준"              # 활동 답이 없다고 와이파이만 쓴다고 볼 수는 없다
    if "video" in acts:
        return "밖에서 영상 위주"
    if acts == ["music"]:
        return "음악 위주"
    return "밖에서 " + "·".join(ACTIVITY_LABEL[x] for x in acts)


def to_spec(a: UsageAnswer, est: GbEstimate | None = None) -> PlanSpec:
    """사용 유형 규칙으로 요금제 스펙을 정한다. 숫자는 모두 이 함수가 계산한다."""
    est = est or estimate_monthly_gb(a)
    acts = [x for x in a.get("activities", []) if x in USAGE_RATES]
    throttle = max((THROTTLE_MIN_MBPS[x] for x in acts if x in THROTTLE_MIN_MBPS), default=None)
    # 스펙 하한은 범위의 가운데로 잡는다 (최댓값을 쓰면 참고값의 넓은 폭 때문에 과하게 커진다). 범위는 가정에 보여 준다
    if est["kind"] == "추정 불가":
        gb = 0                                   # 0 = 모름. 1GB 같은 그럴듯한 값으로 바꾸지 않는다
    elif est["kind"] == "실측":
        gb = max(1, math.ceil(est["high"]))
    else:
        gb = max(1, math.ceil((est["low"] + est["high"]) / 2))
    assumptions = []
    if est["kind"] == "실측":
        assumptions.append(f"최근 실측 사용량 기준 ({est['calc']})")
    elif acts:
        band = {"le30m": "30분 이하", "1h": "1시간", "2to3h": "2~3시간", "gt3h": "3시간 이상"}.get(a.get("outside", "1h"))
        wifi = {"mostly": "대부분 와이파이", "half": "와이파이 반반", "rarely": "와이파이 거의 못 씀"}.get(a.get("wifi", "mostly"))
        assumptions.append(f"밖에서 {', '.join(ACTIVITY_LABEL[x] for x in acts)} 하루 {band}, {wifi}")
        assumptions.append(f"월 약 {est['low']:g}~{est['high']:g}GB로 추정")
    if throttle:
        assumptions.append(f"데이터를 다 써도 {throttle}Mbps 이상이면 {'영상' if throttle >= 3 else '음악'}은 버틴다고 봄")
    caveats = list(est["caveats"])
    if a.get("family_bundle"):
        caveats.append("가족결합 중: 바꾸면 결합 할인이 깨지는지 먼저 확인해야 한다 (검색어에는 넣지 않음)")
    budget = a.get("budget_monthly")
    return {"data_gb_min": gb, "throttle_mbps_min": throttle, "unlimited_calls": bool(a.get("heavy_calls")),
            "tethering": bool(a.get("tethering")), "budget_max": int(budget) if budget else None,
            "profile": _profile(acts, est["kind"]), "assumptions": assumptions, "caveats": caveats}


def build_query(spec: PlanSpec) -> str:
    """스펙만으로 검색어를 만든다. 예산 같은 재무 정보는 넣지 않는다."""
    gb = spec["data_gb_min"]
    if gb <= 0:
        raise ValueError("필요 데이터를 모르는 상태로는 검색어를 만들지 않는다")
    parts = ["알뜰폰 요금제", "데이터 무제한" if gb >= UNLIMITED_FROM_GB else f"월 {gb}GB 이상"]
    if spec["throttle_mbps_min"]:
        parts.append(f"소진 후 {spec['throttle_mbps_min']}Mbps")
    if spec["unlimited_calls"]:
        parts.append("통화 무제한")
    if spec["tethering"]:
        parts.append("테더링")
    return PII.sub("", " ".join(parts)).strip()


def build_search_request(a: UsageAnswer) -> SearchRequest:
    """질문 답 → 검색 요청 (검색은 하지 않는다). 호출부가 GPT 웹 검색에 query 와 allowed_domains 를 넘긴다."""
    spec = to_spec(a)
    domains = [d["domain"] for d in ALLOWED_DOMAINS]
    if spec["data_gb_min"] <= 0:
        return {"status": "needs_info", "query": None, "allowed_domains": domains, "spec": spec}
    return {"status": "ok", "query": build_query(spec), "allowed_domains": domains, "spec": spec}
