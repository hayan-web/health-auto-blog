# app/life_subtopic_stats.py
from __future__ import annotations

from typing import Dict, Optional


def _ensure(state: Dict) -> Dict:
    state = dict(state or {})
    if "life_subtopic_stats" not in state or not isinstance(state["life_subtopic_stats"], dict):
        state["life_subtopic_stats"] = {}
    return state


def record_life_subtopic_impression(state: Dict, subtopic: str, *, n: int = 1) -> Dict:
    state = _ensure(state)
    st = state["life_subtopic_stats"]
    row = st.get(subtopic, {})
    row["impressions"] = int(row.get("impressions", 0)) + int(n)
    # clicks는 여기서 증가시키지 않음
    row["clicks"] = int(row.get("clicks", 0))
    st[subtopic] = row
    return state


def add_life_subtopic_click(state: Dict, subtopic: str, *, n: int = 1) -> Dict:
    """
    클릭 로그를 이 함수로 넘겨줄 수 있으면 CTR 학습이 제일 정확해집니다.
    현재 click_ingest 구조를 모르는 상태라, 연결이 가능할 때만 사용하세요.
    """
    state = _ensure(state)
    st = state["life_subtopic_stats"]
    row = st.get(subtopic, {})
    row["impressions"] = int(row.get("impressions", 0))
    row["clicks"] = int(row.get("clicks", 0)) + int(n)
    st[subtopic] = row
    return state


def try_update_from_post_metrics(state: Dict, *, history_key: str = "history") -> Dict:
    """
    (있으면) state 안의 post metrics로 subtopic 클릭을 간접 업데이트.
    - 다양한 저장 구조를 안전하게 커버하기 위해 '있을 때만' 동작합니다.
    기대 구조(예시):
      state["post_metrics"][post_id] = {"clicks": 3, ...}
    또는:
      state["clicks_by_post_id"][post_id] = 3
    """
    state = _ensure(state)

    post_metrics = state.get("post_metrics")
    clicks_by_post_id = state.get("clicks_by_post_id")

    if not isinstance(post_metrics, dict) and not isinstance(clicks_by_post_id, dict):
        return state  # 정보가 없으면 패스

    hist = state.get(history_key, [])
    if not isinstance(hist, list) or not hist:
        return state

    # 최근 것들만 조금 훑기(과도 업데이트 방지)
    for item in hist[-60:]:
        if not isinstance(item, dict):
            continue
        post_id = item.get("post_id")
        subtopic = item.get("life_subtopic")
        if not post_id or not subtopic:
            continue

        clicks = None
        if isinstance(post_metrics, dict) and post_id in post_metrics:
            m = post_metrics.get(post_id) or {}
            if isinstance(m, dict):
                clicks = m.get("clicks")
        if clicks is None and isinstance(clicks_by_post_id, dict):
            clicks = clicks_by_post_id.get(post_id)

        if clicks is None:
            continue

        # 누적 클릭을 그대로 덮어쓰지 않고 "증가분" 추정은 어렵기 때문에,
        # 여기서는 보수적으로 impression smoothing만 쌓이고,
        # clicks는 별도 파이프라인에서 add_life_subtopic_click로 넣는 걸 권장합니다.
        # (그래도 최소한 구조가 있어야 함)
        try:
            clicks = int(clicks)
        except Exception:
            continue

        # 안전: clicks가 0이면 생략
        if clicks <= 0:
            continue

        # 매우 보수적으로 1만 반영(과대 누적 방지)
        state = add_life_subtopic_click(state, subtopic, n=1)

    return state
