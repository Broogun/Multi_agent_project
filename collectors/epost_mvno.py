"""P01 우체국알뜰폰 요금제 (data.go.kr 15075270, 우정사업본부). 통신 대안 검증(compare_mobile_plan)용.

    python collectors/epost_mvno.py

전체 목록을 한 번에 받아 스냅샷으로 저장한다. 같은 날 다시 받으면 덮어쓴다.
주의: 우체국에서 판매하는 알뜰폰 요금제만 포함 (알뜰폰 전체 시장 아님).
상태: 명세 기반 작성, 활용신청 승인 후 실제 응답으로 검증 필요.
"""
import json

from common import connect, load_key, http_get, parse_xml_items, save_raw, log, now, num, ApiError

SOURCE_ID = "P01_EPOST_MVNO"
URL = ("http://openapi.epost.go.kr/postal/retrieveAlddlChargeService/"
       "retrieveAlddlChargeService/getAlddlChargeList")

SCHEMA = """
CREATE TABLE IF NOT EXISTS mvno_plan (
    snapshot_day TEXT, telecom_name TEXT, biz_name TEXT, charge_name TEXT,
    generation TEXT, charge_div TEXT, monthly_fee REAL,
    voice TEXT, message TEXT, data TEXT,
    over_voice TEXT, over_message TEXT, over_data TEXT,
    fee_12m REAL, fee_24m REAL, raw TEXT, fetched_at TEXT,
    PRIMARY KEY (snapshot_day, biz_name, charge_name, telecom_name)
);
"""


def main():
    key = load_key()
    con = connect(SCHEMA)
    params = {"ServiceKey": key}
    status, body = http_get(URL, params)
    raw = save_raw("epost_mvno", "list.xml", body)
    try:
        rows = parse_xml_items(body, "alddlCharge")
    except ApiError as e:
        log(con, SOURCE_ID, "getAlddlChargeList", params, status, "ERR", 0, raw)
        con.commit()
        raise SystemExit(str(e))
    day, ts = now()[:10].replace("-", ""), now()
    con.executemany(
        "INSERT OR REPLACE INTO mvno_plan VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(day, r.get("telecomName"), r.get("bizName"), r.get("chargeName"),
          r.get("telecomGenerationType"), r.get("chargeDiv"), num(r.get("chargeAmount")),
          r.get("voiceAmount"), r.get("messageAmount"), r.get("dataAmount"),
          r.get("overVoiceAmtUnit"), r.get("overMessageAmtUnit"), r.get("overDataAmtUnit"),
          num(r.get("div12prc")), num(r.get("div24prc")),
          json.dumps(r, ensure_ascii=False), ts) for r in rows],
    )
    log(con, SOURCE_ID, "getAlddlChargeList", params, status, "00", len(rows), raw)
    con.commit()
    print(f"우체국알뜰폰 요금제 {len(rows)}개 저장 (기준일 {day})")


if __name__ == "__main__":
    main()
