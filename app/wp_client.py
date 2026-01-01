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
    """
    wp_url = wp_url.rstrip("/")
    media_endpoint = f"{wp_url}/wp-json/wp/v2/media"

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": "image/png",
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
    return j["source_url"], j["id"]


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
    ✅ 최우선: data["content_html"]가 있으면 그걸 '그대로' 발행
    - 여기서 다시 문단 조립/이미지 삽입을 하면 스타일이 깨집니다.

    ✅ fallback: content_html 없을 때만 간단 조립(안전망)
    """
    wp_url = wp_url.rstrip("/")
    api_endpoint = f"{wp_url}/wp-json/wp/v2/posts"

    title = (data.get("title") or "").strip()

    # =====================================================
    # ✅ 0) 마지막 점검: content_html 우선 (가장 중요)
    # =====================================================
    content_html = (data.get("content_html") or "").strip()
    if content_html:
        final_html = content_html
        print("✅ publish_to_wp: content_html 사용(스타일 유지)")
    else:
        # ---------------------
        # fallback 조립(최소)
        # ---------------------
        raw_text = (data.get("content") or data.get("body") or "").strip()
        raw_paras = [p.strip() for p in raw_text.split("\n") if p.strip()]
        if not raw_paras:
            raw_paras = ["(본문이 비어 있어 기본 문구로 대체되었습니다.)"]

        mid_idx = max(1, len(raw_paras) // 2)

        def ptag(p: str) -> str:
            return f"<p style='margin:0 0 14px; font-size:17px; line-height:1.85; letter-spacing:-0.2px; color:#222;'>{p}</p>"

        top_html = f"""
<div style="margin-bottom:22px;">
  <img src="{hero_url}" alt="{title}" style="width:100%; border-radius:14px; box-shadow:0 6px 18px rgba(0,0,0,0.12);" />
</div>
""".strip()

        mid_img_html = f"""
<div style="margin:22px 0;">
  <img src="{body_url}" alt="{title} 관련 이미지" style="width:100%; border-radius:14px; box-shadow:0 6px 18px rgba(0,0,0,0.10);" />
</div>
""".strip()

        body_parts = []
        for i, p in enumerate(raw_paras):
            if i == mid_idx:
                body_parts.append(mid_img_html)
            body_parts.append(ptag(p))

        final_html = f"""
{top_html}
<div style="line-height:1.85; font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;">
  {''.join(body_parts)}
</div>
""".strip()

        print("⚠️ publish_to_wp: content_html 없음 → fallback 조립 사용")

    payload = {
        "title": title,
        "content": final_html,
        "status": "publish",
        "featured_media": featured_media_id,
    }

    print("📝 POST ->", api_endpoint)
    print("📝 title ->", (title or "")[:80])
    print("📝 content length ->", len(final_html))
    print("📝 featured_media ->", featured_media_id)

    res = requests.post(api_endpoint, auth=(wp_user, wp_pw), json=payload, timeout=timeout)
    print("📝 WP status:", res.status_code)
    print("📝 WP resp:", (res.text or "")[:500])

    if res.status_code != 201:
        raise RuntimeError(f"워드프레스 글 발행 실패: {res.status_code} / {res.text}")

    return res.json()["id"]
