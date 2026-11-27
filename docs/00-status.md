# 00. 현재 상태

새 세션은 [작업 규칙](05-working-agreement.md) 다음에 이 문서를 읽는다. 작업마다 **자기 담당 줄**을 갱신한다. 40줄 이내, 본문 복제 금지(링크만).

- 마지막 갱신: 261124

## 단계
계약·가짜 구현·역할별 TODO를 기본 협업 구조로 사용. 외부 검색·LLM 연동은 보류 → [기본 구조 정리](reports/261124-1655-a-base-structure.md). 이전 코드는 태그 `archive/v0-data-pipeline`에 보존 → [전환 리포트](reports/261124-1600-a-search-pivot.md)

## 핵심 결정
- 검색 결과는 별도 검색 창에 그대로 표시, 에이전트·DB로 들어가지 않음 → [06](06-search-policy.md)
- 계산 가격은 사용자가 판매처에서 확인한 값과 공공 데이터만 → [06](06-search-policy.md)
- LLM은 숫자를 만들지 않음, 결정은 사용자 → [05 §3](05-working-agreement.md#3-원칙-기획의-결정-사항)
- 검색·LLM 제공자는 미정. 가짜 구현으로 개발하고 실제 외부 연동은 보류 → [리포트](reports/261124-1655-a-base-structure.md)
- 역할: A Broogun · B parky2won · C KIM-YUNHO. 모듈 계약은 `contracts.py`, 미완성 부분은 `fakes.py` 로 개발 → [05 §1](05-working-agreement.md#1-역할과-담당-범위)

## 담당별 현황 (자기 줄만 고친다)
| 담당 | 지금 하는 일 | 막힌 것 |
|---|---|---|
| A 검색·에이전트 (Broogun) | 단일 에이전트 기준선 P1 실행 (gpt-6.1-terra, 통과 2·부분 2·실패 5) → [리포트](reports/261126-2030-a-single-agent-p1-baseline.md) | 채점 보강 후 반복·P2~P5 |
| B 데이터·평가 (parky2won) | - | - |
| C 화면·연동 (KIM-YUNHO) | 데모 거래 데이터: 통합 거래내역(입력 경로 B 가정) 생성기 + P1~P5 초안(PR #8 머지), Supabase 열람용 공유(5명 올림), 시연 시나리오 초안 → [09](09-demo-scenario.md), LLM 평가 단계 1: 데모 데이터 1/31까지 연장·기간 중 쟁점 추가 (원래 2주는 그대로) → [리포트](reports/261127-0146-c-demo-live-extension.md) | 입력 경로 B 팀 확인, 기록 형식(3명) 합의, 공유 DB 다시 올리기(머지 뒤) |

## 열린 질문
- 검색 기반으로 바꾸면서 남길 기능 범위 (구독 점검·목표 계산을 유지할지) → [03](03-product-plan.md)
- 검색·LLM 제공자 선정과 이용 조건 확인 → [06](06-search-policy.md#미확인-구현-전에-확인)

## 다음 액션
→ [TODO 목록](todo/00-todo-list.md)
