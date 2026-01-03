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


def _kst_now() -> datetime:
    return datetime.now(tz=KST)


def _kst_date_key(dt: datetime | None = None) -> str:
    d = dt or _kst_now()
    return d.strftime("%Y-%m-%d")


def _as_html(x):
    """format_post_v2 / inject_* 가 (html, ...) 튜플을 반환하는 케이스 안전 처리"""
    if isinstance(x, tuple) and x:
        return x[0]
    return x

def _extract_first_coupang_url(html: str) -> str:
    """
    inject_coupang가 넣어준 쿠팡 링크가 있으면 첫 URL만 뽑습니다.
    - coupang.com / coupang.co.kr / link.coupang.com 등 대응
    """
    if not html:
        return ""
    # href="..."
    m = re.search(r'href=["\'](https?://[^"\']*(?:coupang\.com|coupang\.co\.kr|link\.coupang\.com)[^"\']*)["\']', html, re.I)
    if m:
        return m.group(1)
    # 그냥 텍스트로 들어간 URL
    m = re.search(r'(https?://\S*(?:coupang\.com|coupang\.co\.kr|link\.coupang\.com)\S*)', html, re.I)
    if m:
        return m.group(1).rstrip(').,<>"]\'')
    return ""


def _render_coupang_cta(url: str, *, title: str = "", variant: str = "top") -> str:
    """
    정책적으로 안전한 '확인'형 CTA
    variant: top | mid | bottom (문구/강조만 다르게)
    """
    if not url:
        return ""

    if variant == "top":
        headline = "🔥 쿠팡에서 가격/쿠폰 적용 확인"
        sub = "로켓배송·쿠폰은 시점에 따라 달라질 수 있어요."
        btn = "쿠팡에서 가격 보기"
    elif variant == "mid":
        headline = "✅ 지금 조건(쿠폰/배송) 확인"
        sub = "옵션별 가격이 달라질 수 있어요."
        btn = "할인/옵션 확인하기"
    else:
        headline = "🚚 구매 전 마지막 체크"
        sub = "최종 가격·배송 조건을 한 번 더 확인하세요."
        btn = "최저가/배송 확인하기"

    # 테마가 스타일을 지우더라도 최소한 버튼처럼 보이게 inline style 사용
    return f"""
<div class="coupang-cta" style="border:1px solid #e5e7eb;border-radius:12px;padding:14px 14px;margin:14px 0;background:#fff;">
  <div style="font-weight:700;font-size:16px;line-height:1.2;margin-bottom:6px;">{headline}</div>
  <div style="color:#6b7280;font-size:13px;line-height:1.3;margin-bottom:10px;">{sub}</div>
  <a href="{url}" target="_blank" rel="nofollow sponsored noopener"
     style="display:block;text-align:center;padding:12px 14px;border-radius:10px;
            background:#111827;color:#fff;text-decoration:none;font-weight:700;">
    {btn} →
  </a>
</div>
""".strip()


def _insert_after_first_summary(html: str, block: str) -> str:
    """
    요약 박스(1분 요약) 뒤에 넣고 싶지만 테마별 구조가 달라서,
    우선적으로 첫 번째 <ul> 다음, 없으면 본문 맨 앞에 삽입합니다.
    """
    if not block:
        return html
    if not html:
        return block

    # 첫 <ul> 뒤
    idx = html.find("</ul>")
    if idx != -1:
        return html[: idx + 5] + "\n" + block + "\n" + html[idx + 5 :]

    # fallback: 맨 앞
    return block + "\n" + html


def _insert_near_middle(html: str, block: str) -> str:
    """
    중간 삽입: 두 번째 <h2> 앞 or 대략 절반 지점
    """
    if not block or not html:
        return html

    hs = [m.start() for m in re.finditer(r"<h2\b", html, re.I)]
    if len(hs) >= 2:
        pos = hs[1]
        return html[:pos] + block + "\n" + html[pos:]

    # fallback: 절반
    pos = max(0, len(html) // 2)
    return html[:pos] + "\n" + block + "\n" + html[pos:]


def _insert_before_comments(html: str, block: str) -> str:
    """
    댓글 영역 직전(있다면) 또는 맨 끝에 삽입
    """
    if not block:
        return html
    if not html:
        return block

    # 워드프레스 댓글 anchor 흔한 패턴
    for pat in [r'id="comments"', r'class="comments"', r'댓글 남기기']:
        m = re.search(pat, html, re.I)
        if m:
            pos = m.start()
            return html[:pos] + block + "\n" + html[pos:]

    return html + "\n" + block


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


def _normalize_title(title: str) -> str:
    if not title:
        return title

    t = unicodedata.normalize("NFKC", str(title)).strip()
    t = t.replace("ㅡ", "-").replace("–", "-").replace("—", "-").replace("~", "-")

    t = re.sub(r"\b\d{2}\s*[-~]\s*\d{2}\s*대\b", "", t)
    t = re.sub(r"\b\d{2}\s*대\b", "", t)
    t = re.sub(r"\b30\s*40\s*50\s*대\b", "", t)
    t = re.sub(r"\b3040\b", "", t)

    t = re.sub(r"^[\s\-\–\—\d\.\)\(]+", "", t).strip()
    t = re.sub(r"\s{2,}", " ", t).strip()

    return t or str(title).strip()


def _slot_topic_kst(dt: datetime | None = None) -> str:
    d = dt or _kst_now()
    h = d.hour
    if 9 <= h < 12:
        return "health"
    if 13 <= h < 16:
        return "trend"
    return "life"


def _topics_used_today(state: dict) -> set[str]:
    today = _kst_date_key()
    used: set[str] = set()
    hist = (state or {}).get("history") or []
    if not isinstance(hist, list):
        return used

    for it in reversed(hist[-50:]):
        if not isinstance(it, dict):
            continue
        if it.get("kst_date") == today and it.get("topic"):
            used.add(str(it.get("topic")))
    if used:
        return used

    for it in reversed(hist[-3:]):
        if isinstance(it, dict) and it.get("topic"):
            used.add(str(it.get("topic")))
    return used


def _choose_topic_with_rotation(state: dict, forced: str) -> str:
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


def _build_image_prompt(base: str, *, variant: str, seed: int, style_mode: str) -> str:
    rng = random.Random(seed + (1 if variant == "hero" else 2))

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

    img_key = os.getenv("IMAGE_API_KEY", "").strip() or getattr(S, "IMAGE_API_KEY", "") or S.OPENAI_API_KEY
    img_client = make_gemini_client(img_key)

    state = load_state()
    state = ingest_click_log(state, S.WP_URL)
    state = try_update_from_post_metrics(state)

    history = state.get("history", [])

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
    print(f"🕒 forced={forced} -> chosen={topic} | used_today={sorted(list(_topics_used_today(state)))}")

    system_prompt = build_system_prompt(topic)
    user_prompt = build_user_prompt(topic, keyword)

    # 3) life 하위주제
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

    # 10) HTML 생성 (✅ 튜플 반환 안전 처리)
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

    # 11) 쿠팡 삽입 (life + planned)
    coupang_inserted = False
# ✅ 쿠팡은 life + planned일 때만
coupang_inserted = False
if topic == "life" and coupang_planned:
    html2 = _as_html(inject_coupang(html, keyword=keyword))

    # inject_coupang가 성공했는지 URL로 검증
    coupang_url = _extract_first_coupang_url(html2)

    # 1) 성공: 버튼형 CTA 3곳에 강제 삽입
    if coupang_url:
        top_cta = _render_coupang_cta(coupang_url, title=post.get("title",""), variant="top")
        mid_cta = _render_coupang_cta(coupang_url, title=post.get("title",""), variant="mid")
        bot_cta = _render_coupang_cta(coupang_url, title=post.get("title",""), variant="bottom")

        html2 = _insert_after_first_summary(html2, top_cta)
        html2 = _insert_near_middle(html2, mid_cta)
        html2 = _insert_before_comments(html2, bot_cta)

        # 대가성 문구는 최상단에 보이게
        disclosure = (
            '<div class="disclosure" style="padding:10px 12px;border-radius:10px;background:#fff7ed;'
            'border:1px solid #fed7aa;color:#9a3412;margin:10px 0;">'
            '이 포스팅은 쿠팡 파트너스 활동의 일환으로 일정액의 수수료를 제공받을 수 있습니다.'
            '</div>'
        )
        if '<div class="wrap">' in html2:
            html2 = html2.replace('<div class="wrap">', f'<div class="wrap">\n{disclosure}', 1)
        else:
            html2 = disclosure + "\n" + html2

        state = increment_coupang_count(state)
        coupang_inserted = True
        html = html2
        print("🛒 coupang injected: URL found + CTA blocks inserted")

    # 2) 실패: 로그를 남기고, 글은 그대로 진행(대가성 문구도 넣지 않음)
    else:
        html = html2
        print("⚠️ coupang planned BUT no URL found in HTML (inject failed or stripped).")

        state = increment_coupang_count(state)
        coupang_inserted = True

    # 12) 애드센스 슬롯 (✅ 튜플 반환 안전 처리)
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
