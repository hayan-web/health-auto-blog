from dataclasses import dataclass
import datetime as dt
from typing import Dict, Tuple


@dataclass
class BudgetConfig:
    max_posts_per_day: int = 3
    max_images_per_day: int = 6

    # 간단 추정치(원하면 더 정교화 가능)
    # 이미지 단가: gpt-image-1-mini medium ~ $0.011/이미지 (대략)
    image_cost_usd: float = 0.011

    # 월 예산(달러)
    max_monthly_usd: float = 10.0


def _today_key() -> str:
    return dt.datetime.utcnow().strftime("%Y-%m-%d")


def _month_key() -> str:
    return dt.datetime.utcnow().strftime("%Y-%m")


def can_post(state: Dict, cfg: BudgetConfig) -> Tuple[bool, str]:
    """
    state.json 기반으로 오늘/이번달 제한 체크
    """
    usage = state.get("usage", {})
    day = _today_key()
    month = _month_key()

    day_posts = usage.get("posts", {}).get(day, 0)
    day_images = usage.get("images", {}).get(day, 0)
    month_spend = usage.get("spend_usd", {}).get(month, 0.0)

    if day_posts >= cfg.max_posts_per_day:
        return False, f"일일 포스팅 제한 초과({day_posts}/{cfg.max_posts_per_day})"
    if day_images >= cfg.max_images_per_day:
        return False, f"일일 이미지 제한 초과({day_images}/{cfg.max_images_per_day})"
    if month_spend >= cfg.max_monthly_usd:
        return False, f"월 예산 초과(${month_spend:.2f}/${cfg.max_monthly_usd:.2f})"
    return True, "OK"


def add_usage(state: Dict, posts: int = 0, images: int = 0, spend_usd: float = 0.0) -> Dict:
    usage = state.setdefault("usage", {})
    posts_map = usage.setdefault("posts", {})
    images_map = usage.setdefault("images", {})
    spend_map = usage.setdefault("spend_usd", {})

    day = _today_key()
    month = _month_key()

    posts_map[day] = int(posts_map.get(day, 0)) + int(posts)
    images_map[day] = int(images_map.get(day, 0)) + int(images)

    spend_map[month] = float(spend_map.get(month, 0.0)) + float(spend_usd)
    return state
