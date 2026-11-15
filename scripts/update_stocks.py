"""
한국/미국 상장 종목 리스트를 수집하여 JSON 파일로 생성하는 스크립트.
GitHub Actions에서 매일 자동 실행됩니다.

4개 소스:
  1. FinanceDataReader       → 한국 주식 (우선주 포함)
  2. FinanceDataReader       → 한국 ETF
  3. NASDAQ stock screener   → 미국 주식 (NYSE·NASDAQ·AMEX)
  4. NASDAQ ETF screener     → 미국 ETF

필드: t=ticker, n=name, m=market(KS/KQ/US), ty=E(ETF만)
"""

import json
import os
import re
import sys
import requests
from datetime import datetime

OUTPUT_DIR = "docs"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "stocks.json")
META_FILE = os.path.join(OUTPUT_DIR, "meta.json")


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


# ──────────────────────────────────────────
#  공통: FinanceDataReader DataFrame → 종목 리스트
# ──────────────────────────────────────────
def _fdr_df_to_stocks(df, m_code, ty=None):
    print(f"   shape={df.shape} columns={list(df.columns[:8])}")

    # Symbol이 인덱스인 경우 컬럼으로 전환
    if df.index.name in ('Symbol', 'Code', 'Ticker'):
        df = df.reset_index()

    code_col = next((c for c in ['Symbol', 'Code', 'Ticker'] if c in df.columns), None)
    name_col = next((c for c in ['Name', 'ISU_ABBRV', 'KorName'] if c in df.columns), None)

    if not code_col or not name_col:
        print(f"   컬럼 감지 실패: {list(df.columns)}")
        return []

    result = []
    for _, row in df.iterrows():
        try:
            ticker = str(row[code_col]).strip()
            name = str(row[name_col]).strip()
            if not ticker or not name or ticker == 'nan' or name == 'nan':
                continue
            if len(ticker) != 6 or not ticker.isalnum():
                continue
            item = {"t": ticker, "n": name, "m": m_code}
            if ty:
                item["ty"] = ty
            result.append(item)
        except Exception:
            continue
    return result


# ──────────────────────────────────────────
#  1. 한국 주식 (FinanceDataReader — 우선주 포함)
# ──────────────────────────────────────────
def fetch_kr_stocks():
    print("1. 한국 주식 수집 시작 (FinanceDataReader)...")
    try:
        import FinanceDataReader as fdr
        stocks = []
        for market in ['KOSPI', 'KOSDAQ']:
            m_code = 'KS' if market == 'KOSPI' else 'KQ'
            df = fdr.StockListing(market)
            items = _fdr_df_to_stocks(df, m_code)
            print(f"   {market}: {len(items)}건")
            stocks.extend(items)
        stocks = _dedup(stocks)
        print(f"   한국 주식 완료: {len(stocks)}건")
        return stocks
    except Exception as e:
        print(f"   한국 주식 실패: {e}")
        import traceback
        traceback.print_exc()
        return []


# ──────────────────────────────────────────
#  2. 한국 ETF (FinanceDataReader)
# ──────────────────────────────────────────
def fetch_kr_etfs():
    print("2. 한국 ETF 수집 시작 (FinanceDataReader)...")
    try:
        import FinanceDataReader as fdr
        for market_key in ['ETF/KR', 'KRX-ETF']:
            try:
                df = fdr.StockListing(market_key)
                if df.empty:
                    continue
                etfs = _fdr_df_to_stocks(df, 'KS', ty='E')
                if etfs:
                    etfs = _dedup(etfs)
                    print(f"   한국 ETF 완료: {len(etfs)}건 (key={market_key})")
                    return etfs
            except Exception as e:
                print(f"   ETF({market_key}) 실패: {e}")
                continue
        print("   한국 ETF: 0건")
        return []
    except Exception as e:
        print(f"   한국 ETF 실패: {e}")
        import traceback
        traceback.print_exc()
        return []


# ──────────────────────────────────────────
#  3. 미국 주식 (NASDAQ stock screener — NYSE·NASDAQ·AMEX)
# ──────────────────────────────────────────
# 이름 끝의 「Common Stock」 따위는 종목 종류지 이름이 아니다 — 뗀다.
# 「Class A」는 남긴다 (GOOGL과 GOOG를 가른다).
_US_NAME_TAIL = re.compile(
    r"\s+(Common Stock|Common Shares?|Ordinary Shares?|Capital Stock)"
    r"(\s*\([^)]*\))?$",
    re.IGNORECASE,
)
# 워런트·유닛·권리는 주식이 아니다
_US_NOT_STOCK = re.compile(r"\b(Warrants?|Units?|Rights?)$", re.IGNORECASE)


def _us_stock_rows(rows):
    """NASDAQ screener 줄 → 종목 리스트. 시험할 수 있게 받아 오는 일과 나눴다."""
    stocks = []
    for row in rows:
        symbol = (row.get("symbol") or "").strip()
        name = (row.get("name") or "").strip()
        if not symbol or not name:
            continue
        # BRK/B → BRK-B (시세를 받는 Yahoo의 표기)
        symbol = symbol.replace("/", "-")
        # ^는 우선주 시리즈, 그 밖의 기호는 시세를 못 받는다
        if not re.fullmatch(r"[A-Z]+(-[A-Z])?", symbol):
            continue
        if _US_NOT_STOCK.search(name):
            continue
        name = _US_NAME_TAIL.sub("", name).strip()
        if not name:
            continue
        stocks.append({"t": symbol, "n": name, "m": "US"})
    return _dedup(stocks)


def fetch_us_stocks():
    print("3. 미국 주식 수집 시작...")
    try:
        url = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&download=true"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, timeout=60)
        resp.raise_for_status()
        rows = (resp.json().get("data") or {}).get("rows") or []
        stocks = _us_stock_rows(rows)
        print(f"   미국 주식 완료: {len(stocks)}건")
        return stocks
    except Exception as e:
        print(f"   미국 주식 실패: {e}")
        return []


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

    # 한 곳이라도 모자라면 어제 파일을 그대로 둔다. 예전에는 받아 오는 곳이
    # 죽어도 나머지만으로 덮어써서, 미국 주식이 통째로 빠진 목록이 몇 달 동안
    # 앱으로 나갔다. 실패로 끝내야 GitHub이 메일로 알린다.
    short = [
        name for name, got, need in [
            ("KR 주식", len(kr_stocks), 2000),
            ("KR ETF", len(kr_etfs), 100),
            ("US 주식", len(us_stocks), 3000),
            ("US ETF", len(us_etfs), 100),
        ] if got < need
    ]
    if short:
        print(f"모자란 곳: {', '.join(short)} — 파일을 바꾸지 않습니다.")
        sys.exit(1)

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
