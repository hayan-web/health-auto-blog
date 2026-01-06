import base64
from typing import Tuple, Optional

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


def _force_filename_ext(file_name: str, ext: str) -> str:
    if file_name:
        base = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
        return f"{base}.{ext}"
    return f"image.{ext}"


def _try_convert_to_png(img_bytes: bytes) -> Optional[bytes]:
    """서버가 WEBP 등을 거부할 때를 대비한 PNG 변환(가능하면). 실패하면 None."""
    try:
        from io import BytesIO
        from PIL import Image  # type: ignore

        im = Image.open(BytesIO(img_bytes))
        im = im.convert("RGBA") if im.mode in ("P", "LA", "RGBA") else im.convert("RGB")

        buf = BytesIO()
        im.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:
        return None


def upload_media_to_wp(
    wp_url: str,
    username: str,
    app_password: str,
    img_bytes: bytes,
    file_name: str,
):
    """
    WordPress REST API로 미디어 업로드(✅ 415 방지 버전)
    - 1차: multipart/form-data(files=...) 업로드 (대부분의 서버/보안설정에서 이 방식만 허용)
    - 2차: raw bytes 업로드 fallback
    - 415 발생 + webp면 png 변환 후 재시도
    """
    wp_url = (wp_url or "").rstrip("/")
    if not wp_url:
        raise RuntimeError("wp_url is empty")

    if not isinstance(img_bytes, (bytes, bytearray)) or not img_bytes:
        raise RuntimeError("img_bytes is empty or not bytes")

    auth = base64.b64encode(f"{username}:{app_password}".encode("utf-8")).decode("utf-8")
    media_endpoint = f"{wp_url}/wp-json/wp/v2/media"

    mime, ext = _sniff_image_mime_and_ext(bytes(img_bytes), fallback_ext="png")

    # sniff 실패 시(=octet-stream)라도 서버가 415를 내는 경우가 많아서 png로 강제 시도
    if mime == "application/octet-stream":
        mime, ext = "image/png", "png"

    file_name = _force_filename_ext(file_name, ext)

    base_headers = {
        "Authorization": f"Basic {auth}",
        "Accept": "application/json",
        "User-Agent": "health-auto-blog/1.0",
    }

    def _ok(resp: requests.Response) -> bool:
        return resp.status_code in (200, 201)

    # ---------------------------
    # 1) multipart 업로드 (권장/기본)
    # ---------------------------
    try:
        files = {"file": (file_name, bytes(img_bytes), mime)}
        resp = requests.post(media_endpoint, headers=base_headers, files=files, timeout=90)

        if _ok(resp):
            j = resp.json()
            return j.get("source_url"), j.get("id")

        # 415 + webp → png 변환 후 multipart 재시도
        if resp.status_code == 415 and mime == "image/webp":
            png = _try_convert_to_png(bytes(img_bytes))
            if png:
                files = {"file": (_force_filename_ext(file_name, "png"), png, "image/png")}
                resp2 = requests.post(media_endpoint, headers=base_headers, files=files, timeout=90)
                if _ok(resp2):
                    j = resp2.json()
                    return j.get("source_url"), j.get("id")

        # multipart 실패 시 raw로 fallback
        last_status = resp.status_code
        last_text = (resp.text or "")[:500]
    except Exception as e:
        last_status = -1
        last_text = f"multipart exception: {e}"

    # ---------------------------
    # 2) raw bytes 업로드 (fallback)
    # ---------------------------
    try:
        headers_raw = dict(base_headers)
        headers_raw.update(
            {
                "Content-Disposition": f'attachment; filename="{file_name}"',
                "Content-Type": mime,
            }
        )
        resp = requests.post(media_endpoint, headers=headers_raw, data=bytes(img_bytes), timeout=90)

        if _ok(resp):
            j = resp.json()
            return j.get("source_url"), j.get("id")

        # 415 + webp → png 변환 후 raw 재시도
        if resp.status_code == 415 and mime == "image/webp":
            png = _try_convert_to_png(bytes(img_bytes))
            if png:
                headers_raw.update(
                    {
                        "Content-Disposition": f'attachment; filename="{_force_filename_ext(file_name, "png")}"',
                        "Content-Type": "image/png",
                    }
                )
                resp2 = requests.post(media_endpoint, headers=headers_raw, data=png, timeout=90)
                if _ok(resp2):
                    j = resp2.json()
                    return j.get("source_url"), j.get("id")

        raise RuntimeError(f"Media upload failed: {resp.status_code} {((resp.text or '')[:500])}")
    except Exception as e:
        raise RuntimeError(
            "Media upload failed.\n"
            f"- multipart last: {last_status} {last_text}\n"
            f"- raw error: {e}"
        )


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
    - publish_to_wp는 data["content_html"]이 있으면 그걸 그대로 사용
    - 없으면 기존 content 기반으로 기본 HTML 구성
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

    print("📝 POST ->", api_endpoint)
    print("📝 title ->", (payload["title"] or "")[:80])

    res = requests.post(api_endpoint, auth=(wp_user, wp_pw), json=payload, timeout=timeout)
    print("📝 WP status:", res.status_code)
    print("📝 WP resp:", (res.text or "")[:500])

    if res.status_code != 201:
        raise RuntimeError(f"워드프레스 글 발행 실패: {res.status_code} / {res.text}")

    return res.json()["id"]
