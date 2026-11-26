"""데모 사용자 거래 데이터 생성기.

입력 경로 B 가정: 유저가 자산관리 앱(마이데이터)에서 내보낸 통합 거래내역 CSV를 올린다.
카드·계좌·간편결제 통합은 앱이 이미 했다고 보고, 이 패키지는 그 통합 내역을 만든다.
- 온보딩: D0 이전 3개월치를 한 번에
- 운영: D0부터 하루 단위로 새 거래 + 시나리오에 적힌 유저 기록

실행:
    uv run python -m spending_agent.demo build P4                         # 온보딩분만
    uv run python -m spending_agent.demo build P4 --through 2026-12-14    # 운영 날짜까지
    uv run python -m spending_agent.demo advance P4                       # 하루 넘기기
    uv run python -m spending_agent.demo publish                          # Supabase에 올림 (팀 열람용, publish.py)

출력: data/demo/<ID>/ (git 제외, 언제든 재생성). 형식: docs/02-specs.md "통합 거래내역 형식".
페르소나: personas/<ID>.toml. 가맹점·기관·앱 이름과 금액은 모두 가상의 데모 값이다.

적재·분석 코드는 onboarding/, daily/ 만 읽는다. truth.json 은 평가 전용이다 (에이전트 입력 금지).
"""
