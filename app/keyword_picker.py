import os
import random
from typing import Dict, List, Tuple

from app.naver_api import naver_blog_total_count


def _split_csv(s: str) -> List[str]:
    items = []
    for x in (s or "").split(","):
        x = x.strip()
        if x:
            items.append(x)
    return items


def pick_keyword_by_naver(
    naver_client_id: str,
    naver_client_secret: str,
    history: List[Dict],
    max_candidates: int = 12,
) -> Tuple[str, Dict]:
    """
    씨앗 키워드 목록에서 '중복 제외' 후,
    네이버 블로그 검색 결과 수(total) 기반으로 점수화하여 1개를 선택합니다.
    반환: (chosen_keyword, debug_info)
    """
    seed_csv = os.getenv(
        "NAVER_SEED_KEYWORDS",
        "갱년기,혈압관리,고지혈증,수면질개선,중년운동,스트레스관리,관절건강,식단관리,체중관리,당뇨관리,콜레스테롤,유산소운동",
    )
    seeds = _split_csv(seed_csv)
    random.shuffle(seeds)

    used_keywords = set()
    for h in history[-200:]:
        k = (h.get("keyword") or "").strip()
        if k:
            used_keywords.add(k)

    # 중복 제외 + 최대 후보
    candidates = [k for k in seeds if k not in used_keywords][:max_candidates]
    if not candidates:
        # 다 썼으면 그냥 씨앗에서 랜덤 1개(운영 중단 방지)
        candidates = seeds[:max_candidates]

    scored = []
    for kw in candidates:
        try:
            total = naver_blog_total_count(naver_client_id, naver_client_secret, kw)
        except Exception as e:
            # API 실패 시 해당 후보는 점수 0 처리
            total = 0
            print(f"⚠️ Naver 조회 실패: {kw} / {e}")

        # 간단 점수: total이 너무 큰 키워드는 경쟁도도 크니 완만하게 반영
        # (log 대신 **0.35로 완화)
        score = (total ** 0.35) if total > 0 else 0
        scored.append((kw, total, score))

    # score 기준 내림차순
    scored.sort(key=lambda x: x[2], reverse=True)

    chosen = scored[0][0] if scored else candidates[0]
    debug = {
        "candidates": candidates,
        "scored": [{"keyword": k, "total": t, "score": s} for (k, t, s) in scored],
        "chosen": chosen,
    }
    return chosen, debug
