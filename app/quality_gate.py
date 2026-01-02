# app/quality_gate.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


@dataclass
class QualityResult:
    ok: bool
    score: int
    reasons: List[str]


def _safe_str(x: Any) -> str:
    return str(x) if x is not None else ""


def _len_ok(text: str, min_len: int) -> bool:
    return len((text or "").strip()) >= min_len


def score_post(candidate: Dict[str, Any]) -> QualityResult:
    """
    후보 글 품질 점수화.
    - sections[*].body 길이, 구조 존재 여부, img_prompt 안전성 등 체크
    - 통과 기준(ok)은 score >= 70 권장 (main에서 조정)
    """
    reasons: List[str] = []
    score = 100

    title = _safe_str(candidate.get("title"))
    if not _len_ok(title, 8):
        score -= 15
        reasons.append("title이 너무 짧음")

    img_prompt = _safe_str(candidate.get("img_prompt"))
    # 이미지 프롬프트에 1:1 힌트(느슨하게 체크)
    if "square" not in img_prompt.lower() and "1:1" not in img_prompt:
        score -= 8
        reasons.append("img_prompt에 1:1(square) 힌트가 약함")

    # 콜라주/텍스트 유발 단어(완전 차단은 아님. 경고성 감점)
    bad_words = ["collage", "text", "typography", "logo", "watermark", "letters", "words"]
    if any(w in img_prompt.lower() for w in bad_words):
        score -= 6
        reasons.append("img_prompt에 콜라주/텍스트 유발 단어 포함 가능")

    sections = candidate.get("sections") or []
    if not isinstance(sections, list) or len(sections) < 4:
        score -= 18
        reasons.append("sections 개수가 부족(최소 4 권장)")

    # 각 섹션 바디 최소 길이
    if isinstance(sections, list):
        for i, s in enumerate(sections, start=1):
            body = _safe_str((s or {}).get("body"))
            if not _len_ok(body, 140):
                score -= 7
                reasons.append(f"섹션{i}: body가 너무 짧음(140자 미만)")

    # 요약/체크리스트가 둘 다 없으면 감점(둘 중 하나만 있어도 됨)
    summary = candidate.get("summary_bullets")
    checklist = candidate.get("checklist_bullets")
    if not summary and not checklist:
        score -= 10
        reasons.append("summary_bullets/checklist_bullets 둘 다 없음")

    # 안전 하한
    if score < 0:
        score = 0

    ok = score >= 70
    return QualityResult(ok=ok, score=score, reasons=reasons)


def quality_retry_loop(
    generate_fn,
    *,
    max_retry: int = 3,
) -> Tuple[Dict[str, Any], QualityResult]:
    """
    generate_fn() -> candidate(dict)
    통과할 때까지 자동 재생성.
    """
    last_q = QualityResult(ok=False, score=0, reasons=["초기"])
    last_candidate: Dict[str, Any] = {}

    for attempt in range(1, max_retry + 1):
        c = generate_fn()
        q = score_post(c)
        last_q, last_candidate = q, c

        if q.ok:
            return c, q

        # 로그용
        print(f"🧪 품질 FAIL ({q.score}/100) → 재생성 {attempt}/{max_retry}")
        for r in q.reasons[:8]:
            print(" -", r)

    raise RuntimeError("생성 실패: 품질 조건을 만족하는 글을 만들지 못했습니다.")
