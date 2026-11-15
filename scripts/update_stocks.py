"""
한국/미국 상장 종목 리스트를 수집하여 JSON 파일로 생성하는 스크립트.
GitHub Actions에서 매일 자동 실행됩니다.

4개 소스:
  1. FinanceDataReader       → 한국 주식 (우선주 포함)
  2. FinanceDataReader       → 한국 ETF
  3. NASDAQ stock screener   → 미국 주식 (NYSE·NASDAQ·AMEX)
  4. NASDAQ ETF screener     → 미국 ETF
  5. Wikidata                → 미국 종목의 한글 이름

필드: t=ticker, n=name, m=market(KS/KQ/US), ty=E(ETF만),
      k=한글 이름들(미국 종목 일부만, 「|」로 나눔)
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
#  5. 미국 종목의 한글 이름 (Wikidata — CC0, 누구나 쓸 수 있다)
# ──────────────────────────────────────────
# 「애플」로 AAPL을 찾게 한다. 증권사가 나눠 주는 목록이 훨씬 많지만(6천 개)
# 이용 조건이 그 증권사 고객용이라 쓰지 않는다.
_WIKIDATA_QUERY = """
SELECT ?c ?t ?en ?ko ?alt WHERE {
  ?c p:P414 ?s. ?s ps:P414 ?ex; pq:P249 ?t.
  VALUES ?ex { wd:Q13677 wd:Q82059 wd:Q846626 }
  FILTER NOT EXISTS { ?s pq:P582 ?ended }
  ?c rdfs:label ?ko. FILTER(LANG(?ko)="ko")
  OPTIONAL { ?c rdfs:label ?en. FILTER(LANG(?en)="en") }
  OPTIONAL { ?c skos:altLabel ?alt. FILTER(LANG(?alt)="ko") }
}
"""

_HANGUL = re.compile(r"[가-힣]")


def _first_word(name):
    """회사 이름의 첫 낱말 — 「Apple Inc.」와 「Apple」을 같은 회사로 본다."""
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    words = [w for w in words if w != "the"]
    return words[0] if words else ""


def _korean_names(rows, us_names):
    """Wikidata 줄 → {티커: [한글 이름…]}.

    같은 티커를 예전에 쓰던 회사가 섞여 있다(S = 스프린트·시어스·센티넬원).
    영문 이름의 첫 낱말이 우리 목록의 그 티커와 같은 회사만 받는다 —
    딱 한 회사로 좁혀지지 않으면 그 티커에는 한글 이름을 붙이지 않는다.
    """
    by_ticker = {}
    for r in rows:
        t = (r.get("t") or "").strip().upper().replace(".", "-")
        if t not in us_names:
            continue
        if _first_word(r.get("en")) != _first_word(us_names[t]):
            continue
        names = by_ticker.setdefault(t, {}).setdefault(r["c"], [])
        for n in (r.get("ko"), r.get("alt")):
            n = (n or "").strip()
            # 「페이스북 (회사)」 같은 위키 표제어 꼬리는 이름이 아니다
            n = re.sub(r"\s*\([^)]*\)$", "", n)
            if n and _HANGUL.search(n) and n not in names:
                names.append(n)
    return {
        t: next(iter(cs.values()))
        for t, cs in by_ticker.items()
        if len(cs) == 1 and next(iter(cs.values()))
    }


def fetch_us_korean_names(us_names):
    print("5. 미국 종목 한글 이름 수집 시작 (Wikidata)...")
    try:
        resp = requests.get(
            "https://query.wikidata.org/sparql",
            params={"query": _WIKIDATA_QUERY, "format": "json"},
            headers={"User-Agent": "stock-list-builder/1.0 (GitHub Actions)"},
            timeout=120,
        )
        resp.raise_for_status()
        rows = [
            {k: v["value"] for k, v in b.items()}
            for b in resp.json()["results"]["bindings"]
        ]
        names = _korean_names(rows, us_names)
        print(f"   한글 이름 완료: {len(names)}건")
        return names
    except Exception as e:
        print(f"   한글 이름 실패: {e}")
        return None


def _previous_korean_names():
    """Wikidata가 안 될 때 — 어제 파일에 붙어 있던 이름을 그대로 쓴다."""
    try:
        with open(OUTPUT_FILE, encoding="utf-8") as f:
            return {s["t"]: s["k"] for s in json.load(f) if s.get("k")}
    except Exception:
        return {}


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

    # 한글 이름은 있으면 좋은 것이다 — 못 받아도 목록은 내보낸다
    us_names = {s["t"]: s["n"] for s in all_data if s["m"] == "US"}
    ko = fetch_us_korean_names(us_names)
    if ko is None:
        ko = {t: k.split("|") for t, k in _previous_korean_names().items()}
        print(f"   어제 파일의 한글 이름을 씁니다: {len(ko)}건")
    for s in all_data:
        if s["m"] == "US" and ko.get(s["t"]):
            s["k"] = "|".join(ko[s["t"]])

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, separators=(",", ":"))

    file_size = os.path.getsize(OUTPUT_FILE)

    meta = {
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "kr_stock_count": len(kr_stocks),
        "kr_etf_count": len(kr_etfs),
        "us_stock_count": len(us_stocks),
        "us_etf_count": len(us_etfs),
        "us_korean_name_count": sum(1 for s in all_data if s.get("k")),
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
