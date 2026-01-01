import os
import re
import uuid
from datetime import datetime

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

# ✅ 쿠팡(선택)
from app.monetize_coupang import inject_coupang


S = Settings()


def make_ascii_filename(prefix: str, ext: str = "png") -> str:
    uid = uuid.uuid4().hex[:10]
    prefix = re.sub(r"[^a-zA-Z0-9_-]+", "-", (prefix or "img")).strip("-")
    if not prefix:
        prefix = "img"
    return f"{prefix}-{uid}.{ext}"


def _safe_slug(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-zA-Z0-9가-힣_-]+", "", s)
    return s[:60] or "post"


def save_preview_html(html: str, title: str, keyword: str) -> tuple[str, str]:
    """
    발행 전 최종 HTML을 preview 폴더에 저장
    - preview/preview_latest.html  (항상 최신)
    - preview/preview_YYYYmmdd_HHMMSS_<slug>.html
    반환: (latest_path, stamped_path)
    """
    os.makedirs("preview", exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = _safe_slug(keyword or title)
    stamped_path = os.path.join("preview", f"preview_{ts}_{slug}.html")
    latest_path = os.path.join("preview", "preview_latest.html")

    # 워드프레스/테마 영향 없이 "단독 미리보기"도 가능하게 최소 HTML 래퍼 제공
    wrapper = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{(title or '').strip()}</title>
  <style>
    body {{ margin: 0; padding: 24px; background:#f6f7fb; }}
    .preview-host {{ max-width: 860px; margin: 0 auto; background:#fff; border-radius:16px; padding: 22px; box-shadow:0 10px 30px rgba(0,0,0,0.08); }}
  </style>
</head>
<body>
  <div class="preview-host">
    {html}
  </div>
</body>
</html>
"""

    with open(stamped_path, "w", encoding="utf-8") as f:
        f.write(wrapper)
    with open(latest_path, "w", encoding="utf-8") as f:
        f.write(wrapper)

    return latest_path, stamped_path


def run() -> None:
    S = Settings()

    # (옵션) 발행 없이 미리보기만 저장하고 끝내기
    SKIP_PUBLISH = os.getenv("SKIP_PUBLISH", "0").strip() == "1"

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

    # 4) 이미지 2장 생성 (1:1 + 콜라주 방지)
    hero_prompt = (post.get("img_prompt") or "").strip()
    if not hero_prompt:
        hero_prompt = f"{keyword} 주제의 건강 정보 블로그 삽화, single scene, no collage, no text, square 1:1"

    body_prompt = hero_prompt + ", single scene, no collage, different composition, different angle, no text, square 1:1"

    print("🎨 Gemini 이미지(상단/대표) 생성 중...")
    hero_img = generate_nanobanana_image_png_bytes(gemini_client, S.GEMINI_IMAGE_MODEL, hero_prompt)

    print("🎨 Gemini 이미지(중간) 생성 중...")
    body_img = generate_nanobanana_image_png_bytes(gemini_client, S.GEMINI_IMAGE_MODEL, body_prompt)

    hero_img = to_square_1024(hero_img)
    body_img = to_square_1024(body_img)

    # 5) 대표 이미지에 타이틀 오버레이
    hero_img_titled = add_title_to_image(hero_img, thumb_title)
    hero_img_titled = to_square_1024(hero_img_titled)

    # 6) WP 미디어 업로드
    hero_name = make_ascii_filename("featured")
    body_name = make_ascii_filename("body")

    hero_url, hero_media_id = upload_media_to_wp(S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD, hero_img_titled, hero_name)
    body_url, _ = upload_media_to_wp(S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD, body_img, body_name)

    # 7) A안 레이아웃 HTML 생성
    sections = post.get("sections") or []
    outro = post.get("outro") or ""

    html = format_post_v2(
        title=post["title"],
        keyword=keyword,
        hero_url=hero_url,
        body_url=body_url,
        disclosure_html="",  # 쿠팡 들어가면 아래에서 자동 삽입
        summary_bullets=post.get("summary_bullets") or None,
        sections=sections if isinstance(sections, list) else [],
        warning_bullets=post.get("warning_bullets") or None,
        checklist_bullets=post.get("checklist_bullets") or None,
        outro=outro,
    )

    # 8) 쿠팡 삽입 (실제로 삽입된 경우에만 최상단 대가성 문구)
    html_after_coupang = inject_coupang(html, keyword=keyword)

    coupang_inserted = (html_after_coupang != html)
    if coupang_inserted:
        disclosure = "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
        html_after_coupang = html_after_coupang.replace(
            '<div class="wrap">',
            f'<div class="wrap">\n  <div class="disclosure">{disclosure}</div>',
            1
        )

    html = html_after_coupang

    # 9) 애드센스 수동 슬롯 3개 삽입
    html = inject_adsense_slots(html)

    # ✅ 10) 발행 전 미리보기 HTML 저장 (핵심: 4단계)
    latest_path, stamped_path = save_preview_html(html, title=post["title"], keyword=keyword)
    print("🧪 PREVIEW saved:", latest_path)
    print("🧪 PREVIEW saved:", stamped_path)

    # (옵션) 발행 스킵
    if SKIP_PUBLISH:
        print("🟡 SKIP_PUBLISH=1 이므로 발행 없이 미리보기 저장만 하고 종료합니다.")
        return

    # 11) publish_to_wp가 content_html을 사용하도록 교체
    post["content_html"] = html

    # 12) WP 글 발행
    post_id = publish_to_wp(
        S.WP_URL,
        S.WP_USERNAME,
        S.WP_APP_PASSWORD,
        post,
        hero_url,
        body_url,
        featured_media_id=hero_media_id,
    )

    # 13) 히스토리 저장
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
