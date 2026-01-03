# app/coupang_api.py
import os
import hmac
import hashlib
import requests
from datetime import datetime, timezone
from urllib.parse import quote

def _env(k: str, d: str = "") -> str:
    return (os.getenv(k) or d).strip()

def _signed_date() -> str:
    return datetime.now(timezone.utc).strftime("%y%m%dT%H%M%SZ")

def _signature(secret_key: str, message: str) -> str:
    return hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()

def _cea_auth_header(method: str, path_with_query: str) -> str:
    access_key = _env("COUPANG_ACCESS_KEY")
    secret_key = _env("COUPANG_SECRET_KEY")
    if not access_key or not secret_key:
        raise RuntimeError("ENV 누락: COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY")

    signed_date = _signed_date()
    message = f"{signed_date}{method.upper()}{path_with_query}"
    sig = _signature(secret_key, message)

    # 흔히 쓰이는 CEA 포맷
    return f"CEA algorithm=HmacSHA256, access-key={access_key}, signed-date={signed_date}, signature={sig}"

def search_products(keyword: str, *, limit: int = 8, sub_id: str = "") -> list[dict]:
    """
    키워드로 쿠팡 파트너스 상품검색 후 상위 N개 반환
    반환 keys: id, name, price, url, image, isRocket, rating, reviews
    """
    kw = (keyword or "").strip()
    if not kw:
        return []

    if not sub_id:
        sub_id = _env("COUPANG_SUB_ID")
    if not sub_id:
        sub_id = hashlib.sha1(kw.encode("utf-8")).hexdigest()[:12]

    q = f"keyword={quote(kw)}&limit={int(limit)}&subId={quote(sub_id)}"
    path = f"/v2/providers/affiliate_open_api/apis/openapi/v1/products/search?{q}"
    url = f"https://api-gateway.coupang.com{path}"

    headers = {
        "Authorization": _cea_auth_header("GET", path),
        "Content-Type": "application/json",
    }

    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"Coupang API error {r.status_code}: {r.text[:500]}")

    data = r.json() or {}
    raw = (data.get("data") or {})

    products = raw.get("productData") or raw.get("products") or []
    out: list[dict] = []

    for p in products[: int(limit)]:
        pid = p.get("productId") or p.get("id") or ""
        name = (p.get("productName") or p.get("name") or "").strip()
        url = (p.get("productUrl") or p.get("url") or "").strip()

        if not name or not url:
            continue

        out.append({
            "id": str(pid) if pid else "",
            "name": name,
            "price": p.get("productPrice") or p.get("price") or "",
            "url": url,
            "image": p.get("productImage") or p.get("imageUrl") or p.get("image") or "",
            "isRocket": bool(p.get("isRocket") or p.get("rocket") or False),
            "rating": p.get("ratingAverage") or p.get("rating") or "",
            "reviews": p.get("reviewCount") or p.get("reviews") or "",
        })

    return out
