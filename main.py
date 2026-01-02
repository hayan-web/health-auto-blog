import base64
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

# ✅ 레이아웃 + 애드센스
from app.formatter_v2 import format_post_v2
from app.monetize_adsense import inject_adsense_slots

# ✅ 쿠팡
from app.monetize_coupang import inject_coupang


S = Settings()


def make_ascii_filename(prefix: str, ext: str = "jpg") -> str:
    uid = uuid.uuid4().hex[:10]
    prefix = re.sub(r"[^a-zA-Z0-9_-]+", "-", (prefix or "img")).strip("-")
    if not prefix:
        prefix = "img"
    return f"{prefix}-{uid}.{ext}"


def _fallback_png_bytes(text: str) -> bytes:
    """
    Gemini 실패시 대체 이미지(PNG)
    """
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
        img = Image.new("RGB", (1024, 1024), (245, 245, 245))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 48)
        except Exception:
            font = ImageFont.load_default()

        msg = (text or "health").strip()[:40]
        bbox = draw.textbbox((0, 0), msg, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((1024 - w) / 2, (1024 - h) / 2), msg, fill=(60, 60, 60), font=font)

        from io import BytesIO
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        tiny_png_b64 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMA"
            "ASsJTYQAAAAASUVORK5CYII="
        )
        return base64.b64decode(tiny_png_b64)


def _to_jpeg_bytes(img_bytes: bytes, quality: int = 92) -> bytes:
    """
    어떤 포맷이든 JPG로 변환 (Imsanity 변환/URL변경 이슈 회피)
    """
    from io import BytesIO
    try:
        from PIL import Image  # type: ignore
        im = Image.open(BytesIO(img_bytes))
        if im.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")

        out = BytesIO()
        im.save(out, format="JPEG", quality=quality, optimize=True)
        return out.getvalue()
    except Exception as e:
        print("⚠️ JPG 변환 실패(원본 bytes 사용):", e)
        return img_bytes


def _ensure_str_html(result):
    """
    inject_coupang 반환형(str or (str,bool)) 모두 대응
    """
    if isinstance(result, tuple) and len(result) >= 1:
        html = result[0]
        inserted = bool(result[1]) if len(result) >= 2 else True
        return str(html), inserted
    return str(result), False


def run() -> None:
    S = Settings()

    openai_client = make_openai_client(S.OPENAI_API_KEY)
    gemini_client = make_gemini_client(S.GOOGLE_API_KEY)

    state = load_state()
    history = state.get("history", [])

    # 1) 키워드 선정
    keyword, debug = pick_keyword_by_naver(S.NAVER_CLIENT_ID, S.NAVER_CLIENT_SECRET, history)
    print("🔎 선택된 키워드:", keyword)
    print("🧾 키워드 점수(상위 3):", (debug.get("scored") or [])[:3])

    # 2) 글 생성 + 중복 회피
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

    # 3) 썸네일용 짧은 타이틀
    thumb_title = generate_thumbnail_title(openai_client, S.OPENAI_MODEL, post["title"])
    print("🧩 썸네일 타이틀:", thumb_title)

    # 4) 이미지 2장 생성 (실패 시 fallback)
    hero_prompt = (post.get("img_prompt") or "").strip()
    if not hero_prompt:
        hero_prompt = f"{keyword} 주제의 건강 정보 블로그 삽화, single scene, no collage, no text, square 1:1"
    body_prompt = hero_prompt + ", single scene, no collage, different composition, different angle, no text, square 1:1"

    try:
        print("🎨 Gemini 이미지(상단/대표) 생성 중...")
        hero_img = generate_nanobanana_image_png_bytes(gemini_client, S.GEMINI_IMAGE_MODEL, hero_prompt)
    except Exception as e:
        print(f"⚠️ 대표 이미지 생성 실패 → 대체 이미지로 진행: {e}")
        hero_img = _fallback_png_bytes(f"{keyword}")

    try:
        print("🎨 Gemini 이미지(중간) 생성 중...")
        body_img = generate_nanobanana_image_png_bytes(gemini_client, S.GEMINI_IMAGE_MODEL, body_prompt)
    except Exception as e:
        print(f"⚠️ 중간 이미지 생성 실패 → 대표 이미지 재사용: {e}")
        body_img = hero_img

    # 5) 1:1 + 타이틀 오버레이
    hero_img = to_square_1024(hero_img)
    body_img = to_square_1024(body_img)

    hero_img_titled = add_title_to_image(hero_img, thumb_title)
    hero_img_titled = to_square_1024(hero_img_titled)

    # ✅ 여기서 JPG로 변환해서 업로드 (핵심)
    hero_jpg = _to_jpeg_bytes(hero_img_titled)
    body_jpg = _to_jpeg_bytes(body_img)

    # 6) WP 미디어 업로드 (파일명도 jpg)
    hero_name = make_ascii_filename("featured", "jpg")
    body_name = make_ascii_filename("body", "jpg")

    hero_url, hero_media_id = upload_media_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD, hero_jpg, hero_name
    )
    body_url, _ = upload_media_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD, body_jpg, body_name
    )

    # 7) 레이아웃 HTML 생성
    sections = post.get("sections") or []
    outro = post.get("outro") or ""

    html = format_post_v2(
        title=post["title"],
        keyword=keyword,
        hero_url=hero_url,
        body_url=body_url,
        disclosure_html="",
        summary_bullets=post.get("summary_bullets") or None,
        sections=sections if isinstance(sections, list) else [],
        warning_bullets=post.get("warning_bullets") or None,
        checklist_bullets=post.get("checklist_bullets") or None,
        outro=outro,
    )

    # 8) 쿠팡 삽입 + 삽입된 경우에만 대가성 문구
    coupang_result = inject_coupang(html, keyword=keyword)
    html_after_coupang, inserted_flag = _ensure_str_html(coupang_result)
    coupang_inserted = inserted_flag or (html_after_coupang != html)

    if coupang_inserted:
        disclosure = "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
        html_after_coupang = html_after_coupang.replace(
            '<div class="wrap">',
            f'<div class="wrap">\n  <div class="disclosure">{disclosure}</div>',
            1,
        )

    html = html_after_coupang

    # 9) 애드센스 슬롯 3개 삽입
    html = inject_adsense_slots(html)

    # 10) 본문 교체
    post["content_html"] = html

    # 11) 발행
    post_id = publish_to_wp(
        S.WP_URL,
        S.WP_USERNAME,
        S.WP_APP_PASSWORD,
        post,
        hero_url,
        body_url,
        featured_media_id=hero_media_id,
    )

    # 12) 히스토리 저장
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
