from __future__ import annotations

import base64
import hashlib
import hmac
import html
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
from app.thumb_overlay import to_square_1024, add_title_to_image
from app.wp_client import upload_media_to_wp, publish_to_wp
from app.store import load_state, save_state, add_history_item
from app.dedupe import pick_retry_reason, _title_fingerprint
from app.keyword_picker import pick_keyword_by_naver
from app.click_ingest import ingest_click_log
from app.prioritizer import pick_best_publishing_combo
from app.cooldown import CooldownRule, apply_cooldown_rules
from app.news_context import build_news_context

from app.monetize_adsense import inject_adsense_slots  # (옵션) 자동 삽입 유지용
from app.image_stats import record_impression as record_image_impression, update_score as update_image_score
from app.topic_style_stats import record_impression as record_topic_style_impression, update_score as update_topic_style_score
from app.image_style_picker import pick_image_style
from app.quality_gate import quality_retry_loop
from app.guardrails import GuardConfig, check_limits_or_raise, increment_post_count
from app.thumb_title_stats import (
    record_impression as record_thumb_impression,
    update_score as update_thumb_score,
    record_topic_impression as record_topic_thumb_impression,
    update_topic_score as update_topic_thumb_score,
)
from app.life_subtopic_picker import pick_life_subtopic
from app.life_subtopic_stats import record_life_subtopic_impression, try_update_from_post_metrics

S = Settings()
KST = timezone(timedelta(hours=9))


# -----------------------------
# ENV helpers
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


# -----------------------------
# TIME / SLOT
# -----------------------------
def _kst_now() -> datetime:
    return datetime.now(tz=KST)


def _kst_date_key(dt: datetime | None = None) -> str:
    d = dt or _kst_now()
    return d.strftime("%Y-%m-%d")


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
# TITLE normalize + similarity
# -----------------------------
def _normalize_title(title: str) -> str:
    if not title:
        return title
    t = unicodedata.normalize("NFKC", str(title)).strip()
    t = t.replace("ㅡ", "-").replace("–", "-").replace("—", "-").replace("~", "-")

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


def _title_too_similar(title: str, recent: list[str], threshold: float = 0.50) -> bool:
    a = _tokenize_ko(title)
    for rt in recent[:18]:
        if _jaccard(a, _tokenize_ko(rt)) >= threshold:
            return True
    return False


def _stable_seed_int(*parts: str) -> int:
    s = "|".join([p or "" for p in parts])
    h = hashlib.sha256(s.encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _title_angle(topic: str, seed: int) -> str:
    rng = random.Random(seed)
    if topic == "health":
        pool = ["실천 체크", "주의할 점", "하루 루틴", "핵심 요약", "실수 줄이기", "바로 시작"]
    elif topic == "trend":
        pool = ["지금 포인트", "한눈 요약", "변화 정리", "초보 설명", "체크 포인트", "요점만"]
    else:
        pool = ["바로 적용", "실전 정리", "자주 하는 실수", "빠른 정리", "가볍게 시작", "핵심만"]
    return rng.choice(pool)


# -----------------------------
# Image prompts
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
        style = rng.choice([
            "watercolor illustration, soft wash, paper texture, gentle edges, airy light, pastel palette",
            "watercolor + ink outline, light granulation, calm mood, soft shadows, minimal background",
            "delicate watercolor painting, subtle gradients, hand-painted feel, clean composition",
        ])
        comp = rng.choice(
            ["centered subject, minimal background, plenty of negative space", "iconic main object, simple props, soft morning light"]
            if variant == "hero"
            else ["different angle from hero, include secondary elements", "wider view, gentle perspective change, subtle props"]
        )
        extra = "title-safe area on lower third" if variant == "hero" else "different composition from hero"
        return f"{base_raw}, {style}, {comp}, {extra}"

    if style_mode == "photo":
        style = rng.choice(
            [
                "photorealistic e-commerce product photography, clean white background, softbox studio lighting, ultra sharp, centered",
                "photorealistic product shot on minimal tabletop, studio lighting, crisp edges, high resolution",
            ]
            if variant == "hero"
            else [
                "photorealistic lifestyle in-use photo in a tidy home, natural window light, hands using item (no face), realistic textures",
                "photorealistic usage scene, close-up hands demonstrating item, shallow depth of field, natural indoor light, no faces",
            ]
        )
        comp = rng.choice(
            ["front view, centered, minimal props", "slight top-down angle, catalog composition"]
            if variant == "hero"
            else ["different angle, show use-case, uncluttered background", "close-up detail + action, clean framing"]
        )
        extra = "title-safe area on lower third (keep product away from bottom)" if variant == "hero" else "avoid looking similar to hero"
        return f"{base_raw}, {style}, {comp}, {extra}"

    comp = rng.choice(["centered subject, clean composition", "minimal props, calm mood"])
    extra = "title-safe area on lower third" if variant == "hero" else "different composition from hero"
    return f"{base_raw}, style hint: {style_mode}, {comp}, {extra}"


# -----------------------------
# Coupang deeplink
# -----------------------------
def _coupang_make_auth(method: str, path: str, query: str, access_key: str, secret_key: str) -> str:
    signed_date = datetime.utcnow().strftime("%y%m%dT%H%M%SZ")
    message = signed_date + method + path + query
    signature = hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"CEA algorithm=HmacSHA256, access-key={access_key}, signed-date={signed_date}, signature={signature}"


def _coupang_deeplink_batch(urls: List[str]) -> List[str]:
    access_key = _env("COUPANG_ACCESS_KEY", "")
    secret_key = _env("COUPANG_SECRET_KEY", "")
    if not access_key or not secret_key:
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
            print(f"⚠️ coupang deeplink http={r.status_code} body={(r.text or '')[:200]}")
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
        ("추천",   f"https://www.coupang.com/np/search?q={quote_plus(kw + ' 추천')}"),
        ("할인",   f"https://www.coupang.com/np/search?q={quote_plus(kw + ' 할인')}"),
    ]
    shorts = _coupang_deeplink_batch([u for _, u in raw_urls])
    if not shorts:
        return []
    out: List[Tuple[str, str]] = []
    for i, (label, _) in enumerate(raw_urls):
        if i < len(shorts) and shorts[i]:
            out.append((label, shorts[i]))
    return out


# -----------------------------
# WP category helpers
# -----------------------------
def _wp_get_category_id_by_name(wp_url: str, user: str, pw: str, name: str) -> Optional[int]:
    try:
        wp_url = wp_url.rstrip("/")
        api = f"{wp_url}/wp-json/wp/v2/categories"
        res = requests.get(api, auth=(user, pw), params={"search": name, "per_page": 100}, timeout=20)
        if res.status_code != 200:
            return None
        arr = res.json()
        if not isinstance(arr, list):
            return None
        # 정확히 이름 일치 우선
        for it in arr:
            if isinstance(it, dict) and it.get("name") == name and isinstance(it.get("id"), int):
                return int(it["id"])
        # 없으면 첫 번째라도
        for it in arr:
            if isinstance(it, dict) and isinstance(it.get("id"), int):
                return int(it["id"])
        return None
    except Exception:
        return None


def _topic_to_wp_category_name(topic: str, coupang_planned: bool) -> str:
    if topic == "health":
        return "건강"
    if topic == "trend":
        return "트렌드이슈"
    # life
    return "쇼핑" if coupang_planned else "쇼핑"


# -----------------------------
# Rendering (관리 잘 된 블로그처럼 보이게)
# -----------------------------
def _highlight_placeholders(text: str) -> str:
    """
    본문에서 {H}강조{/H} 를 색+굵게 처리
    """
    color = _env("HIGHLIGHT_COLOR", "#0ea5e9")
    def repl(m: re.Match) -> str:
        inner = html.escape(m.group(1).strip())
        return f'<span style="color:{color};font-weight:800;">{inner}</span>'
    return re.sub(r"\{H\}(.+?)\{\/H\}", repl, text)


def _p(text: str) -> str:
    # 일반 텍스트 -> 안전 escape 후 highlight 적용
    safe = html.escape((text or "").strip())
    safe = _highlight_placeholders(safe)
    return f"<p style='margin:0 0 16px;line-height:1.9;font-size:16px;color:#111827;'>{safe}</p>"


def _h2(title: str) -> str:
    t = html.escape((title or "").strip())
    return (
        "<h2 style='margin:26px 0 12px;padding:12px 14px;"
        "background:#f1f5f9;border-left:6px solid #0ea5e9;border-radius:12px;"
        "font-size:18px;line-height:1.35;color:#0f172a;'>"
        f"{t}</h2>"
    )


def _box(title: str, items: List[str]) -> str:
    if not items:
        return ""
    li = "".join([f"<li style='margin:6px 0;line-height:1.6;'>{_highlight_placeholders(html.escape(x.strip()))}</li>" for x in items if x and x.strip()])
    if not li:
        return ""
    return (
        "<div style='margin:14px 0;padding:14px 14px;border:1px solid #e5e7eb;border-radius:14px;background:#ffffff;'>"
        f"<div style='font-weight:900;margin-bottom:10px;color:#0f172a;'>{html.escape(title)}</div>"
        f"<ul style='margin:0;padding-left:18px;color:#111827;'>{li}</ul>"
        "</div>"
    )


def _ads_block(n: int) -> str:
    code = _env(f"ADSENSE_MANUAL_{n}", "")
    if not code:
        return ""
    # WP가 코드로 감싸지 않게, 우리가 조립하는 HTML 레벨에서 그대로 넣음
    return (
        "<div style='margin:18px 0;padding:12px;border-radius:14px;border:1px dashed #cbd5e1;background:#f8fafc;'>"
        f"{code}"
        "</div>"
    )


def _img(url: str, alt: str = "") -> str:
    if not url:
        return ""
    a = html.escape(alt or "")
    u = html.escape(url)
    return (
        "<div style='margin:18px 0;'>"
        f"<img src='{u}' alt='{a}' style='width:100%;max-width:100%;border-radius:16px;box-shadow:0 10px 26px rgba(0,0,0,0.10);'/>"
        "</div>"
    )


def _section_to_blocks(sec: Any) -> Tuple[str, List[str]]:
    """
    sec 형태가 어떤 것이든 최대한 안전하게 (heading, paragraphs) 추출
    """
    heading = ""
    paras: List[str] = []

    if isinstance(sec, str):
        heading = ""
        paras = [sec]
        return heading, paras

    if isinstance(sec, dict):
        for k in ("title", "heading", "h2", "subtitle", "name"):
            if sec.get(k):
                heading = str(sec.get(k)).strip()
                break

        # paragraphs / content
        if isinstance(sec.get("paragraphs"), list):
            paras = [str(x).strip() for x in sec["paragraphs"] if str(x).strip()]
        elif isinstance(sec.get("content"), list):
            paras = [str(x).strip() for x in sec["content"] if str(x).strip()]
        elif isinstance(sec.get("body"), str):
            paras = [p.strip() for p in str(sec["body"]).split("\n") if p.strip()]
        elif isinstance(sec.get("text"), str):
            paras = [p.strip() for p in str(sec["text"]).split("\n") if p.strip()]

        # bullets가 있으면 문단 앞에 붙임
        bullets = []
        if isinstance(sec.get("bullets"), list):
            bullets = [str(x).strip() for x in sec["bullets"] if str(x).strip()]
        elif isinstance(sec.get("points"), list):
            bullets = [str(x).strip() for x in sec["points"] if str(x).strip()]

        if bullets:
            paras = ["- " + b for b in bullets] + paras

    return heading, paras


def _render_sections_with_ads(sections: List[Any]) -> str:
    """
    사용자가 원하는 구조:
    5) 소제목 6) 본문 7) 소제목 8) 본문2 9) 광고 10) 소제목 11) 본문3 12) 광고
    -> 섹션 3개 기준으로 맞추되, 부족하면 있는 만큼만 출력
    """
    blocks: List[str] = []
    if not sections:
        return ""

    # 최소 3개 확보용(부족하면 빈 섹션 추가)
    secs = list(sections)
    while len(secs) < 3:
        secs.append({"title": "추가로 알아두면 좋은 점", "paragraphs": []})

    for idx, sec in enumerate(secs[:3], start=1):
        h, ps = _section_to_blocks(sec)
        if h:
            blocks.append(_h2(h))
        # 문단/불릿 처리
        for line in ps:
            line = (line or "").strip()
            if not line:
                continue
            if line.startswith("- "):
                # 불릿 묶어서 출력
                # 간단하게 한 줄 불릿을 박스로 처리
                blocks.append(_box("포인트", [line[2:]]))
            else:
                blocks.append(_p(line))

        # 섹션2 끝나고 광고(9번)
        if idx == 2:
            ad2 = _ads_block(2)
            if ad2:
                blocks.append(ad2)

    # 마지막 광고(12번)
    ad3 = _ads_block(3)
    if ad3:
        blocks.append(ad3)

    return "\n".join([b for b in blocks if b])


def _render_coupang_block(keyword: str, links: List[Tuple[str, str]]) -> str:
    if not links:
        return ""

    disclosure_text = _env(
        "COUPANG_DISCLOSURE_TEXT",
        "이 포스팅은 쿠팡 파트너스 활동의 일환으로 일정액의 수수료를 제공받을 수 있습니다.",
    )

    # 카드형 3개
    cards = []
    for label, url in links[:3]:
        title = "바로보기" if label == "바로보기" else ("추천" if label == "추천" else "할인")
        desc = "관련 상품을 빠르게 확인해요." if label == "바로보기" else ("후기 많은 옵션을 먼저 보세요." if label == "추천" else "쿠폰/할인 적용을 확인해요.")
        btn = "지금 확인" if label == "바로보기" else ("추천 보기" if label == "추천" else "할인 확인")
        cards.append(
            "<div style='flex:1;min-width:220px;border:1px solid #e5e7eb;border-radius:14px;padding:12px;background:#fff;'>"
            f"<div style='font-weight:900;margin-bottom:6px;color:#0f172a;'>{html.escape(title)}</div>"
            f"<div style='font-size:13px;color:#6b7280;line-height:1.4;margin-bottom:10px;'>{html.escape(desc)}</div>"
            f"<a href='{html.escape(url)}' target='_blank' rel='nofollow sponsored noopener' "
            "style='display:block;text-align:center;padding:12px 14px;border-radius:12px;background:#111827;color:#fff;text-decoration:none;font-weight:900;'>"
            f"{html.escape(btn)} →</a></div>"
        )
    cards_html = (
        "<div style='margin:14px 0;padding:14px;border:1px solid #e5e7eb;border-radius:16px;background:#f8fafc;'>"
        f"<div style='font-weight:900;margin-bottom:10px;color:#0f172a;'>🛒 “{html.escape(keyword)}” 관련 쿠팡 빠른 확인</div>"
        "<div style='display:flex;flex-wrap:wrap;gap:10px;'>"
        + "".join(cards)
        + "</div>"
        "<div style='margin-top:10px;font-size:12px;color:#64748b;line-height:1.4;'>※ 가격/쿠폰/배송은 시점에 따라 변동될 수 있습니다.</div>"
        "</div>"
    )

    disclosure_html = (
        "<div style='margin:12px 0;padding:12px 14px;border-radius:14px;border:1px solid #fed7aa;background:#fff7ed;color:#9a3412;line-height:1.55;'>"
        "<b>광고 안내</b><br/>"
        f"{html.escape(disclosure_text)}"
        "</div>"
    )

    # “버튼이 사라지고 코드가 보임” 방지용: HTML을 우리가 직접 조립한 구조에서만 넣음
    return disclosure_html + "\n" + cards_html


def _compose_managed_post_html(
    *,
    category_name: str,
    title: str,
    keyword: str,
    hero_url: str,
    body_url: str,
    summary_bullets: List[str],
    sections: List[Any],
    coupang_html: str,
) -> str:
    # 1) 제목(본문에도 시각적으로 1번으로 보이게)
    title_block = (
        "<div style='margin:0 0 10px;'>"
        f"<h1 style='margin:0;font-size:26px;line-height:1.25;color:#0f172a;'>{html.escape(title)}</h1>"
        f"<div style='margin-top:6px;color:#64748b;font-size:13px;'>카테고리: <b>{html.escape(category_name)}</b></div>"
        "</div>"
    )

    # 2) 광고(상단)
    ad1 = _ads_block(1)

    # 3) 본글 요약
    summary_box = _box("✅ 본글 요약", summary_bullets or [])

    # 4) 이미지(대표/바디)
    hero = _img(hero_url, alt=title)
    body = _img(body_url, alt=f"{title} 관련 이미지")

    # 5~12) 섹션 + 중간광고 + 하단광고
    sec_html = _render_sections_with_ads(sections)

    # 쿠팡 블록은 “요약 아래(초반)”에 배치(글 흐름 크게 안 깨고 클릭 유도됨)
    parts = [
        title_block,   # 1
        hero,          # 4-1(상단 이미지)
        ad1,           # 2
        summary_box,   # 3
        coupang_html,  # (쇼핑글일 때만)
        body,          # 4-2(중간 이미지)
        sec_html,      # 5~12
    ]

    return "\n".join([p for p in parts if p])


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

    # 시간창 강제: 스케줄에서만
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

    # 쿠팡(쇼핑)은 life만
    coupang_planned = bool(topic == "life" and _env_bool("FORCE_COUPANG_IN_LIFE", "1"))

    # life subtopic
    life_subtopic = ""
    if topic == "life":
        life_subtopic, sub_dbg = pick_life_subtopic(state)
        print("🧩 life_subtopic:", life_subtopic, "| dbg(top3):", (sub_dbg.get("scored") or [])[:3])
        keyword = f"{keyword} {life_subtopic}".strip()

    # angle
    seed = _stable_seed_int(keyword, run_id, str(int(time.time())))
    angle = _title_angle(topic, seed)

    # ✅ 프롬프트: “HTML 생성 금지 + {H}{/H} 강조표기 허용”으로 안전하게
    # (쿠팡/광고/스타일은 main.py가 담당)
    extra_context = ""
    if topic == "trend":
        extra_context = build_news_context(keyword)

    system_prompt = (
        "당신은 한국어 블로그 글 작성자입니다.\n"
        "- 절대 HTML/코드/마크다운 코드블록(``` )을 출력하지 마세요.\n"
        "- 본문에서 강조할 단어/구절은 {H}강조{/H} 형태로만 표시하세요.\n"
        "- 과장/낚시 금지, 사실은 조심스럽게.\n"
        "- 글은 '요약 bullets' + '섹션 3개' 형태로 구성되게 작성하세요.\n"
    )

    user_prompt = (
        f"[키워드]\n{keyword}\n\n"
        f"[관점]\n{angle}\n\n"
        + (f"[이슈 참고]\n{extra_context}\n\n" if extra_context else "")
        + (
            "[출력 형식]\n"
            "1) title: 한 줄\n"
            "2) summary_bullets: 4~6개 불릿\n"
            "3) sections: 3개 (각 섹션은 heading + paragraphs 3~6줄)\n"
            "4) health일 경우: warning_bullets 3개 + checklist_bullets 4개를 추가\n"
            "주의: HTML/코드/``` 금지. 쿠팡/광고/버튼 문구 금지.\n"
        )
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
        dup, reason = pick_retry_reason(post.get("title", ""), history)
        if dup or _title_too_similar(post.get("title", ""), recent):
            post["sections"] = []
            print(f"♻️ 제목 유사/중복({reason or 'similarity'}) → 재생성 유도")
        return post

    post, _ = quality_retry_loop(_gen, max_retry=3)
    post["title"] = _normalize_title(post.get("title", ""))

    # thumb title
    thumb_title = generate_thumbnail_title(openai_client, S.OPENAI_MODEL, post["title"])
    print("🧩 thumb_title:", thumb_title, "| thumb_variant:", thumb_variant)

    # image style
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

    # image prompts
    if topic == "life" and coupang_planned:
        base_prompt = (
            f"{keyword} related household item, practical home product, "
            f"product clearly visible, clean minimal background, "
            f"no packaging text, no labels"
        )
    else:
        base_prompt = post.get("img_prompt") or f"{keyword} blog illustration"

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

    # upload
    hero_url, hero_media_id = upload_media_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD,
        hero_img_titled, make_ascii_filename("featured")
    )
    body_url, _ = upload_media_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD,
        body_img, make_ascii_filename("body")
    )

    # ✅ 카테고리
    category_name = _topic_to_wp_category_name(topic, coupang_planned)
    categories: List[int] = []
    if _env_bool("WP_SET_CATEGORY", "1"):
        cache = state.get("wp_category_cache") if isinstance(state.get("wp_category_cache"), dict) else {}
        if isinstance(cache, dict) and category_name in cache and isinstance(cache.get(category_name), int):
            categories = [int(cache[category_name])]
        else:
            cid = _wp_get_category_id_by_name(S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD, category_name)
            if cid:
                categories = [cid]
                cache = dict(cache) if isinstance(cache, dict) else {}
                cache[category_name] = cid
                state["wp_category_cache"] = cache
                save_state(state)

    # ✅ 쿠팡 블록(쇼핑글일 때만)
    coupang_inserted = False
    coupang_urls: List[Tuple[str, str]] = []
    coupang_html = ""
    if topic == "life" and coupang_planned:
        coupang_urls = _coupang_links_from_keyword(keyword)
        if coupang_urls:
            coupang_html = _render_coupang_block(keyword, coupang_urls)
            coupang_inserted = True
        else:
            print("⚠️ coupang planned BUT deeplink generation failed → skip coupang for this post")

    # ✅ “관리 잘 된 글” 형태로 main.py에서 최종 HTML 조립
    summary_bullets = post.get("summary_bullets") or []
    if not isinstance(summary_bullets, list):
        summary_bullets = []

    # health면 warning/checklist를 섹션에 자연스럽게 끼워 넣기(원하는 “관리 느낌” 강화)
    sections = post.get("sections") or []
    if not isinstance(sections, list):
        sections = []

    if topic == "health":
        warn = post.get("warning_bullets") or []
        chk = post.get("checklist_bullets") or []
        if isinstance(warn, list) and warn:
            sections = [{"title": "병원 상담이 필요한 신호", "bullets": warn}] + list(sections)
        if isinstance(chk, list) and chk:
            sections = list(sections) + [{"title": "오늘의 체크리스트", "bullets": chk}]

    final_html = _compose_managed_post_html(
        category_name=category_name,
        title=post["title"],
        keyword=keyword,
        hero_url=hero_url,
        body_url=body_url,
        summary_bullets=[str(x) for x in summary_bullets if str(x).strip()],
        sections=sections,
        coupang_html=coupang_html,
    )

    # (옵션) 자동 애드센스 삽입을 계속 쓰고 싶으면 1로
    if _env_bool("USE_AUTOSLOT_ADSENSE", "0"):
        final_html = inject_adsense_slots(final_html)

    post["content_html"] = final_html
    if categories:
        post["categories"] = categories

    # publish
    post_id = publish_to_wp(
        S.WP_URL, S.WP_USERNAME, S.WP_APP_PASSWORD,
        post, hero_url, body_url,
        featured_media_id=hero_media_id,
    )

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
            "angle": angle,
            "wp_category": category_name,
            "wp_categories": categories,
        },
    )
    save_state(state)

    print(
        f"✅ 발행 완료: post_id={post_id} | topic={topic} | category={category_name} | forced_slot={forced_slot} | angle={angle} "
        f"| coupang={coupang_inserted} | img_style={image_style_for_stats}"
    )


if __name__ == "__main__":
    run()
