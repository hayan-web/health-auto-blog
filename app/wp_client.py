# app/wp_client.py
import time
import requests


def _get_media_url(wp_url: str, wp_user: str, wp_pw: str, media_id: int, timeout: int = 30) -> str:
    wp_url = wp_url.rstrip("/")
    endpoint = f"{wp_url}/wp-json/wp/v2/media/{media_id}"

    r = requests.get(endpoint, auth=(wp_user, wp_pw), timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"미디어 조회 실패: {r.status_code} / {r.text}")

    j = r.json()
    url = (
        j.get("source_url")
        or (j.get("guid") or {}).get("rendered")
        or (((j.get("media_details") or {}).get("sizes") or {}).get("full") or {}).get("source_url")
    )
    if not url:
        raise RuntimeError(f"미디어 URL 파싱 실패. keys={list(j.keys())}")
    return str(url)


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
    ✅ Imsanity가 PNG→JPG 변환/리네임을 하더라도 최종 source_url을 다시 조회해서 반환
    반환: (final_source_url, media_id)
    """
    wp_url = wp_url.rstrip("/")
    media_endpoint = f"{wp_url}/wp-json/wp/v2/media"

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
    if not media_id:
        raise RuntimeError("미디어 업로드 응답에 id가 없습니다.")

    # ✅ 변환(리사이즈/포맷변환)이 직후에 적용되면 source_url이 바뀔 수 있으니, 최종 URL 재조회
    # (Imsanity가 서버에서 처리하는데 약간의 시간이 걸리는 환경도 있어 retry)
    last_url = None
    for i in range(1, 6):
        try:
            url = _get_media_url(wp_url, wp_user, wp_pw, int(media_id))
            last_url = url
            if url.startswith("http"):
                return url, int(media_id)
        except Exception as e:
            print(f"⚠️ media url 재조회 실패({i}/5): {e}")
        time.sleep(1)

    raise RuntimeError(f"미디어 업로드는 성공했지만 최종 URL 조회 실패. last_url={last_url}")


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
    ✅ content_html이 있으면 그걸 그대로 사용(스타일 유지)
    """
    wp_url = wp_url.rstrip("/")

    final_html = (data.get("content_html") or "").strip()
    if not final_html:
        raw = (data.get("content") or data.get("body") or "").strip()
        if not raw:
            raise RuntimeError("본문(content/content_html)이 비어 있습니다.")
        paras = [p.strip() for p in raw.split("\n") if p.strip()]
        final_html = "\n".join(f"<p>{p}</p>" for p in paras)

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
