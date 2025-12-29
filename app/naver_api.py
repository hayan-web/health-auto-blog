import requests


NAVER_BLOG_SEARCH_URL = "https://openapi.naver.com/v1/search/blog.json"


def naver_blog_total_count(client_id: str, client_secret: str, query: str, timeout: int = 10) -> int:
    """
    네이버 블로그 검색 API로 해당 키워드의 '총 검색 결과 수(total)'를 가져옵니다.
    - total이 클수록 '수요/관심' 지표로 간주(완벽한 검색량은 아니지만 자동화용으로 유용)
    """
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    params = {
        "query": query,
        "display": 1,
        "start": 1,
        "sort": "sim",
    }
    r = requests.get(NAVER_BLOG_SEARCH_URL, headers=headers, params=params, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"Naver API error {r.status_code}: {r.text[:200]}")
    data = r.json()
    total = int(data.get("total", 0))
    return total
