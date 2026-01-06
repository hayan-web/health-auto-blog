# app/news_context.py
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

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
    # Naver news API는 <b>태그를 섞어줍니다
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _parse_pubdate(pub: str) -> str:
    """
    Naver pubDate 예: 'Mon, 06 Jan 2026 12:34:56 +0900'
    실패하면 빈 문자열
    """
    if not pub:
        return ""
    try:
        dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %z")
        return dt.astimezone(KST).strftime("%Y-%m-%d")
    except Exception:
        return ""


def fetch_naver_news_items(query: str, *, display: int = 8, sort: str = "date", timeout: int = 12) -> List[Dict[str, Any]]:
    """
    네이버 검색 API(뉴스)에서 최근 기사 목록을 가져옵니다.
    - display: 최대 100까지 가능(정책/권한에 따라 다를 수 있음)
    - sort: 'date' or 'sim'
    """
    client_id = _env("NAVER_CLIENT_ID", "")
    client_secret = _env("NAVER_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        print("⚠️ NAVER_CLIENT_ID/SECRET 없음 → 뉴스 컨텍스트 스킵")
        return []

    url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
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


def build_news_context(keyword: str) -> str:
    """
    모델에 붙일 '추가 컨텍스트' 문자열을 만듭니다.
    - URL은 모델이 본문에 그대로 뱉어버릴 수 있어 기본적으로 넣지 않습니다.
    - 제목/요약/날짜만 제공해 사실 기반 작성 유도.
    """
    if not keyword:
        return ""

    enable = _env("NEWS_CONTEXT_ENABLE", "1").lower() in ("1", "true", "yes", "y", "on")
    if not enable:
        return ""

    display = _env_int("NEWS_CONTEXT_ITEMS", 8)
    max_chars = _env_int("NEWS_CONTEXT_MAX_CHARS", 1200)

    items = fetch_naver_news_items(keyword, display=display, sort="date")

    lines: List[str] = []
    total = 0

    for it in items:
        if not isinstance(it, dict):
            continue

        title = _strip_tags(str(it.get("title", "")))
        desc = _strip_tags(str(it.get("description", "")))
        pub = _parse_pubdate(str(it.get("pubDate", "")))

        # 너무 짧은 건 제외
        if len(title) < 6:
            continue

        # 설명이 너무 길면 적당히 컷
        if len(desc) > 120:
            desc = desc[:120].rstrip() + "…"

        one = f"- ({pub}) {title}"
        if desc:
            one += f" / {desc}"

        if total + len(one) + 1 > max_chars:
            break

        lines.append(one)
        total += len(one) + 1

        if len(lines) >= max(3, min(display, 12)):
            break

    if not lines:
        return ""

    # 모델이 ‘사실’로 착각하지 않도록 톤을 명확히
    header = "아래는 키워드 관련 최근 뉴스 검색 결과의 제목/요약입니다. 이 범위 안에서만 사실을 정리하세요."
    return header + "\n" + "\n".join(lines)
