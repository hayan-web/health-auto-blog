# app/wp_client.py
from __future__ import annotations

import base64
from typing import Tuple, Optional, Dict, Any, List

import requests


def _sniff_image_mime_and_ext(data: bytes, fallback_ext: str = "png") -> Tuple[str, str]:
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
    timeout: int = 90,
) -> Tuple[str, int]:
    """
    WordPress REST API로 미디어 업로드.
    - 이미지 bytes의 매직바이트로 MIME을 감지해 Content-Type을 맞춥니다.
    - 파일 확장자도 MIME에 맞게 자동 보정합니다.
    """
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
    resp = requests.post(media_endpoint, headers=headers, data=img_bytes, timeout=timeout)

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Media upload failed: {resp.status_code} {resp.text[:500]}")

    j = resp.json()
    return j.get("source_url"), int(j.get("id"))


def publish_to_wp(
    wp_url: str,
    wp_user: str,
    wp_pw: str,
    data: Dict[str, Any],
    hero_url: str,
    body_url: str,
    featured_media_id: int,
    category_ids: Optional[List[int]] = None,
    timeout: int = 60,
) -> int:
    """
    - data["content_html"]이 있으면 그걸 그대로 사용
    - content는 {"raw": ...}로 전달해서 WP가 HTML을 텍스트로 이스케이프하는 문제를 방지
    """
    wp_url = wp_url.rstrip("/")
    api_endpoint = f"{wp_url}/wp-json/wp/v2/posts"

    if data.get("content_html"):
        final_html = data["content_html"]
    else:
        raise RuntimeError("content_html이 비어 있습니다. formatter_v2 결과를 확인하세요.")

    payload: Dict[str, Any] = {
        "title": {"raw": data.get("title", "")},
        "content": {"raw": final_html},
        "status": "publish",
        "featured_media": int(featured_media_id),
    }

    if category_ids:
        payload["categories"] = [int(x) for x in category_ids if x]

    print("📝 POST ->", api_endpoint)
    print("📝 title ->", (data.get("title", "") or "")[:80])

    res = requests.post(api_endpoint, auth=(wp_user, wp_pw), json=payload, timeout=timeout)
    print("📝 WP status:", res.status_code)
    print("📝 WP resp:", (res.text or "")[:500])

    if res.status_code != 201:
        raise RuntimeError(f"워드프레스 글 발행 실패: {res.status_code} / {res.text}")

    return int(res.json()["id"])
