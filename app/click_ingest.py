from __future__ import annotations

import os
import requests
from typing import Dict, Any

from app.image_stats import record_click as record_image_click, update_score as update_image_score
from app.topic_style_stats import record_click as record_topic_style_click, update_score as update_topic_style_score
from app.thumb_title_stats import (
    record_click as record_thumb_click,
    update_score as update_thumb_score,
    record_topic_click as record_topic_thumb_click,
    update_topic_score as update_topic_thumb_score,
)


def ingest_click_log(state: Dict[str, Any], wp_base_url: str) -> Dict[str, Any]:
    """
    wp-content/uploads/auto-click.log 를 읽어
    state.json에 click 반영
    """
    log_url = wp_base_url.rstrip("/") + "/wp-content/uploads/auto-click.log"

    try:
        resp = requests.get(log_url, timeout=10)
        if resp.status_code != 200:
            print("ℹ️ 클릭 로그 없음")
            return state
    except Exception as e:
        print("ℹ️ 클릭 로그 접근 실패:", e)
        return state

    lines = resp.text.strip().splitlines()
    if not lines:
        return state

    for line in lines[-200:]:  # 최근 200개만 반영
        try:
            _, pid, img, tv, tp, _ = line.split("\t", 5)

            if img and img != "-":
                state = record_image_click(state, img)
                state = update_image_score(state, img)

            if tv and tv != "-":
                state = record_thumb_click(state, tv)
                state = update_thumb_score(state, tv)

            if tp and img and tp != "-" and img != "-":
                state = record_topic_style_click(state, tp, img)
                state = update_topic_style_score(state, tp, img)

            if tp and tv and tp != "-" and tv != "-":
                state = record_topic_thumb_click(state, tp, tv)
                state = update_topic_thumb_score(state, tp, tv)

        except Exception:
            continue

    return state
