# app/quality.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


@dataclass
class QualityResult:
    score: int
    reasons: List[str]


def _len(s: str) -> int:
    return len((s or "").strip())


def score_post(post: Dict[str, Any]) -> QualityResult:
    """
    0~100 품질 점수
    - sections가 있으면 그걸 기준으로 평가(권장)
    - 없으면 content/body 기준으로 최소 평가
    """
    reasons: List[str] = []
    score = 100

    title = (post.get("title") or "").strip()
    if _len(title) < 10:
        score -= 15
        reasons.append("title이 너무 짧음(10자 미만)")

    img_prompt = (post.get("img_prompt") or "").lower()
    if "square" not in img_prompt and "1:1" not in img_prompt:
        score -= 5
        reasons.append("img_prompt에 1:1(square) 힌트가 약함")

    sections = post.get("sections")
    if isinstance(sections, list) and sections:
        if len(sections) < 4:
            score -= 15
            reasons.append("sections 개수가 너무 적음(4개 미만)")

        for idx, sec in enumerate(sections, start=1):
            body = ""
            if isinstance(sec, dict):
                body = (sec.get("body") or sec.get("content") or "").strip()
            elif isinstance(sec, str):
                body = sec.strip()

            if _len(body) < 140:
                score -= 6
                reasons.append(f"섹션{idx}: body가 너무 짧음(140자 미만)")
    else:
        # fallback: content/body 기반 최소 검사
        raw = (post.get("content") or post.get("body") or "").strip()
        if _len(raw) < 800:
            score -= 20
            reasons.append("본문이 너무 짧음(800자 미만)")

    # 보정
    if score < 0:
        score = 0
    if score > 100:
        score = 100

    return QualityResult(score=score, reasons=reasons)
