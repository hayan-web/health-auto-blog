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
from app.seed_keywords import get_seed_keywords

# ✅ 시간대 기반 주제 분기
from app.time_router import get_kst_hour, topic_by_kst_hour
# ✅ NEW: 품질 점수/재생성
from app.quality_gate import quality_retry_loop
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


def _stable_seed_int(*parts: str) -> int:
    s = "|".join([p or "" for p in parts])
    h = hashlib.sha256(s.encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _build_image_prompt(base: str, *, variant: str, seed: int) -> str:
    """
    variant: "hero" or "body"
    seed: 실행마다 다르게(하지만 같은 실행 안에서는 안정적)
    """
    HERO_PRESETS = [
        "clean flat illustration, minimal background, centered subject, soft daylight, 35mm lens",
        "3D clay style, simple props, soft studio lighting, front view, shallow depth of field",
        "watercolor illustration, gentle texture paper, airy composition, warm morning light",
        "isometric illustration, neat geometry, pastel colors, top-down slight angle, crisp edges",
    ]
    BODY_PRESETS = [
        "photo-realistic style, different angle, wide shot, natural indoor light, 24mm lens",
        "hand-drawn sketch + light coloring, dynamic perspective, side view, stronger contrast",
        "bold vector art, graphic shapes, high clarity, off-center composition, cool daylight",
        "soft 3D render, different composition, close-up detail shot, rim light, 50mm lens",
    ]

    rng = random.Random(seed + (1 if variant == "hero" else 2))
    preset = rng.choice(HERO_PRESETS if variant == "hero" else BODY_PRESETS)

    base = (base or "").strip()
    low = base.lower()

    # 필수 규칙 보강
    if "single scene" not in low:
        base += ", single scene"
    if "no collage" not in low:
        base += ", no collage"
    if "no text" not in low:
        base += ", no text"
    if ("square" not in low) and ("1:1" not in low):
        base += ", square 1:1"

    if variant == "hero":
        extra = "title-safe area on lower third, simple background, iconic main object"
    else:
        extra = "different composition, different angle, include secondary elements, not similar to hero"

    return f"{base}, {preset}, {extra}"


def run() -> None:
    S = Settings()

    # === 클라이언트 ===
    openai_client = make_openai_client(S.OPENAI_API_KEY)

    # ⚠️ 이미지도 OpenAI로 통일할 거면 make_gemini_client 내부가 OPENAI 이미지 호출로 래핑되어 있어야 합니다.
    # 지금은 사용자님 요청대로 'OPENAI_API_KEY'를 넣습니다(이전 401 방지).
    img_client = make_gemini_client(S.OPENAI_API_KEY)

    state = load_state()
    history = state.get("history", [])

    # === (3) 발행/비용 가드 ===
    cfg = GuardConfig(
        max_posts_per_day=int(getattr(S, "MAX_POSTS_PER_DAY", 3)),
        max_usd_per_month=float(getattr(S, "MAX_USD_PER_MONTH", 30.0)),
    )
    
    try:
        check_limits_or_raise(state, cfg)
    except RuntimeError as e:
        print(f"⛔ 가드레일 차단: {e}")
        print("➡ 이번 회차는 스킵합니다.")
        return

    # 1) 키워드 선정
# (2) topic은 이미 time_router로 계산된 상태라고 가정
seed_keywords = get_seed_keywords(topic)
print("🧩 seed_keywords:", seed_keywords[:10], f"(총 {len(seed_keywords)}개)")

# ✅ topic별 seed를 picker에 전달(지원하면)
try:
    keyword, debug = pick_keyword_by_naver(
        S.NAVER_CLIENT_ID,
        S.NAVER_CLIENT_SECRET,
        history,
        seed_keywords=seed_keywords,
    )
except TypeError:
    # ✅ picker가 아직 seed_keywords 인자를 지원 안 하면
    # ENV를 임시로 덮어써서 기존 picker를 그대로 활용(틀 안 깨짐)
    os.environ["NAVER_SEED_KEYWORDS"] = ",".join(seed_keywords)
    keyword, debug = pick_keyword_by_naver(
        S.NAVER_CLIENT_ID,
        S.NAVER_CLIENT_SECRET,
        history,
    )

    )
    print("🔎 선택된 키워드:", keyword)
    print("🧾 키워드 점수(상위 3):", (debug.get("scored") or [])[:3])

    # === (2) 시간대 + 키워드 기반 주제 분기 ===
    kst_hour = get_kst_hour()
    time_topic = topic_by_kst_hour(kst_hour)

    # 키워드 힌트가 강하면 keyword 기반, 아니면 시간대 우선
    keyword_topic = guess_topic_from_keyword(keyword)
    topic = time_topic or keyword_topic

    system_prompt = build_system_prompt(topic)
    user_prompt = build_user_prompt(topic, keyword)

    print(f"🧭 KST hour={kst_hour}, time_topic={time_topic}, keyword_topic={keyword_topic}")
    print(f"🧭 final topic={topic}")

    # 2) 글 생성 + 중복 회피 + (1) 품질 점수화 재생성
    MAX_RETRY = 3

    def _generate_once():
        # ✅ generate_blog_post가 (system_prompt, user_prompt)를 아직 지원 안 해도
        # 기존 동작이 깨지지 않도록 호환 처리
        try:
            candidate = generate_blog_post(
                openai_client,
                S.OPENAI_MODEL,
                keyword,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        except TypeError:
            candidate = generate_blog_post(openai_client, S.OPENAI_MODEL, keyword)

        dup, reason = pick_retry_reason(candidate.get("title", ""), history)
        if dup:
            print(f"♻️ 중복 감지({reason}) → 재생성 트리거")
            # 중복이면 점수 떨어뜨려 재생성 루프로 유도
            candidate["sections"] = []
        return candidate

    post, q = quality_retry_loop(_generate_once, max_retry=MAX_RETRY)
    print(f"✅ 품질 OK ({q.score}/100) → 진행")

    # 3) 썸네일용 짧은 타이틀
    thumb_title = generate_thumbnail_title(openai_client, S.OPENAI_MODEL, post["title"])
    print("🧩 썸네일 타이틀:", thumb_title)

    # 4) 이미지 2장 생성 (프롬프트 다양화 + 실패 시 fallback)
    base_prompt = (post.get("img_prompt") or "").strip()
    if not base_prompt:
        base_prompt = f"{keyword} 주제의 블로그 대표 삽화, single scene, no collage, no text, square 1:1"

    seed = _stable_seed_int(keyword, post.get("title", ""), str(int(time.time())))
    hero_prompt = _build_image_prompt(base_prompt, variant="hero", seed=seed)
    body_prompt = _build_image_prompt(base_prompt, variant="body", seed=seed)

    print("🖼️ hero_prompt:", hero_prompt[:140], "...")
    print("🖼️ body_prompt:", body_prompt[:140], "...")

    try:
        print("🎨 이미지(상단/대표) 생성 중...")
        hero_img = generate_nanobanana_image_png_bytes(
            img_client, S.GEMINI_IMAGE_MODEL, hero_prompt
        )
    except Exception as e:
        print(f"⚠️ 대표 이미지 생성 실패 → fallback: {e}")
        hero_img = _fallback_png_bytes(keyword)

    try:
        print("🎨 이미지(중간) 생성 중...")
        body_img = generate_nanobanana_image_png_bytes(
            img_client, S.GEMINI_IMAGE_MODEL, body_prompt
        )
    except Exception as e:
        print(f"⚠️ 중간 이미지 생성 실패 → 대표 이미지 재사용: {e}")
        body_img = hero_img

    hero_img = to_square_1024(hero_img)
    body_img = to_square_1024(body_img)

    # 5) 대표 이미지에 타이틀 오버레이
    hero_img_titled = add_title_to_image(hero_img, thumb_title)
    hero_img_titled = to_square_1024(hero_img_titled)

    # 6) WP 미디어 업로드
    # ⚠️ 업로드 후 Imsanity가 jpg로 변환하더라도, 업로드 파일명은 png여도 상관없습니다.
    hero_name = make_ascii_filename("featured", "png")
    body_name = make_ascii_filename("body", "png")

    hero_url, hero_media_id = upload_media_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD, hero_img_titled, hero_name
    )
    body_url, _ = upload_media_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD, body_img, body_name
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

    # 9) 애드센스 슬롯 삽입
    html = inject_adsense_slots(html)

    # ✅ (4) 발행 전 HTML 미리보기 저장(무조건 생성)
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

    # ✅ 발행 카운트 증가(가드용) - 구현에 따라 state를 반환할 수도 있어 안전하게 처리
    try:
        new_state = increment_post_count(state)
        if isinstance(new_state, dict):
            state = new_state
    except TypeError:
        # increment_post_count(state) 가 in-place라면 그대로 진행
    # 🔢 비용 추정 (텍스트 토큰은 보수적으로 1800으로 가정)
    estimated_usd = estimate_post_usd(
        text_tokens=1800,
        image_count=2,
    )

    state = increment_post_count(
        state,
        estimated_usd=estimated_usd,
    )

    print(f"💰 비용 추정 누적: +${estimated_usd:.4f}")


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
