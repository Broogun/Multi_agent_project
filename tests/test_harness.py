"""하네스 자체 점검: 규칙 문서와 진입 파일이 서로 연결돼 있는지, 비밀값이 저장소에 없는지."""
import importlib
import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("module_name", [
    "spending_agent.config", "spending_agent.search", "spending_agent.agent", "spending_agent.data",
])
def test_package_imports(module_name):
    importlib.import_module(module_name)


def test_entry_files_point_to_working_agreement():
    assert "docs/05-working-agreement.md" in (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "@AGENTS.md" in (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "05-working-agreement.md" in (ROOT / ".cursor/rules/00-team.mdc").read_text(encoding="utf-8")


def test_docs_links_resolve():
    bad = []
    for md in [ROOT / "README.md", *(ROOT / "docs").rglob("*.md")]:
        for link in re.findall(r"\]\(([^)#\s]+\.md)", md.read_text(encoding="utf-8")):
            if not link.startswith("http") and not (md.parent / link).resolve().exists():
                bad.append(f"{md.relative_to(ROOT)} -> {link}")
    assert not bad, bad


def test_no_secrets_tracked():
    files = subprocess.run(["git", "ls-files"], check=True, cwd=ROOT, capture_output=True, text=True).stdout.split()
    assert ".env" not in files
    pattern = re.compile(r"(AIza[0-9A-Za-z_\-]{30,}|sk-[A-Za-z0-9]{20,})")
    hits = [f for f in files if (ROOT / f).is_file() and (ROOT / f).suffix in {".py", ".md", ".toml", ".yml", ".yaml", ".json"}
            and pattern.search((ROOT / f).read_text(encoding="utf-8", errors="ignore"))]
    assert not hits, hits
