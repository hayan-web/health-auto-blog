# main.py (LATEST INTEGRATED FINAL - copy/paste)
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
from typing import Any, List, Tuple, Optional

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
from app.wp_client import upload_media_to_wp, publish_to_wp, ensure_category_id
from app.store import load_state, save_state, add_history_item
from app.dedupe import pick_retry_reason, _title_fingerprint
from app.keyword_picker import pick_keyword_by_naver
from app.click_ingest import ingest_click_log
from app.prioritizer import pick_best_publishing_combo
from app.cooldown import CooldownRule, apply_cooldown_rules
from app.news_context import build_news_context

from app.formatter_v2 import format_post_v2

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
# ENV
# -----------------------------
def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _env_bool(key: str, default: str = "0") -> bool:
    return _env(key, default).lower() in ("1", "true", "yes", "y", "on")


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except Exception:
        return default


def _as_html(x: Any) -> str:
    if isinstance(x, tuple) and len(x) >= 1:
        return x[0] or ""
    return x or ""


# -----------------------------
# TIME / SLOT
# -----------------------------
def _kst_now() -> datetime:
    return datetime.now(tz=KST)


def _kst_date_key(dt: Optional[datetime] = None) -> str:
    d = dt or _kst_now()
    return d.strftime("%Y-%m-%d")


def _slot_topic_kst(dt: Optional[datetime] = None) -> str:
    """
    10시: health
    14시: trend(=이슈)
    19시: life(=쇼핑/쿠팡)
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
    for it in reversed(hist[-300:]):
        if isinstance(it, dict) and it.get("kst_date") == today and it.get("topic"):
            used.add(str(it["topic"]))
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


def _already_ran_this_slot(state: dict, forced_slot: str) -> bool:
    today = _kst_date_key()
    last = (state or {}).get("last_run") or {}
    return isinstance(last, dict) and last.get("kst_date") == today and last.get("forced_slot") == forced_slot


def _mark_ran_this_slot(state: dict, forced_slot: str, run_id: str) -> dict:
    state["last_run"] = {
        "kst_date": _kst_date_key(),
        "kst_hour": _kst_now().hour,
        "forced_slot": forced_slot,
        "run_id": run_id,
        "ts": int(time.time()),
    }
    return state


def _pick_run_topic(state: dict) -> tuple[str, str]:
    run_slot = _env("RUN_SLOT", "").lower()
    strict = _env_bool("STRICT_RUN_SLOT", "1")

    if run_slot in ("health", "trend", "life"):
        forced = run_slot
        if strict:
            return forced, forced
        return forced, _choose_topic_with_rotation(state, forced)

    forced = _slot_topic_kst()
    return forced, _choose_topic_with_rotation(state, forced)


def _expected_hour(slot: str) -> int:
    return {"health": 10, "trend": 14, "life": 19}.get(slot, 19)


def _in_time_window(slot: str) -> bool:
    win = _env_int("SLOT_WINDOW_MIN", 90)
    now = _kst_now()
    target = now.replace(hour=_expected_hour(slot), minute=0, second=0, microsecond=0)
    delta_min = abs(int((now - target).total_seconds() // 60))
    return delta_min <= win


# -----------------------------
# TITLE (유사도 방지 + 티스토리식 짧은 제목)
# -----------------------------
def _normalize_title(title: str) -> str:
    if not title:
        return title
    t = unicodedata.normalize("NFKC", str(title)).strip()
    t = t.replace("ㅡ", "-").replace("–", "-").replace("—", "-").replace("~", "-")

    # 연령/숫자 패턴 제거
    t = re.sub(r"\b\d{2}\s*[-~]\s*\d{2}\s*대(를|을|의|에게|용)?\b", "", t)
    t = re.sub(r"\b\d{2}\s*대(를|을|의|에게|용)?\b", "", t)
    t = re.sub(r"\b3040\b", "", t)

    t = re.sub(r"^\s*(대를|을|를)\s*위한\s+", "", t)
    t = re.sub(r"\s*(대를|을|를)\s*위한\s+", " ", t)

    t = re.sub(r"^[\s\-\–\—\d\.\)\(]+", "", t).strip()
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t or str(title).strip()


def _tokenize_ko(text: str) -> set[str]:
    t = re.sub(r"[^0-9A-Za-z가-힣\s]", " ", text)
    t = re.sub(r"\s+", " ", t).strip()
    return set([x for x in t.split(" ") if len(x) >= 2])


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / (len(a | b) or 1)


def _recent_titles(history: list[dict], n: int = 30) -> list[str]:
    out: list[str] = []
    for it in reversed(history[-400:]):
        if isinstance(it, dict) and it.get("title"):
            out.append(str(it["title"]))
        if len(out) >= n:
            break
    return out


def _title_too_similar(title: str, recent: list[str], threshold: float = 0.45) -> bool:
    a = _tokenize_ko(title)
    for rt in recent[:18]:
        if _jaccard(a, _tokenize_ko(rt)) >= threshold:
            return True
    return False


def _stable_seed_int(*parts: str) -> int:
    s = "|".join([p or "" for p in parts])
    h = hashlib.sha256(s.encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _title_limits(topic: str) -> tuple[int, int]:
    """
    티스토리 느낌: 짧고 선명
    기본: 12~20자
    trend(이슈): 12~22자
    """
    tmin = _env_int("TITLE_MIN", 12)
    tmax = _env_int("TITLE_MAX", 20)
    if topic in ("trend", "issue"):
        tmax = _env_int("TITLE_MAX_ISSUE", 22)
    return tmin, tmax


def _strip_title_fillers(t: str) -> str:
    if not t:
        return t
    t = re.sub(r"(완벽|총정리|완전정리|A부터\s*Z까지|초간단|한방에|모든 것)\s*", "", t)
    t = re.sub(r"(가이드|방법|정리|체크리스트|요약|핵심)\s*(정리|가이드|방법|체크리스트|요약)?$", "", t).strip()
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t


def _clamp_title_len(t: str, min_len: int, max_len: int) -> str:
    t = (t or "").strip()
    if not t:
        return t
    if len(t) > max_len:
        t = t[:max_len].rstrip()
        t = re.sub(r"[\-\:\|\·\s]+$", "", t).strip()
    return t


def _title_hooks(topic: str) -> list[str]:
    if topic == "health":
        return ["의외로 놓치는", "딱 이것부터", "먼저 확인", "잘못하면", "꾸준히 되는", "하루만 해도", "바로 써먹는", "요즘 더 중요한"]
    if topic in ("trend", "issue"):
        return ["지금 핵심만", "왜 갑자기", "한 번에 이해", "이렇게 바뀐다", "요점만 정리", "지금 체크", "핵심 포인트", "정리해보면"]
    return ["사기 전 체크", "후회 줄이는", "이렇게 고르면", "은근 실패하는", "지금 많이 찾는", "딱 맞는", "바로 비교", "간단 정리"]


def _build_title_prompt(topic: str, keyword: str, bad_title: str, recent_titles: list[str]) -> str:
    min_len, max_len = _title_limits(topic)
    hooks = " / ".join(_title_hooks(topic)[:8])
    recent = "\n".join(f"- {t}" for t in (recent_titles or [])[:14])

    return f"""
한국어 블로그 제목을 1개만 만들어 주세요.

[필수 규칙]
- 글자수: {min_len}~{max_len}자 (공백 포함)
- 연령대/숫자(예: 30~50대, 20대, 3040, top5 등) 금지
- 과장/낚시 금지 (현실적/담백)
- 키워드가 자연스럽게 들어가야 함
- 최근 제목들과 단어/구조 반복 피하기(유사하면 실패)
- 제목 끝에 "가이드/정리/체크리스트/요약" 남발 금지
- 출력: 제목 한 줄만 (따옴표/번호/부가설명 금지)

[주제] {topic}
[키워드] {keyword}
[문제 제목] {bad_title}

[가능한 훅(참고)]
{hooks}

[최근 제목]
{recent}
""".strip()


def _rewrite_title_openai_tistory(client, model: str, *, topic: str, keyword: str, bad_title: str, recent_titles: list[str]) -> str:
    prompt = _build_title_prompt(topic, keyword, bad_title, recent_titles)
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "제목 1줄만 출력하세요."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.95,
        )
        t = (r.choices[0].message.content or "").strip().splitlines()[0].strip()
        t = t.strip('"').strip("'")
        t = _normalize_title(t)
        t = _strip_title_fillers(t)
        min_len, max_len = _title_limits(topic)
        t = _clamp_title_len(t, min_len, max_len)
        return t
    except Exception as e:
        print(f"⚠️ title rewrite fail: {e}")
        return ""


def _fallback_title_tistory(topic: str, keyword: str, seed: int) -> str:
    min_len, max_len = _title_limits(topic)
    rng = random.Random(seed)
    hook = rng.choice(_title_hooks(topic))
    kw = (keyword or "").strip()
    if len(kw) > 14:
        kw = kw[:14].strip()

    candidates = [
        f"{kw}, {hook}",
        f"{hook} {kw}",
        f"{kw} 이렇게 하면 달라져요",
        f"{kw} 먼저 확인할 것",
        f"{kw} 은근히 놓치는 포인트",
        f"{kw} 실패 줄이는 방법",
    ]
    t = _normalize_title(rng.choice(candidates))
    t = _strip_title_fillers(t)
    t = _clamp_title_len(t, min_len, max_len)
    if len(t) < min_len:
        t = _clamp_title_len(f"{t} 포인트", min_len, max_len)
    return t


def _finalize_title(topic: str, keyword: str, title: str, recent_titles: list[str], seed: int) -> str:
    min_len, max_len = _title_limits(topic)
    t = _normalize_title(title or "")
    t = _strip_title_fillers(t)
    t = _clamp_title_len(t, min_len, max_len)

    if (not t) or (len(t) < min_len) or _title_too_similar(t, recent_titles or [], threshold=0.45):
        return _fallback_title_tistory(topic, keyword, seed)
    return t


# -----------------------------
# IMAGE
# -----------------------------
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


def _build_image_prompt(base: str, *, variant: str, seed: int, style_mode: str) -> str:
    rng = random.Random(seed + (1 if variant == "hero" else 2))
    base_raw = (base or "").strip()

    must_rules = [
        "single scene",
        "no collage",
        "no text",
        "no watermark",
        "no logos",
        "no brand names",
        "square 1:1",
    ]
    low = base_raw.lower()
    for r in must_rules:
        if r not in low:
            base_raw += f", {r}"

    if style_mode == "watercolor":
        style = rng.choice([
            "watercolor illustration, soft wash, paper texture, gentle edges, airy light",
            "watercolor + ink outline, light granulation, calm mood, minimal background",
        ])
        comp = "centered subject, minimal background, plenty of negative space" if variant == "hero" else "different angle from hero, gentle perspective change"
        return f"{base_raw}, {style}, {comp}"

    style = rng.choice([
        "photorealistic, natural light, clean composition",
        "photorealistic, minimal home interior, tidy, realistic textures",
    ])
    comp = "front view, centered, uncluttered" if variant == "hero" else "different angle, show use-case, uncluttered"
    return f"{base_raw}, {style}, {comp}"


# -----------------------------
# COUPANG: 키워드 -> 딥링크 3개 (✅ 여기서 끊김 없게 완성)
# -----------------------------
def _coupang_make_auth(method: str, path: str, query: str, access_key: str, secret_key: str) -> str:
    signed_date = datetime.utcnow().strftime("%y%m%dT%H%M%SZ")
    message = signed_date + method + path + query
    signature = hmac.new(
        secret_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return (
        "CEA algorithm=HmacSHA256, "
        f"access-key={access_key}, "
        f"signed-date={signed_date}, "
        f"signature={signature}"
    )


def _coupang_deeplink_batch(urls: List[str]) -> List[str]:
    access_key = _env("COUPANG_ACCESS_KEY", "")
    secret_key = _env("COUPANG_SECRET_KEY", "")
    if not access_key or not secret_key:
        print("⚠️ COUPANG_ACCESS_KEY/COUPANG_SECRET_KEY 없음 → 딥링크 생성 스킵")
        return []

    host = "https://api-gateway.coupang.com"
    path = "/v2/providers/affiliate_open_api/apis/openapi/deeplink"
    url = host + path

    headers = {
        "Authorization": _coupang_make_auth("POST", path, "", access_key, secret_key),
        "Content-Type": "application/json",
    }
    payload = {"coupangUrls": urls}

    try:
        r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=12)
        if r.status_code != 200:
            print(f"⚠️ coupang deeplink http={r.status_code} body={r.text[:200]}")
            return []
        data = r.json()
        arr = (data.get("data") or []) if isinstance(data, dict) else []
        out: List[str] = []
        if isinstance(arr, list):
            for it in arr:
                if isinstance(it, dict) and it.get("shortenUrl"):
                    out.append(str(it["shortenUrl"]).strip())
        return [x for x in out if x]
    except Exception as e:
        print(f"⚠️ coupang deeplink error: {e}")
        return []


def _coupang_links_from_keyword(keyword: str) -> List[Tuple[str, str]]:
    kw = keyword.strip()
    if not kw:
        return []

    from urllib.parse import quote_plus
    raw_urls = [
        ("바로보기", f"https://www.coupang.com/np/search?q={quote_plus(kw)}"),
        ("추천",     f"https://www.coupang.com/np/search?q={quote_plus(kw + ' 추천')}"),
        ("할인",     f"https://www.coupang.com/np/search?q={quote_plus(kw + ' 할인')}"),
    ]

    for attempt in range(1, 3):
        shorts = _coupang_deeplink_batch([u for _, u in raw_urls])
        if len(shorts) >= 1:
            out: List[Tuple[str, str]] = []
            for i, (label, _) in enumerate(raw_urls):
                if i < len(shorts) and shorts[i]:
                    out.append((label, shorts[i]))
            return out
        time.sleep(0.8 * attempt)

    return []


# -----------------------------
# COUPANG UI (✅ 버튼만! + 눈에 띄게)
# -----------------------------
def _coupang_disclosure_html() -> str:
    txt = _env(
        "COUPANG_DISCLOSURE_TEXT",
        "이 포스팅은 쿠팡 파트너스 활동의 일환으로 일정액의 수수료를 제공받을 수 있습니다.",
    )
    return (
        "<div style='margin:12px 0;padding:12px 14px;border-radius:12px;"
        "background:#fff7ed;border:1px solid #fed7aa;color:#9a3412;line-height:1.55;'>"
        "<b>광고 안내</b><br>"
        f"{txt}"
        "</div>"
    )


def _coupang_buttons_html(links: List[Tuple[str, str]], keyword: str) -> str:
    if not links:
        return ""

    # 버튼만 나오게: 세로 스택 + 풀폭
    btns = []
    for label, url in links[:3]:
        if label == "바로보기":
            text = "쿠팡에서 관련 상품 보기"
            bg = "#16a34a"
        elif label == "추천":
            text = "후기 많은 추천 옵션 보기"
            bg = "#111827"
        else:
            text = "할인/쿠폰 적용 확인하기"
            bg = "#0ea5e9"

        btns.append(
            f"<a href='{url}' target='_blank' rel='nofollow sponsored noopener' "
            "style='display:block;width:100%;box-sizing:border-box;"
            "padding:14px 14px;border-radius:12px;margin:10px 0;"
            f"background:{bg};color:#fff;text-decoration:none;font-weight:800;"
            "text-align:center;font-size:15px;letter-spacing:-0.2px;'>"
            f"{text} →</a>"
        )

    return (
        f"<div style='margin:16px 0;padding:14px;border:1px solid #e5e7eb;border-radius:14px;background:#f8fafc;'>"
        f"<div style='font-weight:900;font-size:16px;margin-bottom:10px;'>🛒 {keyword} 빠른 확인</div>"
        + "".join(btns) +
        "<div style='color:#6b7280;font-size:12px;line-height:1.4;margin-top:8px;'>"
        "※ 가격/쿠폰/배송은 시점에 따라 변동될 수 있습니다."
        "</div></div>"
    )


# -----------------------------
# HTML INSERT (pre/code 안쪽 회피)
# -----------------------------
def _count_tags_before(html: str, pos: int, open_pat: str, close_pat: str) -> tuple[int, int]:
    opens = len(re.findall(open_pat, html[:pos], flags=re.I))
    closes = len(re.findall(close_pat, html[:pos], flags=re.I))
    return opens, closes


def _is_inside_code_like(html: str, pos: int) -> bool:
    pre_o, pre_c = _count_tags_before(html, pos, r"<pre\b", r"</pre>")
    code_o, code_c = _count_tags_before(html, pos, r"<code\b", r"</code>")
    return (pre_o > pre_c) or (code_o > code_c)


def _insert_after_first_ul_safe(html: str, block: str) -> str:
    if not block:
        return html

    start = 0
    while True:
        idx = html.find("</ul>", start)
        if idx == -1:
            return block + "\n" + html
        insert_pos = idx + 5
        if not _is_inside_code_like(html, insert_pos):
            return html[:insert_pos] + "\n" + block + "\n" + html[insert_pos:]
        start = insert_pos


def _insert_near_second_h2_safe(html: str, block: str) -> str:
    if not block:
        return html
    hs = [m.start() for m in re.finditer(r"<h2\b", html, re.I)]
    candidates = []
    if len(hs) >= 2:
        candidates.append(hs[1])
    if hs:
        candidates.append(hs[-1])

    for pos in candidates:
        if not _is_inside_code_like(html, pos):
            return html[:pos] + "\n" + block + "\n" + html[pos:]

    pos = max(0, len(html) // 2)
    if _is_inside_code_like(html, pos):
        pos = min(len(html), pos + 2000)
    return html[:pos] + "\n" + block + "\n" + html[pos:]


def _insert_end(html: str, block: str) -> str:
    return html + "\n" + block if block else html


# -----------------------------
# CATEGORY
# -----------------------------
def _category_name_for_topic(topic: str) -> str:
    if topic == "health":
        return _env("WP_CAT_HEALTH_NAME", "건강")
    if topic == "trend":
        return _env("WP_CAT_ISSUE_NAME", "트렌드이슈")
    return _env("WP_CAT_SHOPPING_NAME", "쇼핑")


def _set_post_category(wp_url: str, user: str, pw: str, post_id: int, cat_id: int) -> None:
    """
    publish_to_wp가 categories를 반영하지 않는 버전이어도, 발행 후 PATCH로 카테고리를 확실히 맞춥니다.
    """
    try:
        wp_url = wp_url.rstrip("/")
        endpoint = f"{wp_url}/wp-json/wp/v2/posts/{post_id}"
        r = requests.post(endpoint, auth=(user, pw), json={"categories": [cat_id]}, timeout=30)
        if r.status_code not in (200, 201):
            print(f"⚠️ category patch failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"⚠️ category patch error: {e}")


# -----------------------------
# RUN
# -----------------------------
def run() -> None:
    S = Settings()
    run_id = uuid.uuid4().hex[:10]

    event_name = _env("GITHUB_EVENT_NAME", "")
    is_schedule = (event_name == "schedule")

    openai_client = make_openai_client(S.OPENAI_API_KEY)
    img_key = _env("IMAGE_API_KEY", "") or getattr(S, "IMAGE_API_KEY", "") or S.OPENAI_API_KEY
    img_client = make_gemini_client(img_key)

    state = load_state()
    state = ingest_click_log(state, S.WP_URL)
    state = try_update_from_post_metrics(state)

    history = state.get("history", []) if isinstance(state.get("history", []), list) else []

    # Guardrails
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

    # slot/topic
    forced_slot, topic = _pick_run_topic(state)
    print(f"🕒 run_id={run_id} | event={event_name} | forced_slot={forced_slot} -> topic={topic} | kst_now={_kst_now()}")

    # 시간창 강제는 스케줄에서만
    if _env("RUN_SLOT", "").lower() in ("health", "trend", "life"):
        if is_schedule and _env_bool("ENFORCE_TIME_WINDOW", "1"):
            if not _in_time_window(forced_slot):
                print(f"🛑 out of time window: slot={forced_slot} expected={_expected_hour(forced_slot)}:00 KST → exit(0)")
                return

    # 같은 슬롯 중복 방지: 스케줄에서만
    if is_schedule and _env_bool("SKIP_DUPLICATE_SLOT", "1"):
        if _already_ran_this_slot(state, forced_slot):
            print(f"🛑 same slot already ran today: {forced_slot} → exit(0)")
            return

    # mark run
    state = _mark_ran_this_slot(state, forced_slot, run_id)
    save_state(state)

    # keyword
    keyword, _ = pick_keyword_by_naver(S.NAVER_CLIENT_ID, S.NAVER_CLIENT_SECRET, history)

    # life(=쇼핑) subtopic
    life_subtopic = ""
    if topic == "life":
        life_subtopic, sub_dbg = pick_life_subtopic(state)
        print("🧩 life_subtopic:", life_subtopic, "| dbg(top3):", (sub_dbg.get("scored") or [])[:3])
        keyword = f"{keyword} {life_subtopic}".strip()

    # seed
    seed = _stable_seed_int(keyword, run_id, str(int(time.time())))

    system_prompt = build_system_prompt(topic)

    extra_context = ""
    if topic == "trend":
        extra_context = build_news_context(keyword)

    # build_user_prompt 호환 처리 (extra_context 파라미터 지원/미지원 둘 다)
    try:
        base_user_prompt = build_user_prompt(topic, keyword, extra_context=extra_context)
    except TypeError:
        base_user_prompt = build_user_prompt(topic, keyword)

    user_prompt = base_user_prompt + (
        "\n\n[추가 지시] 같은 단어/같은 문장 패턴 반복을 피하고, 소제목 표현도 다양하게. "
        "각 소제목 본문은 공백 제외 260자 이상."
    )

    best_image_style, thumb_variant, _ = pick_best_publishing_combo(state, topic=topic)
    recent = _recent_titles(history, n=30)

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

        # 품질게이트에서 img_prompt 단어(콜라주/텍스트)로 실패하는 것 방지
        post["img_prompt"] = f"{keyword} concept illustration, single scene, no collage, no text, no watermark"

        dup, reason = pick_retry_reason(post.get("title", ""), history)
        if dup or _title_too_similar(post.get("title", ""), recent, threshold=0.45):
            post["sections"] = []
            print(f"♻️ 제목 유사/중복({reason or 'similarity'}) → 재생성 유도")
        return post

    # 품질게이트 실패 시 강제 진행 옵션
    try:
        post, _ = quality_retry_loop(_gen, max_retry=4)
    except Exception as e:
        if _env_bool("ALLOW_QUALITY_FALLBACK", "1"):
            print(f"⚠️ quality_gate 실패 → 마지막 초안으로 진행(허용): {e}")
            post = _gen()
        else:
            raise

    # ✅ 티스토리식 짧은 제목으로 “강제 업그레이드”
    raw_title = post.get("title", "")
    post["title"] = _finalize_title(topic, keyword, raw_title, recent, seed)

    # 제목이 여전히 유사하면 OpenAI로 1~2회만 재작성
    for _ in range(2):
        if (not post["title"]) or _title_too_similar(post["title"], recent, threshold=0.45):
            t2 = _rewrite_title_openai_tistory(
                openai_client,
                S.OPENAI_MODEL,
                topic=topic,
                keyword=keyword,
                bad_title=post["title"] or raw_title,
                recent_titles=recent,
            )
            post["title"] = _finalize_title(topic, keyword, t2, recent, seed)
        else:
            break

    # thumb title (너무 길면 썸네일 깨짐 방지)
    thumb_title = generate_thumbnail_title(openai_client, S.OPENAI_MODEL, post["title"])
    thumb_title = (thumb_title or "").strip()
    if len(thumb_title) > 18:
        thumb_title = thumb_title[:18].rstrip()
    print("🧩 thumb_title:", thumb_title, "| thumb_variant:", thumb_variant)

    # 쿠팡: life만
    coupang_planned = bool(topic == "life" and _env_bool("FORCE_COUPANG_IN_LIFE", "1"))

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

    # 안전 base_prompt
    if topic == "life" and coupang_planned:
        base_prompt = (
            f"{keyword} related household item, practical home product, "
            f"product clearly visible, clean minimal background, no packaging text, no labels"
        )
    else:
        base_prompt = f"{keyword} calm illustration, clean background"

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

    hero_url, hero_media_id = upload_media_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD,
        hero_img_titled, make_ascii_filename("featured")
    )
    body_url, _ = upload_media_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD,
        body_img, make_ascii_filename("body")
    )

    # 카테고리 지정
    cat_name = _category_name_for_topic(topic)
    cat_id = ensure_category_id(S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD, name=cat_name)
    if cat_id:
        post["categories"] = [cat_id]
        print(f"📁 category set: {cat_name} (id={cat_id})")
    else:
        print(f"⚠️ category resolve failed: {cat_name} → skip categories")

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

    # 쿠팡: 버튼만 + 눈에 띄게
    coupang_inserted = False
    coupang_urls: List[Tuple[str, str]] = []

    if topic == "life" and coupang_planned:
        coupang_urls = _coupang_links_from_keyword(keyword)
        if coupang_urls:
            disclosure = _coupang_disclosure_html()
            buttons = _coupang_buttons_html(coupang_urls, keyword=keyword)

            # 상단: 대가성 문구
            html = disclosure + "\n" + html

            # 요약(첫 ul) 다음: 버튼 묶음
            html = _insert_after_first_ul_safe(html, buttons)

            # 중간/하단: 한번 더 리마인드(버튼만)
            html = _insert_near_second_h2_safe(html, buttons)
            html = _insert_end(html, buttons)

            coupang_inserted = True
            print("🛒 coupang injected: buttons only")
        else:
            print("⚠️ coupang planned BUT deeplink generation failed → skip")

    post["content_html"] = html

    post_id = publish_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD,
        post, hero_url, body_url,
        featured_media_id=hero_media_id,
    )

    # ✅ publish_to_wp가 categories를 반영 안 해도, 발행 후 강제 PATCH
    if cat_id:
        _set_post_category(S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD, int(post_id), int(cat_id))

    # stats
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
            "run_id": run_id,
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
            "coupang_urls": coupang_urls,
            "kst_date": _kst_date_key(),
            "kst_hour": _kst_now().hour,
            "forced_slot": forced_slot,
        },
    )
    save_state(state)

    print(
        f"✅ 발행 완료: post_id={post_id} | topic={topic} | forced_slot={forced_slot} "
        f"| coupang={coupang_inserted} | img_style={image_style_for_stats}"
    )


if __name__ == "__main__":
    run()
