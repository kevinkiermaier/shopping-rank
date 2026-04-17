from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import requests
import re
import os
import json
import sqlite3
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

EXT_DB = '/tmp/ext_cache.db' if os.environ.get('VERCEL') else 'ext_cache.db'

def init_ext_db():
    conn = sqlite3.connect(EXT_DB)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS ext_sessions (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword  TEXT,
            products TEXT,
            saved_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_ext_db()

NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")

SCRAPE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


def clean_html(text):
    return re.sub(r"<[^>]+>", "", text)


def format_date(val):
    """20240101 또는 '2024-01-01' 등 -> 2024.01.01"""
    if not val:
        return "-"
    s = str(val).replace("-", "").replace(".", "").strip()
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[:4]}.{s[4:6]}.{s[6:8]}"
    return str(val)


def estimate_sales(review):
    """리뷰수 기반 판매량 추정 (리뷰 작성률 약 2~5% 가정)"""
    if review <= 0:
        return "-", "-"
    total_est = review * 25  # 리뷰 작성률 ~4% 가정
    sales_7d = max(1, round(total_est / 78))   # 약 18개월 판매 기준 주간 환산
    sales_6m = max(1, round(total_est / 3))    # 6개월치
    # 보기 좋게 반올림
    def rnd(n):
        if n >= 10000: return f"~{round(n/1000)*1000:,}"
        if n >= 1000:  return f"~{round(n/100)*100:,}"
        if n >= 100:   return f"~{round(n/10)*10:,}"
        return f"~{n:,}"
    return rnd(sales_7d), rnd(sales_6m)


def calc_score(rank, review, purchase_cnt=0):
    # 리뷰 점수
    if review >= 10000:
        review_score = 5
    elif review >= 3000:
        review_score = 4
    elif review >= 500:
        review_score = 3
    elif review >= 50:
        review_score = 2
    else:
        review_score = 1

    # 클릭 점수 (순위 기반)
    if rank <= 3:
        click_score = 5
    elif rank <= 10:
        click_score = 4
    elif rank <= 20:
        click_score = 3
    else:
        click_score = 2

    # 판매 점수
    if purchase_cnt >= 5000:
        sales_score = 5
    elif purchase_cnt >= 1000:
        sales_score = 4
    elif purchase_cnt >= 100:
        sales_score = 3
    elif purchase_cnt > 0:
        sales_score = 2
    else:
        if review >= 5000:
            sales_score = 5
        elif review >= 1000:
            sales_score = 4
        elif review >= 100:
            sales_score = 3
        else:
            sales_score = 2

    popularity = round((review_score + click_score + sales_score) / 3 * 2) / 2
    total = round(review_score * 0.3 + click_score * 0.3 + sales_score * 0.4)
    total = min(5, max(1, total))

    return {
        "total": total,
        "click_score": click_score,
        "sales_score": sales_score,
        "review_score": review_score,
        "popularity": popularity,
        "recency": 2,
        "penalty": 0,
        "relevance": 1,
        "reliability": 1,
    }


def extract_products_from_state(state):
    """initialState에서 상품 목록 추출 (구조가 변할 수 있어 유연하게 처리)"""
    # 시도 1: products.list
    try:
        return state["products"]["list"]
    except (KeyError, TypeError):
        pass
    # 시도 2: products.items
    try:
        return state["products"]["items"]
    except (KeyError, TypeError):
        pass
    # 시도 3: searchResult.products
    try:
        return state["searchResult"]["products"]
    except (KeyError, TypeError):
        pass
    # 시도 4: 재귀적으로 리스트 찾기 (첫 번째 큰 배열)
    def find_product_list(obj, depth=0):
        if depth > 5:
            return None
        if isinstance(obj, list) and len(obj) > 0:
            first = obj[0]
            if isinstance(first, dict):
                item = first.get("item", first)
                if any(k in item for k in ("productTitle", "title", "mallName", "price", "lprice")):
                    return obj
        if isinstance(obj, dict):
            for v in obj.values():
                result = find_product_list(v, depth + 1)
                if result:
                    return result
        return None

    return find_product_list(state) or []


def parse_product(p, rank):
    """상품 dict에서 필드 추출"""
    item = p.get("item", p)

    title = clean_html(
        item.get("productTitle")
        or item.get("title")
        or item.get("name")
        or ""
    )
    mall = item.get("mallName", "")
    if not mall and item.get("openMallList"):
        mall = item["openMallList"][0].get("mallName", "")

    price = int(item.get("price") or item.get("lprice") or 0)
    image = item.get("imageUrl") or item.get("image") or ""
    link = item.get("mallProductUrl") or item.get("link") or ""
    if not link.startswith("http"):
        nvmid = item.get("nvMid") or item.get("id") or ""
        link = f"https://search.shopping.naver.com/catalog/{nvmid}" if nvmid else ""

    review = int(item.get("reviewCount") or item.get("review") or 0)
    purchase = int(item.get("purchaseCnt") or item.get("purchaseCount") or 0)
    wish = item.get("wishCount") or item.get("likeCount") or 0

    category = " > ".join(filter(None, [
        item.get("category1Name") or item.get("category1") or "",
        item.get("category2Name") or item.get("category2") or "",
        item.get("category3Name") or item.get("category3") or "",
        item.get("category4Name") or item.get("category4") or "",
    ]))

    reg_raw = (
        item.get("registDate")
        or item.get("regDate")
        or item.get("openDate")
        or item.get("enrollDate")
        or ""
    )
    reg_date = format_date(reg_raw)

    scores = calc_score(rank, review, purchase)

    return {
        "rank": rank,
        "image": image,
        "title": title,
        "link": link,
        "mall": mall,
        "sales_7d": f"{purchase:,}" if purchase > 0 else "-",
        "sales_6m": "-",
        "price": f"{price:,}",
        "category": category,
        "reg_date": reg_date,
        "total": scores["total"],
        "wish": f"{int(wish):,}" if wish and int(wish) > 0 else "-",
        "review": f"{review:,}" if review > 0 else "-",
        "click_score": scores["click_score"],
        "sales_score": scores["sales_score"],
        "review_score": scores["review_score"],
        "popularity": scores["popularity"],
        "recency": scores["recency"],
        "penalty": scores["penalty"],
        "relevance": scores["relevance"],
        "reliability": scores["reliability"],
        "grade": "GOOD",
    }


def search_naver_scrape(keyword, display=40):
    """네이버 쇼핑 HTML 스크래핑 (등록일시·판매량 포함)"""
    try:
        url = "https://search.shopping.naver.com/search/all"
        params = {"query": keyword, "sort": "rel", "pagingIndex": 1, "pagingSize": 40}

        res = requests.get(url, headers=SCRAPE_HEADERS, params=params, timeout=15)
        if res.status_code != 200:
            return None, f"Naver 스크래핑 차단 (HTTP {res.status_code})"

        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            res.text,
            re.DOTALL,
        )
        if not match:
            return None, "스크래핑 실패: __NEXT_DATA__ 없음"

        next_data = json.loads(match.group(1))

        try:
            state = next_data["props"]["pageProps"]["initialState"]
        except (KeyError, TypeError):
            return None, "initialState 없음"

        products = extract_products_from_state(state)
        if not products:
            return None, "상품 목록 파싱 실패"

        results = []
        for i, p in enumerate(products[:display], 1):
            results.append(parse_product(p, i))

        return results, None

    except Exception as e:
        return None, f"스크래핑 오류: {str(e)}"


def search_naver_api(keyword, display=100):
    """네이버 공식 검색 API (폴백)"""
    url = "https://openapi.naver.com/v1/search/shop.json"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    params = {"query": keyword, "display": display, "start": 1, "sort": "sim"}

    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    items = res.json().get("items", [])

    results = []
    for i, item in enumerate(items, 1):
        review = int(item.get("reviewCount") or 0)
        scores = calc_score(i, review)
        price = int(item.get("lprice") or 0)
        sales_7d, sales_6m = estimate_sales(review)
        category = " > ".join(filter(None, [
            item.get("category1", ""),
            item.get("category2", ""),
            item.get("category3", ""),
            item.get("category4", ""),
        ]))
        results.append({
            "rank": i,
            "image": item.get("image", ""),
            "title": clean_html(item.get("title", "")),
            "link": item.get("link", ""),
            "mall": item.get("mallName", ""),
            "sales_7d": sales_7d,
            "sales_6m": sales_6m,
            "price": f"{price:,}",
            "category": category,
            "reg_date": "-",
            "total": scores["total"],
            "wish": "-",
            "review": f"{review:,}" if review else "-",
            "click_score": scores["click_score"],
            "sales_score": scores["sales_score"],
            "review_score": scores["review_score"],
            "popularity": scores["popularity"],
            "recency": scores["recency"],
            "penalty": scores["penalty"],
            "relevance": scores["relevance"],
            "reliability": scores["reliability"],
            "grade": "GOOD",
        })
    return results


@app.route('/api/analysis', methods=['POST'])
def receive_ext():
    data = request.get_json(force=True)
    keyword = (data.get('keyword') or '').strip()
    products = data.get('products', [])
    if not products:
        return jsonify({'error': 'no data'}), 400
    conn = sqlite3.connect(EXT_DB)
    conn.execute(
        'INSERT INTO ext_sessions (keyword, products, saved_at) VALUES (?,?,?)',
        (keyword, json.dumps(products), datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'count': len(products)})


@app.route('/api/analysis/latest', methods=['GET'])
def latest_ext():
    conn = sqlite3.connect(EXT_DB)
    row = conn.execute(
        'SELECT id, keyword, products, saved_at FROM ext_sessions ORDER BY id DESC LIMIT 1'
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({'session': None, 'products': []})
    return jsonify({
        'session': {'id': row[0], 'keyword': row[1], 'scraped_at': row[3]},
        'products': json.loads(row[2])
    })


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/debug-env")
def debug_env():
    client_id = os.environ.get("NAVER_CLIENT_ID", "")
    client_secret = os.environ.get("NAVER_CLIENT_SECRET", "")
    return jsonify({
        "NAVER_CLIENT_ID": client_id[:4] + "****" if client_id else "비어있음",
        "NAVER_CLIENT_SECRET": client_secret[:4] + "****" if client_secret else "비어있음",
        "all_env_keys": [k for k in os.environ.keys() if "NAVER" in k],
    })


@app.route("/debug-scrape")
def debug_scrape():
    """스크래핑 결과 원시 데이터 확인용 (개발용)"""
    keyword = request.args.get("q", "현수막")
    url = "https://search.shopping.naver.com/search/all"
    params = {"query": keyword, "sort": "rel", "pagingIndex": 1, "pagingSize": 3}
    try:
        res = requests.get(url, headers=SCRAPE_HEADERS, params=params, timeout=15)
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            res.text, re.DOTALL
        )
        if not match:
            return jsonify({"error": "__NEXT_DATA__ 없음", "status": res.status_code})
        next_data = json.loads(match.group(1))
        state = next_data.get("props", {}).get("pageProps", {}).get("initialState", {})
        products = extract_products_from_state(state)
        sample = products[:2] if products else []
        return jsonify({
            "status": res.status_code,
            "state_keys": list(state.keys()) if isinstance(state, dict) else "not dict",
            "product_count": len(products),
            "sample": sample,
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/search", methods=["POST"])
def search():
    data = request.json
    keyword = data.get("keyword", "").strip()
    platform = data.get("platform", "naver")

    if not keyword:
        return jsonify({"error": "키워드를 입력하세요.", "results": []})

    if platform == "naver":
        # 1차: 스크래핑 시도
        results, scrape_error = search_naver_scrape(keyword)

        # 2차: 스크래핑 실패 시 공식 API 폴백
        if results is None:
            if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
                return jsonify({
                    "error": f"스크래핑 실패: {scrape_error} / API키도 없음",
                    "results": [],
                })
            try:
                results = search_naver_api(keyword)
                source = "공식API(폴백)"
            except Exception as e:
                return jsonify({"error": f"API 오류: {str(e)}", "results": []})
        else:
            source = "스크래핑"

        return jsonify({"results": results, "total": len(results), "source": source})

    elif platform == "coupang":
        return jsonify({"error": "쿠팡 검색은 준비 중입니다.", "results": []})

    return jsonify({"error": "지원하지 않는 플랫폼입니다.", "results": []})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    debug = os.environ.get("FLASK_ENV") != "production"
    app.run(host="0.0.0.0", port=port, debug=debug)
