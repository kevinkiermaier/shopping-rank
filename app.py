from flask import Flask, render_template, request, jsonify
import requests
import re
import os
from dotenv import load_dotenv

load_dotenv()  # 로컬 .env 파일 자동 로드

app = Flask(__name__)

# API 키는 환경변수에서 읽음 (GitHub에 절대 직접 입력 X)
# 로컬: .env 파일에 입력
# Railway: 대시보드 Variables에서 설정
NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")


def clean_html(text):
    return re.sub(r"<[^>]+>", "", text)


def calc_score(item, rank):
    """상품 데이터로 각종 점수 계산"""
    review = int(item.get("reviewCount", 0) or 0)
    price = int(item.get("lprice", 0) or 0)

    # 리뷰 점수 (0~5)
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

    # 판매 점수 (리뷰수 기반 추정)
    if review >= 5000:
        sales_score = 5
    elif review >= 1000:
        sales_score = 4
    elif review >= 100:
        sales_score = 3
    else:
        sales_score = 2

    # 인기도 종합점수
    popularity = round((review_score + click_score + sales_score) / 3 * 2) / 2

    # 최신성 (상품 등록일 기반 - API에서 미제공, 기본값)
    recency = 2

    # 종합 점수
    total = round((review_score * 0.3 + click_score * 0.3 + sales_score * 0.4))
    total = min(5, max(1, total))

    return {
        "total": total,
        "click_score": click_score,
        "sales_score": sales_score,
        "review_score": review_score,
        "popularity": popularity,
        "recency": recency,
        "penalty": 0,
        "relevance": 1,
        "reliability": 1,
    }


def search_naver(keyword, display=100):
    url = "https://openapi.naver.com/v1/search/shop.json"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    params = {
        "query": keyword,
        "display": display,
        "start": 1,
        "sort": "sim",  # 정확도순
    }

    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        res.raise_for_status()
        data = res.json()
        items = data.get("items", [])

        results = []
        for i, item in enumerate(items, 1):
            title = clean_html(item.get("title", ""))
            mall = item.get("mallName", "")
            price = int(item.get("lprice", 0) or 0)
            image = item.get("image", "")
            link = item.get("link", "")
            category = " > ".join(
                filter(
                    None,
                    [
                        item.get("category1", ""),
                        item.get("category2", ""),
                        item.get("category3", ""),
                        item.get("category4", ""),
                    ],
                )
            )
            review = int(item.get("reviewCount", 0) or 0)

            scores = calc_score(item, i)

            results.append(
                {
                    "rank": i,
                    "image": image,
                    "title": title,
                    "link": link,
                    "mall": mall,
                    "sales_7d": "-",
                    "sales_6m": "-",
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
                }
            )
        return results, None

    except requests.exceptions.HTTPError as e:
        status = e.response.status_code
        body = e.response.text[:200]
        id_loaded = "있음" if NAVER_CLIENT_ID else "없음(빈값)"
        secret_loaded = "있음" if NAVER_CLIENT_SECRET else "없음(빈값)"
        return [], f"HTTP {status} 오류 | ID환경변수:{id_loaded} | SECRET환경변수:{secret_loaded} | 응답:{body}"
    except Exception as e:
        return [], f"오류 발생: {str(e)}"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/debug-env")
def debug_env():
    import os
    client_id = os.environ.get("NAVER_CLIENT_ID", "")
    client_secret = os.environ.get("NAVER_CLIENT_SECRET", "")
    return jsonify({
        "NAVER_CLIENT_ID": client_id[:4] + "****" if client_id else "비어있음",
        "NAVER_CLIENT_SECRET": client_secret[:4] + "****" if client_secret else "비어있음",
        "all_env_keys": [k for k in os.environ.keys() if "NAVER" in k]
    })


@app.route("/search", methods=["POST"])
def search():
    data = request.json
    keyword = data.get("keyword", "").strip()
    platform = data.get("platform", "naver")

    if not keyword:
        return jsonify({"error": "키워드를 입력하세요.", "results": []})

    if platform == "naver":
        results, error = search_naver(keyword)
        if error:
            return jsonify({"error": error, "results": []})
        return jsonify({"results": results, "total": len(results)})

    elif platform == "coupang":
        # 쿠팡은 공개 API 없음 → 추후 구현
        return jsonify({"error": "쿠팡 검색은 준비 중입니다.", "results": []})

    return jsonify({"error": "지원하지 않는 플랫폼입니다.", "results": []})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    debug = os.environ.get("FLASK_ENV") != "production"
    app.run(host="0.0.0.0", port=port, debug=debug)
