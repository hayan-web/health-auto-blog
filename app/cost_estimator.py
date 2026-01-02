from __future__ import annotations


def estimate_post_usd(
    *,
    text_tokens: int,
    image_count: int,
    text_usd_per_1k: float = 0.002,   # 예: gpt-5 mini 추정
    image_usd_each: float = 0.02,     # 이미지 1장 추정
) -> float:
    """
    보수적 비용 추정
    """
    text_cost = (text_tokens / 1000.0) * text_usd_per_1k
    image_cost = image_count * image_usd_each
    return round(text_cost + image_cost, 4)
