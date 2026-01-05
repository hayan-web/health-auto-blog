# main.py
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import random
import re
import time
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

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

# ✅ 생활 하위주제 선택/학습
from app.life_subtopic_picker import pick_life_subtopic
from app.life_subtopic_stats import (
    record_life_subtopic_impression,
    try_update_from_post_metrics,
)

S = Settings()
KST = timezone(timedelta(hours=9))


# -----------------------------
# Utils
# -----------------------------
def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _env_bool(key: str, default: str = "0") -> bool:
    return _env(key, default) in ("1", "true", "True", "YES", "yes", "y", "Y")


def _as_html(x: Any) -> str:
    """format_post_v2 / inject_* 가 (html, ...) 튜플을 반환하는 케이스 안전 처리"""
    if isinstance(x, tuple) and len(x) >= 1:
        return x[0] or ""
    return x or ""


def _kst_now() -> datetime:
    return datetime.now(tz=KST)


def _kst_date_key(dt: datetime | None = None) -> str:
    d = dt or _kst_now()
    return d.strftime("%Y-%m-%d")


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


# -----------------------------
# Title: normalize + diversify
# -----------------------------
def _normalize_title(title: str) -> str:
    if not title:
        return title

    t = unicodedata.normalize("NFKC", str(title)).strip()
    # 이상 대시/물결/특수문자 정리
    t = t.replace("ㅡ", "-").replace("–", "-").replace("—", "-").replace("~", "-")

    # 연령대 제거
    t = re.sub(r"\b\d{2}\s*[-~]\s*\d{2}\s*대(를|을|의|에게|용)?\b", "", t)
    t = re.sub(r"\b\d{2}\s*대(를|을|의|에게|용)?\b", "", t)
    t = re.sub(r"\b3040\b", "", t)

    # 제거 후 남는 찌꺼기(“대를 위한…”, “을 위한…”) 정리
    t = re.sub(r"^\s*(대를|을|를)\s*위한\s+", "", t)
    t = re.sub(r"\s*(대를|을|를)\s*위한\s+", " ", t)

    # 제목 앞 숫자/기호 제거
    t = re.sub(r"^[\s\-\–\—\d\.\)\(]+", "", t).strip()
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t or str(title).strip()


def _recent_titles(history: list[dict], n: int = 20) -> list[str]:
    out: list[str] = []
    for it in reversed(history[-max(50, n * 3):]):
        if isinstance(it, dict) and it.get("title"):
            out.append(str(it["title"]))
        if len(out) >= n:
            break
    return out


def _rewrite_title_openai(client, model: str, *, keyword: str, topic: str, bad_title: str, history_titles: list[str]) -> str:
    """
    제목이 너무 비슷/이상할 때 '제목만' 재작성 (비용 최소화)
    """
    recent = "\n".join(f"- {t}" for t in history_titles[:12])
    sys = "당신은 한국어 블로그 제목 편집자입니다. 조건을 지키며 제목 1개만 출력하세요."
    user = f"""
아래 조건을 지켜 한국어 블로그 제목을 1개만 만들어주세요.

[조건]
- 연령대/숫자(예: 30~50대, 20대, 3040 등) 언급 금지
- 과장/낚시 금지, 자연스럽고 현실적인 표현
- 15~30자 내외
- 가능하면 키워드를 자연스럽게 포함
- 최근 제목들과 최대한 다른 뉘앙스(단어/구조 반복 피하기)
- 출력은 제목 한 줄만(따옴표/번호/부가설명 금지)

[주제] {topic}
[키워드] {keyword}
[현재 제목(문제)] {bad_title}

[최근 제목]
{recent}
""".strip()

    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
            temperature=0.9,
        )
        txt = (r.choices[0].message.content or "").strip()
        txt = txt.splitlines()[0].strip().strip('"').strip("'")
        return _normalize_title(txt)
    except Exception as e:
        print(f"⚠️ title rewrite fail -> fallback: {e}")
        return ""


def _fallback_title_by_topic(keyword: str, topic: str, seed: int) -> str:
    rng = random.Random(seed)
    kw = keyword.strip()
    # 너무 길면 앞쪽만
    if len(kw) > 18:
        kw = kw[:18].strip()

    if topic == "health":
        cands = [
            f"{kw} 실천 체크리스트",
            f"{kw} 핵심만 정리",
            f"{kw} 하루 루틴 가이드",
            f"{kw} 실수 줄이는 방법",
        ]
    elif topic == "trend":
        cands = [
            f"{kw} 한눈에 정리",
            f"{kw} 지금 알아야 할 포인트",
            f"{kw} 최신 변화 요약",
            f"{kw} 초보도 이해하는 정리",
        ]
    else:  # life
        cands = [
            f"{kw} 실전 정리 팁",
            f"{kw} 바로 써먹는 방법",
            f"{kw} 생활에 적용하는 요령",
            f"{kw} 실패 줄이는 정리",
        ]
    return _normalize_title(rng.choice(cands))


# -----------------------------
# Slot/Topic control (KST)
# -----------------------------
def _slot_topic_kst(dt: datetime | None = None) -> str:
    """
    KST 기준 슬롯 매핑
    - 10시대(09~11): health
    - 14시대(13~15): trend
    - 그 외(주로 19시대): life
    """
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

    for it in reversed(hist[-80:]):
        if not isinstance(it, dict):
            continue
        if it.get("kst_date") == today and it.get("topic"):
            used.add(str(it.get("topic")))
    return used


def _choose_topic_with_rotation(state: dict, forced: str) -> str:
    """
    같은 날 같은 슬롯이 중복 실행되면, 남은 토픽으로 자동 회전
    (원칙적으로는 GH 스케줄이 정확하면 forced 그대로 가는 게 정상)
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


def _already_ran_this_slot(state: dict, slot: str) -> bool:
    """
    스케줄 재시도/중복 트리거로 같은 슬롯이 또 돌면 조용히 종료하기 위한 안전장치
    """
    today = _kst_date_key()
    last = (state or {}).get("last_run") or {}
    if isinstance(last, dict):
        if last.get("kst_date") == today and last.get("forced_slot") == slot:
            return True
    return False


def _mark_ran_this_slot(state: dict, slot: str) -> dict:
    state["last_run"] = {
        "kst_date": _kst_date_key(),
        "kst_hour": _kst_now().hour,
        "forced_slot": slot,
        "ts": int(time.time()),
    }
    return state


# -----------------------------
# Image prompt (watercolor/photo)
# -----------------------------
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

    # learned/other
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
# Coupang: keyword -> deeplink(short url)
# -----------------------------
def _coupang_make_auth(method: str, path: str, query: str, access_key: str, secret_key: str) -> tuple[str, str]:
    """
    Coupang Partners Open API 서명
    """
    signed_date = datetime.utcnow().strftime("%y%m%dT%H%M%SZ")
    message = signed_date + method + path + query
    signature = hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    auth = f"CEA algorithm=HmacSHA256, access-key={access_key}, signed-date={signed_date}, signature={signature}"
    return auth, signed_date


def _coupang_deeplink_from_keyword(keyword: str) -> str:
    """
    키워드 기반 쿠팡 검색 URL을 만들고, Deeplink API로 단축 URL을 받아옵니다.
    - 필요한 시크릿:
      COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY
    """
    access_key = _env("COUPANG_ACCESS_KEY", "")
    secret_key = _env("COUPANG_SECRET_KEY", "")
    if not access_key or not secret_key:
        print("⚠️ COUPANG_ACCESS_KEY/COUPANG_SECRET_KEY 없음 → 쿠팡 링크 생성 스킵")
        return ""

    q = keyword.strip()
    if not q:
        return ""

    # 쿠팡 검색 URL(키워드마다 매번 달라짐)
    from urllib.parse import quote_plus
    search_url = f"https://www.coupang.com/np/search?q={quote_plus(q)}"

    host = "https://api-gateway.coupang.com"
    path = "/v2/providers/affiliate_open_api/apis/openapi/deeplink"
    url = host + path
    method = "POST"
    query = ""  # path에 query를 붙이지 않는 방식(POST 바디로 전달)

    auth, _ = _coupang_make_auth(method, path, query, access_key, secret_key)
    headers = {
        "Authorization": auth,
        "Content-Type": "application/json",
    }
    payload = {"coupangUrls": [search_url]}

    try:
        r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=12)
        if r.status_code != 200:
            print(f"⚠️ coupang deeplink http={r.status_code} body={r.text[:200]}")
            return ""
        data = r.json()
        # 기대 형태: data["data"][0]["shortenUrl"]
        short = ""
        if isinstance(data, dict):
            arr = data.get("data") or []
            if isinstance(arr, list) and arr:
                short = (arr[0].get("shortenUrl") or "").strip()
        if short:
            return short
        print(f"⚠️ coupang deeplink no shortenUrl: {str(data)[:250]}")
        return ""
    except Exception as e:
        print(f"⚠️ coupang deeplink error: {e}")
        return ""


def _extract_first_coupang_url(html: str) -> str:
    if not html:
        return ""
    m = re.search(
        r'href=["\'](https?://[^"\']*(?:coupang\.com|coupang\.co\.kr|link\.coupang\.com|coupa\.ng)[^"\']*)["\']',
        html,
        re.I,
    )
    if m:
        return m.group(1)
    m = re.search(r'(https?://\S*(?:coupang\.com|coupang\.co\.kr|link\.coupang\.com|coupa\.ng)\S*)', html, re.I)
    if m:
        return m.group(1).rstrip(').,<>"]\'')
    return ""


def _render_coupang_cta(url: str, *, variant: str = "top") -> str:
    if not url:
        return ""

    if variant == "top":
        headline = "🔥 쿠팡에서 가격/쿠폰 적용 확인"
        sub = "쿠폰·옵션·배송은 시점에 따라 달라질 수 있어요."
        btn = "쿠팡에서 조건 보기"
    elif variant == "mid":
        headline = "✅ 지금 옵션/할인 확인"
        sub = "옵션별 가격이 다를 수 있어요."
        btn = "할인/옵션 확인하기"
    else:
        headline = "🚚 구매 전 마지막 체크"
        sub = "최종 가격·배송 조건을 한 번 더 확인하세요."
        btn = "가격/배송 확인하기"

    return f"""
<div class="coupang-cta" style="border:1px solid #e5e7eb;border-radius:12px;padding:14px;margin:14px 0;background:#fff;">
  <div style="font-weight:800;font-size:16px;margin-bottom:6px;">{headline}</div>
  <div style="color:#6b7280;font-size:13px;margin-bottom:10px;line-height:1.35;">{sub}</div>
  <a href="{url}" target="_blank" rel="nofollow sponsored noopener"
     style="display:block;text-align:center;padding:12px 14px;border-radius:10px;
            background:#111827;color:#fff;text-decoration:none;font-weight:800;">
    {btn} →
  </a>
</div>
""".strip()


def _insert_after_first_ul(html: str, block: str) -> str:
    if not block:
        return html
    if not html:
        return block
    idx = html.find("</ul>")
    if idx != -1:
        return html[: idx + 5] + "\n" + block + "\n" + html[idx + 5 :]
    return block + "\n" + html


def _insert_near_middle(html: str, block: str) -> str:
    if not block or not html:
        return html
    hs = [m.start() for m in re.finditer(r"<h2\b", html, re.I)]
    if len(hs) >= 2:
        pos = hs[1]
        return html[:pos] + block + "\n" + html[pos:]
    pos = max(0, len(html) // 2)
    return html[:pos] + "\n" + block + "\n" + html[pos:]


def _insert_before_end(html: str, block: str) -> str:
    if not block:
        return html
    if not html:
        return block
    return html + "\n" + block


def _insert_disclosure_top(html: str) -> str:
    disclosure_text = _env(
        "COUPANG_DISCLOSURE_TEXT",
        "이 포스팅은 쿠팡 파트너스 활동의 일환으로 일정액의 수수료를 제공받을 수 있습니다.",
    )
    disclosure = (
        '<div class="disclosure" style="padding:10px 12px;border-radius:10px;background:#fff7ed;'
        'border:1px solid #fed7aa;color:#9a3412;margin:10px 0;line-height:1.55;">'
        f"<b>광고 안내</b><br/>{disclosure_text}"
        "</div>"
    )
    if '<div class="wrap">' in html:
        return html.replace('<div class="wrap">', f'<div class="wrap">\n{disclosure}', 1)
    return disclosure + "\n" + html


# -----------------------------
# Main
# -----------------------------
def run() -> None:
    S = Settings()

    # OpenAI / Image client
    openai_client = make_openai_client(S.OPENAI_API_KEY)

    img_key = os.getenv("IMAGE_API_KEY", "").strip() or getattr(S, "IMAGE_API_KEY", "") or S.OPENAI_API_KEY
    img_client = make_gemini_client(img_key)

    # Load state
    state = load_state()
    state = ingest_click_log(state, S.WP_URL)
    state = try_update_from_post_metrics(state)

    history = state.get("history", []) if isinstance(state.get("history", []), list) else []

    # -----------------------------
    # Guardrails
    # -----------------------------
    cfg = GuardConfig(
        max_posts_per_day=int(getattr(S, "MAX_POSTS_PER_DAY", 3)),
        max_usd_per_month=float(getattr(S, "MAX_USD_PER_MONTH", 30.0)),
    )
    allow_over_budget = _env_bool("ALLOW_OVER_BUDGET", str(getattr(S, "ALLOW_OVER_BUDGET", 1)))
    if allow_over_budget:
        try:
            check_limits_or_raise(state, cfg)
        except Exception as e:
            print(f"⚠️ 가드레일 초과(허용 모드) → 계속 진행: {e}")
    else:
        check_limits_or_raise(state, cfg)

    # -----------------------------
    # Slot / Topic
    # -----------------------------
    forced_slot = _slot_topic_kst()
    if _already_ran_this_slot(state, forced_slot) and _env_bool("SKIP_DUPLICATE_SLOT", "1"):
        print(f"🛑 same slot already ran today: {forced_slot} → exit")
        return

    topic = _choose_topic_with_rotation(state, forced_slot)
    print(f"🕒 forced_slot={forced_slot} -> chosen_topic={topic} | used_today={sorted(list(_topics_used_today(state)))}")

    # 먼저 '실행했다' 마킹(중복 트리거 방지)
    state = _mark_ran_this_slot(state, forced_slot)
    save_state(state)

    # -----------------------------
    # Keyword
    # -----------------------------
    keyword, _ = pick_keyword_by_naver(
        S.NAVER_CLIENT_ID,
        S.NAVER_CLIENT_SECRET,
        history,
    )

    system_prompt = build_system_prompt(topic)
    user_prompt = build_user_prompt(topic, keyword)

    # life 하위주제
    life_subtopic = ""
    if topic == "life":
        life_subtopic, sub_dbg = pick_life_subtopic(state)
        print("🧩 life_subtopic:", life_subtopic, "| dbg(top3):", (sub_dbg.get("scored") or [])[:3])
        keyword = f"{keyword} {life_subtopic}".strip()
        user_prompt = build_user_prompt(topic, keyword)

    # -----------------------------
    # Pick style/thumb
    # -----------------------------
    best_image_style, thumb_variant, _ = pick_best_publishing_combo(state, topic=topic)

    # -----------------------------
    # Generate post + quality
    # -----------------------------
    recent_titles = _recent_titles(history, n=18)

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

    # 제목이 여전히 이상/유사하면 제목만 재작성(2회)
    for _ in range(2):
        t = post.get("title", "")
        if not t or len(t) < 8 or t.startswith(("대", "을", "를")):
            new_t = _rewrite_title_openai(
                openai_client, S.OPENAI_MODEL,
                keyword=keyword, topic=topic, bad_title=t, history_titles=recent_titles
            )
            if new_t:
                post["title"] = new_t
            continue

        dup, _reason = pick_retry_reason(t, history)
        if dup:
            new_t = _rewrite_title_openai(
                openai_client, S.OPENAI_MODEL,
                keyword=keyword, topic=topic, bad_title=t, history_titles=recent_titles
            )
            if new_t:
                post["title"] = new_t
            continue
        break

    if not post.get("title"):
        post["title"] = _fallback_title_by_topic(keyword, topic, _stable_seed_int(keyword, str(time.time())))

    # -----------------------------
    # Thumbnail title
    # -----------------------------
    thumb_title = generate_thumbnail_title(openai_client, S.OPENAI_MODEL, post["title"])
    print("🧩 thumb_title:", thumb_title, "| thumb_variant:", thumb_variant)

    # -----------------------------
    # Coupang plan (life slot 기본 ON)
    # -----------------------------
    force_coupang_in_life = _env_bool("FORCE_COUPANG_IN_LIFE", "1")
    coupang_planned = bool(topic == "life" and force_coupang_in_life)

    # -----------------------------
    # Image style forcing
    # -----------------------------
    forced_style_mode = ""
    if topic in ("health", "trend"):
        forced_style_mode = "watercolor"
    elif topic == "life" and coupang_planned:
        forced_style_mode = "photo"

    learned_style = best_image_style or pick_image_style(state, topic=topic)
    style_mode = forced_style_mode or learned_style
    image_style_for_stats = forced_style_mode or learned_style

    print("🎨 style_mode:", style_mode, "| forced:", bool(forced_style_mode), "| learned:", learned_style)
    print("🛒 coupang_planned:", coupang_planned)

    # -----------------------------
    # Build image prompts
    # -----------------------------
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

    # -----------------------------
    # Generate images
    # -----------------------------
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

    # -----------------------------
    # Upload media
    # -----------------------------
    hero_url, hero_media_id = upload_media_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD,
        hero_img_titled, make_ascii_filename("featured")
    )
    body_url, _ = upload_media_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD,
        body_img, make_ascii_filename("body")
    )

    # -----------------------------
    # Build HTML
    # -----------------------------
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

    # -----------------------------
    # Coupang inject (dynamic link per keyword)
    # -----------------------------
    coupang_inserted = False
    coupang_url = ""

    if topic == "life" and coupang_planned:
        # 1) 키워드 기반 딥링크 생성 → inject_coupang가 쓰는 env에 주입
        dynamic_link = _coupang_deeplink_from_keyword(keyword)
        if dynamic_link:
            os.environ["COUPANG_LINK_URL"] = dynamic_link

            # 2) 기본 쿠팡 박스 삽입
            html2 = _as_html(inject_coupang(html, keyword=keyword))

            # 3) 실제 URL 검증
            coupang_url = _extract_first_coupang_url(html2)
            if coupang_url:
                # 4) CTA 3곳 강제 삽입
                html2 = _insert_after_first_ul(html2, _render_coupang_cta(coupang_url, variant="top"))
                html2 = _insert_near_middle(html2, _render_coupang_cta(coupang_url, variant="mid"))
                html2 = _insert_before_end(html2, _render_coupang_cta(coupang_url, variant="bottom"))

                # 5) 대가성 문구 최상단
                html2 = _insert_disclosure_top(html2)

                html = html2
                coupang_inserted = True
                print("🛒 coupang injected: dynamic deeplink OK")
            else:
                html = html2
                print("⚠️ coupang planned BUT no URL found after inject (theme may strip?).")
        else:
            print("⚠️ coupang planned BUT deeplink generation failed → skip coupang for this post")

    # -----------------------------
    # Adsense inject (always)
    # -----------------------------
    html = _as_html(inject_adsense_slots(html))
    post["content_html"] = html

    # -----------------------------
    # Publish
    # -----------------------------
    post_id = publish_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD,
        post, hero_url, body_url,
        featured_media_id=hero_media_id,
    )

    # -----------------------------
    # Stats / Learning
    # -----------------------------
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

    # ✅ 반환값 재대입(중요)
    state = increment_post_count(state)

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
            "coupang_url": coupang_url,
            "kst_date": _kst_date_key(),
            "kst_hour": _kst_now().hour,
            "forced_slot": forced_slot,
        },
    )
    save_state(state)

    print(
        f"✅ 발행 완료: post_id={post_id} | topic={topic} | forced_slot={forced_slot} | sub={life_subtopic} "
        f"| coupang={coupang_inserted} | img_style={image_style_for_stats}"
    )


if __name__ == "__main__":
    run()
