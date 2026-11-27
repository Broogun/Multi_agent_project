"""LLM 연결부. 에이전트는 이 형식만 보고, 제공자별 차이는 어댑터가 맡는다.

- `step(system, transcript, tools)`: 모델을 한 번 부르고 답(text)이나 도구 호출(calls)을 돌려준다. 도구 실행은 부르는 쪽이 한다.
- transcript 항목: {"role": "user", "text"} | {"role": "assistant", "text", "calls", "native"} | {"role": "tool", "call_id", "output"}
  `native`는 어댑터가 돌려받은 원래 출력이다 (OpenAI 추론 모델은 도구 호출 앞의 reasoning 항목을 다시 보내야 한다).
- 테스트는 `ScriptedLLM`(가짜 LLM)으로 돌린다. 실제 API는 `OpenAIResponses`이며 CI에서 부르지 않는다 (docs/05 §6).
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Step:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    seconds: float = 0.0
    native: Any = None


class LLM(Protocol):
    model: str

    def step(self, system: str, transcript: list[dict], tools: list[dict]) -> Step: ...


def tool_outputs(transcript: list[dict]) -> list[dict]:
    """transcript 의 도구 결과를 dict 로 (가짜 LLM 정책에서 쓴다)."""
    out = []
    for t in transcript:
        if t["role"] == "tool":
            try:
                out.append({"name": t.get("name"), **json.loads(t["output"])})
            except (json.JSONDecodeError, TypeError):
                out.append({"name": t.get("name"), "raw": t["output"]})
    return out


# ------------------------------------------------------------------ 가짜 LLM
class ScriptedLLM:
    """정책 함수로 답하는 가짜 LLM. policy(system, transcript, tools) -> Step."""

    def __init__(self, policy: Callable[[str, list[dict], list[dict]], Step], model: str = "scripted"):
        self.policy, self.model, self.inputs = policy, model, []

    def step(self, system, transcript, tools):
        self.inputs.append({"system": system, "transcript": [dict(t, native=None) for t in transcript],
                            "tools": [t["name"] for t in tools]})
        return self.policy(system, transcript, tools)


def _dry_policy(system: str, transcript: list[dict], tools: list[dict]) -> Step:
    """하네스 연습용: 피드백이면 목표 달성도를 한 번 보고, 빈 형식으로 답한다. 숫자를 만들지 않는다."""
    names = {t["name"] for t in tools}
    if not names:
        return Step(text=json.dumps({"profile_updates": [], "patterns": [], "questions_for_next": []}))
    if "goal_progress" in names and not tool_outputs(transcript):
        return Step(calls=[ToolCall("dry-1", "goal_progress", {})])
    return Step(text=json.dumps({"issues": [], "goal": {"on_track": None, "summary": ""}, "suggestions": [],
                                 "message": "내역을 확인했어요."}, ensure_ascii=False))


def dry_run_llm() -> ScriptedLLM:
    return ScriptedLLM(_dry_policy, model="dry")


# ------------------------------------------------------------------ OpenAI (Responses API)
class OpenAIResponses:
    """OpenAI Responses API 어댑터. `uv run --with openai ...` 로 쓴다 (기본 의존성에 넣지 않음). 키는 .env 의 OPENAI_API_KEY."""

    def __init__(self, model: str, client: Any = None):
        if client is None:
            from openai import OpenAI  # 선택 설치

            from .. import config  # noqa: F401  (.env 로딩)
            client = OpenAI()
        self.model, self.client = model, client

    @staticmethod
    def to_input(transcript: list[dict]) -> list:
        items: list = []
        for t in transcript:
            if t["role"] == "user":
                items.append({"role": "user", "content": t["text"]})
            elif t["role"] == "assistant":
                if t.get("native") is not None:
                    items += list(t["native"])
                else:
                    if t.get("text"):
                        items.append({"role": "assistant", "content": t["text"]})
                    items += [{"type": "function_call", "call_id": c.id, "name": c.name,
                               "arguments": json.dumps(c.args, ensure_ascii=False)} for c in t.get("calls", [])]
            elif t["role"] == "tool":
                items.append({"type": "function_call_output", "call_id": t["call_id"], "output": t["output"]})
        return items

    def step(self, system, transcript, tools):
        t0 = time.time()
        specs = [{"type": "function", **t} for t in tools]
        r = self.client.responses.create(model=self.model, instructions=system, input=self.to_input(transcript),
                                         **({"tools": specs} if specs else {}))
        calls = [ToolCall(o.call_id, o.name, json.loads(o.arguments or "{}"))
                 for o in r.output if getattr(o, "type", None) == "function_call"]
        return Step(text=r.output_text or "", calls=calls, native=r.output, seconds=time.time() - t0,
                    usage={"input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens})
