import re
import uuid

from app.config import Settings
from app.ai_openai import (
    make_openai_client,
    generate_blog_post,
    generate_thumbnail_title,
)
from app.ai_gemini_image import (
    make_gemini_client,
    generate_nanobanana_image_png_bytes,
)
from app.thumb_overlay import to_square_1024, add_title_to_image
from app.wp_client import upload_media_to_wp, publish_to_wp
from app.store import load_state, save_state, add_history_item
from app.dedupe import pick_retry_reason, _title_fingerprint
from app.keyword_picker import pick_keyword_by_naver

# ✅ 문단 스타일/수익화
from app.formatter import format_post_body
from app.monetize_adsense import inject_ads
from app.monetize_coupang import inject_coupang


def make_ascii_filename(prefix: str, ext: str = "png") -> str:
    """헤더에 넣어도 안전한 ASCII 파일명 생성 (한글/특수문자 없음)"""
    uid = uuid.uuid4().hex[:10]
    prefix = re.sub(r"[^a-zA-Z0-9_-]+", "-", (prefix or "img")).strip("-")
    if not prefix:
        prefix = "img"
    return f"{prefix}-{uid}.{ext}"


def _fallback_html(title: str, hero_url: str, body_url: str, raw_text: str) -> str:
    """formatter가 실패하거나 sections 구조가 없을 때 최소 스타일 HTML"""
    paras = [p.strip() for p in (raw_text or "").split("\n") if p.strip()]
    if not paras:
        paras = ["(본문이 비어 있어 기본 문구로 대체되었습니다.)"]

    mid_idx = max(1, len(paras) // 2)

    def ptag(p: str) -> str:
        p = (
            p.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        return f"<p style='margin:0 0 14px; font-size:17px; line-height:1.85; letter-spacing:-0.2px; color:#222;'>{p}</p>"

    top_img = f"""
<div style="margin:0 0 22px;">
  <img src="{hero_url}" alt="{title}" style="width:100%; border-radius:14px; box-shadow:0 6px 18px rgba(0,0,0,0.12);" />
</div>
""".strip()

    mid_img = f"""
<div style="margin:22px 0;">
  <img src="{body_url}" alt="{title} 관련 이미지" style="width:100%; border-radius:14px; box-shadow:0 6px 18px rgba(0,0,0,0.10);" />
</div>
""".strip()

    body_parts = []
    for i, p in enumerate(paras):
        if i == mid_idx:
            body_parts.append(mid_img)
        body_parts.append(ptag(p))

    return f"""
{top_img}
<div style="font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;">
  <h2 style="margin:6px 0 14px; font-size:22px; line-height:1.35; letter-spacing:-0.4px;">{title}</h2>
  {''.join(body_parts)}
</div>
""".strip()


def run() -> None:
    s = Settings()

    # 1) 클라이언트 준비
    openai_client = make_openai_client(s.OPENAI_API_KEY)
    gemini_client = make_gemini_client(s.GOOGLE_API_KEY)

    # 2) 중복 방지 state 로드
    state = load_state()
    history = state.get("history", [])

    # 3) 네이버 기반 키워드 선정
    keyword, debug = pick_keyword_by_naver(
        s.NAVER_CLIENT_ID, s.NAVER_CLIENT_SECRET, history
    )
    print("🔎 선택된 키워드:", keyword)
    print("🧾 키워드 점수(상위 3):", (debug.get("scored") or [])[:3])

    # 4) 글 생성(OpenAI) + 중복 회피
    MAX_RETRY = 3
    post = None

    for i in range(1, MAX_RETRY + 1):
        candidate = generate_blog_post(openai_client, s.OPENAI_MODEL, keyword)

        dup, reason = pick_retry_reason(candidate.get("title", ""), history)
        if dup:
            print(f"♻️ 중복 감지({reason}) → 재생성 {i}/{MAX_RETRY}")
            continue

        post = candidate
        break

    if not post:
        raise RuntimeError("중복 회피 실패: 재시도 횟수 초과")

    # 5) 썸네일용 짧은 타이틀
    thumb_title = generate_thumbnail_title(openai_client, s.OPENAI_MODEL, post["title"])
    print("🧩 썸네일 타이틀:", thumb_title)

    # 6) 이미지 2장 생성 (NanoBanana)
    print("🎨 Gemini 이미지(상단/대표) 생성 중...")
    hero_img = generate_nanobanana_image_png_bytes(
        gemini_client, s.GEMINI_IMAGE_MODEL, post["img_prompt"]
    )

    print("🎨 Gemini 이미지(중간) 생성 중...")
    body_img = generate_nanobanana_image_png_bytes(
        gemini_client,
        s.GEMINI_IMAGE_MODEL,
        post["img_prompt"]
        + ", single scene, no collage, different composition, different angle, no text, square 1:1",
    )

    # 7) 1:1 고정 + 썸네일 타이틀 오버레이
    hero_img = to_square_1024(hero_img)
    body_img = to_square_1024(body_img)

    hero_img_titled = add_title_to_image(hero_img, thumb_title)
    hero_img_titled = to_square_1024(hero_img_titled)

    # 8) WP 미디어 업로드
    hero_name = make_ascii_filename("featured")
    body_name = make_ascii_filename("body")

    hero_url, hero_media_id = upload_media_to_wp(
        s.WP_URL, s.WP_USERNAME, s.WP_APP_PASSWORD, hero_img_titled, hero_name
    )
    body_url, _ = upload_media_to_wp(
        s.WP_URL, s.WP_USERNAME, s.WP_APP_PASSWORD, body_img, body_name
    )

    # ==========================================================
    # 9) 본문 스타일 적용 + 쿠팡/애드센스 삽입 (발행 전에!)
    # - publish_to_wp()에서 data["content_html"] 우선 사용 필요
    # ==========================================================
    try:
        styled_html = format_post_body(
            title=post["title"],
            hero_url=hero_url,
            body_url=body_url,
            intro=post.get("intro", ""),
            sections=post.get("sections", []),
            outro=post.get("outro", ""),
            disclaimer="의학적 진단이 아닌 일반 정보입니다. 증상이 지속되면 전문가 상담을 권장드립니다.",
        )
    except Exception as e:
        print("⚠️ format_post_body 실패 → fallback HTML 사용:", str(e)[:200])
        raw = post.get("content") or post.get("body") or ""
        styled_html = _fallback_html(post["title"], hero_url, body_url, raw)

    # ✅ 쿠팡: 대가성 문구를 "최상단"에 붙이도록 inject_coupang에서 처리되어야 함
    styled_html = inject_coupang(styled_html, keyword)

    # ✅ 애드센스: ENV 설정된 경우만 삽입
    styled_html = inject_ads(styled_html)

    # ✅ WP 발행 시 이 HTML을 그대로 사용
    post["content_html"] = styled_html

    # 10) WP 글 발행
    post_id = publish_to_wp(
        s.WP_URL,
        s.WP_USERNAME,
        s.WP_APP_PASSWORD,
        post,
        hero_url,
        body_url,
        featured_media_id=hero_media_id,
    )

    # 11) 히스토리 저장
    state = add_history_item(
        state,
        {
            "post_id": post_id,
            "keyword": post.get("keyword", keyword),
            "title": post["title"],
            "title_fp": _title_fingerprint(post["title"]),
        },
    )
    save_state(state)

    print(f"✅ 발행 완료! post_id={post_id}")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"❌ 시스템 종료: {e}")
        raise
