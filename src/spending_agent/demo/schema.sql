-- 데모 데이터 공유용 스키마 (팀 열람 전용). 앱 저장 형식이 아니다 (docs/02-specs.md "데모 데이터 공유").
-- publish 할 때마다 실행된다. 여러 번 실행해도 결과가 같아야 한다.
-- 테이블 구조를 바꿨으면 `publish --reset` 으로 두 스키마를 지우고 다시 만든다.

create schema if not exists demo;
create schema if not exists demo_eval;

comment on schema demo is '데모 사용자 통합 거래내역·유저 기록 (가상 데이터, 열람용). 앱이 받는 것과 같은 모습';
comment on schema demo_eval is '데모 평가 정답 (truth). 앱·에이전트 입력에 넣지 않는다';

create table if not exists demo.personas (
  persona_id   text primary key,
  user_id      text not null,
  gender       text,
  age          int,
  occupation   text,
  housing      text,
  summary      text,
  d0           date not null,          -- 온보딩 날
  history_from date not null,          -- 온보딩에 한 번에 들어오는 기간
  history_to   date not null,
  live_to      date not null,          -- 운영(일별) 마지막 날
  generated_at timestamptz not null,
  source_rev   text                    -- 생성한 코드의 git 커밋
);

create table if not exists demo.transactions (
  persona_id     text not null references demo.personas (persona_id) on delete cascade,
  tx_id          text not null,
  batch          text not null,        -- 'onboarding' 또는 운영 날짜 (YYYY-MM-DD)
  visible_on     date not null,        -- 앱에 들어온 날 (밤늦은 거래는 다음 날)
  tx_date        date not null,
  tx_time        time not null,
  type           text not null,        -- 지출 / 수입 / 이체 (앱이 붙인 값)
  category_main  text not null,        -- 앱 자동 분류 (틀릴 수 있음)
  category_sub   text,
  description    text not null,
  amount         bigint not null,      -- 지출·출금 음수, 수입·환불 양수
  payment_method text not null,
  memo           text,
  primary key (persona_id, tx_id)
);
create index if not exists transactions_visible_idx on demo.transactions (persona_id, visible_on);

create table if not exists demo.records (
  persona_id     text not null references demo.personas (persona_id) on delete cascade,
  record_id      text not null,
  user_id        text not null,
  written_at     timestamp not null,   -- KST
  text           text not null,
  amount         bigint,
  transaction_id text,                 -- 기록 시점에 이미 들어온 거래만. 아니면 null
  primary key (persona_id, record_id)
);

create table if not exists demo_eval.truth (
  persona_id   text primary key,
  generated_at timestamptz not null,
  truth        jsonb not null          -- truth.json 전체 (facts / user_sim / rubric)
);

create table if not exists demo_eval.cases (
  persona_id text not null,
  case_id    text not null,
  phase      text not null,            -- onboarding / live
  level      int not null,
  target     text not null,            -- data / agent / data+agent
  composite  boolean not null,
  signal     text not null,
  expect     jsonb not null,
  must_not   jsonb not null,
  why        text,
  primary key (persona_id, case_id)
);

create table if not exists demo_eval.tx_truth (
  persona_id    text not null,
  tx_id         text not null,
  event_id      text not null,
  kind          text not null,         -- purchase / cancel / transfer_in / transfer_out / atm / charge / card_bill
  channel       text not null,
  true_category text not null,         -- 실제 용도 (앱 분류와 다를 수 있음)
  amount        bigint not null,
  group_id      text,                  -- 1/n 정산 묶음
  ref_event     text,                  -- 취소 → 원래 결제
  installment   int not null,
  visible_on    date not null,
  primary key (persona_id, tx_id)
);

-- 앱 분류와 실제 용도를 나란히 보는 뷰 (평가·열람용)
create or replace view demo_eval.tx_compare as
select t.persona_id, t.tx_date, t.tx_time, t.type, t.category_main, t.category_sub, t.description, t.amount,
       e.kind, e.true_category, e.group_id, e.installment, t.batch, t.tx_id
from demo.transactions t
join demo_eval.tx_truth e using (persona_id, tx_id);

-- 공개 키(anon·authenticated)로는 두 스키마 모두 열지 않는다. 대시보드와 DB 연결 문자열로만 본다.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on schema demo, demo_eval from anon, authenticated;
    revoke all on all tables in schema demo, demo_eval from anon, authenticated;
  end if;
end $$;
