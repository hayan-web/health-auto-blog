import requests
from typing import Tuple


def upload_media_to_wp(
    wp_url: str,
    wp_user: str,
    wp_pw: str,
    image_bytes: bytes,
    filename: str,
    timeout: int = 60,
) -> Tuple[str, int]:
    """
    WP 미디어 업로드
    - JPG로 업로드(권장): Imsanity가 변환하면서 URL이 바뀌는 문제를 회피
    - 업로드 후 /media/{id} 재조회로 "최종 source_url" 확보(플러그인 후처리 대비)
    반환: (source_url, media_id)
    """
    wp_url = wp_url.rstrip("/")
    media_endpoint = f"{wp_url}/wp-json/wp/v2/media"

    # 확장자/헤더 정리 (jpg 고정)
    if not filename.lower().endswith((".jpg", ".jpeg")):
        filename = f"{filename.rsplit('.', 1)[0]}.jpg" if "." in filename else f"{filename}.jpg"

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": "image/jpeg",
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
    media_id = j["id"]

    # ✅ 플러그인(예: Imsanity)이 업로드 직후 파일/URL을 바꿔도 최종 URL을 다시 가져오기
    try:
        get_ep = f"{wp_url}/wp-json/wp/v2/media/{media_id}"
        res2 = requests.get(get_ep, auth=(wp_user, wp_pw), timeout=timeout)
        if res2.status_code == 200:
            j2 = res2.json()
            final_url = j2.get("source_url") or j.get("source_url")
            return final_url, media_id
    except Exception as e:
        print("⚠️ media 재조회 실패(무시하고 진행):", e)

    return j["source_url"], media_id


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

    # ✅ main.py에서 완성 HTML을 content_html로 넘기면 그걸 우선 사용
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
