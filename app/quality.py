import re
from typing import Dict, Tuple, List


def _word_count(s: str) -> int:
    if not s:
        return 0
    return len(re.findall(r"\S+", s))


def score_post(post: Dict) -> Tuple[int, List[str]]:
    """
    0~100 점수(휴리스틱)
    - 너무 짧거나
    - 섹션 구조 없거나
    - 반복/스팸 느낌 강하면 감점
    """
    reasons = []
    score = 100

    title = (post.get("title") or "").strip()
    content = (post.get("content") or post.get("body") or "").strip()
    intro = (post.get("intro") or "").strip()
    outro = (post.get("outro") or "").strip()
    sections = post.get("sections") or []

    # 제목
    if len(title) < 8:
        score -= 15
        reasons.append("제목이 너무 짧음")
    if len(title) > 60:
        score -= 10
        reasons.append("제목이 너무 김")

    # 본문 길이
    wc = _word_count(content)
    # content가 비어있고 sections 기반이면 sections로 대체 측정
    if wc < 200 and isinstance(sections, list) and sections:
        merged = " ".join([(s.get("body") or "") for s in sections if isinstance(s, dict)])
        wc = _word_count(merged)

    if wc < 350:
        score -= 25
        reasons.append(f"본문이 짧음({wc} words)")
    elif wc < 600:
        score -= 10
        reasons.append(f"본문이 다소 짧음({wc} words)")

    # 섹션 구조
    if not sections or not isinstance(sections, list):
        score -= 15
        reasons.append("섹션 구조 없음(sections 비어있음)")

    # 반복 감점(단순 휴리스틱)
    combined = " ".join([title, intro, content, outro])
    if combined:
        # 같은 문장/구가 과하게 반복되는지 간단 체크
        tokens = re.findall(r"[가-힣A-Za-z0-9]{2,}", combined.lower())
        if len(tokens) > 50:
            top = {}
            for t in tokens:
                top[t] = top.get(t, 0) + 1
            worst = max(top.values())
            if worst >= 18:
                score -= 15
                reasons.append("단어 반복이 과함")

    # 점수 하한/상한
    score = max(0, min(100, score))
    return score, reasons


def needs_regen(score: int, threshold: int = 75) -> bool:
    return score < threshold
