"""환경 설정. 비밀값은 .env 에서만 읽는다 (docs/05 §5)."""
from __future__ import annotations

import os
import pathlib

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def get(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"{name} 를 .env 에 넣어주세요 (.env.example 참고).")
    return value
