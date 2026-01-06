import base64
from typing import Any, Optional, Tuple

import requests


def _sniff_image_mime_and_ext(data: bytes, fallback_ext: str = "png"):
    if not data:
        return "application/octet-stream", fallback_ext
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg", "jpg"
    if data.startswith(b"RIFF") and b"WEBP" in data[8:16]:
        return "image/webp", "webp"
    return "application/octet-stream", fallback_ext


def upload_media_to_wp(
    wp_url: str,
    username: str,
    app_password: str,
    img_bytes: bytes,
    file_name: str,
) -> Tuple[str, int]:
    """WordPress REST API로 미디어 업로드 (415 방지: MIME/확장자 자동 감지)."""
    wp_url = wp_url.rstrip("/")
    auth = base64.b64encode(f"{username}:{app_password}".encode("utf-8")).decode("utf-8")
    mime, ext = _sniff_image_mime_and_ext(img_bytes, fallback_ext="png")

    # file_name 확장자 보정
    if file_name:
        base = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
        file_name = f"{base}.{ext}"
    else:
        file_name = f"image.{ext}"

    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Disposition": f'attachment; filename="{file_name}"',
        "Content-Type": mime,
    }

    media_endpoint = f"{wp_url}/wp-json/wp/v2/media"
    resp = requests.post(media_endpoint, headers=headers, data=img_bytes, timeout=90)

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Media upload failed: {resp.status_code} {resp.text[:500]}")

    j = resp.json()
    return j.get("source_url"), int(j.get("id"))


def ensure_category_id(
    wp_url: str,
    wp_user: str,
    wp_pw: str,
    *,
    name: str,
    slug: Optional[str] = None,
) -> Optional[int]:
    """
    카테고리 이름으로 ID 조회 → 없으면 생성.
    - 관리자 권한이면 대부분 생성 가능
    """
    if not name:
        return None

    wp_url = wp_url.rstrip("/")
    base = f"{wp_url}/wp-json/wp/v2/categories"

    try:
        # search로 후보 찾기
        r = requests.get(base, auth=(wp_user, wp_pw), params={"search": name, "per_page": 100}, timeout=20)
        if r.status_code == 200 and isinstance(r.json(), list):
            for it in r.json():
                if isinstance(it, dict) and (it.get("name") == name):
                    return int(it.get("id"))
    except Exception:
        pass

    # 없으면 생성
    payload: dict[str, Any] = {"name": name}
    if slug:
        payload["slug"] = slug

    try:
        r2 = requests.post(base, auth=(wp_user, wp_pw), json=payload, timeout=20)
        if r2.status_code in (200, 201) and isinstance(r2.json(), dict):
            return int(r2.json().get("id"))
        # 생성 실패는 치명적이지 않게 None 처리
        return None
    except Exception:
        return None


def publish_to_wp(
    wp_url: str,
    wp_user: str,
    wp_pw: str,
    data: dict,
    hero_url: str,
    body_url: str,
    featured_media_id: int,
    timeout: int = 60,
) -> int:
    """
    - data["content_html"] 있으면 그대로 사용
    - data["categories"] (list[int]) 있으면 카테고리까지 지정
    """
    wp_url = wp_url.rstrip("/")
    api_endpoint = f"{wp_url}/wp-json/wp/v2/posts"

    final_html = data.get("content_html") or ""
    if not final_html:
        raise RuntimeError("content_html이 비어 있습니다. (formatter 결과를 확인하세요)")

    payload: dict[str, Any] = {
        "title": data.get("title", ""),
        "content": final_html,
        "status": "publish",
        "featured_media": int(featured_media_id),
    }

    if isinstance(data.get("categories"), list) and data["categories"]:
        payload["categories"] = data["categories"]

    print("📝 POST ->", api_endpoint)
    print("📝 title ->", (payload["title"] or "")[:80])

    res = requests.post(api_endpoint, auth=(wp_user, wp_pw), json=payload, timeout=timeout)
    print("📝 WP status:", res.status_code)
    print("📝 WP resp:", (res.text or "")[:500])

    if res.status_code != 201:
        raise RuntimeError(f"워드프레스 글 발행 실패: {res.status_code} / {res.text}")

    return int(res.json()["id"])
