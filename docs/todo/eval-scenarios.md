# 평가 시나리오 10개

- 담당: B (parky2won)
- 상태: 대기 (calc-core 다음)
- 관련: [eval/README](../../eval/README.md), 통합 문서 1-3 탭 "오프라인 평가 지표"

## 목표
`eval/scenarios.jsonl` 에 구매 고민 시나리오 10개와 기대 행동이 있고, A 의 에이전트를 돌려 자동 채점할 수 있다.

## 형식 (한 줄 = 한 시나리오)
```json
{"id": "S01", "messages": ["쌀 3kg 살까 1kg 살까"], "user_context": {"daily_use_g": 10},
 "expect": {"tools": ["calc_bulk_purchase"], "search_panel": false, "must_not": ["검색 결과 가격으로 계산"],
            "answer_numbers_from_tools": true}, "why": "근거 설명"}
```

## 할 일
- [ ] 유형 나누기: 필요 없는 구매(보류 제안), 꼭 필요한 구매(검색 창), 확인 가격 계산, 목표 점검, 근거 부족(needs_check)
- [ ] 10개 작성 (정답은 근거와 함께, LLM이 만든 답을 그대로 정답으로 쓰지 않음)
- [ ] 채점 스크립트 `scripts/run_eval.py` (fake_chat 으로 먼저 돌려보기)

## 완료 기준
fake_chat 으로 채점 스크립트가 끝까지 돌고, 결과가 `eval/runs/`(git 제외)에 남는다.
