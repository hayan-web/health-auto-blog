import requests


def upload_media_to_wp(
    wp_url: str,
    wp_user: str,
    wp_pw: str,
    image_bytes: bytes,
    filename: str,
    timeout: int = 60,
) -> tuple[str, int]:
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
    ✅ 핵심 변경:
    - data["content_html"]가 있으면 그걸 그대로 content로 사용 (스타일 적용됨)
    - 없을 때만 fallback으로 기존 단순 조립
    """
    wp_url = wp_url.rstrip("/")
    api_endpoint = f"{wp_url}/wp-json/wp/v2/posts"

    # ✅ 1) content_html 우선
    if data.get("content_html"):
        final_html = data["content_html"]
    else:
        # fallback: 최소한의 기본 구성 (예전 방식)
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
        for i, ptxt in enumerate(raw_paras):
            if i == mid_idx:
                body_parts.append(mid_img_html)
            body_parts.append(ptag(ptxt))

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
