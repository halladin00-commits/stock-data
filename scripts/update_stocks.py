"""
한국/미국 상장 종목 리스트를 수집하여 JSON 파일로 생성하는 스크립트.
GitHub Actions에서 매일 자동 실행됩니다.

한국: 공공데이터포털 - 금융위원회_KRX상장종목정보
미국: dumbstockapi.com
"""

import json
import os
import requests
from datetime import datetime

OUTPUT_DIR = "docs"  # GitHub Pages는 docs 폴더를 서빙
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "stocks.json")
META_FILE = os.path.join(OUTPUT_DIR, "meta.json")

# 공공데이터포털 API 키 (GitHub Secrets에서 주입)
DATA_GO_KR_API_KEY = os.environ.get("DATA_GO_KR_API_KEY", "")


def fetch_kr_stocks():
    """공공데이터포털에서 한국 상장 종목 목록 수집"""
    print("한국 종목 수집 시작...")
    stocks = []

    if not DATA_GO_KR_API_KEY:
        print("⚠️ DATA_GO_KR_API_KEY가 설정되지 않았습니다. 한국 종목을 건너뜁니다.")
        return stocks

    base_url = "https://apis.data.go.kr/1160100/service/GetKrxListedInfoService/getItemInfo"
    page = 1
    per_page = 500
    total_fetched = 0

    while True:
        params = {
            "serviceKey": DATA_GO_KR_API_KEY,
            "resultType": "json",
            "numOfRows": per_page,
            "pageNo": page,
        }

        try:
            resp = requests.get(base_url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            items = data.get("response", {}).get("body", {}).get("items", {}).get("item", [])
            total_count = data.get("response", {}).get("body", {}).get("totalCount", 0)

            if not items:
                break

            for item in items:
                short_code = item.get("srtnCd", "")  # 단축코드 (6자리)
                name = item.get("itmsNm", "")         # 종목명
                market = item.get("mrktCtg", "")       # 시장구분 (KOSPI/KOSDAQ/KONEX)

                if not short_code or not name:
                    continue

                # KONEX 제외 (거래량 매우 적음)
                if market == "KONEX":
                    continue

                # 시장 코드 변환
                m = "KS" if market == "KOSPI" else "KQ"

                stocks.append({
                    "t": short_code,
                    "n": name,
                    "m": m,
                })

            total_fetched += len(items)
            print(f"  페이지 {page}: {len(items)}건 (누적 {total_fetched}/{total_count})")

            if total_fetched >= total_count:
                break

            page += 1

        except Exception as e:
            print(f"  ❌ 페이지 {page} 수집 실패: {e}")
            break

    print(f"한국 종목 수집 완료: {len(stocks)}건")
    return stocks


def fetch_us_stocks():
    """dumbstockapi.com에서 미국 상장 종목 목록 수집"""
    print("미국 종목 수집 시작...")
    stocks = []

    try:
        # NYSE + NASDAQ
        url = "https://dumbstockapi.com/stock?exchanges=NYSE,NASDAQ&format=json"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for item in data:
            ticker = item.get("ticker", "")
            name = item.get("name", "")

            if not ticker or not name:
                continue

            # 우선주, 워런트 등 특수 종목 제외 (티커에 특수문자 포함)
            if any(c in ticker for c in [".", "-", "^", "/"]):
                continue

            stocks.append({
                "t": ticker,
                "n": name.strip(),
                "m": "US",
            })

        print(f"미국 종목 수집 완료: {len(stocks)}건")

    except Exception as e:
        print(f"❌ 미국 종목 수집 실패: {e}")

    return stocks


def main():
    # 출력 디렉토리 생성
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 데이터 수집
    kr_stocks = fetch_kr_stocks()
    us_stocks = fetch_us_stocks()

    all_stocks = kr_stocks + us_stocks
    print(f"\n전체 종목 수: {len(all_stocks)}건 (한국 {len(kr_stocks)} + 미국 {len(us_stocks)})")

    # JSON 파일 생성
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_stocks, f, ensure_ascii=False, separators=(",", ":"))

    file_size = os.path.getsize(OUTPUT_FILE)
    print(f"파일 크기: {file_size / 1024:.1f} KB")

    # 메타 정보 파일 생성
    meta = {
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "kr_count": len(kr_stocks),
        "us_count": len(us_stocks),
        "total_count": len(all_stocks),
        "file_size": file_size,
    }
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    # GitHub Pages용 index.html
    index_path = os.path.join(OUTPUT_DIR, "index.html")
    with open(index_path, "w") as f:
        f.write(f"<html><body><h1>Stock Data</h1><p>Updated: {meta['updated_at']}</p>"
                f"<p>Total: {meta['total_count']} (KR: {meta['kr_count']}, US: {meta['us_count']})</p>"
                f"<p><a href='stocks.json'>stocks.json</a> ({file_size/1024:.0f}KB)</p>"
                f"<p><a href='meta.json'>meta.json</a></p></body></html>")

    print("완료!")


if __name__ == "__main__":
    main()
