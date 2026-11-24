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
from tools.descriptions import DESCRIPTIONS  # noqa: E402

INSTRUCTIONS = """소비 관리 에이전트 Tool 모음.
- 숫자(금액·절감액·기간)는 Tool 결과의 value/calc 에 있는 것만 인용한다. 직접 계산하거나 추정하지 않는다.
- status 가 needs_check 이면 수치를 말하지 말고 caveats 의 이유와 필요한 확인을 안내한다.
- 답변에는 sources 의 출처명과 기준일(as_of)을 함께 쓴다.
- 결정(해지·변경·구매)은 사용자가 한다. 추천은 선택지와 근거로 제시한다.
- 검색 결과에 보이는 가격은 계산에 넣지 않는다. 사용자가 판매처에서 확인한 가격만 record_user_price 로 저장한다."""

server = MCPServer(name="spending-agent-tools", instructions=INSTRUCTIONS)


for fn, desc in DESCRIPTIONS.items():
    server.add_tool(fn, name=fn.__name__, description=desc)

if __name__ == "__main__":
    server.run("stdio")
