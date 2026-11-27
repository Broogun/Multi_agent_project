"""데모 데이터 CLI. 사용법은 패키지 설명(__init__.py) 참고."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import shutil
from pathlib import Path

from ..config import ROOT
from .generator import EXPORT_COLUMNS, World, build_world, load_persona

OUT = ROOT / "data" / "demo"
FORMAT = "통합 거래내역 CSV (자산관리 앱 내보내기 가정, docs/02-specs.md)"


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:     # 엑셀에서 바로 열리게 BOM 포함
        writer = csv.DictWriter(f, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _write_day(w: World, root: Path, day: str) -> dict:
    rows, records = w.batches[day], w.records.get(day, [])
    _write_csv(root / "daily" / day / "transactions.csv", rows)
    if records:
        _dump(root / "daily" / day / "records.json", {"note": "유저 기록 (형식 임시, contracts.py 합의 전)",
                                                       "records": records})
    return {"transactions": len(rows), "records": len(records)}


def _summary(day: str, counts: dict, kind: str | None) -> str:
    """운영 하루: 거래는 쌓기만, 기록(유저 이벤트)이 있으면 유저 이해, 피드백 날이면 저녁에 피드백 (docs/10 §2)."""
    action = "유저 이해" if counts["records"] else "기록 없음"
    evening = {"weekly": " | 저녁: 주간 피드백", "monthly": " | 저녁: 월간 피드백"}.get(kind, "")
    return f"{day}  새 거래 {counts['transactions']}건 | 기록 {counts['records']}건 → {action}{evening}"


def build(pid: str, through: dt.date | None, out_root: Path = OUT) -> Path:
    w = build_world(load_persona(pid))
    root = out_root / pid
    if root.exists():
        shutil.rmtree(root)
    last = w.d0 - dt.timedelta(days=1)
    _write_csv(root / "onboarding" / "transactions.csv", w.batches["onboarding"])
    _dump(root / "truth.json", w.truth)
    manifest = {"persona": pid, "format": FORMAT, "d0": w.d0.isoformat(),
                "onboarding": {"from": w.start.isoformat(), "to": last.isoformat(),
                               "transactions": len(w.batches["onboarding"])},
                "live_end": (w.end - dt.timedelta(days=1)).isoformat(), "feedback": w.feedback, "days": {}}
    print(f"{pid} 온보딩 {w.start} ~ {last}: 거래 {len(w.batches['onboarding'])}건")
    _dump(root / "manifest.json", manifest | {"built_through": last.isoformat()})
    if through:
        advance(pid, (min(through, w.end - dt.timedelta(days=1)) - last).days, out_root, w)
    return root


def advance(pid: str, days: int = 1, out_root: Path = OUT, w: World | None = None) -> list[str]:
    root = out_root / pid
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    w = w or build_world(load_persona(pid))
    last = dt.date.fromisoformat(manifest["built_through"])
    kinds = {pt["date"]: pt["kind"] for pt in w.feedback}
    written = []
    for _ in range(days):
        day = last + dt.timedelta(days=1)
        if day >= w.end:
            print(f"운영 기간 끝 ({manifest['live_end']})")
            break
        counts = _write_day(w, root, day.isoformat())
        manifest["days"][day.isoformat()] = counts
        print(_summary(day.isoformat(), counts, kinds.get(day.isoformat())))
        written.append(day.isoformat())
        last = day
    _dump(root / "manifest.json", manifest | {"built_through": last.isoformat()})
    return written


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m spending_agent.demo", description="데모 거래 데이터 생성")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="온보딩 3개월치 통합 내역 생성 (기존 출력은 지우고 다시 만듦)")
    b.add_argument("persona")
    b.add_argument("--through", type=dt.date.fromisoformat, help="이 날짜까지 운영 일자도 생성 (YYYY-MM-DD)")
    a = sub.add_parser("advance", help="운영 하루 넘기기 (그날 새 거래 + 유저 기록)")
    a.add_argument("persona")
    a.add_argument("--days", type=int, default=1)
    s = sub.add_parser("publish", help="Supabase(Postgres)에 올림: 팀 열람용, .env 의 SUPABASE_DB_URL 사용")
    s.add_argument("personas", nargs="*", help="비우면 전체")
    s.add_argument("--reset", action="store_true", help="demo·demo_eval 스키마를 지우고 다시 만든다")
    args = parser.parse_args(argv)
    if args.cmd == "build":
        build(args.persona, args.through)
    elif args.cmd == "advance":
        advance(args.persona, args.days)
    else:
        from .publish import publish  # psycopg 는 선택 설치라 여기서만 불러온다
        for pid, counts in publish(args.personas, args.reset).items():
            print(f"{pid} 올림: " + " · ".join(f"{t} {n}건" for t, n in counts.items()))


if __name__ == "__main__":
    main()
