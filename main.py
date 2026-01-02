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

from app.thumb_title_stats import (
    record_impression as record_thumb_impression,
    update_score as update_thumb_score,
    record_topic_impression as record_topic_thumb_impression,
    update_topic_score as update_topic_thumb_score,
)

# ✅ 생활 하위주제 선택/학습
from app.life_subtopic_picker import pick_life_subtopic
from app.life_subtopic_stats import (
    record_life_subtopic_impression,
    try_update_from_post_metrics,
)

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


def _sanitize_title_remove_age(title: str) -> str:
    if not title:
        return title
    t = title
    t = re.sub(r"\b\d{2}\s*[~-]\s*\d{2}\s*대(를|을|의|에게|용|을 위한|를 위한)?\b", "", t)
    t = re.sub(r"\b\d{2}\s*대(를|을|의|에게|용|을 위한|를 위한)?\b", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    t = re.sub(r"^[\-\:\|\·\s]+", "", t).strip()
    return t


def _build_image_prompt(base: str, *, variant: str, seed: int, style_mode: str) -> str:
    """
    style_mode:
      - "watercolor" : 수채화
      - "photo"      : 실사/제품컷/라이프스타일 사진(쿠팡)
      - 그 외        : 학습 스타일 문자열(약하게 힌트)
    """
    rng = random.Random(seed + (1 if variant == "hero" else 2))

    # --- 공통 금지/품질 규칙 (강화) ---
    base_raw = (base or "").strip()
    low = base_raw.lower()

    must_rules = [
        "single scene",
        "no collage",
        "no text",
        "no watermark",
        "no logos",
        "no brand names",
        "no trademarks",
        "square 1:1",
    ]
    for r in must_rules:
        if r not in low:
            base_raw += f", {r}"

    # --- 스타일별 프리셋 ---
    if style_mode == "watercolor":
        wc_presets = [
            "watercolor illustration, soft wash, paper texture, gentle edges, airy light, pastel palette",
            "watercolor + ink outline, light granulation, calm mood, soft shadows, minimal background",
            "delicate watercolor painting, subtle gradients, hand-painted feel, clean composition",
        ]
        style = rng.choice(wc_presets)

        hero_comp = [
            "centered subject, minimal background, plenty of negative space, calm composition",
            "iconic main object, simple props, soft morning light, clean framing",
        ]
        body_comp = [
            "different angle from hero, include secondary elements, natural indoor scene, balanced spacing",
            "wider view, gentle perspective change, subtle storytelling props",
        ]
        comp = rng.choice(hero_comp if variant == "hero" else body_comp)

        extra = "title-safe area on lower third" if variant == "hero" else "different composition from hero"
        return f"{base_raw}, {style}, {comp}, {extra}"

    if style_mode == "photo":
        # ✅ 쿠팡용 “제품 실사 강화”
        # hero: 이커머스 메인 제품컷 / body: 사용 장면(라이프스타일), 손만(얼굴 X)
        product_hero = [
            "photorealistic e-commerce product photography, clean white or light neutral background, softbox studio lighting, natural shadow, ultra sharp, high detail, 85mm lens look, centered",
            "photorealistic product shot on minimal tabletop, studio lighting, clean background, crisp edges, high resolution, professional catalog photo",
        ]
        product_body = [
            "photorealistic lifestyle in-use photo in a tidy home, natural window light, hands using the item (no face), realistic textures, 35mm lens look, candid but clean",
            "photorealistic usage scene, close-up hands demonstrating the item, shallow depth of field, natural indoor light, clean modern home, no people faces",
        ]
        style = rng.choice(product_hero if variant == "hero" else product_body)

        # 구도/안전 보강
        hero_comp = [
            "front view, centered, minimal props, premium clean look",
            "slight top-down angle, catalog composition, product clearly visible",
        ]
        body_comp = [
            "different angle from hero, show real use-case, include subtle context objects",
            "close-up detail + action, show how it works, keep background uncluttered",
        ]
        comp = rng.choice(hero_comp if variant == "hero" else body_comp)

        extra = "title-safe area on lower third (keep product away from bottom text area)" if variant == "hero" else "avoid looking similar to hero"
        return f"{base_raw}, {style}, {comp}, {extra}"

    # --- 학습/기타 스타일 (약하게 힌트만) ---
    comp_pool_hero = [
        "centered subject, simple background, soft daylight, clean composition",
        "iconic main object, calm mood, minimal props, negative space",
    ]
    comp_pool_body = [
        "different angle, wider shot, secondary elements, clean framing",
        "off-center composition, detail emphasis, different perspective",
    ]
    comp = rng.choice(comp_pool_hero if variant == "hero" else comp_pool_body)
    extra = "title-safe area on lower third" if variant == "hero" else "different composition from hero"
    return f"{base_raw}, style hint: {style_mode}, {comp}, {extra}"


def run() -> None:
    S = Settings()

    openai_client = make_openai_client(S.OPENAI_API_KEY)

    # 이미지 키: 프로젝트 구조 유지
    img_key = os.getenv("IMAGE_API_KEY", "").strip() or getattr(S, "IMAGE_API_KEY", "") or S.OPENAI_API_KEY
    img_client = make_gemini_client(img_key)

    state = load_state()
    state = ingest_click_log(state, S.WP_URL)
    state = try_update_from_post_metrics(state)

    history = state.get("history", [])

    # 0) 가드레일 (자동발행 우선이면 초과해도 계속)
    cfg = GuardConfig(
        max_posts_per_day=int(getattr(S, "MAX_POSTS_PER_DAY", 3)),
        max_usd_per_month=float(getattr(S, "MAX_USD_PER_MONTH", 30.0)),
    )
    allow_over_budget = bool(int(os.getenv("ALLOW_OVER_BUDGET", str(getattr(S, "ALLOW_OVER_BUDGET", 1)))))
    if allow_over_budget:
        try:
            check_limits_or_raise(state, cfg)
        except Exception as e:
            print(f"⚠️ 가드레일 초과(허용 모드) → 계속 진행: {e}")
    else:
        check_limits_or_raise(state, cfg)

    # 1) 키워드
    keyword, _ = pick_keyword_by_naver(
        S.NAVER_CLIENT_ID,
        S.NAVER_CLIENT_SECRET,
        history,
    )

    # 2) 주제
    topic = guess_topic_from_keyword(keyword)
    system_prompt = build_system_prompt(topic)
    user_prompt = build_user_prompt(topic, keyword)

    # 생활 하위주제
    life_subtopic = ""
    if topic == "life":
        life_subtopic, sub_dbg = pick_life_subtopic(state)
        print("🧩 life_subtopic:", life_subtopic, "| dbg(top3):", (sub_dbg.get("scored") or [])[:3])
        keyword = f"{keyword} {life_subtopic}".strip()

    best_image_style, thumb_variant, _ = pick_best_publishing_combo(state, topic=topic)

    # 3) 글 생성 + 품질
    def _gen():
        try:
            post = generate_blog_post(
                openai_client,
                S.OPENAI_MODEL,
                keyword,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        except TypeError:
            post = generate_blog_post(openai_client, S.OPENAI_MODEL, keyword)

        dup, reason = pick_retry_reason(post.get("title", ""), history)
        if dup:
            post["sections"] = []
            print(f"♻️ 중복 감지({reason}) → 재생성 유도")
        return post

    post, _ = quality_retry_loop(_gen, max_retry=3)

    # 제목에서 연령대 제거(기본 ON)
    if bool(int(os.getenv("REMOVE_AGE_IN_TITLE", "1"))):
        post["title"] = _sanitize_title_remove_age(post.get("title", ""))

    # 4) 썸 타이틀
    thumb_title = generate_thumbnail_title(openai_client, S.OPENAI_MODEL, post["title"])
    print("🧩 thumb_title:", thumb_title, "| thumb_variant:", thumb_variant)

    # ✅ 쿠팡 “삽입 예정”을 이미지 생성 전에 판단
    coupang_planned = False
    coupang_reason = ""
    if topic == "life":
        try:
            r = should_inject_coupang(state, topic=topic, keyword=keyword, post=post, subtopic=life_subtopic)
        except TypeError:
            r = should_inject_coupang(state, topic=topic, keyword=keyword, post=post)

        if isinstance(r, tuple):
            coupang_planned = bool(r[0])
            coupang_reason = str(r[1]) if len(r) > 1 else ""
        else:
            coupang_planned = bool(r)

    # ✅ 주제별 스타일 강제
    forced_style_mode = ""
    if topic in ("health", "trend"):
        forced_style_mode = "watercolor"
    elif topic == "life" and coupang_planned:
        forced_style_mode = "photo"

    learned_style = best_image_style or pick_image_style(state, topic=topic)
    style_mode = forced_style_mode or learned_style
    image_style_for_stats = forced_style_mode or learned_style

    print("🎨 style_mode:", style_mode, "| forced:", bool(forced_style_mode), "| learned:", learned_style)
    if topic == "life":
        print("🛒 coupang_planned:", coupang_planned, "| reason:", coupang_reason)

    # 5) 이미지 프롬프트 (✅ 쿠팡일 때 “제품 실사 전용 베이스 프롬프트”로 오버라이드)
    if topic == "life" and coupang_planned:
        # keyword는 이미 (키워드 + 하위주제)로 확장돼있으니, 제품 맥락을 강하게 부여
        subject = keyword.strip()
        base_prompt = (
            f"{subject} 관련 생활용품, practical household item, "
            f"product clearly visible, simple clean background, "
            f"no packaging text, no labels"
        )
    else:
        base_prompt = post.get("img_prompt") or f"{keyword} blog illustration"

    seed = _stable_seed_int(keyword, post.get("title", ""), str(int(time.time())))
    hero_prompt = _build_image_prompt(base_prompt, variant="hero", seed=seed, style_mode=style_mode)
    body_prompt = _build_image_prompt(base_prompt, variant="body", seed=seed, style_mode=style_mode)

    try:
        hero_img = generate_nanobanana_image_png_bytes(img_client, S.GEMINI_IMAGE_MODEL, hero_prompt)
    except Exception as e:
        print(f"⚠️ hero image fail -> fallback: {e}")
        hero_img = _fallback_png_bytes(keyword)

    try:
        body_img = generate_nanobanana_image_png_bytes(img_client, S.GEMINI_IMAGE_MODEL, body_prompt)
    except Exception as e:
        print(f"⚠️ body image fail -> reuse hero: {e}")
        body_img = hero_img

    hero_img = to_square_1024(hero_img)
    body_img = to_square_1024(body_img)
    hero_img_titled = to_square_1024(add_title_to_image(hero_img, thumb_title))

    # 6) 업로드
    hero_url, hero_media_id = upload_media_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD,
        hero_img_titled, make_ascii_filename("featured")
    )
    body_url, _ = upload_media_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD,
        body_img, make_ascii_filename("body")
    )

    # 7) HTML
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

    # 쿠팡은 life + coupang_planned일 때만
    coupang_inserted = False
    if topic == "life" and coupang_planned:
        html = inject_coupang(html, keyword=keyword)
        html = html.replace(
            '<div class="wrap">',
            '<div class="wrap">\n<div class="disclosure">이 포스팅은 쿠팡 파트너스 활동의 일환으로 일정액의 수수료를 제공받을 수 있습니다.</div>',
            1,
        )
        state = increment_coupang_count(state)
        coupang_inserted = True

    # 애드센스 공통
    html = inject_adsense_slots(html)
    post["content_html"] = html

    # 8) 발행
    post_id = publish_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD,
        post, hero_url, body_url,
        featured_media_id=hero_media_id,
    )

    # 9) 통계/학습
    state = record_image_impression(state, image_style_for_stats)
    state = update_image_score(state, image_style_for_stats)
    state = record_topic_style_impression(state, topic, image_style_for_stats)
    state = update_topic_style_score(state, topic, image_style_for_stats)

    state = record_thumb_impression(state, thumb_variant)
    state = update_thumb_score(state, thumb_variant)
    state = record_topic_thumb_impression(state, topic, thumb_variant)
    state = update_topic_thumb_score(state, topic, thumb_variant)

    if topic == "life" and life_subtopic:
        state = record_life_subtopic_impression(state, life_subtopic, n=1)

    increment_post_count(state)

    rule = CooldownRule(
        min_impressions=int(getattr(S, "COOLDOWN_MIN_IMPRESSIONS", 120)),
        ctr_floor=float(getattr(S, "COOLDOWN_CTR_FLOOR", 0.0025)),
        cooldown_days=int(getattr(S, "COOLDOWN_DAYS", 3)),
    )
    state = apply_cooldown_rules(state, topic=topic, img=image_style_for_stats, tv=thumb_variant, rule=rule)

    state = add_history_item(
        state,
        {
            "post_id": post_id,
            "keyword": keyword,
            "title": post["title"],
            "title_fp": _title_fingerprint(post["title"]),
            "thumb_variant": thumb_variant,
            "image_style": image_style_for_stats,
            "topic": topic,
            "life_subtopic": life_subtopic,
            "coupang_planned": coupang_planned,
            "coupang_inserted": coupang_inserted,
        },
    )
    save_state(state)

    print(f"✅ 발행 완료: post_id={post_id} | topic={topic} | sub={life_subtopic} | coupang={coupang_inserted} | img_style={image_style_for_stats}")


if __name__ == "__main__":
    run()
