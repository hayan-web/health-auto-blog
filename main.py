import base64
import os
import re
import uuid
from pathlib import Path

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

from app.formatter_v2 import format_post_v2
from app.monetize_adsense import inject_adsense_slots
from app.monetize_coupang import inject_coupang

# ✅ 신규: 이미지 변주 + 품질 점수 + 예산가드
from app.image_variants import build_image_prompts
from app.quality import score_post, needs_regen
from app.budget_guard import BudgetConfig, can_post, add_usage


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
        from io import BytesIO

        img = Image.new("RGB", (1024, 1024), (245, 245, 245))
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 48)
        except Exception:
            font = ImageFont.load_default()

        msg = (text or "image").strip()[:40]
        bbox = draw.textbbox((0, 0), msg, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        draw.text(((1024 - w) / 2, (1024 - h) / 2), msg, fill=(60, 60, 60), font=font)

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


def _classify_topic(keyword: str) -> str:
    """
    2️⃣ 주제별 프롬프트 분기(간단 룰베이스)
    - 원하면 키워드 리스트/정규식으로 더 정교하게 확장 가능
    """
    k = (keyword or "").lower()
    health = ["갱년기", "혈압", "고지혈증", "수면", "관절", "운동", "스트레스", "식단", "건강", "영양"]
    it = ["스마트폰", "pc", "윈도우", "아이폰", "안드로이드", "앱", "오류", "설정", "보안", "와이파이"]
    for w in health:
        if w in k:
            return "health"
    for w in it:
        if w in k:
            return "it"
    return "life"


def _save_preview_html(title: str, html: str) -> str:
    """
    4️⃣ 발행 전 HTML 미리보기 저장
    GitHub Actions에서 upload-artifact로 올릴 수 있게 preview/에 저장
    """
    preview_dir = Path("preview")
    preview_dir.mkdir(parents=True, exist_ok=True)

    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", title).strip("-")[:60] or "post"
    fname = f"{slug}.html"
    path = preview_dir / fname
    path.write_text(html, encoding="utf-8")
    return str(path)


def run() -> None:
    S = Settings()

    openai_client = make_openai_client(S.OPENAI_API_KEY)
    img_client = make_gemini_client(S.OPENAI_API_KEY)  # 내부는 OpenAI 이미지 client

    state = load_state()
    history = state.get("history", [])

    # 3️⃣ 발행 횟수·API 비용 제어 (예산 가드)
    cfg = BudgetConfig(
        max_posts_per_day=int(getattr(S, "MAX_POSTS_PER_DAY", 3) or 3),
        max_images_per_day=int(getattr(S, "MAX_IMAGES_PER_DAY", 6) or 6),
        image_cost_usd=float(getattr(S, "IMAGE_COST_USD", 0.011) or 0.011),
        max_monthly_usd=float(getattr(S, "MAX_MONTHLY_USD", 15.0) or 15.0),
    )
    ok, reason = can_post(state, cfg)
    if not ok:
        print(f"⛔ 스킵: {reason}")
        return

    # 1) 키워드 선정
    keyword, debug = pick_keyword_by_naver(S.NAVER_CLIENT_ID, S.NAVER_CLIENT_SECRET, history)
    print("🔎 선택된 키워드:", keyword)
    print("🧾 키워드 점수(상위 3):", (debug.get("scored") or [])[:3])

    topic = _classify_topic(keyword)
    print("🧭 topic:", topic)

    # 2) 글 생성 + (중복 회피) + (품질 점수화로 재생성 트리거)
    MAX_RETRY = 4
    post = None

    for i in range(1, MAX_RETRY + 1):
        candidate = generate_blog_post(openai_client, S.OPENAI_MODEL, keyword)

        dup, reason_dup = pick_retry_reason(candidate.get("title", ""), history)
        if dup:
            print(f"♻️ 중복 감지({reason_dup}) → 재생성 {i}/{MAX_RETRY}")
            continue

        score, reasons = score_post(candidate)
        print(f"🧪 품질 점수: {score}/100", (" / ".join(reasons) if reasons else ""))

        if needs_regen(score, threshold=int(getattr(S, "QUALITY_THRESHOLD", 75) or 75)):
            print(f"🔁 품질 미달 → 재생성 {i}/{MAX_RETRY}")
            continue

        post = candidate
        break

    if not post:
        raise RuntimeError("생성 실패: 중복/품질 기준을 만족하는 글을 만들지 못했습니다.")

    # 3) 썸네일용 짧은 타이틀
    thumb_title = generate_thumbnail_title(openai_client, S.OPENAI_MODEL, post["title"])
    print("🧩 썸네일 타이틀:", thumb_title)

    # 4) 이미지 프롬프트 다양화(대표/본문)
    base_prompt = (post.get("img_prompt") or "").strip()
    hero_prompt, body_prompt = build_image_prompts(base_prompt, keyword)

    # 5) 이미지 2장 생성 (OpenAI 이미지로 통일된 함수)
    try:
        print("🎨 이미지(상단/대표) 생성 중...")
        hero_img = generate_nanobanana_image_png_bytes(img_client, S.GEMINI_IMAGE_MODEL, hero_prompt)
    except Exception as e:
        print(f"⚠️ 대표 이미지 생성 실패 → fallback: {e}")
        hero_img = _fallback_png_bytes(keyword)

    try:
        print("🎨 이미지(중간) 생성 중...")
        body_img = generate_nanobanana_image_png_bytes(img_client, S.GEMINI_IMAGE_MODEL, body_prompt)
    except Exception as e:
        print(f"⚠️ 중간 이미지 생성 실패 → 대표 이미지 재사용: {e}")
        body_img = hero_img

    hero_img = to_square_1024(hero_img)
    body_img = to_square_1024(body_img)

    # 6) 대표 이미지에 타이틀 오버레이
    hero_img_titled = add_title_to_image(hero_img, thumb_title)
    hero_img_titled = to_square_1024(hero_img_titled)

    # 7) WP 미디어 업로드
    hero_name = make_ascii_filename("featured", "png")
    body_name = make_ascii_filename("body", "png")

    hero_url, hero_media_id = upload_media_to_wp(S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD, hero_img_titled, hero_name)
    body_url, _ = upload_media_to_wp(S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD, body_img, body_name)

    # 8) A안 레이아웃 HTML 생성
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

    # 9) 쿠팡 삽입 + 실제 삽입 시에만 대가성 문구 최상단
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

    # 10) 애드센스 슬롯 3개 삽입
    html = inject_adsense_slots(html)

    # 11) 4️⃣ 발행 전 HTML 미리보기 저장
    preview_path = _save_preview_html(post["title"], html)
    print("🧾 preview saved:", preview_path)

    # 12) WP 글 발행
    post["content_html"] = html
    post_id = publish_to_wp(
        S.WP_URL,
        S.WP_USERNAME,
        S.WP_APP_PASSWORD,
        post,
        hero_url,
        body_url,
        featured_media_id=hero_media_id,
    )

    # 13) 히스토리 저장 + 예산 사용량 기록(이미지 2장 + 포스팅 1회)
    state = add_history_item(
        state,
        {
            "post_id": post_id,
            "keyword": post.get("keyword", keyword),
            "title": post["title"],
            "title_fp": _title_fingerprint(post["title"]),
        },
    )

    # 비용/횟수 카운팅(간단 추정)
    state = add_usage(state, posts=1, images=2, spend_usd=2 * cfg.image_cost_usd)
    save_state(state)

    print(f"✅ 발행 완료! post_id={post_id}")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"❌ 시스템 종료: {e}")
        raise
