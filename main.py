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

# ✅ 레이아웃 / 수익화
from app.formatter_v2 import format_post_v2
from app.monetize_adsense import inject_adsense_slots
from app.monetize_coupang import inject_coupang


S = Settings()


def make_ascii_filename(prefix: str, ext: str = "png") -> str:
    uid = uuid.uuid4().hex[:10]
    prefix = re.sub(r"[^a-zA-Z0-9_-]+", "-", (prefix or "img")).strip("-")
    if not prefix:
        prefix = "img"
    return f"{prefix}-{uid}.{ext}"


def run() -> None:
    # =========================
    # 0) 기본 준비
    # =========================
    s = Settings()

    openai_client = make_openai_client(s.OPENAI_API_KEY)
    gemini_client = make_gemini_client(s.GOOGLE_API_KEY)

    state = load_state()
    history = state.get("history", [])

    # =========================
    # 1) 키워드 선정
    # =========================
    keyword, debug = pick_keyword_by_naver(
        s.NAVER_CLIENT_ID, s.NAVER_CLIENT_SECRET, history
    )
    print("🔎 선택된 키워드:", keyword)
    print("🧾 키워드 점수(상위 3):", (debug.get("scored") or [])[:3])

    # =========================
    # 2) 글 생성 + 중복 회피
    # =========================
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

    # =========================
    # 3) 썸네일 타이틀
    # =========================
    thumb_title = generate_thumbnail_title(
        openai_client, s.OPENAI_MODEL, post["title"]
    )
    print("🧩 썸네일 타이틀:", thumb_title)

    # =========================
    # 4) 이미지 생성 (1:1, 단일 장면)
    # =========================
    hero_prompt = (post.get("img_prompt") or "").strip()
    if not hero_prompt:
        hero_prompt = (
            f"{keyword} 주제의 정보형 블로그 일러스트, "
            "single scene, no collage, no text, square 1:1"
        )

    body_prompt = hero_prompt + ", different angle, square 1:1"

    hero_img = generate_nanobanana_image_png_bytes(
        gemini_client, s.GEMINI_IMAGE_MODEL, hero_prompt
    )
    body_img = generate_nanobanana_image_png_bytes(
        gemini_client, s.GEMINI_IMAGE_MODEL, body_prompt
    )

    hero_img = to_square_1024(hero_img)
    body_img = to_square_1024(body_img)

    hero_img_titled = add_title_to_image(hero_img, thumb_title)
    hero_img_titled = to_square_1024(hero_img_titled)

    # =========================
    # 5) WP 미디어 업로드
    # =========================
    hero_name = make_ascii_filename("featured")
    body_name = make_ascii_filename("body")

    hero_url, hero_media_id = upload_media_to_wp(
        s.WP_URL, s.WP_USERNAME, s.WP_APP_PASSWORD,
        hero_img_titled, hero_name
    )
    body_url, _ = upload_media_to_wp(
        s.WP_URL, s.WP_USERNAME, s.WP_APP_PASSWORD,
        body_img, body_name
    )

    # =========================
    # 6) A안 레이아웃 HTML 생성
    # =========================
    html = format_post_v2(
        title=post["title"],
        keyword=keyword,
        hero_url=hero_url,
        body_url=body_url,
        disclosure_html="",
        summary_bullets=post.get("summary_bullets"),
        sections=post.get("sections") or [],
        warning_bullets=post.get("warning_bullets"),
        checklist_bullets=post.get("checklist_bullets"),
        outro=post.get("outro", ""),
    )

    # =========================
    # 7) 쿠팡 삽입 + 대가성 문구(조건부)
    # =========================
    html_after_coupang, coupang_inserted = inject_coupang(html, keyword=keyword)

    if coupang_inserted:
        disclosure = (
            "이 포스팅은 쿠팡 파트너스 활동의 일환으로, "
            "이에 따른 일정액의 수수료를 제공받습니다."
        )
        html_after_coupang = html_after_coupang.replace(
            '<div class="wrap">',
            f'<div class="wrap">\n  <div class="disclosure">{disclosure}</div>',
            1,
        )

    html = html_after_coupang

    # =========================
    # 8) 애드센스 수동 광고 3슬롯 삽입
    # =========================
    html = inject_adsense_slots(html)

    # =========================
    # 9) WP 발행 (content_html 그대로)
    # =========================
    post["content_html"] = html

    post_id = publish_to_wp(
        s.WP_URL,
        s.WP_USERNAME,
        s.WP_APP_PASSWORD,
        post,
        hero_url,
        body_url,
        featured_media_id=hero_media_id,
    )

    # =========================
    # 10) 히스토리 저장
    # =========================
    state = add_history_item(
        state,
        {
            "post_id": post_id,
            "keyword": keyword,
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
