import re
import uuid

from app.config import Settings
from app.ai_openai import make_openai_client, generate_blog_post, generate_thumbnail_title
from app.ai_gemini_image import make_gemini_client, generate_nanobanana_image_png_bytes
from app.thumb_overlay import to_square_1024, add_title_to_image
from app.wp_client import upload_media_to_wp, publish_to_wp
from app.store import load_state, save_state, add_history_item
from app.dedupe import pick_retry_reason


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

    # 2) 중복 방지용 state 로드 (⭐ 반드시 여기!)
    state = load_state()
    history = state.get("history", [])

    # 3) 글 생성(OpenAI) + 중복 회피
    MAX_RETRY = 3
    post = None

    for i in range(1, MAX_RETRY + 1):
        candidate = generate_blog_post(openai_client, S.OPENAI_MODEL)

        dup, reason = pick_retry_reason(candidate.get("title", ""), history)
        if dup:
            print(f"♻️ 중복 감지({reason}) → 재생성 {i}/{MAX_RETRY}")
            continue

        post = candidate
        break

    if not post:
        raise RuntimeError("중복 회피 실패: 재시도 횟수 초과")

    # 4) 썸네일용 짧은 타이틀
    thumb_title = generate_thumbnail_title(
        openai_client, S.OPENAI_MODEL, post["title"]
    )
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
        post["img_prompt"] + ", different composition, different angle, no text",
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
            "title": post["title"],
            "title_fp": __import__(
                "app.dedupe", fromlist=["_title_fingerprint"]
            )._title_fingerprint(post["title"]),
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
