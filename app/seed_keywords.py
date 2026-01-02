from __future__ import annotations

import os
from typing import List


def _split_csv(s: str) -> List[str]:
    items = []
    for x in (s or "").split(","):
        t = x.strip()
        if t:
            items.append(t)
    return items


def get_seed_keywords(topic: str) -> List[str]:
    """
    topic 별 seed keyword 목록 반환
    - env 우선순위:
      1) NAVER_SEED_KEYWORDS_<TOPIC>  (예: NAVER_SEED_KEYWORDS_HEALTH)
      2) NAVER_SEED_KEYWORDS         (fallback)
    """
    topic = (topic or "").strip().upper()
    key_topic = f"NAVER_SEED_KEYWORDS_{topic}"

    s_topic = os.getenv(key_topic, "").strip()
    if s_topic:
        return _split_csv(s_topic)

    # fallback
    s_all = os.getenv("NAVER_SEED_KEYWORDS", "").strip()
    return _split_csv(s_all)
