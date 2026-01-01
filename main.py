import base64
import re
import uuid

from app.config import Settings
from app.ai_openai import (
    make_openai_client,
    generate_blog_post,
    generate_thumbnail_title,
)

# ✅ make_gemini_client가 레포 상황에 따라 없을 수 있어 ImportError 방어
try:
    from app.ai_gemini_image import (
        make_gemini_client,
        generate_nanobanana_image_png_bytes,
    )
except ImportError:
    make_gemini_client = None  # type: ignore
    from app.ai_gemini_image import generate_nanobanana_image_png_bytes  # type: ignore

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

# ✅ (NEW) 1~4 기능 추가
from app.quality import score_post
from app.prompt_router import get_generation_context
from app.budget_guard import assert_can_run, mark_post_published
from app.preview import save_html_preview


S = Settings()


def make_ascii_filename(prefix: str, ext: str = "png") -> str:
    uid = uuid.uuid4().hex[:10]
    prefix = re.sub(r"[^a-zA-Z0-9_-]+", "-", (prefix or "img")).strip("-")
    if not prefix:
        prefix = "img"
    return f"{prefix}-{uid}.{ext}"


def _fallback_png_bytes(text: str) -> bytes:
    """
    Gemini가 실패할 때 대체 이미지 생성.
    - PIL 있으면 1024x1024로 텍스트 넣어 생성
    - PIL 없으면 최소 PNG(1x1)라도 반환해서 파이프라인이 죽지 않게
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

        w, h = draw.textbbox((0, 0), msg, font=font)[2:]
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
    """
    inject_coupang이 아래 케이스 모두 커버:
    - str 반환
    - (str, bool) 반환
    """
    if isinstance(result, tuple) and len(result) >= 1:
        html = result[0]
        inserted = bool(result[1]) if len(result) >= 2 else True
        return str(html), inserted
    return str(result), False  # 변화 여부는 호출부에서 비교로 판단 가능


def run() -> None:
    S = Settings()

    openai_client = make_openai_client(S.OPENAI_API_KEY)

    # ✅ Gemini 클라이언트는 없을 수도 있으니 방어
    gemini_client = None
    if make_gemini_client is not None:
        try:
            gemini_client = make_gemini_client(S.GOOGLE_API_KEY)  # type: ignore
        except Exception as e:
            print(f"⚠️ Gemini client 생성 실패 → fallback 이미지로 진행: {e}")
            gemini_client = None

    state = load_state()
    history = state.get("history", [])

    # ✅ 3) 발행 횟수/비용 제어(일단 '발행 횟수' 중심)
    # 기본 daily_limit=3 (원하면 Settings에 값 추가해서 쓰면 더 좋음)
    assert_can_run(state, daily_limit=int(getattr(S, "DAILY_POST_LIMIT", 3)))

    # 1) 키워드 선정
    keyword, debug = pick_keyword_by_naver(
        S.NAVER_CLIENT_ID, S.NAVER_CLIENT_SECRET, history
    )
    print("🔎 선택된 키워드:", keyword)
    print("🧾 키워드 점수(상위 3):", (debug.get("scored") or [])[:3])

    # ✅ 2) 주제별 프롬프트 분기(health/life/it)
    category, extra_prompt = get_generation_context(keyword)
    print("🧭 카테고리:", category)

    # 2) 글 생성 + 중복 회피 + ✅ 1) 품질 점수화(미달이면 자동 재생성)
    MAX_RETRY = 4
    MIN_QUALITY = int(getattr(S, "MIN_QUALITY_SCORE", 80))

    post = None

    for i in range(1, MAX_RETRY + 1):
        # (중요) generate_blog_post 시그니처가 다를 수 있으니 TypeError fallback
        try:
            candidate = generate_blog_post(
                openai_client,
                S.OPENAI_MODEL,
                keyword,
                category=category,
                extra_prompt=extra_prompt,
            )
        except TypeError:
            candidate = generate_blog_post(openai_client, S.OPENAI_MODEL, keyword)

        # 품질 점수
        q = score_post(candidate)
        if q.score < MIN_QUALITY:
            print(f"🧪 품질 FAIL ({q.score}/100) → 재생성 {i}/{MAX_RETRY}")
            for r in q.reasons[:8]:
                print(" -", r)
            continue
        else:
            print(f"🧪 품질 OK ({q.score}/100) → 진행")

        # 중복 체크
        dup, reason = pick_retry_reason(candidate.get("title", ""), history)
        if dup:
            print(f"♻️ 중복 감지({reason}) → 재생성 {i}/{MAX_RETRY}")
            continue

        post = candidate
        break

    if not post:
        raise RuntimeError("생성 실패: 품질/중복 조건을 만족하는 글을 만들지 못했습니다.")

    # 3) 썸네일용 짧은 타이틀
    thumb_title = generate_thumbnail_title(openai_client, S.OPENAI_MODEL, post["title"])
    print("🧩 썸네일 타이틀:", thumb_title)

    # 4) 이미지 2장 생성 (실패 시 fallback)
    hero_prompt = (post.get("img_prompt") or "").strip()
    if not hero_prompt:
        hero_prompt = f"{keyword} 주제의 {category} 블로그 삽화, single scene, no collage, no text, square 1:1"

    body_prompt = hero_prompt + ", single scene, no collage, different composition, different angle, no text, square 1:1"

    # 대표 이미지
    try:
        if gemini_client is None:
            raise RuntimeError("Gemini client 없음")
        print("🎨 Gemini 이미지(상단/대표) 생성 중...")
        hero_img = generate_nanobanana_image_png_bytes(
            gemini_client, S.GEMINI_IMAGE_MODEL, hero_prompt
        )
    except Exception as e:
        print(f"⚠️ 대표 이미지 생성 실패 → 대체 이미지로 진행: {e}")
        hero_img = _fallback_png_bytes(f"{keyword}")

    # 중간 이미지
    try:
        if gemini_client is None:
            raise RuntimeError("Gemini client 없음")
        print("🎨 Gemini 이미지(중간) 생성 중...")
        body_img = generate_nanobanana_image_png_bytes(
            gemini_client, S.GEMINI_IMAGE_MODEL, body_prompt
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
        disclosure_html="",  # 쿠팡 실제 삽입 시 아래에서 채움
        summary_bullets=post.get("summary_bullets") or None,
        sections=sections if isinstance(sections, list) else [],
        warning_bullets=post.get("warning_bullets") or None,
        checklist_bullets=post.get("checklist_bullets") or None,
        outro=outro,
    )

    # 8) 쿠팡 삽입 + “실제 삽입”일 때만 대가성 문구 최상단
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

    # 9) 애드센스 수동 슬롯 3개 삽입
    html = inject_adsense_slots(html)

    # ✅ 4) 발행 전 HTML 미리보기 저장
    preview_path = save_html_preview(html, title=post["title"])
    print("👀 HTML 미리보기 저장:", preview_path)

    # 10) publish_to_wp가 content_html을 우선 사용하도록 교체
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

    # 12) 히스토리 저장 (+ budget 카운트 업데이트)
    state = add_history_item(
        state,
        {
            "post_id": post_id,
            "keyword": post.get("keyword", keyword),
            "title": post["title"],
            "title_fp": _title_fingerprint(post["title"]),
        },
    )

    # 비용은 지금은 "추정치"만 (원하면 나중에 토큰 usage로 정확히 누적 가능)
    est_cost = float(getattr(S, "EST_COST_PER_POST_USD", 0.03))
    state = mark_post_published(state, est_cost_usd=est_cost)

    save_state(state)

    print(f"✅ 발행 완료! post_id={post_id}")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"❌ 시스템 종료: {e}")
        raise
