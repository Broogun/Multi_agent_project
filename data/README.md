# data

로컬 데이터 보관 위치다. 이 안내 문서만 Git으로 공유한다.

DB, 원본 응답, 사용자 데이터, 수동 YAML 등 실제 데이터는 모두 Git에서 제외한다. 기존 `manual/` YAML은 현재 코드에서 사용하지 않아 추적을 해제했으며, 로컬 원본은 유지한다. 이전 버전은 태그 `archive/v0-data-pipeline`에 보존되어 있다.

`demo/<페르소나>/`는 데모 거래 생성기 출력이다. `uv run python -m spending_agent.demo build P4`로 다시 만든다 (안내: docs/08-demo-data.md, 형식: docs/02-specs.md).
