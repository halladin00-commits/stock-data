"""
한국/미국 상장 종목 + ETF 리스트를 수집하여 JSON 파일로 생성하는 스크립트.
GitHub Actions에서 매일 자동 실행됩니다.

필드: t=ticker, n=name, m=market(KS/KQ/US), ty=type(S/E)
"""

import json
import os
import requests
from datetime import datetime, timedelta

OUTPUT_DIR = "docs"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "stocks.json")
META_FILE = os.path.join(OUTPUT_DIR, "meta.json")

DATA_GO_KR_API_KEY = os.environ.get("DATA_GO_KR_API_KEY", "")

MAX_PAGES = 20
PER_PAGE = 500


def _strip_kr_prefix(code: str) -> str:
    code = code.strip()
    if code.startswith("A") and code[1:].isdigit():
        return code[1:]
    return code


def _dedup(stocks, key="t"):
    seen = set()
    result = []
    for s in stocks:
        k = s[key]
        if k not in seen:
            seen.add(k)
            result.append(s)
    return result


# ──────────────────────────────────────────
#  한국 주식 (공공데이터포털 getItemInfo)
# ──────────────────────────────────────────
def fetch_kr_stocks():
    print("한국 주식 수집 시작...")
    stocks = []
    found_markets = set()

    if not DATA_GO_KR_API_KEY:
        print("  DATA_GO_KR_API_KEY 미설정. 건너뜁니다.")
        return stocks

    base_url = "https://apis.data.go.kr/1160100/service/GetKrxListedInfoService/getItemInfo"

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
                break

            for item in items:
                short_code = item.get("srtnCd", "")
                name = item.get("itmsNm", "")
                market = item.get("mrktCtg", "")
                found_markets.add(market)

                if not short_code or not name:
                    continue
                if market == "KONEX":
                    continue

                m = "KS" if market == "KOSPI" else "KQ"
                t = _strip_kr_prefix(short_code)
                stocks.append({"t": t, "n": name, "m": m, "ty": "S"})

            print(f"  페이지 {page}: {len(items)}건 (누적 {len(stocks)}/{total_count})")
            if len(stocks) >= total_count or len(items) < PER_PAGE:
                break
        except Exception as e:
            print(f"  페이지 {page} 실패: {e}")
            break

    stocks = _dedup(stocks)
    print(f"한국 주식 수집 완료: {len(stocks)}건")
    print(f"  발견된 mrktCtg 값: {found_markets}")
    return stocks


# ──────────────────────────────────────────
#  한국 ETF (여러 소스 시도)
# ──────────────────────────────────────────
def fetch_kr_etfs():
    print("한국 ETF 수집 시작...")

    # 방법 1: KRX JSON API
    etfs = _try_krx_etfs()
    if len(etfs) >= 100:
        return etfs

    # 방법 2: 공공데이터포털 getETFItemInfo → getItemInfo diff
    print("  KRX 실패. 공공데이터포털 diff 방식 시도...")
    etfs = _try_datago_diff()
    if len(etfs) >= 100:
        return etfs

    print(f"  ⚠ 한국 ETF {len(etfs)}건만 수집됨 (정상: 700+)")
    return etfs


def _try_krx_etfs():
    """KRX 한국거래소에서 ETF 전종목 가져오기"""
    print("  KRX ETF 시도...")
    etfs = []

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020101",
    }

    # 최근 영업일 계산 (오늘부터 7일 이내)
    for days_ago in range(0, 7):
        trd_date = (datetime.utcnow() - timedelta(days=days_ago)).strftime("%Y%m%d")

        try:
            # OTP 생성
            otp_url = "http://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd"
            otp_data = {
                "locale": "ko_KR",
                "mktId": "ETF",
                "trdDd": trd_date,
                "share": "1",
                "money": "1",
                "csvxls_isNo": "false",
                "name": "fileDown",
                "url": "dbms/MDC/STAT/standard/MDCSTAT04301",
            }
            otp_resp = requests.post(otp_url, data=otp_data, headers=headers, timeout=10)
            otp = otp_resp.text.strip()

            if otp.startswith("<") or len(otp) < 10:
                print(f"    {trd_date}: OTP 실패")
                continue

            # CSV 다운로드
            down_url = "http://data.krx.co.kr/comm/fileDn/download_csv/download.cmd"
            csv_resp = requests.post(down_url, data={"code": otp}, headers=headers, timeout=30)

            if csv_resp.status_code != 200:
                continue

            text = csv_resp.content.decode("euc-kr", errors="replace")
            lines = text.strip().split("\n")

            if len(lines) < 10:
                print(f"    {trd_date}: 데이터 부족 ({len(lines)}줄)")
                continue

            # CSV 파싱 (첫줄 = 헤더)
            header = lines[0].split(",")
            # 종목코드, 종목명 컬럼 찾기
            code_idx = None
            name_idx = None
            for i, h in enumerate(header):
                h = h.strip().strip('"')
                if "종목코드" in h or "단축코드" in h:
                    code_idx = i
                if "종목명" in h:
                    name_idx = i

            if code_idx is None or name_idx is None:
                print(f"    {trd_date}: 컬럼 못 찾음. 헤더: {header[:5]}")
                continue

            for line in lines[1:]:
                cols = line.split(",")
                if len(cols) <= max(code_idx, name_idx):
                    continue
                code = cols[code_idx].strip().strip('"')
                name = cols[name_idx].strip().strip('"')
                if code and name:
                    etfs.append({"t": code, "n": name, "m": "KS", "ty": "E"})

            etfs = _dedup(etfs)
            print(f"    {trd_date}: KRX ETF {len(etfs)}건 수집")
            break

        except Exception as e:
            print(f"    {trd_date}: {e}")
            continue

    return etfs


def _try_datago_diff():
    """공공데이터포털 getETFItemInfo 호출 → getItemInfo와 diff"""
    if not DATA_GO_KR_API_KEY:
        return []

    # getETFItemInfo 호출
    print("  getETFItemInfo 호출...")
    etf_items = []
    base_url = "https://apis.data.go.kr/1160100/service/GetKrxListedInfoService/getETFItemInfo"

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
                break

            for item in items:
                short_code = item.get("srtnCd", "")
                name = item.get("itmsNm", "")
                if short_code and name:
                    t = _strip_kr_prefix(short_code)
                    etf_items.append({"t": t, "n": name})

            print(f"    페이지 {page}: {len(items)}건 (누적 {len(etf_items)}/{total_count})")
            if len(etf_items) >= total_count or len(items) < PER_PAGE:
                break
        except Exception as e:
            print(f"    페이지 {page} 실패: {e}")
            break

    if not etf_items:
        return []

    # getItemInfo 티커 목록 로드 (이미 수집된 주식 목록 파일 활용)
    stock_tickers = set()
    try:
        if os.path.exists(OUTPUT_FILE):
            with open(OUTPUT_FILE) as f:
                existing = json.load(f)
            stock_tickers = {
                s["t"] for s in existing if s.get("m") in ("KS", "KQ") and s.get("ty") == "S"
            }
    except:
        pass

    # diff: ETFItemInfo에 있지만 주식에 없는 항목 = ETF
    etfs = []
    for item in etf_items:
        if item["t"] not in stock_tickers:
            etfs.append({"t": item["t"], "n": item["n"], "m": "KS", "ty": "E"})

    etfs = _dedup(etfs)
    print(f"  diff 방식 결과: ETFItemInfo {len(etf_items)}건 - 주식 {len(stock_tickers)}건 = ETF {len(etfs)}건")
    return etfs


# ──────────────────────────────────────────
#  미국 주식 (dumbstockapi)
# ──────────────────────────────────────────
def fetch_us_stocks():
    print("미국 주식 수집 시작...")
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
            stocks.append({"t": ticker, "n": name.strip(), "m": "US", "ty": "S"})
        print(f"미국 주식 수집 완료: {len(stocks)}건")
    except Exception as e:
        print(f"미국 주식 수집 실패: {e}")
    return stocks


# ──────────────────────────────────────────
#  미국 ETF (NASDAQ screener)
# ──────────────────────────────────────────
def fetch_us_etfs():
    print("미국 ETF 수집 시작...")
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

    # 1) 한국 주식 먼저 수집 + 저장 (diff용)
    kr_stocks = fetch_kr_stocks()

    # 임시 저장 (diff 방식에서 참조)
    temp_data = kr_stocks[:]
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(temp_data, f, ensure_ascii=False, separators=(",", ":"))

    # 2) 한국 ETF
    kr_etfs = fetch_kr_etfs()

    # 3) 미국
    us_stocks = fetch_us_stocks()
    us_etfs = fetch_us_etfs()

    # US 중복 제거 (ETF와 주식 겹침)
    us_etf_tickers = {e["t"] for e in us_etfs}
    us_stocks_dedup = [s for s in us_stocks if s["t"] not in us_etf_tickers]

    all_stocks = kr_stocks + kr_etfs + us_stocks_dedup + us_etfs
    etf_total = len(kr_etfs) + len(us_etfs)

    print(f"\n{'='*50}")
    print(f"전체: {len(all_stocks)}건")
    print(f"  KR 주식: {len(kr_stocks)}")
    print(f"  KR ETF:  {len(kr_etfs)} {'⚠ 부족!' if len(kr_etfs) < 100 else '✓'}")
    print(f"  US 주식: {len(us_stocks_dedup)}")
    print(f"  US ETF:  {len(us_etfs)} {'⚠ 부족!' if len(us_etfs) < 100 else '✓'}")
    print(f"{'='*50}")

    if len(all_stocks) == 0:
        print("데이터가 없습니다. 파일을 생성하지 않습니다.")
        return

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_stocks, f, ensure_ascii=False, separators=(",", ":"))

    file_size = os.path.getsize(OUTPUT_FILE)

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
            f"<p>KR Stock: {meta['kr_stock_count']}, KR ETF: {meta['kr_etf_count']}</p>"
            f"<p>US Stock: {meta['us_stock_count']}, US ETF: {meta['us_etf_count']}</p>"
            f"<p>Total: {meta['total_count']}</p>"
            f"<p><a href='stocks.json'>stocks.json</a> ({file_size/1024:.0f}KB)</p>"
            f"</body></html>"
        )

    print("완료!")


if __name__ == "__main__":
    main()
