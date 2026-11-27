"""압축 메모리 기반 Swarm 의 상태 형식 (docs/architecture/memory-backed-swarm-plan.md §4.1).

대화 원문을 넘기지 않는다. 에이전트 사이에는 압축 유저 메모리(UserMemory) + 이번 일의 작업판(Board) +
바통 메모(HandoffNote) 만 오간다. 도구 결과 원문은 하네스(ToolBox.log)에만 남기고 숫자 검증에 쓴다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypedDict

AgentName = Literal["counselor", "analyst", "strategist", "reviewer"]
AGENTS: tuple[str, ...] = ("counselor", "analyst", "strategist", "reviewer")

MAX_HOPS = 8            # 한 번의 일(피드백)에서 바통 터치 최대 횟수
MAX_BOUNCE = 2          # 같은 쌍(strategist ↔ reviewer) 왕복 최대 횟수


class HandoffNote(TypedDict):
    from_agent: str
    to_agent: str
    reason: str             # 왜 넘기는지 (예: "부족분 40,000 대비 26,300 뿐")
    focus: list[str]        # 지금 볼 항목


class GapSummary(TypedDict, total=False):
    monthly_shortfall: int | None   # 목표 월 부족액 (도구 결과에 있는 숫자만)
    proposed_saving_sum: int        # 제안 절감 합계 (코드 계산)
    remaining_gap: int | None
    covered: bool | None
    calc: str


class UserMemory(TypedDict, total=False):
    fixed_answers: dict | None
    profile: dict                   # 확정 사실·추정 (field → {value, kind, evidence})
    patterns: list[str]
    queued_questions: list[dict]
    active_issues: list[dict]       # 쟁점 보드 (지난 피드백 기준)
    last_goal: dict | None
    recent_decisions: list[dict]    # 제안과 유저 반응
    qa: list[dict]                  # 질문과 답


def new_memory() -> UserMemory:
    return UserMemory(fixed_answers=None, profile={}, patterns=[], queued_questions=[], active_issues=[],
                      last_goal=None, recent_decisions=[], qa=[])


@dataclass
class Board:
    """한 번의 일(피드백) 동안 에이전트들이 같이 보는 작업판. 끝나면 쟁점만 메모리로 올라간다."""
    precomputed: dict = field(default_factory=dict)       # 하네스 규칙 전처리 결과 (요약)
    issues: dict[str, dict] = field(default_factory=dict)  # key = "유형|대상"
    proposals: list[dict] = field(default_factory=list)
    gap: GapSummary = field(default_factory=dict)
    review: dict = field(default_factory=dict)            # {"ok": bool, "reason": str}
    trace: list[HandoffNote] = field(default_factory=list)
    forced: str | None = None                             # 가드가 강제로 마무리시킨 이유
    cache: dict[str, dict] = field(default_factory=dict)  # 이번 피드백의 조회 결과 (도구+인자 → 결과)
    seen: dict[str, set] = field(default_factory=dict)    # 그 결과를 이미 받은 역할

    def view(self, role: str = "analyst") -> dict:
        """역할마다 필요한 것만 넘긴다 (호출당 입력을 줄이려고). 전처리 전체는 analyst 만 본다."""
        base = {"쟁점": list(self.issues.values()), "제안 후보": self.proposals, "부족분 검산": self.gap,
                "리뷰": self.review, "바통 기록": self.trace[-4:], **({"강제 마무리": self.forced} if self.forced else {})}
        if role == "analyst":
            return {"규칙 전처리": self.precomputed, **base}
        if role in ("strategist", "reviewer"):
            return {"목표": self.precomputed.get("목표", {}), **base}
        return base
