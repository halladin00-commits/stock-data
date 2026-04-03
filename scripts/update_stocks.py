"""
한국/미국 상장 종목 리스트를 수집하여 JSON 파일로 생성하는 스크립트.
GitHub Actions에서 매일 자동 실행됩니다.

4개 소스:
  1. 공공데이터포털 getItemInfo       → 한국 주식
  2. 공공데이터포털 getETFPriceInfo   → 한국 ETF
  3. dumbstockapi                     → 미국 주식
  4. NASDAQ ETF screener              → 미국 ETF

필드: t=ticker, n=name, m=market(KS/KQ/US)
"""

import json
import os
import requests
from datetime import datetime, timedelta

OUTPUT_DIR = "docs"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "stocks.json")
META_FILE = os.path.join(OUTPUT_DIR, "meta.json")

DATA_GO_KR_API_KEY = os.environ.get("DATA_GO_KR_API_KEY", "")

MAX_PAGES = 50   # getItemInfo의 totalCount가 매우 크므로 여유 있게
PER_PAGE = 1000  # 한 번에 더 많이 받기


def _strip_kr_prefix(code: str) -> str:
    """한국 종목코드에서 'A' 접두사 제거"""
    code = code.strip()
    if code.startswith("A") and code[1:].isdigit():
        return code[1:]
    return code


def _dedup(stocks, key="t"):
    """중복 제거 (첫 번째 등장 유지)"""
    seen = set()
    result = []
    for s in stocks:
        k = s[key]
        if k not in seen:
            seen.add(k)
            result.append(s)
    return result


def _fetch_datago_paged(base_url, extra_params=None, label=""):
    """공공데이터포털 페이징 공통 로직"""
    all_items = []
    if not DATA_GO_KR_API_KEY:
        print(f"  DATA_GO_KR_API_KEY 미설정. {label} 건너뜁니다.")
        return all_items

    for page in range(1, MAX_PAGES + 1):
        params = {
            "serviceKey": DATA_GO_KR_API_KEY,
            "resultType": "json",
            "numOfRows": PER_PAGE,
            "pageNo": page,
        }
        if extra_params:
            params.update(extra_params)

        try:
            resp = requests.get(base_url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            body = data.get("response", {}).get("body", {})
            items = body.get("items", {}).get("item", [])
            total_count = int(body.get("totalCount", 0))

            if isinstance(items, dict):
                items = [items]
            if not items:
                break

            all_items.extend(items)
            print(f"  {label} 페이지 {page}: {len(items)}건 (누적 {len(all_items)}/{total_count})")

            if len(all_items) >= total_count or len(items) < PER_PAGE:
                break
        except Exception as e:
            print(f"  {label} 페이지 {page} 실패: {e}")
            break

    return all_items


# ──────────────────────────────────────────
#  1. 한국 주식 (공공데이터포털 getItemInfo)
# ──────────────────────────────────────────
def fetch_kr_stocks():
    print("1. 한국 주식 수집 시작...")
    url = "https://apis.data.go.kr/1160100/service/GetKrxListedInfoService/getItemInfo"
    raw = _fetch_datago_paged(url, label="KR주식")

    stocks = []
    for item in raw:
        code = item.get("srtnCd", "")
        name = item.get("itmsNm", "")
        market = item.get("mrktCtg", "")

        if not code or not name:
            continue
        if market == "KONEX":
            continue

        m = "KS" if market == "KOSPI" else "KQ"
        t = _strip_kr_prefix(code)
        stocks.append({"t": t, "n": name, "m": m})

    stocks = _dedup(stocks)
    print(f"   한국 주식 완료: {len(stocks)}건")
    return stocks


# ──────────────────────────────────────────
#  2. 한국 ETF (공공데이터포털 getETFPriceInfo)
# ──────────────────────────────────────────
def fetch_kr_etfs():
    print("2. 한국 ETF 수집 시작...")

    # 최근 영업일 찾기: 오늘부터 7일 전까지 시도
    for days_ago in range(0, 8):
        dt = datetime.utcnow() + timedelta(hours=9) - timedelta(days=days_ago)
        bas_dt = dt.strftime("%Y%m%d")

        url = "https://apis.data.go.kr/1160100/service/GetSecuritiesProductInfoService/getETFPriceInfo"
        raw = _fetch_datago_paged(url, extra_params={"basDt": bas_dt}, label=f"KR-ETF({bas_dt})")

        if len(raw) >= 100:
            print(f"   기준일 {bas_dt} 사용")
            break
        elif len(raw) > 0:
            print(f"   {bas_dt}: {len(raw)}건 (너무 적음, 이전 날짜 시도)")
        else:
            print(f"   {bas_dt}: 0건 (휴일?, 이전 날짜 시도)")

    etfs = []
    for item in raw:
        code = item.get("srtnCd", "")
        name = item.get("itmsNm", "")

        if not code or not name:
            continue

        t = _strip_kr_prefix(code)
        etfs.append({"t": t, "n": name, "m": "KS"})

    etfs = _dedup(etfs)
    print(f"   한국 ETF 완료: {len(etfs)}건")
    return etfs


# ──────────────────────────────────────────
#  3. 미국 주식 (dumbstockapi)
# ──────────────────────────────────────────
def fetch_us_stocks():
    print("3. 미국 주식 수집 시작...")
    stocks = []
    try:
        url = "https://dumbstockapi.com/stock?exchanges=NYSE,NASDAQ&format=json"
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        for item in resp.json():
            ticker = item.get("ticker", "")
            name = item.get("name", "")
            if not ticker or not name:
                continue
            if any(c in ticker for c in [".", "-", "^", "/"]):
                continue
            stocks.append({"t": ticker, "n": name.strip(), "m": "US"})
        print(f"   미국 주식 완료: {len(stocks)}건")
    except Exception as e:
        print(f"   미국 주식 실패: {e}")
    return stocks


# ──────────────────────────────────────────
#  4. 미국 ETF (NASDAQ ETF screener)
# ──────────────────────────────────────────
def fetch_us_etfs():
    print("4. 미국 ETF 수집 시작...")
    etfs = []
    try:
        url = "https://api.nasdaq.com/api/screener/etf?tableonly=true&download=true"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, timeout=60)
        resp.raise_for_status()
        rows = resp.json().get("data", {}).get("data", {}).get("rows", [])
        for row in rows:
            symbol = (row.get("symbol") or "").strip()
            name = (row.get("companyName") or "").strip()
            if not symbol or not name:
                continue
            if any(c in symbol for c in [".", "-", "^", "/"]):
                continue
            etfs.append({"t": symbol, "n": name, "m": "US"})
        etfs = _dedup(etfs)
        print(f"   미국 ETF 완료: {len(etfs)}건")
    except Exception as e:
        print(f"   미국 ETF 실패: {e}")
    return etfs


# ──────────────────────────────────────────
#  메인
# ──────────────────────────────────────────
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    kr_stocks = fetch_kr_stocks()
    kr_etfs = fetch_kr_etfs()
    us_stocks = fetch_us_stocks()
    us_etfs = fetch_us_etfs()

    # 합치기 + 전체 중복 제거
    all_data = kr_stocks + kr_etfs + us_stocks + us_etfs
    all_data = _dedup(all_data)

    print(f"\n{'='*50}")
    print(f"전체: {len(all_data)}건")
    print(f"  KR 주식: {len(kr_stocks)}  {'✓' if len(kr_stocks) > 2000 else '⚠ 부족!'}")
    print(f"  KR ETF:  {len(kr_etfs)}  {'✓' if len(kr_etfs) >= 100 else '⚠ 부족!'}")
    print(f"  US 주식: {len(us_stocks)}  {'✓' if len(us_stocks) > 3000 else '⚠ 부족!'}")
    print(f"  US ETF:  {len(us_etfs)}  {'✓' if len(us_etfs) >= 100 else '⚠ 부족!'}")
    print(f"{'='*50}")

    if len(all_data) == 0:
        print("데이터가 없습니다. 파일을 생성하지 않습니다.")
        return

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, separators=(",", ":"))

    file_size = os.path.getsize(OUTPUT_FILE)

    meta = {
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "kr_stock_count": len(kr_stocks),
        "kr_etf_count": len(kr_etfs),
        "us_stock_count": len(us_stocks),
        "us_etf_count": len(us_etfs),
        "total_count": len(all_data),
        "file_size": file_size,
    }
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    index_path = os.path.join(OUTPUT_DIR, "index.html")
    with open(index_path, "w") as f:
        f.write(
            f"<html><body><h1>Stock Data</h1>"
            f"<p>Updated: {meta['updated_at']}</p>"
            f"<p>KR: {meta['kr_stock_count']} + ETF {meta['kr_etf_count']}</p>"
            f"<p>US: {meta['us_stock_count']} + ETF {meta['us_etf_count']}</p>"
            f"<p>Total: {meta['total_count']}</p>"
            f"<p><a href='stocks.json'>stocks.json</a> ({file_size/1024:.0f}KB)</p>"
            f"</body></html>"
        )

    print("완료!")


if __name__ == "__main__":
    main()
