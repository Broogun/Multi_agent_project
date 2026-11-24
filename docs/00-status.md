# 00. 현재 상태

새 세션은 [작업 규칙](05-working-agreement.md) 다음에 이 문서를 읽는다. 작업마다 **자기 담당 줄**을 갱신한다. 40줄 이내, 본문 복제 금지(링크만).

- 마지막 갱신: 261124

## 단계
협업 하네스 구성 완료, 검색 기반으로 다시 시작. 이전 코드(공공데이터 수집기·거래 파이프라인·MCP Tool·LangGraph 뼈대)는 태그 `archive/v0-data-pipeline`에 보존 → [전환 리포트](reports/261124-1600-a-search-pivot.md)

## 핵심 결정
- 검색은 Gemini Google 검색 그라운딩. 결과는 별도 검색 창에 그대로 표시, 에이전트·DB로 들어가지 않음 → [06](06-search-policy.md)
- 계산 가격은 사용자가 판매처에서 확인한 값과 공공 데이터만 → [06](06-search-policy.md)
- LLM은 숫자를 만들지 않음, 결정은 사용자 → [05 §3](05-working-agreement.md#3-원칙-기획의-결정-사항)
- 에이전트 LLM은 Gemini로 통일 (SDK `google-genai`, 흐름 `langgraph`)

## 담당별 현황 (자기 줄만 고친다)
| 담당 | 지금 하는 일 | 막힌 것 |
|---|---|---|
| A 검색·에이전트 | - | Gemini API 키(결제 연결) |
| B 데이터·평가 | - | 실제 거래 CSV 확보 |
| C 화면·연동 | - | - |

## 열린 질문
- 팀원 이름·역할 확정 → [05 §1](05-working-agreement.md#1-역할과-담당-범위), `.github/CODEOWNERS`
- 검색 기반으로 바꾸면서 남길 기능 범위 (구독 점검·목표 계산을 유지할지) → [03](03-product-plan.md)
- Gemini 모델 ID와 한국 사용 가능 지역 확인

## 다음 액션
→ [TODO 목록](todo/00-todo-list.md)
