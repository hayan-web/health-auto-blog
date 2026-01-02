import base64
import os
import re
import uuid

from app.config import Settings
from app.ai_openai import (
    make_openai_client,
    generate_blog_post,
    generate_thumbnail_title,
)
from app.ai_gemini_image import (  # 파일명은 그대로여도 됩니다(내부가 OpenAI 이미지여도 OK)
    make_gemini_client,
    generate_nanobanana_image_png_bytes,
)
from app.thumb_overlay import to_square_1024, add_title_to_image
from app.wp_client import upload_media_to_wp, publish_to_wp
from app.store import load_state, save_state, add_history_item
from app.dedupe import pick_retry_reason, _title_fingerprint
from app.keyword_picker import pick_keyword_by_naver

from app.formatter_v2 import format_post_v2
from app.monetize_adsense import inject_adsense_slots
from app.monetize_coupang import inject_coupang

# ✅ NEW: 품질 점수/재생성
from app.quality_gate import quality_retry_loop, score_post
# ✅ NEW: 주제 분기
from app.prompt_router import guess_topic_from_keyword, build_system_prompt, build_user_prompt
# ✅ NEW: 발행/비용 가드
from app.guardrails import GuardConfig, check_limits_or_raise, increment_post_count

S = Settings()


def make_ascii_filename(prefix: str, ext: str = "png") -> str:
    uid = uuid.uuid4().hex[:10]
    prefix = re.sub(r"[^a-zA-Z0-9_-]+", "-", (prefix or "img")).strip("-")
    if not prefix:
        prefix = "img"
    return f"{prefix}-{uid}.{ext}"


def _fallback_png_bytes(text: str) -> bytes:
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
        img = Image.new("RGB", (1024, 1024), (245, 245, 245))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 48)
        except Exception:
            font = ImageFont.load_default()
        msg = (text or "health").strip()[:40]
        box = draw.textbbox((0, 0), msg, font=font)
        w, h = box[2] - box[0], box[3] - box[1]
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


def _ensure_str_html(result):
    if isinstance(result, tuple) and len(result) >= 1:
        html = result[0]
        inserted = bool(result[1]) if len(result) >= 2 else True
        return str(html), inserted
    return str(result), False


def _save_preview_html(html: str) -> None:
    os.makedirs("preview", exist_ok=True)
    with open("preview/post.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("🧾 preview saved: preview/post.html")


def run() -> None:
    S = Settings()

    # === 클라이언트 ===
    openai_client = make_openai_client(S.OPENAI_API_KEY)

    # ⚠️ 중요: 이미지도 OpenAI로 통일할 거면 여기 키는 OPENAI_API_KEY
    # (이전 이슈: GOOGLE_API_KEY를 넣어서 401나고 fallback만 업로드됨)
    img_client = make_gemini_client(S.OPENAI_API_KEY)

    state = load_state()
    history = state.get("history", [])

    # === (3) 발행/비용 가드 ===
    cfg = GuardConfig(
        max_posts_per_day=int(getattr(S, "MAX_POSTS_PER_DAY", 3)),
        max_usd_per_month=float(getattr(S, "MAX_USD_PER_MONTH", 30.0)),
    )
    check_limits_or_raise(state, cfg)

    # 1) 키워드 선정
    keyword, debug = pick_keyword_by_naver(
        S.NAVER_CLIENT_ID, S.NAVER_CLIENT_SECRET, history
    )
    print("🔎 선택된 키워드:", keyword)
    print("🧾 키워드 점수(상위 3):", (debug.get("scored") or [])[:3])

    # === (2) 주제 분기 프롬프트 ===
    topic = guess_topic_from_keyword(keyword)
    system_prompt = build_system_prompt(topic)
    user_prompt = build_user_prompt(topic, keyword)
    print(f"🧭 topic: {topic}")

    # 2) 글 생성 + 중복 회피 + (1) 품질 점수화 재생성
    MAX_RETRY = 3

    def _generate_once():
        # generate_blog_post 내부가 system/user prompt를 받을 수 있도록 확장되어 있으면 그대로 넘기고,
        # 아직 없다면 generate_blog_post 안에서 keyword 기반으로 프롬프트를 구성하는 방식으로 구현해도 됩니다.
        candidate = generate_blog_post(
            openai_client,
            S.OPENAI_MODEL,
            keyword,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        dup, reason = pick_retry_reason(candidate.get("title", ""), history)
        if dup:
            print(f"♻️ 중복 감지({reason}) → 재생성")
            # 중복이면 강제로 FAIL로 만들어 재생성 루프로
            candidate["sections"] = []  # 점수 떨어뜨리기
        return candidate

    post, q = quality_retry_loop(_generate_once, max_retry=MAX_RETRY)
    print(f"✅ 품질 OK ({q.score}/100) → 진행")

    # 3) 썸네일용 짧은 타이틀
    thumb_title = generate_thumbnail_title(openai_client, S.OPENAI_MODEL, post["title"])
    print("🧩 썸네일 타이틀:", thumb_title)

    # 4) 이미지 2장 생성 (다양화: 프롬프트/구도/스타일 분리)
    base_prompt = (post.get("img_prompt") or "").strip()
    if not base_prompt:
        base_prompt = f"{keyword} 주제의 블로그 삽화, single scene, no collage, no text, square 1:1"

    # ✅ 서로 다른 “구도/피사체/렌즈/스타일 힌트”를 넣어 강제로 다르게 만듭니다
    hero_prompt = (
        base_prompt
        + ", wide composition, clean minimal illustration, soft lighting, different subject placement"
        + ", single scene, no collage, no text, square 1:1"
    )
    body_prompt = (
        base_prompt
        + ", close-up composition, different angle, different scene elements, more detailed background"
        + ", single scene, no collage, no text, square 1:1"
    )

    try:
        print("🎨 이미지(상단/대표) 생성 중...")
        hero_img = generate_nanobanana_image_png_bytes(img_client, S.GEMINI_IMAGE_MODEL, hero_prompt)
    except Exception as e:
        print(f"⚠️ 대표 이미지 생성 실패 → 대체 이미지: {e}")
        hero_img = _fallback_png_bytes(keyword)

    try:
        print("🎨 이미지(중간) 생성 중...")
        body_img = generate_nanobanana_image_png_bytes(img_client, S.GEMINI_IMAGE_MODEL, body_prompt)
    except Exception as e:
        print(f"⚠️ 중간 이미지 생성 실패 → 대표 이미지 재사용: {e}")
        body_img = hero_img

    hero_img = to_square_1024(hero_img)
    body_img = to_square_1024(body_img)

    # 5) 대표 이미지에 타이틀 오버레이
    hero_img_titled = add_title_to_image(hero_img, thumb_title)
    hero_img_titled = to_square_1024(hero_img_titled)

    # 6) WP 미디어 업로드
    hero_name = make_ascii_filename("featured", "png")
    body_name = make_ascii_filename("body", "png")

    hero_url, hero_media_id = upload_media_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD, hero_img_titled, hero_name
    )
    body_url, _ = upload_media_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD, body_img, body_name
    )

    # 7) A안 레이아웃 HTML 생성
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

    # 8) 쿠팡 삽입 + 실제 삽입 시 대가성 문구
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

    # ✅ (4번 보강) 미리보기 저장(무조건 생성)
    _save_preview_html(html)

    # 10) publish_to_wp가 content_html 우선 사용
    post["content_html"] = html

    # 11) WP 글 발행
    post_id = publish_to_wp(
        S.WP_URL,
        S.WP_USERNAME,
        S.WP_APP_PASSWORD,
        post,
        hero_url,
        body_url,
        featured_media_id=hero_media_id,
    )

    # ✅ 발행 카운트 증가(가드용)
    increment_post_count(state)

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
    run()
