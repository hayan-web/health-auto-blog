import base64
import os
import re
import uuid
from datetime import datetime, timezone, timedelta

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


# KST 기준(서버가 UTC여도 일/월 카운트 흔들리지 않게)
KST = timezone(timedelta(hours=9))

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

        bbox = draw.textbbox((0, 0), msg, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
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
    return str(result), False


def _topic_from_keyword(keyword: str) -> str:
    """
    2️⃣ 주제별 프롬프트 분기(건강/생활/IT)
    - 기존 generate_blog_post() 시그니처를 안 바꾸기 위해 keyword를 살짝 보강하는 방식으로만 사용
    """
    k = (keyword or "").lower()

    it_words = ["스마트폰", "pc", "윈도우", "앱", "계정", "구독", "보안", "설정", "오류", "인터넷", "와이파이", "노트북"]
    life_words = ["청소", "정리", "세탁", "요리", "레시피", "살림", "자취", "생활비", "가계부", "수납", "이사", "집안"]
    health_words = ["혈압", "고지혈", "당뇨", "관절", "스트레스", "수면", "식단", "운동", "갱년기", "비만", "통증", "건강"]

    if any(w.lower() in k for w in it_words):
        return "it"
    if any(w.lower() in k for w in life_words):
        return "life"
    if any(w.lower() in k for w in health_words):
        return "health"
    return "health"


def _keyword_for_prompt(keyword: str, topic: str) -> str:
    """
    주제 태그를 keyword에 덧붙여 모델이 ‘톤/구성’을 더 안정적으로 따르도록 유도.
    (기존 로직을 깨지 않기 위해 "keyword 문자열만" 가공)
    """
    tag = {
        "health": "건강 정보(과장 금지, 실천 팁 중심)",
        "life": "생활 정보(실용 팁, 체크리스트 중심)",
        "it": "IT 문제 해결(초간단 단계, 오류 원인/해결 중심)",
    }.get(topic, "건강 정보")
    return f"{keyword} | {tag}"


def _quality_score_post(post: dict) -> tuple[int, list[str]]:
    """
    1️⃣ 글 품질 점수화 (자동 재생성 트리거)
    - 기존 생성 포맷( sections / summary_bullets 등 )에 최대한 맞춰 점수 부여
    """
    reasons: list[str] = []
    score = 100

    title = (post.get("title") or "").strip()
    if len(title) < 10:
        score -= 15
        reasons.append("제목이 너무 짧음(10자 미만)")

    sections = post.get("sections") or []
    if not isinstance(sections, list) or len(sections) < 4:
        score -= 20
        reasons.append("섹션 개수가 부족함(최소 4개 권장)")

    if isinstance(sections, list):
        for idx, sec in enumerate(sections[:8], start=1):
            body = ""
            if isinstance(sec, dict):
                body = (sec.get("body") or "").strip()
            elif isinstance(sec, str):
                body = sec.strip()

            # 너무 짧은 섹션은 글이 얇아 보임
            if len(body) < 140:
                score -= 6
                reasons.append(f"섹션{idx}: body가 너무 짧음(140자 미만)")

    img_prompt = (post.get("img_prompt") or "").strip().lower()
    if "collage" in img_prompt or "text" in img_prompt:
        score -= 8
        reasons.append("img_prompt에 콜라주/텍스트 유발 단어 포함 가능")

    # 요약/체크리스트가 있으면 가산(없어도 FAIL은 아님)
    if post.get("summary_bullets"):
        score += 3
    if post.get("checklist_bullets"):
        score += 3

    score = max(0, min(100, score))
    return score, reasons


def _enforce_budget(state: dict) -> dict:
    """
    3️⃣ 발행 횟수·(간이)비용 자동 제어
    - 일/월 발행 횟수 제한(ENV로 제어)
    - 여기서는 비용을 ‘발행 횟수’로 1차 제어(토큰/원가 추적은 ai_openai 쪽 데이터가 있어야 정밀 가능)
    """
    max_posts_per_day = int(os.getenv("MAX_POSTS_PER_DAY", "3"))
    max_posts_per_month = int(os.getenv("MAX_POSTS_PER_MONTH", "60"))

    now = datetime.now(KST)
    day_key = now.strftime("%Y-%m-%d")
    month_key = now.strftime("%Y-%m")

    stats = state.get("stats") or {}
    daily = stats.get("daily") or {}
    monthly = stats.get("monthly") or {}

    daily_count = int(daily.get(day_key, 0))
    monthly_count = int(monthly.get(month_key, 0))

    if daily_count >= max_posts_per_day:
        raise RuntimeError(f"예산/횟수 제한: 오늘 발행 한도 초과 ({daily_count}/{max_posts_per_day})")
    if monthly_count >= max_posts_per_month:
        raise RuntimeError(f"예산/횟수 제한: 이번달 발행 한도 초과 ({monthly_count}/{max_posts_per_month})")

    # 아직 증가시키지 않고, 발행 성공 후 증가시키기 위해 state에 담아둠
    state["_budget_meta"] = {
        "day_key": day_key,
        "month_key": month_key,
        "daily_count": daily_count,
        "monthly_count": monthly_count,
        "max_posts_per_day": max_posts_per_day,
        "max_posts_per_month": max_posts_per_month,
    }
    return state


def _bump_budget_counts(state: dict) -> dict:
    meta = state.get("_budget_meta") or {}
    day_key = meta.get("day_key")
    month_key = meta.get("month_key")
    if not day_key or not month_key:
        return state

    stats = state.get("stats") or {}
    daily = stats.get("daily") or {}
    monthly = stats.get("monthly") or {}

    daily[day_key] = int(daily.get(day_key, 0)) + 1
    monthly[month_key] = int(monthly.get(month_key, 0)) + 1

    stats["daily"] = daily
    stats["monthly"] = monthly
    state["stats"] = stats
    state.pop("_budget_meta", None)
    return state


def _save_preview_html(html: str, title: str) -> str:
    """
    4️⃣ 발행 전 HTML 미리보기 저장
    - preview/latest.html
    - preview/post-YYYYMMDD-HHMMSS-<slug>.html
    """
    os.makedirs("preview", exist_ok=True)

    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", (title or "post")).strip("-")
    slug = slug[:60] if slug else "post"

    ts = datetime.now(KST).strftime("%Y%m%d-%H%M%S")
    path = os.path.join("preview", f"post-{ts}-{slug}.html")
    latest = os.path.join("preview", "latest.html")

    # 브라우저에서 바로 보기 좋게 최소한의 wrapper
    doc = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>{title}</title>
</head>
<body>
{html}
</body>
</html>
"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    with open(latest, "w", encoding="utf-8") as f:
        f.write(doc)

    print(f"🧾 preview saved: {path}")
    return path


def run() -> None:
    S = Settings()

    openai_client = make_openai_client(S.OPENAI_API_KEY)
    gemini_client = make_gemini_client(S.GOOGLE_API_KEY)

    state = load_state()
    history = state.get("history", [])

    # 3️⃣ 발행 횟수/간이 예산 제한 (발행 전에 체크)
    state = _enforce_budget(state)

    # 1) 키워드 선정
    keyword, debug = pick_keyword_by_naver(
        S.NAVER_CLIENT_ID, S.NAVER_CLIENT_SECRET, history
    )
    print("🔎 선택된 키워드:", keyword)
    print("🧾 키워드 점수(상위 3):", (debug.get("scored") or [])[:3])

    # 2️⃣ 주제 분기
    topic = _topic_from_keyword(keyword)
    keyword_prompt = _keyword_for_prompt(keyword, topic)
    print("🧭 topic:", topic)
    print("🧩 keyword_for_prompt:", keyword_prompt)

    # 2) 글 생성 + 중복 회피 + 1️⃣ 품질 점수화(재생성 트리거)
    MAX_RETRY = int(os.getenv("MAX_RETRY", "4"))
    QUALITY_MIN = int(os.getenv("QUALITY_MIN", "75"))

    post = None
    last_score = 0
    last_reasons: list[str] = []

    for i in range(1, MAX_RETRY + 1):
        candidate = generate_blog_post(openai_client, S.OPENAI_MODEL, keyword_prompt)

        # 중복 체크(최근 제목과 유사)
        dup, reason = pick_retry_reason(candidate.get("title", ""), history)
        if dup:
            print(f"♻️ 중복 감지({reason}) → 재생성 {i}/{MAX_RETRY}")
            continue

        # 품질 점수화
        score, reasons = _quality_score_post(candidate)
        last_score, last_reasons = score, reasons

        if score < QUALITY_MIN:
            print(f"🧪 품질 FAIL ({score}/100) → 재생성 {i}/{MAX_RETRY}")
            for r in reasons[:10]:
                print("  -", r)
            continue

        print(f"🧪 품질 OK ({score}/100) → 진행")
        post = candidate
        break

    if not post:
        print(f"🧪 마지막 품질 점수: {last_score}/100")
        for r in last_reasons[:12]:
            print("  -", r)
        raise RuntimeError("생성 실패: 품질/중복 조건을 만족하는 글을 만들지 못했습니다.")

    # 3) 썸네일용 짧은 타이틀
    thumb_title = generate_thumbnail_title(openai_client, S.OPENAI_MODEL, post["title"])
    print("🧩 썸네일 타이틀:", thumb_title)

    # 4) 이미지 2장 생성 (실패 시 fallback)
    hero_prompt = (post.get("img_prompt") or "").strip()
    if not hero_prompt:
        # topic별 기본 이미지 프롬프트
        if topic == "it":
            hero_prompt = f"{keyword} 주제의 IT 문제 해결을 표현한 친근한 일러스트, single scene, no collage, no text, square 1:1"
        elif topic == "life":
            hero_prompt = f"{keyword} 주제의 생활 정보 일러스트, single scene, no collage, no text, square 1:1"
        else:
            hero_prompt = f"{keyword} 주제의 건강 정보 블로그 삽화, single scene, no collage, no text, square 1:1"

    body_prompt = hero_prompt + ", single scene, no collage, different composition, different angle, no text, square 1:1"

    try:
        print("🎨 Gemini 이미지(상단/대표) 생성 중...")
        hero_img = generate_nanobanana_image_png_bytes(
            gemini_client, S.GEMINI_IMAGE_MODEL, hero_prompt
        )
    except Exception as e:
        print(f"⚠️ 대표 이미지 생성 실패 → 대체 이미지로 진행: {e}")
        hero_img = _fallback_png_bytes(f"{keyword}")

    try:
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
        # 최상단(가시영역) 강제 노출
        html_after_coupang = html_after_coupang.replace(
            '<div class="wrap">',
            f'<div class="wrap">\n  <div class="disclosure">{disclosure}</div>',
            1,
        )

    html = html_after_coupang

    # 9) 애드센스 수동 슬롯 3개 삽입
    html = inject_adsense_slots(html)

    # 4️⃣ 발행 전 HTML 미리보기 저장(아티팩트 업로드 경고 제거)
    _save_preview_html(html, post.get("title") or keyword)

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

    # 12) 히스토리 저장
    state = load_state()  # 혹시 다른 곳에서 저장했을 수 있어 안전하게 다시 로드
    state = add_history_item(
        state,
        {
            "post_id": post_id,
            "keyword": post.get("keyword", keyword),
            "title": post["title"],
            "title_fp": _title_fingerprint(post["title"]),
        },
    )

    # 3️⃣ 발행 성공 후 카운트 증가
    state = _bump_budget_counts(state)
    save_state(state)

    print(f"✅ 발행 완료! post_id={post_id}")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"❌ 시스템 종료: {e}")
        raise
