"""
한국/미국 상장 종목 + ETF 리스트를 수집하여 JSON 파일로 생성하는 스크립트.
GitHub Actions에서 매일 자동 실행됩니다.

필드 설명:
  t  = ticker (한국: 6자리 숫자, 미국: 영문 심볼)
  n  = name (종목명)
  m  = market (KS=KOSPI, KQ=KOSDAQ, US=미국)
  ty = type (S=주식, E=ETF)  ※ 없으면 주식으로 간주
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

# 한국 ETF 브랜드명 (이름에 포함되면 ETF로 분류)
KR_ETF_KEYWORDS = [
    "KODEX", "TIGER", "KBSTAR", "KOSEF", "ARIRANG", "SOL",
    "HANARO", "TIMEFOLIO", "ACE", "BNK", "WOORI", "PLUS",
    "FOCUS", "TREX", "파워", "마이티", "히어로",
]


def _strip_kr_prefix(code: str) -> str:
    """한국 종목코드에서 'A' 접두사 제거 -> 숫자 6자리만 반환"""
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


def _is_kr_etf(name: str) -> bool:
    """한국 종목명으로 ETF 여부 판별"""
    upper = name.upper()
    # 이름에 "ETF" 포함
    if "ETF" in upper:
        return True
    # 알려진 ETF 브랜드명 포함
    for kw in KR_ETF_KEYWORDS:
        if kw.upper() in upper:
            return True
    return False


# ──────────────────────────────────────────
#  한국 종목 (주식 + ETF 통합 수집)
# ──────────────────────────────────────────
def fetch_kr_all():
    """공공데이터포털 getItemInfo에서 한국 전체 종목 수집 (주식+ETF)"""
    print("한국 종목 수집 시작...")
    stocks = []
    etfs = []

    if not DATA_GO_KR_API_KEY:
        print("  DATA_GO_KR_API_KEY 미설정. 건너뜁니다.")
        return stocks, etfs

    base_url = "https://apis.data.go.kr/1160100/service/GetKrxListedInfoService/getItemInfo"

    all_items = []
    for page in range(1, MAX_PAGES + 1):
        params = {
            "serviceKey": DATA_GO_KR_API_KEY,
            "resultType": "json",
            "numOfRows": PER_PAGE,
            "pageNo": page,
        }

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
                print(f"  페이지 {page}: 데이터 없음. 종료.")
                break

            all_items.extend(items)
            print(f"  페이지 {page}: {len(items)}건 (누적 {len(all_items)}/{total_count})")

            if len(all_items) >= total_count or len(items) < PER_PAGE:
                break

        except Exception as e:
            print(f"  페이지 {page} 실패: {e}")
            break

    # 종목 분류
    for item in all_items:
        short_code = item.get("srtnCd", "")
        name = item.get("itmsNm", "")
        market = item.get("mrktCtg", "")

        if not short_code or not name:
            continue
        if market == "KONEX":
            continue

        m = "KS" if market == "KOSPI" else "KQ"
        t = _strip_kr_prefix(short_code)

        if _is_kr_etf(name):
            etfs.append({"t": t, "n": name, "m": m, "ty": "E"})
        else:
            stocks.append({"t": t, "n": name, "m": m, "ty": "S"})

    stocks = _dedup(stocks)
    etfs = _dedup(etfs)

    print(f"한국 수집 완료: 주식 {len(stocks)}건 + ETF {len(etfs)}건")
    return stocks, etfs


# ──────────────────────────────────────────
#  미국 주식 (dumbstockapi)
# ──────────────────────────────────────────
def fetch_us_stocks():
    """dumbstockapi에서 미국 주식 수집"""
    print("미국 주식 수집 시작...")
    stocks = []

    try:
        url = "https://dumbstockapi.com/stock?exchanges=NYSE,NASDAQ&format=json"
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        for item in data:
            ticker = item.get("ticker", "")
            name = item.get("name", "")

            if not ticker or not name:
                continue
            if any(c in ticker for c in [".", "-", "^", "/"]):
                continue

            stocks.append({"t": ticker, "n": name.strip(), "m": "US", "ty": "S"})

        print(f"미국 주식 수집 완료: {len(stocks)}건")
    except Exception as e:
        print(f"미국 주식 수집 실패: {e}")

    return stocks


# ──────────────────────────────────────────
#  미국 ETF (NASDAQ ETF screener)
# ──────────────────────────────────────────
def fetch_us_etfs():
    """NASDAQ ETF screener에서 미국 ETF 수집"""
    print("미국 ETF 수집 시작...")
    etfs = []

    try:
        url = "https://api.nasdaq.com/api/screener/etf?tableonly=true&download=true"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        rows = data.get("data", {}).get("data", {}).get("rows", [])
        print(f"  NASDAQ ETF screener: {len(rows)}건 수신")

        for row in rows:
            symbol = (row.get("symbol") or "").strip()
            name = (row.get("companyName") or "").strip()

            if not symbol or not name:
                continue
            if any(c in symbol for c in [".", "-", "^", "/"]):
                continue

            etfs.append({"t": symbol, "n": name, "m": "US", "ty": "E"})

        etfs = _dedup(etfs)
        print(f"미국 ETF 수집 완료: {len(etfs)}건")

    except Exception as e:
        print(f"미국 ETF 수집 실패: {e}")

    return etfs


# ──────────────────────────────────────────
#  메인
# ──────────────────────────────────────────
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    kr_stocks, kr_etfs = fetch_kr_all()
    us_stocks = fetch_us_stocks()
    us_etfs = fetch_us_etfs()

    # US: 주식에서 ETF와 겹치는 항목 제거
    us_etf_tickers = {e["t"] for e in us_etfs}
    us_stocks_dedup = [s for s in us_stocks if s["t"] not in us_etf_tickers]

    all_stocks = kr_stocks + kr_etfs + us_stocks_dedup + us_etfs
    etf_total = len(kr_etfs) + len(us_etfs)

    print(f"\n전체: {len(all_stocks)}건")
    print(f"  KR: {len(kr_stocks) + len(kr_etfs)} (주식 {len(kr_stocks)} + ETF {len(kr_etfs)})")
    print(f"  US: {len(us_stocks_dedup) + len(us_etfs)} (주식 {len(us_stocks_dedup)} + ETF {len(us_etfs)})")
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
        "kr_stock_count": len(kr_stocks),
        "kr_etf_count": len(kr_etfs),
        "us_stock_count": len(us_stocks_dedup),
        "us_etf_count": len(us_etfs),
        "etf_count": etf_total,
        "total_count": len(all_stocks),
        "file_size": file_size,
        "version": 2,
    }
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    index_path = os.path.join(OUTPUT_DIR, "index.html")
    with open(index_path, "w") as f:
        f.write(
            f"<html><body><h1>Stock Data v2</h1>"
            f"<p>Updated: {meta['updated_at']}</p>"
            f"<p>Total: {meta['total_count']}"
            f" (KR Stock: {meta['kr_stock_count']}"
            f", KR ETF: {meta['kr_etf_count']}"
            f", US Stock: {meta['us_stock_count']}"
            f", US ETF: {meta['us_etf_count']})</p>"
            f"<p><a href='stocks.json'>stocks.json</a> ({file_size/1024:.0f}KB)</p>"
            f"</body></html>"
        )

    print("완료!")


if __name__ == "__main__":
    main()
