import base64
import requests
from typing import Tuple, Optional, List


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


def upload_media_to_wp(wp_url: str, username: str, app_password: str, img_bytes: bytes, file_name: str):
    """
    WordPress REST API로 미디어 업로드.
    - 이미지 bytes 매직바이트로 MIME 감지 -> Content-Type 정확히 설정 (415 방지)
    - 파일 확장자도 MIME에 맞게 자동 보정
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
    resp = requests.post(media_endpoint, headers=headers, data=img_bytes, timeout=90)

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Media upload failed: {resp.status_code} {resp.text[:500]}")

    j = resp.json()
    return j.get("source_url"), j.get("id")


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
    ✅ data["content_html"]이 있으면 그대로 발행
    ✅ data["categories"] (list[int])가 있으면 WP 카테고리 지정
    """
    wp_url = wp_url.rstrip("/")
    api_endpoint = f"{wp_url}/wp-json/wp/v2/posts"

    if data.get("content_html"):
        final_html = data["content_html"]
    else:
        raw_paras = [p.strip() for p in (data.get("content") or "").split("\n") if p.strip()]
        if not raw_paras:
            raise RuntimeError("본문(content)이 비어 있습니다.")

        mid_idx = max(1, len(raw_paras) // 2)

        def ptag(p: str) -> str:
            return f"<p style='margin-bottom:1.6em; font-size:18px; color:#333;'>{p}</p>"

        top_html = f"""
<div style="margin-bottom:28px;">
  <img src="{hero_url}" alt="{data.get("title","")}" style="width:100%; border-radius:14px; box-shadow:0 4px 14px rgba(0,0,0,0.14);" />
</div>
"""

        mid_img_html = f"""
<div style="margin:28px 0;">
  <img src="{body_url}" alt="{data.get("title","")} 관련 이미지" style="width:100%; border-radius:14px; box-shadow:0 4px 14px rgba(0,0,0,0.12);" />
</div>
"""

        body_parts = []
        for i, p in enumerate(raw_paras):
            if i == mid_idx:
                body_parts.append(mid_img_html)
            body_parts.append(ptag(p))

        final_html = f"""
{top_html}
<div style="line-height:1.9; font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;">
  {''.join(body_parts)}
</div>
"""

    payload = {
        "title": data.get("title", ""),
        "content": final_html,
        "status": "publish",
        "featured_media": featured_media_id,
    }

    # ✅ 카테고리 지정
    cats = data.get("categories")
    if isinstance(cats, list) and all(isinstance(x, int) for x in cats):
        payload["categories"] = cats

    print("📝 POST ->", api_endpoint)
    print("📝 title ->", (payload["title"] or "")[:80])

    res = requests.post(api_endpoint, auth=(wp_user, wp_pw), json=payload, timeout=timeout)
    print("📝 WP status:", res.status_code)
    print("📝 WP resp:", (res.text or "")[:500])

    if res.status_code != 201:
        raise RuntimeError(f"워드프레스 글 발행 실패: {res.status_code} / {res.text}")

    return res.json()["id"]
