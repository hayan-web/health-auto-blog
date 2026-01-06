# app/news_context.py
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Tuple
from urllib.parse import urlparse

import requests

KST = timezone(timedelta(hours=9))


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except Exception:
        return default


def _strip_tags(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _parse_pubdate(pub: str) -> str:
    # Naver pubDate 예: 'Mon, 06 Jan 2026 12:34:56 +0900'
    if not pub:
        return ""
    try:
        dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %z")
        return dt.astimezone(KST).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _domain_of(url: str) -> str:
    if not url:
        return ""
    try:
        d = urlparse(url).netloc.lower()
        d = d.replace("www.", "")
        return d
    except Exception:
        return ""


def _tokenize(text: str) -> set[str]:
    t = re.sub(r"[^0-9A-Za-z가-힣\s]", " ", text or "")
    t = re.sub(r"\s+", " ", t).strip()
    return set([x for x in t.split(" ") if len(x) >= 2])


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / (len(a | b) or 1)


def is_policy_keyword(keyword: str) -> bool:
    """
    '정부지원금/정책/신청/제도/보조금/세금/대출/금리/복지' 등
    공공/정책 성격이면 공식 톤 강제.
    """
    k = (keyword or "").lower()
    signals = [
        "지원금", "보조금", "정책", "제도", "신청", "접수", "대상", "요건", "서류", "기한",
        "정부", "지자체", "복지", "세금", "연말정산", "환급", "대출", "금리", "규정",
        "법", "시행", "고시", "공고", "변경", "개정",
    ]
    return any(s in k for s in signals)


def fetch_naver_news_items(query: str, *, display: int = 10, sort: str = "date", timeout: int = 12) -> List[Dict[str, Any]]:
    client_id = _env("NAVER_CLIENT_ID", "")
    client_secret = _env("NAVER_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print("⚠️ NAVER_CLIENT_ID/SECRET 없음 → 뉴스 컨텍스트 스킵")
        return []

    url = "https://openapi.naver.com/v1/search/news.json"
    headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}
    params = {"query": query, "display": max(1, min(display, 30)), "sort": sort}

    try:
        r = requests.get(url, headers=headers, params=params, timeout=timeout)
        if r.status_code != 200:
            print(f"⚠️ naver news api http={r.status_code} body={(r.text or '')[:200]}")
            return []
        data = r.json() if isinstance(r.json(), dict) else {}
        items = data.get("items") or []
        return items if isinstance(items, list) else []
    except Exception as e:
        print(f"⚠️ naver news api error: {e}")
        return []


def _dedupe_news(items: List[Dict[str, Any]], sim_threshold: float = 0.62) -> List[Dict[str, Any]]:
    """
    제목 유사(자카드) 중복 제거
    """
    kept: List[Dict[str, Any]] = []
    tokens_kept: List[set[str]] = []

    for it in items:
        if not isinstance(it, dict):
            continue
        title = _strip_tags(str(it.get("title", "")))
        if len(title) < 6:
            continue
        tok = _tokenize(title)
        dup = False
        for kt in tokens_kept:
            if _jaccard(tok, kt) >= sim_threshold:
                dup = True
                break
        if dup:
            continue
        kept.append(it)
        tokens_kept.append(tok)
    return kept


def build_news_context(keyword: str) -> str:
    """
    ✅ 모델에 제공할 뉴스 컨텍스트:
    - 최대 3개(기본)
    - 제목/요약/날짜/도메인만 제공(링크 원문은 미제공)
    - '이 범위 밖 사실 단정 금지'를 강하게 명시
    """
    if not keyword:
        return ""

    enable = _env("NEWS_CONTEXT_ENABLE", "1").lower() in ("1", "true", "yes", "y", "on")
    if not enable:
        return ""

    display = _env_int("NEWS_CONTEXT_ITEMS", 10)
    keep = _env_int("NEWS_CONTEXT_KEEP", 3)
    max_chars = _env_int("NEWS_CONTEXT_MAX_CHARS", 900)

    items = fetch_naver_news_items(keyword, display=display, sort="date")
    items = _dedupe_news(items, sim_threshold=float(_env("NEWS_CONTEXT_SIM_THRESHOLD", "0.62") or "0.62"))

    lines: List[str] = []
    total = 0

    for it in items:
        title = _strip_tags(str(it.get("title", "")))
        desc = _strip_tags(str(it.get("description", "")))
        pub = _parse_pubdate(str(it.get("pubDate", "")))

        # source domain (originallink 우선)
        src = _domain_of(str(it.get("originallink", "")) or str(it.get("link", "")))

        if len(desc) > 120:
            desc = desc[:120].rstrip() + "…"

        one = f"- ({pub}) {title}"
        if src:
            one += f" [{src}]"
        if desc:
            one += f" / {desc}"

        if total + len(one) + 1 > max_chars:
            break

        lines.append(one)
        total += len(one) + 1
        if len(lines) >= max(1, min(keep, 5)):
            break

    if not lines:
        return ""

    header = (
        "아래는 ‘키워드 관련 최근 뉴스 검색 결과(제목/요약/날짜/출처도메인)’입니다.\n"
        "⚠️ 반드시 아래 내용에서 확인 가능한 범위로만 사실을 요약하세요.\n"
        "⚠️ 아래에 없는 구체 수치/날짜/기관명/발표 내용은 임의로 만들지 마세요.\n"
    )
    return header + "\n".join(lines)
