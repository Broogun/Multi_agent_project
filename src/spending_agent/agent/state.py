"""공유 칠판(CaseState): 한 번의 요청/세션 동안 팀장과 에이전트들이 함께 보는 작업판.

외부 계약(contracts.py)이 아니라 A 내부 설계다. 장기 유저 DB 는 직접 쓰지 않고
profile_deltas 에 변경안만 남긴 뒤 하네스가 검증·사용자 확인 후 반영한다.
검색 결과 본문은 여기에 넣지 않는다 (panel_id 만 기록, docs/06).
"""
from __future__ import annotations

from typing import Literal, TypedDict

from ..contracts import SearchPanelEvent, ToolResult

Trigger = Literal["onboarding", "user_record", "chat", "weekly_review", "monthly_review", "goal_completed"]
Decision = Literal["respond", "ask_user", "no_action"]
AgentName = Literal["lead", "user_understanding", "strategy_calc", "strategy_habit", "care_report"]


class ProfileDelta(TypedDict):
    kind: Literal["hard_fact", "soft_signal"]
    field: str
    value: object
    evidence_ref: str
    requires_user_confirm: bool
    confidence: float


class Question(TypedDict):
    question: str
    options: list[str]


class CaseState(TypedDict, total=False):
    user_id: str
    trigger_type: Trigger
    turn_count: int
    questions_this_week: int

    user_snapshot: dict
    open_issues: list[dict]
    active_plan: list[str]
    candidates: list[dict]
    tool_results: list[ToolResult]
    critique_notes: list[str]
    profile_deltas: list[ProfileDelta]
    proposed_actions: list[dict]
    pending_questions: list[Question]
    next_agent: AgentName | None

    search_panel_ids: list[str]
    pending_events: list[SearchPanelEvent]
    decision: Decision
    answer: str


MAX_TURNS = 6
MAX_QUESTIONS_PER_WEEK = 3


def new_state(user_id: str, trigger_type: Trigger) -> CaseState:
    return CaseState(
        user_id=user_id, trigger_type=trigger_type, turn_count=0, questions_this_week=0,
        user_snapshot={}, open_issues=[], active_plan=[], candidates=[], tool_results=[], critique_notes=[],
        profile_deltas=[], proposed_actions=[], pending_questions=[], next_agent=None,
        search_panel_ids=[], pending_events=[], decision="respond", answer="",
    )
