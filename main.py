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

# ✅ 추가(문단 스타일/수익화)
from app.formatter import format_post_body
from app.monetize_adsense import inject_ads
from app.monetize_coupang import inject_coupang


# =========================
# Settings 인스턴스 (필수)
# =========================
S = Settings()


def make_ascii_filename(prefix: str, ext: str = "png") -> str:
    """
    헤더에 넣어도 안전한 ASCII 파일명 생성 (한글/특수문자 없음)
    """
    uid = uuid.uuid4().hex[:10]
    prefix = re.sub(r"[^a-zA-Z0-9_-]+", "-", (prefix or "img")).strip("-")
    if not prefix:
        prefix = "img"
    return f"{prefix}-{uid}.{ext}"


def run() -> None:
    # 0) Settings 로드
    S = Settings()

    # 1) 클라이언트 준비
    openai_client = make_openai_client(S.OPENAI_API_KEY)
    gemini_client = make_gemini_client(S.GOOGLE_API_KEY)

    # 2) 중복 방지용 state 로드
    state = load_state()
    history = state.get("history", [])

    # ✅ 2.5) 네이버 기반 키워드 선정
    keyword, debug = pick_keyword_by_naver(
        S.NAVER_CLIENT_ID, S.NAVER_CLIENT_SECRET, history
    )
    print("🔎 선택된 키워드:", keyword)
    print("🧾 키워드 점수(상위 3):", (debug.get("scored") or [])[:3])

    # 3) 글 생성(OpenAI) + 중복 회피
    MAX_RETRY = 3
    post = None

    for i in range(1, MAX_RETRY + 1):
        candidate = generate_blog_post(openai_client, S.OPENAI_MODEL, keyword)

        dup, reason = pick_retry_reason(candidate.get("title", ""), history)
        if dup:
            print(f"♻️ 중복 감지({reason}) → 재생성 {i}/{MAX_RETRY}")
            continue

        post = candidate
        break

    if not post:
        raise RuntimeError("중복 회피 실패: 재시도 횟수 초과")

    # 4) 썸네일용 짧은 타이틀
    thumb_title = generate_thumbnail_title(openai_client, S.OPENAI_MODEL, post["title"])
    print("🧩 썸네일 타이틀:", thumb_title)

    # 5) 이미지 2장 생성 (Gemini NanoBanana)
    print("🎨 Gemini 이미지(상단/대표) 생성 중...")
    hero_img = generate_nanobanana_image_png_bytes(
        gemini_client, S.GEMINI_IMAGE_MODEL, post["img_prompt"]
    )

    print("🎨 Gemini 이미지(중간) 생성 중...")
    body_img = generate_nanobanana_image_png_bytes(
        gemini_client,
        S.GEMINI_IMAGE_MODEL,
        post["img_prompt"] + ", single scene, no collage, different composition, different angle, no text, square 1:1",
    )

    # 6) 1:1 고정
    hero_img = to_square_1024(hero_img)
    body_img = to_square_1024(body_img)

    # 7) 대표 이미지에 타이틀 오버레이
    hero_img_titled = add_title_to_image(hero_img, thumb_title)
    hero_img_titled = to_square_1024(hero_img_titled)

    # 8) WP 미디어 업로드
    hero_name = make_ascii_filename("featured")
    body_name = make_ascii_filename("body")

    hero_url, hero_media_id = upload_media_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD, hero_img_titled, hero_name
    )
    body_url, _ = upload_media_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD, body_img, body_name
    )

    # ==========================================================
    # ✅ 8.5) 본문 스타일 적용 + 쿠팡/애드센스 삽입 (발행 전에!)
    # - generate_blog_post()가 intro/sections/outro를 주면 그대로 사용
    # - 아니면 content만 있는 경우 대비로 fallback 처리
    # ==========================================================
    if post.get("sections"):
        styled_html = format_post_body(
            title=post["title"],
            intro=post.get("intro", ""),
            sections=post.get("sections", []),
            outro=post.get("outro", ""),
            disclaimer="의학적 진단이 아닌 일반 정보입니다. 증상이 지속되면 전문가 상담을 권장드립니다.",
        )
    else:
        # fallback: content 단일 문자열일 때
        raw = post.get("content", "") or post.get("body", "") or ""
        styled_html = f"""
        <p style="margin:0 0 14px; font-size:17px; line-height:1.75; letter-spacing:-0.2px;">{raw}</p>
        """.strip()

    # ✅ 쿠팡 박스 삽입(키워드 기반)
    styled_html = inject_coupang(styled_html, keyword)

    # ✅ 애드센스 블록 삽입(ENV에 설정된 경우만)
    styled_html = inject_ads(styled_html)

    # ✅ publish_to_wp가 content_html을 우선 사용하도록 해둔 상태라면 이걸로 본문 교체됨
    post["content_html"] = styled_html

    # 9) WP 글 발행
    post_id = publish_to_wp(
        S.WP_URL,
        S.WP_USERNAME,
        S.WP_APP_PASSWORD,
        post,
        hero_url,
        body_url,
        featured_media_id=hero_media_id,
    )

    # 10) 히스토리 저장
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
