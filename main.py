# main.py
import base64
import os
import re
import uuid
import random
import hashlib
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Tuple

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
from app.monetize_coupang import inject_coupang  # 최신: (html, inserted, state) 권장

from app.image_stats import (
    record_impression as record_image_impression,
    update_score as update_image_score,
)
from app.image_style_picker import pick_image_style

from app.quality_gate import quality_retry_loop
from app.prompt_router import build_system_prompt, build_user_prompt
from app.guardrails import GuardConfig, check_limits_or_raise, increment_post_count

from app.thumb_title_stats import (
    record_impression as record_thumb_impression,
    update_score as update_thumb_score,
    record_topic_impression as record_topic_thumb_impression,
    update_topic_score as update_topic_thumb_score,
)

from app.life_subtopic_picker import pick_life_subtopic
from app.life_subtopic_stats import (
    record_life_subtopic_impression,
    try_update_from_post_metrics,
)

S = Settings()

KST = timezone(timedelta(hours=9))


# -----------------------------
# Time helpers (KST)
# -----------------------------
def _kst_now() -> datetime:
    return datetime.now(tz=KST)


def _kst_date_key(dt: datetime | None = None) -> str:
    d = dt or _kst_now()
    return d.strftime("%Y-%m-%d")


def _slot_topic_kst(dt: datetime | None = None) -> str:
    """
    KST 기준 슬롯:
      09~11  -> health
      13~15  -> trend
      그 외  -> life
    """
    d = dt or _kst_now()
    h = d.hour
    if 9 <= h < 12:
        return "health"
    if 13 <= h < 16:
        return "trend"
    return "life"


# -----------------------------
# Safe tuple->html coercion
# -----------------------------
def _as_html(x: Any) -> str:
    """
    format_post_v2 / inject_* 가 (html, ...) 튜플 반환하는 케이스 안전 처리
    """
    if isinstance(x, tuple) and x:
        return str(x[0] or "")
    return str(x or "")


# -----------------------------
# Title normalizer
# -----------------------------
def _normalize_title(title: str) -> str:
    if not title:
        return title

    t = unicodedata.normalize("NFKC", str(title)).strip()
    # 이상 대시/물결/문자 정리
    t = t.replace("ㅡ", "-").replace("–", "-").replace("—", "-").replace("~", "-")

    # 연령대/숫자 앞머리 제거
    t = re.sub(r"\b\d{2}\s*[-~]\s*\d{2}\s*대\b", "", t)
    t = re.sub(r"\b\d{2}\s*대\b", "", t)
    t = re.sub(r"\b30\s*40\s*50\s*대\b", "", t)
    t = re.sub(r"\b3040\b", "", t)

    # 맨 앞 숫자/기호 제거
    t = re.sub(r"^[\s\-\–\—\d\.\)\(]+", "", t).strip()
    t = re.sub(r"\s{2,}", " ", t).strip()

    return t or str(title).strip()


# -----------------------------
# Daily topic rotation
# -----------------------------
def _topics_used_today(state: dict) -> set[str]:
    today = _kst_date_key()
    used: set[str] = set()

    hist = (state or {}).get("history") or []
    if not isinstance(hist, list):
        return used

    # 같은 날 기록(kst_date)이 있으면 그걸 우선
    for it in reversed(hist[-80:]):
        if not isinstance(it, dict):
            continue
        if it.get("kst_date") == today and it.get("topic"):
            used.add(str(it.get("topic")))
    if used:
        return used

    # fallback: 최근 3개
    for it in reversed(hist[-3:]):
        if isinstance(it, dict) and it.get("topic"):
            used.add(str(it.get("topic")))
    return used


def _choose_topic_with_rotation(state: dict, forced: str) -> str:
    """
    같은 날 같은 topic이 이미 사용되었으면 다음 topic으로 회전
    """
    order = ["health", "trend", "life"]
    used = _topics_used_today(state)

    if forced not in order:
        forced = "life"

    if forced not in used:
        return forced

    start = order.index(forced)
    for i in range(1, len(order) + 1):
        cand = order[(start + i) % len(order)]
        if cand not in used:
            return cand

    return forced


# -----------------------------
# Image helpers
# -----------------------------
def make_ascii_filename(prefix: str, ext: str = "png") -> str:
    uid = uuid.uuid4().hex[:10]
    prefix = re.sub(r"[^a-zA-Z0-9_-]+", "-", (prefix or "img")).strip("-") or "img"
    return f"{prefix}-{uid}.{ext}"


def _fallback_png_bytes(text: str) -> bytes:
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
        from io import BytesIO

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


def _build_image_prompt(base: str, *, variant: str, seed: int, style_mode: str) -> str:
    """
    style_mode:
      - watercolor : 건강/트렌드 수채화
      - photo      : 쿠팡(실사 제품/사용컷)
      - 기타       : 학습 스타일 문자열 (약하게 힌트)
    """
    rng = random.Random(seed + (1 if variant == "hero" else 2))

    base_raw = (base or "").strip()
    low = base_raw.lower()

    # 공통 금지/안전 규칙 강화
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
        product_hero = [
            "photorealistic e-commerce product photography, clean white or light neutral background, softbox studio lighting, natural shadow, ultra sharp, high detail, centered",
            "photorealistic product shot on minimal tabletop, studio lighting, clean background, crisp edges, high resolution, professional catalog photo",
        ]
        product_body = [
            "photorealistic lifestyle in-use photo in a tidy home, natural window light, hands using the item (no face), realistic textures, clean modern home",
            "photorealistic usage scene, close-up hands demonstrating the item, shallow depth of field, natural indoor light, uncluttered background, no faces",
        ]
        style = rng.choice(product_hero if variant == "hero" else product_body)
        hero_comp = [
            "front view, centered, minimal props, premium clean look",
            "slight top-down angle, catalog composition, product clearly visible",
        ]
        body_comp = [
            "different angle from hero, show real use-case, include subtle context objects",
            "close-up detail + action, show how it works, keep background uncluttered",
        ]
        comp = rng.choice(hero_comp if variant == "hero" else body_comp)
        extra = "title-safe area on lower third (keep product away from bottom)" if variant == "hero" else "avoid looking similar to hero"
        return f"{base_raw}, {style}, {comp}, {extra}"

    # 기타 스타일 힌트
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


# -----------------------------
# Coupang safe wrapper
# -----------------------------
def _inject_coupang_safe(html: str, keyword: str, state: dict) -> Tuple[str, bool, dict]:
    """
    최신: inject_coupang(html, keyword, state) -> (html, inserted, state)
    구버전 호환:
      - inject_coupang(html, keyword=...) -> str 또는 (html, inserted)
      - inject_coupang(html, keyword=...) -> str
    """
    try:
        # 최신 시그니처 권장
        out = inject_coupang(html, keyword=keyword, state=state)  # type: ignore
        if isinstance(out, tuple):
            if len(out) >= 3:
                return _as_html(out[0]), bool(out[1]), out[2]
            if len(out) == 2:
                return _as_html(out[0]), bool(out[1]), state
        return _as_html(out), True, state
    except TypeError:
        # 구버전 시도
        out = inject_coupang(html, keyword=keyword)  # type: ignore
        if isinstance(out, tuple):
            if len(out) == 2:
                return _as_html(out[0]), bool(out[1]), state
            return _as_html(out[0]), True, state
        return _as_html(out), True, state
    except Exception as e:
        print(f"⚠️ inject_coupang failed: {e}")
        return html, False, state


def run() -> None:
    S = Settings()

    openai_client = make_openai_client(S.OPENAI_API_KEY)

    # 이미지 클라이언트(프로젝트 구조 유지)
    img_key = os.getenv("IMAGE_API_KEY", "").strip() or getattr(S, "IMAGE_API_KEY", "") or S.OPENAI_API_KEY
    img_client = make_gemini_client(img_key)

    # state
    state = load_state()
    state = ingest_click_log(state, S.WP_URL)
    state = try_update_from_post_metrics(state)
    history = state.get("history", [])

    # 가드레일
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

    # 2) 시간대 topic 강제 + 같은날 중복 방지 로테이션
    forced = _slot_topic_kst()
    topic = _choose_topic_with_rotation(state, forced)
    used_today = sorted(list(_topics_used_today(state)))
    print(f"🕒 forced={forced} -> chosen={topic} | used_today={used_today}")

    system_prompt = build_system_prompt(topic)
    user_prompt = build_user_prompt(topic, keyword)

    # 3) life 하위주제(성과 기반)
    life_subtopic = ""
    if topic == "life":
        life_subtopic, sub_dbg = pick_life_subtopic(state)
        print("🧩 life_subtopic:", life_subtopic, "| dbg(top3):", (sub_dbg.get("scored") or [])[:3])
        keyword = f"{keyword} {life_subtopic}".strip()
        user_prompt = build_user_prompt(topic, keyword)

    best_image_style, thumb_variant, _ = pick_best_publishing_combo(state, topic=topic)

    # 4) 글 생성 + 품질
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

        post["title"] = _normalize_title(post.get("title", ""))

        dup, reason = pick_retry_reason(post.get("title", ""), history)
        if dup:
            post["sections"] = []
            print(f"♻️ 중복 감지({reason}) → 재생성 유도")
        return post

    post, _ = quality_retry_loop(_gen, max_retry=3)
    post["title"] = _normalize_title(post.get("title", ""))

    # 5) 썸네일 타이틀
    thumb_title = generate_thumbnail_title(openai_client, S.OPENAI_MODEL, post["title"])
    print("🧩 thumb_title:", thumb_title, "| thumb_variant:", thumb_variant)

    # 6) 쿠팡 삽입 “계획” 판단
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

    # 7) 주제별 이미지 스타일 강제
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

    # 8) 이미지 프롬프트 구성
    if topic == "life" and coupang_planned:
        subject = keyword.strip()
        base_prompt = (
            f"{subject} related household item, practical home product, "
            f"product clearly visible, clean minimal background, "
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

    # 9) WP 업로드
    hero_url, hero_media_id = upload_media_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD,
        hero_img_titled, make_ascii_filename("featured")
    )
    body_url, _ = upload_media_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD,
        body_img, make_ascii_filename("body")
    )

    # 10) HTML 생성
    html = _as_html(
        format_post_v2(
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
    )

    # 11) 쿠팡 삽입 (life + planned)  ✅ “진짜 삽입 성공”일 때만 count 증가
    coupang_inserted = False
    if topic == "life" and coupang_planned:
        html, inserted, state = _inject_coupang_safe(html, keyword=keyword, state=state)
        if inserted:
            state = increment_coupang_count(state)
            coupang_inserted = True
            print("🛒 coupang inserted: True")
        else:
            print("⚠️ coupang planned BUT insert failed -> skip count/disclosure")

    # 12) 애드센스 슬롯 (모든 글 공통)
    html = _as_html(inject_adsense_slots(html))
    post["content_html"] = html

    # 13) 발행
    post_id = publish_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD,
        post, hero_url, body_url,
        featured_media_id=hero_media_id,
    )

    # 14) 통계/학습
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

    # history 기록
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
            "kst_date": _kst_date_key(),
            "kst_hour": _kst_now().hour,
            "forced_slot": forced,
        },
    )
    save_state(state)

    print(
        f"✅ 발행 완료: post_id={post_id} | topic={topic} | forced={forced} | sub={life_subtopic} "
        f"| coupang={coupang_inserted} | img_style={image_style_for_stats}"
    )


if __name__ == "__main__":
    run()
