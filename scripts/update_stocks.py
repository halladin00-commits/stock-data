"""
한국/미국 상장 종목 + ETF 리스트를 수집하여 JSON 파일로 생성하는 스크립트.
GitHub Actions에서 매일 자동 실행됩니다.

필드 설명:
  t  = ticker (한국: 6자리 숫자, 미국: 영문 심볼)
  n  = name (종목명)
  m  = market (KS=KOSPI, KQ=KOSDAQ, US=미국)
  ty = type (S=주식, E=ETF)
"""

import json
import os
import requests
from datetime import datetime

OUTPUT_DIR = "docs"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "stocks.json")
META_FILE = os.path.join(OUTPUT_DIR, "meta.json")

DATA_GO_KR_API_KEY = os.environ.get("DATA_GO_KR_API_KEY", "")

MAX_PAGES = 20
PER_PAGE = 500


def _strip_kr_prefix(code: str) -> str:
    """한국 종목코드에서 'A' 접두사 제거 -> 숫자 6자리만 반환"""
    code = code.strip()
    if code.startswith("A") and code[1:].isdigit():
        return code[1:]
    return code


def _fetch_paged(base_url, extra_params=None, label=""):
    """공공데이터포털 페이징 공통 로직"""
    items_all = []
    if not DATA_GO_KR_API_KEY:
        print(f"  DATA_GO_KR_API_KEY 미설정. {label} 건너뜁니다.")
        return items_all

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

            items_all.extend(items)
            print(f"  {label} 페이지 {page}: {len(items)}건 (누적 {len(items_all)}/{total_count})")

            if len(items_all) >= total_count or len(items) < PER_PAGE:
                break
        except Exception as e:
            print(f"  {label} 페이지 {page} 실패: {e}")
            break

    return items_all


def fetch_kr_stocks():
    """공공데이터포털 - 한국 상장 주식"""
    print("한국 주식 수집 시작...")
    url = "https://apis.data.go.kr/1160100/service/GetKrxListedInfoService/getItemInfo"
    raw = _fetch_paged(url, label="KR주식")

    stocks = []
    for item in raw:
        short_code = item.get("srtnCd", "")
        name = item.get("itmsNm", "")
        market = item.get("mrktCtg", "")

        if not short_code or not name:
            continue
        if market == "KONEX":
            continue

        m = "KS" if market == "KOSPI" else "KQ"
        t = _strip_kr_prefix(short_code)
        stocks.append({"t": t, "n": name, "m": m, "ty": "S"})

    # 중복 제거
    seen = set()
    unique = []
    for s in stocks:
        if s["t"] not in seen:
            seen.add(s["t"])
            unique.append(s)

    print(f"한국 주식 수집 완료: {len(unique)}건")
    return unique


def fetch_kr_etfs():
    """공공데이터포털 - 한국 상장 ETF"""
    print("한국 ETF 수집 시작...")

    # 방법 1: ETF 전용 엔드포인트
    url = "https://apis.data.go.kr/1160100/service/GetKrxListedInfoService/getETFItemInfo"
    raw = _fetch_paged(url, label="KR-ETF")

    if not raw:
        # 방법 2: 일반 엔드포인트에 mrktCtg=ETF
        print("  ETF 전용 API 실패. 대안 시도...")
        url2 = "https://apis.data.go.kr/1160100/service/GetKrxListedInfoService/getItemInfo"
        raw = _fetch_paged(url2, extra_params={"mrktCtg": "ETF"}, label="KR-ETF대안")

    etfs = []
    for item in raw:
        short_code = item.get("srtnCd", "")
        name = item.get("itmsNm", "")

        if not short_code or not name:
            continue

        t = _strip_kr_prefix(short_code)
        etfs.append({"t": t, "n": name, "m": "KS", "ty": "E"})

    seen = set()
    unique = []
    for s in etfs:
        if s["t"] not in seen:
            seen.add(s["t"])
            unique.append(s)

    print(f"한국 ETF 수집 완료: {len(unique)}건")
    return unique


def fetch_us_stocks():
    """dumbstockapi - 미국 종목 (주식 + ETF 자동 구분)"""
    print("미국 종목 수집 시작...")
    stocks = []

    try:
        url = "https://dumbstockapi.com/stock?exchanges=NYSE,NASDAQ&format=json"
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        for item in data:
            ticker = item.get("ticker", "")
            name = item.get("name", "")
            stock_type = item.get("type", "")

            if not ticker or not name:
                continue
            if any(c in ticker for c in [".", "-", "^", "/"]):
                continue

            ty = "E" if stock_type == "ETF" else "S"
            stocks.append({"t": ticker, "n": name.strip(), "m": "US", "ty": ty})

        etf_count = sum(1 for s in stocks if s["ty"] == "E")
        print(f"미국 종목 수집 완료: {len(stocks)}건 (주식 {len(stocks)-etf_count} + ETF {etf_count})")

    except Exception as e:
        print(f"미국 종목 수집 실패: {e}")

    return stocks


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    kr_stocks = fetch_kr_stocks()
    kr_etfs = fetch_kr_etfs()
    us_stocks = fetch_us_stocks()

    # KR 주식에서 ETF와 중복되는 항목 제거
    kr_etf_tickers = {e["t"] for e in kr_etfs}
    kr_stocks_dedup = [s for s in kr_stocks if s["t"] not in kr_etf_tickers]

    all_stocks = kr_stocks_dedup + kr_etfs + us_stocks
    etf_total = len(kr_etfs) + sum(1 for s in us_stocks if s["ty"] == "E")

    print(f"\n전체: {len(all_stocks)}건")
    print(f"  KR: {len(kr_stocks_dedup) + len(kr_etfs)} (주식 {len(kr_stocks_dedup)} + ETF {len(kr_etfs)})")
    print(f"  US: {len(us_stocks)}")
    print(f"  ETF 합계: {etf_total}")

    if len(all_stocks) == 0:
        print("데이터가 없습니다. 파일을 생성하지 않습니다.")
        return

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_stocks, f, ensure_ascii=False, separators=(",", ":"))

    file_size = os.path.getsize(OUTPUT_FILE)
    print(f"파일 크기: {file_size / 1024:.1f} KB")

    meta = {
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "kr_stock_count": len(kr_stocks_dedup),
        "kr_etf_count": len(kr_etfs),
        "us_count": len(us_stocks),
        "etf_count": etf_total,
        "total_count": len(all_stocks),
        "file_size": file_size,
        "version": 2,
    }
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    index_path = os.path.join(OUTPUT_DIR, "index.html")
    with open(index_path, "w") as f:
        f.write(f"<html><body><h1>Stock Data v2</h1>"
                f"<p>Updated: {meta['updated_at']}</p>"
                f"<p>Total: {meta['total_count']}"
                f" (KR Stock: {meta['kr_stock_count']}"
                f", KR ETF: {meta['kr_etf_count']}"
                f", US: {meta['us_count']})</p>"
                f"<p>ETF Total: {meta['etf_count']}</p>"
                f"<p><a href='stocks.json'>stocks.json</a> ({file_size/1024:.0f}KB)</p>"
                f"</body></html>")

    print("완료!")


if __name__ == "__main__":
    main()
