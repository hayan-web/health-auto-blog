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


def _kst_date_key(dt: datetime | None = None) -> str:
    d = dt or _kst_now()
    return d.strftime("%Y-%m-%d")


def _slot_topic_kst(dt: datetime | None = None) -> str:
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
# TITLE (유사도 방지 + 티스토리식 짧은 제목 업그레이드)
# -----------------------------
def _normalize_title(title: str) -> str:
    if not title:
        return title
    t = unicodedata.normalize("NFKC", str(title)).strip()
    t = t.replace("ㅡ", "-").replace("–", "-").replace("—", "-").replace("~", "-")

    # 연령/숫자 패턴 제거(제목이 길어지는 원인)
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


def _title_angle(topic: str, seed: int) -> str:
    rng = random.Random(seed)
    if topic == "health":
        pool = ["실천 체크", "주의할 점", "하루 루틴", "핵심 요약", "실수 줄이기", "바로 시작"]
    elif topic == "trend":
        pool = ["지금 포인트", "한눈 요약", "변화 정리", "초보 설명", "체크 포인트", "요점만"]
    else:
        pool = ["바로 적용", "실전 정리", "자주 하는 실수", "빠른 정리", "핵심만"]
    return rng.choice(pool)


def _title_limits(topic: str) -> tuple[int, int]:
    """
    티스토리 느낌: 짧고 선명
    기본: 12~20자
    trend(이슈): 12~22자
    ENV:
      TITLE_MIN=12
      TITLE_MAX=20
      TITLE_MAX_ISSUE=22
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
    # 끝에 붙는 남발 단어 제거(짧게 만들기)
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
    # 숫자/연령대 금지 전제
    if topic == "health":
        return ["의외로 놓치는", "하루만 해도", "요즘 더 중요한", "딱 이것부터", "잘못하면", "먼저 확인", "바로 써먹는", "꾸준히 되는"]
    if topic in ("trend", "issue"):
        return ["지금 핵심만", "요점만 정리", "왜 갑자기", "한 번에 이해", "핵심 포인트", "이렇게 바뀐다", "정리해보면", "지금 체크"]
    return ["이렇게 고르면", "은근 실패하는", "지금 많이 찾는", "딱 맞는", "사기 전 체크", "후회 줄이는", "바로 비교", "간단 정리"]


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

[가능한 훅(참고, 그대로 복붙하지 말고 변형)]
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
# COUPANG: 키워드 -> 딥링크 3개
# -----------------------------
def _coupang_make_auth(method: str, path: str, query: str, access_key: str, secret_key: str) -> str:
    signed_date = datetime.utcnow().strftime("%y%m%dT%H%M%SZ")
    message = signed_date + method + path + query
    signature = hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"
