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

# 2026-09-28: 주가 Performance 표가 해외 종목 시총을 전일 환율로 원화 환산하도록 바뀌면서
# 대상 통화를 KRW/CNY로 한정하지 않고 ECB가 고시하는 전 통화를 받아 usd_rates로 저장한다
# ECB가 고시하지 않는 통화(TWD 등)는 open.er-api.com(무료·키 불필요, 일 1회 갱신)에서
# 보충한다 — ECB 값이 있는 통화는 절대 덮어쓰지 않으므로 기존 환율(KRW/CNY 등)은 그대로다.
# 보충 조회가 실패하면 ECB 값만 저장하고(해당 통화는 프런트엔드가 스크레이퍼 고정 환율로 폴백)
# 스크립트는 정상 종료한다.
API_URL = "https://api.frankfurter.app/latest?from=USD"
SUPPLEMENT_API_URL = "https://open.er-api.com/v6/latest/USD"
# 보충 대상 — stock_performance 유니버스에 있는데 ECB가 고시하지 않는 통화만.
SUPPLEMENT_CURRENCIES = ["TWD"]

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
        "all": rates,
    }


def fetch_supplement(existing):
    """ECB에 없는 SUPPLEMENT_CURRENCIES만 open.er-api.com에서 받아 반환. 실패 시 빈 dict."""
    missing = [c for c in SUPPLEMENT_CURRENCIES if c not in existing]
    if not missing:
        return {}, None
    try:
        req = urllib.request.Request(SUPPLEMENT_API_URL, headers={"User-Agent": "medtech-dashboard/1.0"})
        with urllib.request.urlopen(req, timeout=15) as res:
            data = json.loads(res.read().decode("utf-8"))
        if data.get("result") != "success":
            raise ValueError(f"result={data.get('result')}")
        rates = data["rates"]
        got = {c: rates[c] for c in missing if isinstance(rates.get(c), (int, float))}
        return got, data.get("time_last_update_utc")
    except Exception as e:
        print(f"[WARN] 보충 환율({', '.join(missing)}) 조회 실패, ECB 값만 저장합니다: {e}", file=sys.stderr)
        return {}, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="fx_rate.json")
    args = ap.parse_args()

    try:
        fx = fetch()
    except Exception as e:
        print(f"[ERROR] 환율 조회 실패, fx_rate.json을 건드리지 않습니다: {e}", file=sys.stderr)
        sys.exit(1)

    supplement, supplement_updated = fetch_supplement(fx["all"])
    usd_rates = dict(fx["all"])
    usd_rates.update(supplement)

    out_data = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": "frankfurter.app (ECB 기준 환율, 무료·키 불필요)",
        "rate_date": fx["date"],
        "usd_krw": fx["krw"],
        "usd_cny": fx["cny"],
        "usd_rates": usd_rates,  # 1달러당 각 통화 금액 (예: {"KRW": 1355.05, "EUR": 0.877, ...})
        "fallback": FALLBACK,
    }
    if supplement:
        out_data["usd_rates_supplement"] = {
            "source": "open.er-api.com (무료·키 불필요, ECB 미고시 통화 보충)",
            "updated": supplement_updated,
            "currencies": sorted(supplement),
        }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)
    print(f"완료: {args.out} (기준일 {fx['date']}, USD/KRW={fx['krw']}, USD/CNY={fx['cny']}, 보충={supplement or '없음'})", file=sys.stderr)


if __name__ == "__main__":
    main()
