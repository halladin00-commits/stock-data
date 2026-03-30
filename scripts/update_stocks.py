"""

한국/미국 상장 종목 리스트를 수집하여 JSON 파일로 생성하는 스크립트.

GitHub Actions에서 매일 자동 실행됩니다.

"""



import json

import os

import requests

from datetime import datetime



OUTPUT_DIR = "docs"

OUTPUT_FILE = os.path.join(OUTPUT_DIR, "stocks.json")

META_FILE = os.path.join(OUTPUT_DIR, "meta.json")



DATA_GO_KR_API_KEY = os.environ.get("DATA_GO_KR_API_KEY", "")



MAX_PAGES = 20  # 안전장치: 최대 페이지 수 제한

PER_PAGE = 500  # 페이지당 건수





def fetch_kr_stocks():

    """공공데이터포털에서 한국 상장 종목 목록 수집"""

    print("한국 종목 수집 시작...")

    stocks = []



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



            # items가 dict로 올 수도 있음 (1건일 때)

            if isinstance(items, dict):

                items = [items]



            if not items:

                print(f"  페이지 {page}: 데이터 없음. 종료.")

                break



            for item in items:

                short_code = item.get("srtnCd", "")

                name = item.get("itmsNm", "")

                market = item.get("mrktCtg", "")



                if not short_code or not name:

                    continue

                if market == "KONEX":

                    continue



                m = "KS" if market == "KOSPI" else "KQ"

                stocks.append({"t": short_code, "n": name, "m": m})



            print(f"  페이지 {page}: {len(items)}건 (누적 {len(stocks)}/{total_count})")



            # 전부 받았으면 종료

            if len(stocks) >= total_count or len(items) < PER_PAGE:

                break



        except Exception as e:

            print(f"  페이지 {page} 실패: {e}")

            break



    # 중복 제거 (종목코드 기준)

    seen = set()

    unique = []

    for s in stocks:

        if s["t"] not in seen:

            seen.add(s["t"])

            unique.append(s)

    stocks = unique



    print(f"한국 종목 수집 완료: {len(stocks)}건")

    return stocks





def fetch_us_stocks():

    """dumbstockapi.com에서 미국 상장 종목 목록 수집"""

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



            if not ticker or not name:

                continue

            if any(c in ticker for c in [".", "-", "^", "/"]):

                continue



            stocks.append({"t": ticker, "n": name.strip(), "m": "US"})



        print(f"미국 종목 수집 완료: {len(stocks)}건")



    except Exception as e:

        print(f"미국 종목 수집 실패: {e}")



    return stocks





def main():

    os.makedirs(OUTPUT_DIR, exist_ok=True)



    kr_stocks = fetch_kr_stocks()

    us_stocks = fetch_us_stocks()

    all_stocks = kr_stocks + us_stocks



    print(f"\n전체: {len(all_stocks)}건 (KR {len(kr_stocks)} + US {len(us_stocks)})")



    if len(all_stocks) == 0:

        print("데이터가 없습니다. 파일을 생성하지 않습니다.")

        return



    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:

        json.dump(all_stocks, f, ensure_ascii=False, separators=(",", ":"))



    file_size = os.path.getsize(OUTPUT_FILE)

    print(f"파일 크기: {file_size / 1024:.1f} KB")



    meta = {

        "updated_at": datetime.utcnow().isoformat() + "Z",

        "kr_count": len(kr_stocks),

        "us_count": len(us_stocks),

        "total_count": len(all_stocks),

        "file_size": file_size,

    }

    with open(META_FILE, "w", encoding="utf-8") as f:

        json.dump(meta, f, indent=2)



    index_path = os.path.join(OUTPUT_DIR, "index.html")

    with open(index_path, "w") as f:

        f.write(f"<html><body><h1>Stock Data</h1>"

                f"<p>Updated: {meta['updated_at']}</p>"

                f"<p>Total: {meta['total_count']} (KR: {meta['kr_count']}, US: {meta['us_count']})</p>"

                f"<p><a href='stocks.json'>stocks.json</a> ({file_size/1024:.0f}KB)</p>"

                f"</body></html>")



    print("완료!")





if __name__ == "__main__":

    main()
