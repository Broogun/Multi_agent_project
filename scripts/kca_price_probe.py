"""한국소비자원_생필품 가격 정보_GW (data.go.kr 15158701) 호출 확인용 스크립트.

사용법 (PowerShell):
    $env:DATA_GO_KR_KEY = "<공공데이터포털 Decoding 인증키>"
    python scripts/kca_price_probe.py

공식 페이지에 오퍼레이션 경로가 적혀 있지 않아, 후보 경로를 차례로 호출해 보고
정상 응답(resultCode 00 또는 데이터 포함)이 오는 경로를 알려준다.
응답 원문은 scripts/_probe_out/ 에 저장된다 (git에 올리지 말 것).
"""
import datetime as dt
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://apis.data.go.kr/B551919/ProductPriceInfoService"
OUT = pathlib.Path(__file__).parent / "_probe_out"


def last_friday(today=None):
    d = today or dt.date.today()
    # 이번 주 금요일 자료는 아직 없을 수 있어 1주 전 금요일을 사용
    d -= dt.timedelta(days=(d.weekday() - 4) % 7 + 7)
    return d.strftime("%Y%m%d")


# (이름, 후보 경로들, 파라미터, 키 파라미터 이름)
OPS = [
    ("판매점 정보", ["getStoreInfoSvc", "getStoreInfoSvc.do"], {}, "ServiceKey"),
    ("상품 정보", ["getProductInfoSvc", "getProductInfoSvc.do"], {}, "ServiceKey"),
    ("기준 데이터(AR)", ["getStandardInfoSvc", "getStandardInfoSvc.do"], {"classCode": "AR"}, "ServiceKey"),
    ("가격 정보", ["getProductPriceInfoSvc", "getProductPriceInfoSvc.do"],
     {"goodInspectDay": last_friday(), "goodId": "1182"}, "serviceKey"),
]


def call(path, params, key_name, key):
    q = urllib.parse.urlencode({key_name: key, **params})
    url = f"{BASE}/{path}?{q}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def main():
    key = os.environ.get("DATA_GO_KR_KEY")
    env_file = pathlib.Path(__file__).parent.parent / ".env"
    if not key and env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("DATA_GO_KR_KEY="):
                key = line.split("=", 1)[1].strip().strip('"')
    if not key:
        sys.exit("DATA_GO_KR_KEY 환경변수에 Decoding 인증키를 넣어주세요.")
    OUT.mkdir(exist_ok=True)
    for name, paths, params, key_name in OPS:
        for p in paths:
            status, body = call(p, params, key_name, key)
            (OUT / f"{p}.xml").write_text(body, encoding="utf-8")
            head = " ".join(body.split())[:160]
            print(f"[{name}] {p} -> HTTP {status} | {head}")
            if status == 200 and ("<resultCode>00" in body or "<item>" in body):
                print(f"  ✓ 동작하는 경로: {p}")
                break


if __name__ == "__main__":
    main()
