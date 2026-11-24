"""터미널에서 에이전트와 대화 (Gemini 키 필요).

    python -m agent.run --user u_demo
    python -m agent.run --user u_demo -m "10개월 뒤 여행 가려고 200만 원 모으고 있어. 지금 50만 원 있어. 가능할까?"

검색 창 이벤트는 [검색 창] 블록으로 따로 출력한다 (에이전트 답변과 섞지 않음).
"""
import argparse

from .graph import run
from .llm import GeminiLLM


def show_panel(e):
    print("\n┌─[검색 창] " + e["query"])
    print("│ " + e["notice"])
    for line in (e["text"] or "").splitlines():
        print("│ " + line)
    for s in e["sources"]:
        print(f"│  - {s['title']}: {s['uri']}")
    if e.get("suggestions_html"):
        print("│ (Google 검색 제안 HTML은 웹 UI에서 그대로 렌더링)")
    print("└─")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("-m", "--message")
    a = ap.parse_args()
    llm = GeminiLLM()
    history = []
    msgs = [a.message] if a.message else None
    while True:
        msg = msgs.pop(0) if msgs else input("\n나> ").strip()
        if not msg or msg in ("q", "quit", "exit"):
            break
        final, events = run(llm, a.user, msg, history)
        for e in events:
            show_panel(e)
        print("\n에이전트> " + final.get("answer", ""))
        if not final.get("validation", {}).get("ok", True):
            print(f"  (검증기: {final['validation']})")
        history = [h for h in final["history"]]
        if a.message:
            break


if __name__ == "__main__":
    main()
