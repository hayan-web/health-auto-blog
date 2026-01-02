# main.py
import base64
import os
import re
import uuid
import random
import hashlib
import time

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
from app.topic_style_stats import (
    record_impression as record_topic_style_impression,
    update_score as update_topic_style_score,
)
from app.thumb_overlay import to_square_1024, add_title_to_image
from app.wp_client import upload_media_to_wp, publish_to_wp
from app.store import load_state, save_state, add_history_item
from app.dedupe import pick_retry_reason, _title_fingerprint
from app.keyword_picker import pick_keyword_by_naver
from app.click_ingest import ingest_click_log
from app.prioritizer import pick_best_publishing_combo
from app.cooldown import CooldownRule, apply_cooldown_rules

# ✅ 쿠팡 정책 (생활 주제만 허용)
from app.coupang_policy import should_inject_coupang, increment_coupang_count

from app.formatter_v2 import format_post_v2
from app.monetize_adsense import inject_adsense_slots
from app.monetize_coupang import inject_coupang

from app.image_stats import (
    record_impression as record_image_impression,
    update_score as update_image_score,
)
from app.image_style_picker import pick_image_style

from app.quality_gate import quality_retry_loop
from app.prompt_router import guess_topic_from_keyword, build_system_prompt, build_user_prompt
from app.guardrails import GuardConfig, check_limits_or_raise, increment_post_count

# ✅ 썸네일 타이틀 A/B
from app.thumb_title_ab import generate_thumbnail_title_ab
from app.thumb_title_stats import (
    record_impression as record_thumb_impression,
    update_score as update_thumb_score,
    record_topic_impression as record_topic_thumb_impression,
    update_topic_score as update_topic_thumb_score,
)

S = Settings()


def make_ascii_filename(prefix: str, ext: str = "png") -> str:
    uid = uuid.uuid4().hex[:10]
    prefix = re.sub(r"[^a-zA-Z0-9_-]+", "-", (prefix or "img")).strip("-")
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
        msg = (text or "image").strip()[:40]
        box = draw.textbbox((0, 0), msg, font=font)
        w, h = box[2] - box[0], box[3] - box[1]
        draw.text(((1024 - w) / 2, (1024 - h) / 2), msg, fill=(60, 60, 60), font=font)
        from io import BytesIO
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMA"
            "ASsJTYQAAAAASUVORK5CYII="
        )


def _stable_seed_int(*parts: str) -> int:
    s = "|".join([p or "" for p in parts])
    h = hashlib.sha256(s.encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _build_image_prompt(base: str, *, variant: str, seed: int) -> str:
    HERO_PRESETS = [
        "clean flat illustration, minimal background, centered subject, soft daylight",
        "3D clay style, simple props, soft studio lighting",
        "watercolor illustration, gentle texture paper, warm light",
        "isometric illustration, neat geometry, pastel colors",
    ]
    BODY_PRESETS = [
        "photo-realistic style, wide shot, natural light",
        "hand-drawn sketch style, dynamic perspective",
        "bold vector art, off-center composition",
        "soft 3D render, close-up detail shot",
    ]

    rng = random.Random(seed + (1 if variant == "hero" else 2))
    preset = rng.choice(HERO_PRESETS if variant == "hero" else BODY_PRESETS)

    base_raw = (base or "").strip()
    low = base_raw.lower()

    if "single scene" not in low:
        base_raw += ", single scene"
    if "no collage" not in low:
        base_raw += ", no collage"
    if "no text" not in low:
        base_raw += ", no text"
    if ("square" not in low) and ("1:1" not in low):
        base_raw += ", square 1:1"

    extra = (
        "title-safe area, iconic main object"
        if variant == "hero"
        else "different composition, secondary elements, different angle"
    )

    return f"{base_raw}, {preset}, {extra}"


def run() -> None:
    S = Settings()

    openai_client = make_openai_client(S.OPENAI_API_KEY)
    img_client = make_gemini_client(S.OPENAI_API_KEY)

    state = load_state()
    state = ingest_click_log(state, S.WP_URL)
    history = state.get("history", [])

    # -------------------------
    # 0) 가드레일
    # -------------------------
    cfg = GuardConfig(
        max_posts_per_day=int(getattr(S, "MAX_POSTS_PER_DAY", 3)),
        max_usd_per_month=float(getattr(S, "MAX_USD_PER_MONTH", 30.0)),
    )
    check_limits_or_raise(state, cfg)

    # -------------------------
    # 1) 키워드
    # -------------------------
    keyword, _ = pick_keyword_by_naver(
        S.NAVER_CLIENT_ID,
        S.NAVER_CLIENT_SECRET,
        history,
    )

    # -------------------------
    # 2) 주제 분기
    # -------------------------
    topic = guess_topic_from_keyword(keyword)
    system_prompt = build_system_prompt(topic)
    user_prompt = build_user_prompt(topic, keyword)

    best_image_style, thumb_variant, _ = pick_best_publishing_combo(state, topic=topic)

    # -------------------------
    # 3) 글 생성 + 품질
    # -------------------------
    def _gen():
        post = generate_blog_post(openai_client, S.OPENAI_MODEL, keyword)
        dup, reason = pick_retry_reason(post.get("title", ""), history)
        if dup:
            post["sections"] = []
        return post

    post, _ = quality_retry_loop(_gen, max_retry=3)

    # -------------------------
    # 4) 썸네일 타이틀
    # -------------------------
    thumb_title = generate_thumbnail_title(openai_client, S.OPENAI_MODEL, post["title"])

    # -------------------------
    # 5) 이미지 생성
    # -------------------------
    base_prompt = post.get("img_prompt") or f"{keyword} blog illustration"
    image_style = best_image_style or pick_image_style(state, topic=topic)
    seed = _stable_seed_int(keyword, post.get("title", ""), str(int(time.time())))

    hero_prompt = _build_image_prompt(base_prompt, variant="hero", seed=seed) + f", style: {image_style}"
    body_prompt = _build_image_prompt(base_prompt, variant="body", seed=seed) + f", style: {image_style}"

    try:
        hero_img = generate_nanobanana_image_png_bytes(img_client, S.GEMINI_IMAGE_MODEL, hero_prompt)
    except Exception:
        hero_img = _fallback_png_bytes(keyword)

    try:
        body_img = generate_nanobanana_image_png_bytes(img_client, S.GEMINI_IMAGE_MODEL, body_prompt)
    except Exception:
        body_img = hero_img

    hero_img = to_square_1024(hero_img)
    body_img = to_square_1024(body_img)
    hero_img_titled = to_square_1024(add_title_to_image(hero_img, thumb_title))

    # -------------------------
    # 6) 업로드
    # -------------------------
    hero_url, hero_media_id = upload_media_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD,
        hero_img_titled, make_ascii_filename("featured")
    )
    body_url, _ = upload_media_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD,
        body_img, make_ascii_filename("body")
    )

    # -------------------------
    # 7) HTML + 광고 정책
    # -------------------------
    html = format_post_v2(
        title=post["title"],
        keyword=keyword,
        hero_url=hero_url,
        body_url=body_url,
        disclosure_html="",
        summary_bullets=post.get("summary_bullets"),
        sections=post.get("sections"),
        warning_bullets=post.get("warning_bullets"),
        checklist_bullets=post.get("checklist_bullets"),
        outro=post.get("outro"),
    )

    # ✅ 쿠팡은 "생활(life)" 주제일 때만
    if topic == "life":
        allow, reason = should_inject_coupang(state, topic=topic, keyword=keyword, post=post)
        if allow:
            html = inject_coupang(html, keyword=keyword)
            html = html.replace(
                '<div class="wrap">',
                '<div class="wrap">\n<div class="disclosure">이 포스팅은 쿠팡 파트너스 활동의 일환으로 일정액의 수수료를 제공받을 수 있습니다.</div>',
                1,
            )
            state = increment_coupang_count(state)

    # 에드센스는 모든 글에 자연스럽게
    html = inject_adsense_slots(html)
    post["content_html"] = html

    # -------------------------
    # 8) 발행
    # -------------------------
    post_id = publish_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD,
        post, hero_url, body_url,
        featured_media_id=hero_media_id,
    )

    # -------------------------
    # 9) 통계 + 학습
    # -------------------------
    state = record_image_impression(state, image_style)
    state = update_image_score(state, image_style)
    state = record_topic_style_impression(state, topic, image_style)
    state = update_topic_style_score(state, topic, image_style)

    state = record_thumb_impression(state, thumb_variant)
    state = update_thumb_score(state, thumb_variant)
    state = record_topic_thumb_impression(state, topic, thumb_variant)
    state = update_topic_thumb_score(state, topic, thumb_variant)

    increment_post_count(state)

    rule = CooldownRule(
        min_impressions=int(getattr(S, "COOLDOWN_MIN_IMPRESSIONS", 120)),
        ctr_floor=float(getattr(S, "COOLDOWN_CTR_FLOOR", 0.0025)),
        cooldown_days=int(getattr(S, "COOLDOWN_DAYS", 3)),
    )
    state = apply_cooldown_rules(state, topic=topic, img=image_style, tv=thumb_variant, rule=rule)

    state = add_history_item(
        state,
        {
            "post_id": post_id,
            "keyword": keyword,
            "title": post["title"],
            "title_fp": _title_fingerprint(post["title"]),
            "thumb_variant": thumb_variant,
            "image_style": image_style,
            "topic": topic,
        },
    )
    save_state(state)

    print(f"✅ 발행 완료: post_id={post_id}")


if __name__ == "__main__":
    run()
