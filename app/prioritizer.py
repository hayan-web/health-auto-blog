# app/prioritizer.py
from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Tuple, Optional


def _safe_get(d: Dict[str, Any], *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _ctr(impressions: int, clicks: int, *, alpha: float = 1.0, beta: float = 25.0) -> float:
    """
    CTR 추정치 (스무딩 포함)
    - alpha/beta는 초반 데이터 적을 때 폭주 방지용
    """
    impressions = max(0, int(impressions))
    clicks = max(0, int(clicks))
    return (clicks + alpha) / (impressions + beta)


def _u_cb(ctr_est: float, impressions: int, *, c: float = 0.35) -> float:
    """
    탐색(Explore) 보너스: 노출 적은 후보를 가끔 띄워줌
    """
    impressions = max(1, int(impressions))
    return ctr_est + c * math.sqrt(math.log(1 + impressions) / impressions)


def _get_image_candidates(state: Dict[str, Any], topic: str) -> List[str]:
    """
    image_style_picker가 관리하는 스타일 목록이 state에 없다면,
    지금까지 관측된 키들 기반으로 후보를 구성합니다.
    """
    # topic별 스타일 성과가 있다면 그 키를 우선 후보로
    ts = _safe_get(state, "topic_style_stats", topic, default={})
    if isinstance(ts, dict) and ts:
        return list(ts.keys())

    # 전역 image_stats를 후보로
    im = _safe_get(state, "image_stats", default={})
    if isinstance(im, dict) and im:
        return list(im.keys())

    # 아무것도 없으면 기본 후보
    return ["clean_flat", "3d_clay", "watercolor", "isometric", "photo", "sketch", "vector", "soft3d"]


def _get_thumb_candidates(state: Dict[str, Any], topic: str) -> List[str]:
    """
    thumb_title_stats 모듈을 쓰고 있다면 그 키를 후보로.
    없으면 기본 후보 4개 제공.
    """
    tt = _safe_get(state, "thumb_title_stats", default={})
    if isinstance(tt, dict) and tt:
        return list(tt.keys())

    # topic별 thumb가 있다면 거기서
    ttt = _safe_get(state, "topic_thumb_title_stats", topic, default={})
    if isinstance(ttt, dict) and ttt:
        return list(ttt.keys())

    # 기본 후보(variant id)
    return ["benefit_short", "howto_short", "mythbust_short", "checklist_short"]


def _rpm_weight(topic: str) -> float:
    """
    RPM(수익성) 가중치 기본값.
    - GA/애드센스/쿠팡 실제 RPM을 넣게 되면 여기 로직을 state 기반으로 바꿔도 됨.
    """
    # 기본: 건강(높음) > 생활(중간) > IT(중간)
    t = (topic or "").lower()
    if "health" in t or "건강" in t:
        return 1.15
    if "it" in t:
        return 1.00
    if "life" in t or "생활" in t:
        return 1.05
    return 1.00


def pick_best_publishing_combo(
    state: Dict[str, Any],
    *,
    topic: str,
    epsilon: float = 0.12,
) -> Tuple[str, str, Dict[str, Any]]:
    """
    (image_style, thumb_variant, debug)를 반환합니다.

    점수 = (CTR 추정치 + 탐색 보너스) * RPM가중치
    - CTR은 click 로그 기반으로 실측 학습
    - RPM은 기본 가중치로 시작(후에 실데이터로 대체 가능)
    """
    topic = topic or "general"
    rpm = _rpm_weight(topic)

    img_candidates = _get_image_candidates(state, topic)
    tv_candidates = _get_thumb_candidates(state, topic)

    # 랜덤 탐색(초반/정체 시 탈출용)
    if random.random() < epsilon:
        img = random.choice(img_candidates)
        tv = random.choice(tv_candidates)
        return img, tv, {"mode": "epsilon", "topic": topic, "rpm": rpm, "img": img, "tv": tv}

    # 성과 기반 선택
    # 1) 이미지 스타일 점수
    img_best = None
    img_best_score = -1.0
    img_dbg = []

    for img in img_candidates:
        # topic별 우선, 없으면 전역
        tnode = _safe_get(state, "topic_style_stats", topic, img, default=None)
        if isinstance(tnode, dict):
            imp = int(tnode.get("impressions", 0))
            clk = int(tnode.get("clicks", 0))
        else:
            node = _safe_get(state, "image_stats", img, default={})
            imp = int(node.get("impressions", 0)) if isinstance(node, dict) else 0
            clk = int(node.get("clicks", 0)) if isinstance(node, dict) else 0

        ctr_est = _ctr(imp, clk)
        score = _u_cb(ctr_est, imp) * rpm
        img_dbg.append((img, imp, clk, ctr_est, score))

        if score > img_best_score:
            img_best_score = score
            img_best = img

    # 2) 썸네일 문구(variant) 점수
    tv_best = None
    tv_best_score = -1.0
    tv_dbg = []

    for tv in tv_candidates:
        node = _safe_get(state, "thumb_title_stats", tv, default=None)
        if isinstance(node, dict):
            imp = int(node.get("impressions", 0))
            clk = int(node.get("clicks", 0))
        else:
            tnode = _safe_get(state, "topic_thumb_title_stats", topic, tv, default={})
            imp = int(tnode.get("impressions", 0)) if isinstance(tnode, dict) else 0
            clk = int(tnode.get("clicks", 0)) if isinstance(tnode, dict) else 0

        ctr_est = _ctr(imp, clk)
        score = _u_cb(ctr_est, imp) * rpm
        tv_dbg.append((tv, imp, clk, ctr_est, score))

        if score > tv_best_score:
            tv_best_score = score
            tv_best = tv

    img_best = img_best or random.choice(img_candidates)
    tv_best = tv_best or random.choice(tv_candidates)

    debug = {
        "mode": "score",
        "topic": topic,
        "rpm": rpm,
        "img_best": img_best,
        "img_best_score": img_best_score,
        "tv_best": tv_best,
        "tv_best_score": tv_best_score,
        "img_top5": sorted(img_dbg, key=lambda x: x[-1], reverse=True)[:5],
        "tv_top5": sorted(tv_dbg, key=lambda x: x[-1], reverse=True)[:5],
    }
    return img_best, tv_best, debug
