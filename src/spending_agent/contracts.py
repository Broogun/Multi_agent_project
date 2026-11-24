"""모듈 사이 계약 (A·B·C가 서로 기다리지 않고 구현하기 위한 고정 인터페이스).

이 파일을 바꾸면 세 사람 모두 영향을 받는다 → 같은 PR에서 docs/02-specs.md 를 고치고 3명 모두 리뷰어로 넣는다.

흐름:
    C app ──HTTP──▶ ChatRequest ──▶ A agent.run() ──▶ B data 함수 (계산·저장)
                                       │
                                       └──▶ A search.search_panel() ──▶ SearchPanelEvent ──▶ C 검색 창
    C app ◀────────── ChatResponse (answer + events)
"""
from __future__ import annotations

from typing import Literal, TypedDict

# ---------------------------------------------------------------- 공통: 계산 결과 (B → A)
Status = Literal["ok", "needs_check", "no_data", "stale"]


class Source(TypedDict, total=False):
    id: str          # 예: U_PRICE, P02_KCA_PRICE
    name: str
    as_of: str | None
    url: str | None


class ToolResult(TypedDict):
    """B 의 모든 계산·조회 함수가 돌려주는 형식. status 가 ok 가 아니면 에이전트는 숫자를 말하지 않는다."""
    status: Status
    value: dict | None
    calc: dict[str, str]        # 항목 → 계산식 문자열 (예: "6,900 ÷ 3,000g = 2.3")
    sources: list[Source]
    as_of: str | None
    caveats: list[str]


# ---------------------------------------------------------------- 사용자 확인 가격 (C → B)
PriceSourceType = Literal["user_verified", "receipt_ocr", "capture_ocr"]


class UserPrice(TypedDict, total=False):
    user_id: str                 # 필수
    product_name: str            # 필수
    price: int                   # 필수, 원
    checked_date: str            # 필수, YYYY-MM-DD
    source_type: PriceSourceType  # 필수. 검색 결과 가격은 받지 않는다
    seller: str
    size: str
    unit_size: float
    unit: str


# ---------------------------------------------------------------- 검색 창 (A → C)
class SearchSource(TypedDict):
    title: str
    uri: str


class SearchPanelEvent(TypedDict):
    """Gemini 그라운딩 결과. C 는 text·suggestions_html·sources 를 수정 없이 그대로 렌더링한다 (docs/06)."""
    type: Literal["search_panel"]
    panel_id: str
    query: str
    text: str
    suggestions_html: str | None
    sources: list[SearchSource]
    notice: str


SEARCH_NOTICE = "검색 결과이며, 실제 가격은 판매처에서 확인하세요."


# ---------------------------------------------------------------- 채팅 API (C ↔ A)
class ChatRequest(TypedDict):
    user_id: str
    message: str
    session_id: str


class Validation(TypedDict, total=False):
    ok: bool
    action: Literal["retry", "removed"]
    bad: list[str]


class ChatResponse(TypedDict):
    answer: str                        # 에이전트 답변 (검색 결과와 섞지 않음)
    events: list[SearchPanelEvent]     # 화면에 따로 띄울 검색 창
    validation: Validation             # 숫자 검증 결과
