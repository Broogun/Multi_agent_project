"""소비 관리 에이전트용 MCP 서버.

    python mcp_server.py                 # stdio (Claude Code·Claude Desktop 등에서 연결)

Claude Code 등록 예:
    claude mcp add spending -- python C:/Users/Admin/Desktop/Multi_agent_project/mcp_server.py

모든 Tool은 {status, value, calc, sources, as_of, caveats} 를 돌려준다.
에이전트 규칙: 숫자는 Tool 결과에 있는 것만 인용한다. status 가 needs_check / no_data 면 숫자를 말하지 않는다.
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from tools import core  # noqa: E402

INSTRUCTIONS = """소비 관리 에이전트 Tool 모음.
- 숫자(금액·절감액·기간)는 Tool 결과의 value/calc 에 있는 것만 인용한다. 직접 계산하거나 추정하지 않는다.
- status 가 needs_check 이면 수치를 말하지 말고 caveats 의 이유와 필요한 확인을 안내한다.
- 답변에는 sources 의 출처명과 기준일(as_of)을 함께 쓴다.
- 결정(해지·변경·구매)은 사용자가 한다. 추천은 선택지와 근거로 제시한다.
- 검색 결과에 보이는 가격은 계산에 넣지 않는다. 사용자가 판매처에서 확인한 가격만 record_user_price 로 저장한다."""

server = MCPServer(name="spending-agent-tools", instructions=INSTRUCTIONS)

DESCRIPTIONS = {
    core.detect_recurring: "사용자 거래내역에서 정기결제(구독·월세·통신비 등)를 찾는다. 금액, 주기, 다음 예상 결제일, 결제수단을 돌려준다.",
    core.check_subscriptions: "구독 점검: 정기결제 중 구독 서비스를 요금제 DB와 대조하고, 연결제·하위 요금제 선택지, "
                              "같은 달 중복 결제, 무료체험 유료 전환을 알려준다. 해지 여부는 사용자가 정한다.",
    core.forecast_goal: "재무 목표 점검: 목표 금액(원), 기한(YYYY-MM-DD), 현재 모은 돈으로 필요한 월 저축액, "
                        "최근 3개월 지출 기준 여유자금, 기한 시점 예상 금액과 부족액을 계산한다. 가정 기반 시뮬레이션이다.",
    core.compare_mobile_plan: "통신요금 비교: 현재 월 요금, 필요한 데이터(GB), 남은 약정 개월, 위약금, 결합할인 해제액으로 "
                              "우체국알뜰폰 요금제 중 12개월 순절감액 상위 후보를 계산한다. 약정이 남았으면 위약금 입력이 필요하다.",
    core.calc_transit_refund: "대중교통 환급 비교: 월 교통비, 사용자 유형(일반/청년 등), 지역으로 환급 유형별 금액을 계산한다. "
                              "환급 규칙이 공식 확인 전이면 needs_check 를 돌려준다.",
    core.get_price_baseline: "생필품 가격 기준선: 상품명(예: 신라면, 우유)으로 한국소비자원 매장 가격(최저·중앙·최고, 단위가격)과 "
                             "온라인 가격 범위를 조회한다. 실시간 최저가가 아니다.",
    core.calc_bulk_purchase: "대용량 구매 판단: 선택지별 가격·수량·1개당 용량과 하루 사용량으로 단위가격, 소진 기간, "
                             "유통기한 안 소진 여부를 계산한다. 가격은 source_type 이 user_verified/receipt_ocr/capture_ocr/public_api 인 것만 받는다.",
    core.record_user_price: "사용자가 판매처에서 직접 확인한 가격을 저장한다 (user_verified/receipt_ocr/capture_ocr). 검색 결과 가격은 저장하지 않는다.",
    core.check_benefit_programs: "환급·포인트·할인 제도(탄소중립포인트, 개인컵 할인, 에너지캐시백, 카드포인트 조회 등) 목록과 "
                                 "계산 가능 여부를 돌려준다.",
    core.get_source_info: "출처 ID(예: P02_KCA_PRICE)의 이름, URL, 라이선스를 돌려준다.",
}

for fn, desc in DESCRIPTIONS.items():
    server.add_tool(fn, name=fn.__name__, description=desc)

if __name__ == "__main__":
    server.run("stdio")
