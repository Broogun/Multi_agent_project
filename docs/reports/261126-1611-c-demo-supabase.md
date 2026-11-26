# 데모 데이터를 Supabase에 올려 팀이 함께 본다 (열람용)

- 날짜: 261126
- 작성: C (KIM-YUNHO)
- 관련: [TODO demo-data](../todo/demo-data.md), [02-specs 데모 데이터 공유](../02-specs.md), [07 §5 데모 데이터 보기](../07-onboarding.md), PR #8 (데모 데이터 생성기)

## 결론
데모 데이터(P1~P5)를 Supabase(Postgres)에 올려 팀원이 대시보드에서 같은 데이터를 본다. **열람용 공유 DB이고 앱 저장소가 아니다.** 사용자 데이터 저장 형식은 B가 정하고, SQLite 계획은 그대로다. 정답은 별도 스키마 `demo_eval`에 둔다.

## 배경
- 생성기는 결과가 항상 같아서, 각자 `build`해도 같은 데이터가 나온다.
- 그래도 코드를 돌리지 않고 브라우저에서 바로 보고, 앱 분류와 실제 용도를 SQL로 비교할 공유 장소가 필요했다.
- 정답(`truth`)도 평가 때 같이 볼 수 있어야 한다. 대신 앱·에이전트가 읽는 데이터와는 분리해야 한다.

## 한 일 / 결정
- `uv run python -m spending_agent.demo publish [P1 ...] [--reset]`를 추가했다. [`schema.sql`](../../src/spending_agent/demo/schema.sql)을 실행한 뒤, 페르소나별로 지우고 다시 넣는다. 한 트랜잭션으로 처리하고, 몇 번 실행해도 결과가 같다.
- **스키마 분리**:
  - `demo`: personas, transactions, records. 앱이 받는 모습과 같다.
  - `demo_eval`: truth(jsonb), cases, tx_truth, 그리고 앱 분류와 실제 용도를 나란히 보는 뷰 `tx_compare`
- **공개 API로 열지 않는다**: 두 스키마 모두 `anon`·`authenticated` 권한을 회수했다. Supabase는 직접 만든 스키마를 따로 노출하고 권한을 줘야 Data API로 보인다 ([Data API 변경 공지](https://supabase.com/changelog/45329-breaking-change-tables-not-exposed-to-data-and-graphql-api-automatically), [API 보안](https://supabase.com/docs/guides/api/securing-your-api)). 보는 경로는 대시보드와 DB 연결 문자열뿐이다.
- **연결**: `.env`의 `SUPABASE_DB_URL`에 대시보드 **Connect → Session pooler** 연결 문자열을 넣는다. Session pooler는 IPv4 네트워크에서도 된다 ([연결 가이드](https://supabase.com/docs/guides/database/connecting-to-postgres), [IPv4/IPv6 호환](https://supabase.com/kb/troubleshooting/supabase--your-network-ipv4-and-ipv6-compatibility-cHe3BP)). 비밀번호가 들어 있어서 저장소·대화에는 넣지 않고 DM으로만 전달한다 (05 §6).
- **의존성**: `psycopg[binary]`를 선택 그룹 `supabase`로 추가했다. CI와 `uv sync --group dev`에는 영향이 없고, 올리는 사람만 `--group supabase`로 설치한다.

## 버린 대안
- `data/demo/`를 git에 커밋: 생성기가 결정적이라 중복이다. 생성물이 커밋될 때마다 diff가 커지고, 정답이 저장소에 섞인다.
- Supabase Storage에 CSV 파일 업로드: 파일로만 보여서 조회·비교(앱 분류 vs 실제 용도)를 할 수 없다.
- `supabase-py`(REST) 클라이언트: 테이블 생성(DDL)을 따로 해야 하고, 공개 API에 테이블을 노출해야 한다.
- `public` 스키마에 테이블 생성: 공개 키로 노출될 여지가 생긴다. 정답과 앱용 데이터를 나누기도 어렵다.

## 근거
- `uv run pytest`: 126 passed, 1 skipped (Supabase에 실제로 붙는 테스트는 `live`라 CI 제외). `uv run ruff check .` 통과.
- 로컬 Postgres 17 컨테이너로 검증했다. 공개 역할은 Supabase처럼 `anon`·`authenticated`를 만들어 확인했다.
  - 5명 전체 적재: 거래 1,526건, 기록 21건, 케이스 53개, tx_truth 1,526건, truth 5건
  - 다시 실행해도 같은 건수
  - `--reset` 뒤 다시 적재됨
  - `anon`으로 `demo`, `authenticated`로 `demo_eval`을 읽으면 `permission denied for schema`
  - `tx_compare` 예시 (P4): '한누리대학교생활협동조합' 30건이 앱 분류는 쇼핑, 실제 용도는 식사
- 실제 Supabase 프로젝트에서도 확인했다 (Postgres 17.12, Session pooler 연결).
  - 5명 전체 적재: 거래 1,526건, 기록 21건, 케이스 53개, tx_truth 1,526건, truth 5건 (로컬 검증과 같음)
  - `has_schema_privilege`로 보면 `anon`·`authenticated`는 `demo`·`demo_eval` 사용 권한이 모두 없다
  - `uv run --group supabase pytest -m live` 통과. 이 테스트는 읽기만 한다: 공유 DB 건수가 지금 생성기 결과와 같은지 비교한다. 처음엔 P4를 다시 올리는 테스트였는데, `.env`에 연결 문자열이 있으면 평소 `uv run pytest`로도 공유 데이터를 다시 쓰게 돼서 바꿨다
- 처음 설정할 때 헷갈린 점: 프로젝트 API 키(`SUPABASE_URL`, publishable·secret key)를 연결 문자열로 착각하기 쉽다. 이번 업로드에 필요한 건 Connect → Session pooler의 `postgresql://` 주소와 DB 비밀번호뿐이다 ([07 §5](../07-onboarding.md)에 반영).
- 미확인:
  - Supabase 무료 플랜의 한도와 비활성 프로젝트 일시정지 정책
  - 대시보드 메뉴 이름 (바뀔 수 있음)
  - Table Editor에서 직접 만든 스키마가 보이는지

## 다음
- C: 팀원을 Supabase 프로젝트에 초대, Table Editor에서 `demo`·`demo_eval`이 보이는지 확인
- 팀: 나중에 앱·에이전트가 읽는 DB가 필요해지면 Supabase로 갈지는 B의 저장 형식 결정과 함께 따로 정한다 (이번 결정의 범위 밖)
