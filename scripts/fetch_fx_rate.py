#!/usr/bin/env python3
"""
글로벌 대시보드(Peer Table)가 쓰는 USD/KRW, USD/CNY 환율을 갱신한다.

- 이전엔 index.html에 GD_FX = {usd:1, krw:1400, cny:7.1} 로 하드코딩된 고정 가정치를
  썼다(2026-09-21 사용자 지적 — "고정 환율 대신 전일 환율이라도 쓸 수 있게 해달라").
- 실시간 환율 API는 대부분 유료라, 무료·키 불필요한 ECB(유럽중앙은행) 기준 환율을 제공하는
  frankfurter.app(https://frankfurter.app, fixer.io의 무료 오픈소스 후신)을 쓴다. ECB는
  매 영업일 CET 16:00경 환율을 고시하므로 이 스크립트가 받아오는 값은 "최신 고시 환율"
  (주말/공휴일에 실행하면 자연히 직전 영업일 값)이다 — 완전한 실시간은 아니지만 고정값보다는
  훨씬 낫다.
- 출력(fx_rate.json)은 index.html이 fetch해서 GD_FX를 덮어쓰는 데 쓴다. API 호출이 실패하면
  기존 fx_rate.json을 그대로 두고(덮어쓰지 않음) 프런트엔드는 자체 폴백(1400/7.1)을 쓴다.

사용법:
    python scripts/fetch_fx_rate.py --out fx_rate.json
"""
import argparse
import datetime
import json
import sys
import urllib.request

API_URL = "https://api.frankfurter.app/latest?from=USD&to=KRW,CNY"

# API 자체가 죽었을 때 프런트엔드가 쓸 최후 폴백(기존 하드코딩 값과 동일).
FALLBACK = {"krw": 1400.0, "cny": 7.1}


def fetch():
    req = urllib.request.Request(API_URL, headers={"User-Agent": "medtech-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=15) as res:
        data = json.loads(res.read().decode("utf-8"))
    rates = data["rates"]
    return {
        "date": data["date"],  # ECB 환율 고시일(YYYY-MM-DD, 보통 직전 영업일)
        "krw": rates["KRW"],
        "cny": rates["CNY"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="fx_rate.json")
    args = ap.parse_args()

    try:
        fx = fetch()
    except Exception as e:
        print(f"[ERROR] 환율 조회 실패, fx_rate.json을 건드리지 않습니다: {e}", file=sys.stderr)
        sys.exit(1)

    out_data = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": "frankfurter.app (ECB 기준 환율, 무료·키 불필요)",
        "rate_date": fx["date"],
        "usd_krw": fx["krw"],
        "usd_cny": fx["cny"],
        "fallback": FALLBACK,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)
    print(f"완료: {args.out} (기준일 {fx['date']}, USD/KRW={fx['krw']}, USD/CNY={fx['cny']})", file=sys.stderr)


if __name__ == "__main__":
    main()
