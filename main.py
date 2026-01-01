# main.py
import re
import uuid

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

# ✅ 품질 점수화(이번 단계 핵심)
from app.quality import evaluate_post_quality


S = Settings()


def make_ascii_filename(prefix: str, ext: str = "png") -> str:
    uid = uuid.uuid4().hex[:10]
    prefix = re.sub(r"[^a-zA-Z0-9_-]+", "-", (prefix or "img")).strip("-")
    if not prefix:
        prefix = "img"
    return f"{prefix}-{uid}.{ext}"


def run() -> None:
    S = Settings()

    openai_client = make_openai_client(S.OPENAI_API_KEY)
    gemini_client = make_gemini_client(S.GOOGLE_API_KEY)

    state = load_state()
    history = state.get("history", [])

    # 1) 키워드 선정
    keyword, debug = pick_keyword_by_naver(
        S.NAVER_CLIENT_ID, S.NAVER_CLIENT_SECRET, history
    )
    print("🔎 선택된 키워드:", keyword)
    print("🧾 키워드 점수(상위 3):", (debug.get("scored") or [])[:3])

    # 2) 글 생성 + (중복 회피 + 품질 점수화) 동시 적용
    #    - 품질 FAIL이면 '이미지 생성 전' 단계에서 재생성 → 비용 절약
    MAX_RETRY = 4
    post = None

    for i in range(1, MAX_RETRY + 1):
        candidate = generate_blog_post(openai_client, S.OPENAI_MODEL, keyword)

        # 2-1) 품질 평가(가장 먼저)
        q = evaluate_post_quality(candidate)
        if not q.ok:
            print(f"🧪 품질 FAIL ({q.score}/100) → 재생성 {i}/{MAX_RETRY}")
            for r in q.reasons[:6]:
                print("   -", r)
            continue

        # 2-2) 중복 검사(품질 통과한 것만)
        dup, reason = pick_retry_reason(candidate.get("title", ""), history)
        if dup:
            print(f"♻️ 중복 감지({reason}) → 재생성 {i}/{MAX_RETRY}")
            continue

        post = candidate
        print(f"🧪 품질 OK ({q.score}/100) → 진행")
        break

    if not post:
        raise RuntimeError("생성 실패: 품질/중복 조건을 만족하는 글을 만들지 못했습니다.")

    # 3) 썸네일용 짧은 타이틀
    thumb_title = generate_thumbnail_title(openai_client, S.OPENAI_MODEL, post["title"])
    print("🧩 썸네일 타이틀:", thumb_title)

    # 4) 이미지 2장 생성 (1:1 + 콜라주 방지)
    hero_prompt = (post.get("img_prompt") or "").strip()
    if not hero_prompt:
        hero_prompt = f"{keyword} 주제의 건강 정보 블로그 삽화, single scene, no collage, no text, square 1:1"

    # body는 구도만 바꾸되 '한 장면' 유지
    body_prompt = (
        hero_prompt
        + ", single scene, no collage, different composition, different angle, no text, square 1:1"
    )

    print("🎨 Gemini 이미지(상단/대표) 생성 중...")
    hero_img = generate_nanobanana_image_png_bytes(
        gemini_client, S.GEMINI_IMAGE_MODEL, hero_prompt
    )

    print("🎨 Gemini 이미지(중간) 생성 중...")
    body_img = generate_nanobanana_image_png_bytes(
        gemini_client, S.GEMINI_IMAGE_MODEL, body_prompt
    )

    hero_img = to_square_1024(hero_img)
    body_img = to_square_1024(body_img)

    # 5) 대표 이미지에 타이틀 오버레이
    hero_img_titled = add_title_to_image(hero_img, thumb_title)
    hero_img_titled = to_square_1024(hero_img_titled)

    # 6) WP 미디어 업로드
    hero_name = make_ascii_filename("featured")
    body_name = make_ascii_filename("body")

    hero_url, hero_media_id = upload_media_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD, hero_img_titled, hero_name
    )
    body_url, _ = upload_media_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD, body_img, body_name
    )

    # ==========================================================
    # ✅ A안 레이아웃(완성 HTML)
    # - 쿠팡이 실제로 삽입된 경우에만 "최상단 대가성 문구" 자동 노출
    # ==========================================================
    sections = post.get("sections") or []
    outro = post.get("outro") or ""

    html = format_post_v2(
        title=post["title"],
        keyword=keyword,
        hero_url=hero_url,
        body_url=body_url,
        disclosure_html="",  # 쿠팡 들어가면 아래에서 채움
        summary_bullets=post.get("summary_bullets") or None,
        sections=sections if isinstance(sections, list) else [],
        warning_bullets=post.get("warning_bullets") or None,
        checklist_bullets=post.get("checklist_bullets") or None,
        outro=outro,
    )

    # 7) 쿠팡 삽입 + (삽입된 경우에만) 대가성 문구 최상단 부착
    html_after_coupang = inject_coupang(html, keyword=keyword)
    coupang_inserted = (html_after_coupang != html)

    if coupang_inserted:
        disclosure = (
            "이 포스팅은 쿠팡 파트너스 활동의 일환으로, "
            "이에 따른 일정액의 수수료를 제공받습니다."
        )
        # formatter_v2가 wrap을 쓰는 구조라는 전제 하에 최상단 배치
        html_after_coupang = html_after_coupang.replace(
            '<div class="wrap">',
            '<div class="wrap">\n'
            f'  <div class="disclosure" style="margin:12px 0 18px; padding:12px 14px; '
            f'border:1px solid rgba(0,0,0,0.08); border-radius:12px; background:#fff7e6; '
            f'font-size:14px; line-height:1.6; color:#333;">{disclosure}</div>',
            1,
        )

    html = html_after_coupang

    # 8) 애드센스 슬롯 3개 삽입(요약박스 위 / 소제목카드 위 / 맨 아래)
    html = inject_adsense_slots(html)

    # 9) publish_to_wp가 content_html을 우선 사용하도록 본문 교체
    post["content_html"] = html

    # 10) WP 글 발행
    post_id = publish_to_wp(
        S.WP_URL,
        S.WP_USERNAME,
        S.WP_APP_PASSWORD,
        post,
        hero_url,
        body_url,
        featured_media_id=hero_media_id,
    )

    # 11) 히스토리 저장
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
