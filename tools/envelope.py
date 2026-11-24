"""모든 Tool의 공통 응답 형식과 출처 레지스트리 (통합 문서 3-2 탭: 값, 계산 내역, 출처, 기준일, 주의사항).

status:
  ok          계산 완료
  needs_check 근거가 공식 확인 전이거나 입력이 부족해 계산을 보류 (에이전트는 숫자를 말하지 않는다)
  stale       캐시가 신선도 기준을 넘었고 API도 실패 → 값은 참고용
  no_data     해당 데이터 없음
"""
from __future__ import annotations

import datetime as dt

SOURCES = {
    "U_TXN": {"name": "사용자 거래내역 (본인 업로드)", "url": None, "license": "사용자 데이터 (본인 범위에서만 사용)"},
    "U_PRICE": {"name": "사용자가 확인한 가격", "url": None, "license": "사용자 데이터 (본인 범위에서만 사용)"},
    "P01_EPOST_MVNO": {"name": "우정사업본부 우체국알뜰폰 요금제", "url": "https://www.data.go.kr/data/15075270/openapi.do",
                       "license": "공공데이터포털 이용허락범위 제한 없음", "ttl_days": 14},
    "P02_KCA_PRICE": {"name": "한국소비자원 생필품 가격 정보", "url": "https://www.data.go.kr/data/15158701/openapi.do",
                      "license": "공공누리 제1유형 (출처: 한국소비자원)", "ttl_days": 45},
    "P04_KOSTAT_ONLINE": {"name": "국가데이터처 온라인 수집 가격 정보", "url": "https://www.data.go.kr/data/15080757/openapi.do",
                          "license": "공공데이터포털 이용허락범위 제한 없음", "ttl_days": 3},
    "P09_AT_TREND": {"name": "aT 가격 추이 정보", "url": "https://www.data.go.kr/data/15156069/openapi.do",
                     "license": "공공데이터포털 이용허락범위 제한 없음", "ttl_days": 3},
    "M_RULES": {"name": "수동 규칙 DB (data_manual/*.yaml)", "url": None, "license": "공식 공지의 사실 정보 정리"},
}


def source(sid: str, as_of: str | None = None, **extra) -> dict:
    s = {"id": sid, **{k: v for k, v in SOURCES.get(sid, {"name": sid}).items() if k != "ttl_days"}, "as_of": as_of}
    s.update(extra)
    return s


def result(value=None, *, calc=None, sources=(), caveats=(), status="ok") -> dict:
    sources = list(sources)
    dates = [s["as_of"] for s in sources if s.get("as_of")]
    return {
        "status": status,
        "value": value,
        "calc": calc or {},
        "sources": sources,
        "as_of": min(dates) if dates else None,  # 가장 오래된 근거의 기준일
        "caveats": [c for c in caveats if c],
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
