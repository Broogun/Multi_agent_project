# 00. 현재 상태

새 세션은 [작업 규칙](05-working-agreement.md) 다음에 이 문서를 읽는다. 작업마다 **자기 담당 줄**을 갱신한다. 40줄 이내, 본문 복제 금지(링크만).

- 마지막 갱신: 261128

## 단계
계약·가짜 구현·역할별 TODO를 기본 협업 구조로 사용. 외부 검색·LLM 연동은 보류 → [기본 구조 정리](reports/261124-1655-a-base-structure.md). 이전 코드는 태그 `archive/v0-data-pipeline`에 보존 → [전환 리포트](reports/261124-1600-a-search-pivot.md)

## 핵심 결정
- 검색 결과는 별도 검색 창에 그대로 표시, 에이전트·DB로 들어가지 않음 → [06](06-search-policy.md)
- 계산 가격은 사용자가 판매처에서 확인한 값과 공공 데이터만 → [06](06-search-policy.md)
- LLM은 숫자를 만들지 않음, 결정은 사용자 → [05 §3](05-working-agreement.md#3-원칙-기획의-결정-사항)
- 검색은 GPT 웹 검색 + 허용 도메인 + 표시 전 필터, 링크만 표시 → [06](06-search-policy.md). 에이전트 LLM은 GPT(gpt-6.1-terra)로 실행 중
- 역할: A Broogun · B parky2won · C KIM-YUNHO. 모듈 계약은 `contracts.py`, 미완성 부분은 `fakes.py` 로 개발 → [05 §1](05-working-agreement.md#1-역할과-담당-범위)

## 담당별 현황 (자기 줄만 고친다)
| 담당 | 지금 하는 일 | 막힌 것 |
|---|---|---|
| A 검색·에이전트 (Broogun) | Swarm 코어·리뷰 반영(PR #17·#18), 비용 개선 P1 입력 토큰 −61% → [리포트](reports/261127-1700-a-swarm-cost.md), 서비스 개선 계획 → [계획](architecture/service-improvement-plan.md), 검색: 요금제 스펙 추정(`search/plan_spec.py`, #23) + GPT 검색 어댑터(`search/gpt_search.py`, 알뜰폰·통신 허용 도메인·경로 규칙·고정 링크·페이지 열기 차단, #24 리뷰 중), 발표 자료 v2(현재 구조 반영 + 공지 항목) | 월 저축액 정의(C), `SearchPanelEvent` '더 보기' 칸 계약 변경(C), 허용 사이트 약관 확인, 시간당 데이터 참고값 출처 |
| B 데이터·평가 (parky2won) | - | - |
| C 화면·연동 (KIM-YUNHO) | 데모 거래 데이터: 통합 거래내역(입력 경로 B 가정) 생성기 + P1~P5 초안(PR #8 머지), Supabase 열람용 공유(5명, 1/31까지), 시연 시나리오 초안 → [09](09-demo-scenario.md), LLM 평가 단계 1: 데모 데이터 1/31까지 연장·기간 중 쟁점 추가 (원래 2주는 그대로) → [리포트](reports/261127-0146-c-demo-live-extension.md), P1 기준선을 기록 응답으로 채점할 수 있는 케이스만 돌리는 스크립트 (`scripts/eval_single_agent_p1_reply_cases.py`), 싱글 에이전트 기준선 + 비교 하네스 초안 (`src/spending_agent/baseline/`, 가짜 LLM으로만 검증) → [리포트](reports/261127-1119-c-single-agent-baseline.md), LLM 평가 단계 2: 반응 규칙·자기 기록 효과 초안 P1~P5, 세 세계 비교 → [리포트](reports/261127-1135-c-response-rules.md), 압축 메모리 기반 Swarm 설계·실행 계획 초안 → [설계 계획](architecture/memory-backed-swarm-plan.md), 기준선 하네스를 P1~P5 반응 규칙에 맞춤 (제안 강도 4가지·제안한 날·기댓값 채점·막은 쟁점) → [리포트](reports/261127-1400-c-harness-strengths.md), 온보딩 목표를 대화로 정하게 바꿈 (고정 질의에서 목표 뺌, 보고서·목표안·목표 생성 버튼, 온보딩 질문 5개, 남는 돈 = 수입 − 지출, Swarm 온보딩 흐름, 목표 달성 채점) → [리포트](reports/261127-1900-c-onboarding-goal.md) | 입력 경로 B 팀 확인, 기록 형식(3명) 합의, 온보딩 보고서·목표안 형식 계약 합의, 공유 DB 다시 올리기 (머지 뒤) |

## 열린 질문
- 검색 기반으로 바꾸면서 남길 기능 범위 (구독 점검·목표 계산을 유지할지) → [03](03-product-plan.md)
- 검색 이용 조건(데이터 보관·출처 표시·결과 순서 변경) 확인 → [06](06-search-policy.md#미확인-구현-전에-확인)

## 다음 액션
→ [TODO 목록](todo/00-todo-list.md)
