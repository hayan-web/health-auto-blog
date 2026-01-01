# app/wp_client.py
import requests


def upload_media_to_wp(
    wp_url: str,
    wp_user: str,
    wp_pw: str,
    image_bytes: bytes,
    filename: str,
    timeout: int = 60,
) -> tuple[str, int]:
    """
    WP 미디어 업로드 (RAW binary + headers 방식: 415 방지)
    반환: (source_url, media_id)

    ✅ WP/플러그인/테마 환경에 따라 source_url 키가 없거나 비어있는 경우가 있어
       guid.rendered / media_details.sizes.full.source_url 까지 fallback 처리
    """
    wp_url = wp_url.rstrip("/")
    media_endpoint = f"{wp_url}/wp-json/wp/v2/media"

    # 파일 확장자 기반으로 Content-Type 보정(서버가 확장자 보고 처리하는 경우가 있음)
    lower = (filename or "").lower()
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        ctype = "image/jpeg"
    elif lower.endswith(".webp"):
        ctype = "image/webp"
    else:
        ctype = "image/png"

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": ctype,
    }

    res = requests.post(
        media_endpoint,
        auth=(wp_user, wp_pw),
        headers=headers,
        data=image_bytes,
        timeout=timeout,
    )

    print("🖼️ WP media status:", res.status_code)
    print("🖼️ WP media resp:", (res.text or "")[:300])

    if res.status_code not in (200, 201):
        raise RuntimeError(f"미디어 업로드 실패: {res.status_code} / {res.text}")

    j = res.json()
    media_id = j.get("id")

    # ✅ URL fallback
    url = (
        j.get("source_url")
        or (j.get("guid") or {}).get("rendered")
        or (((j.get("media_details") or {}).get("sizes") or {}).get("full") or {}).get("source_url")
    )

    if not media_id or not url or not str(url).startswith("http"):
        raise RuntimeError(
            f"미디어 업로드는 성공했지만 URL 파싱 실패. id={media_id}, url={url}, keys={list(j.keys())}"
        )

    return str(url), int(media_id)


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
    ✅ content_html이 있으면 그걸 그대로 사용(포맷터 스타일 유지)
    - 이미지 2장: 포맷터가 이미 넣었으면 중복 삽입 안 함
    - featured_media 지정
    반환: post_id
    """
    wp_url = wp_url.rstrip("/")

    # ✅ formatter_v2 결과를 최우선으로 사용
    final_html = (data.get("content_html") or "").strip()
    if not final_html:
        # fallback: 기존 content를 단순 p로라도 감싸서 발행
        raw = (data.get("content") or data.get("body") or "").strip()
        if not raw:
            raise RuntimeError("본문(content/content_html)이 비어 있습니다.")
        paras = [p.strip() for p in raw.split("\n") if p.strip()]

        def ptag(p: str) -> str:
            return f"<p>{p}</p>"

        final_html = "\n".join(ptag(p) for p in paras)

    api_endpoint = f"{wp_url}/wp-json/wp/v2/posts"
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
