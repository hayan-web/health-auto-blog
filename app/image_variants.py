import datetime as _dt
import random
from typing import Tuple


_VARIANTS = [
    # 구도/시점/거리/배경을 강하게 바꿔주는 템플릿들
    "wide shot, environment context, bright daylight, clean background",
    "close-up shot, detailed focus, shallow depth of field, soft natural light",
    "top-down flat lay, minimal desk setup, objects neatly arranged",
    "side angle, candid lifestyle scene, warm indoor lighting",
    "isometric illustration, modern infographic style, simple shapes",
    "cutaway diagram style, simplified labeled-like (but NO TEXT), clean vector look",
    "outdoor scene, calm background, gentle gradient sky, realistic photo style",
    "medical illustration style, clean white background, high clarity (NO TEXT)",
]

def build_image_prompts(base_prompt: str, keyword: str) -> Tuple[str, str]:
    """
    base_prompt를 기반으로:
    - hero: 기본(대표)
    - body: 강제 변주(다른 구도/다른 씬) + 매번 달라지는 변주키
    """
    base = (base_prompt or "").strip()
    if not base:
        base = f"{keyword} topic illustration, single scene, no collage, no text, square 1:1"

    # 변주키(매 실행마다 달라지게): 날짜 + 랜덤
    today = _dt.datetime.utcnow().strftime("%Y%m%d")
    salt = random.randint(1000, 9999)

    hero = base + ", single scene, no collage, no text, square 1:1"

    variant = random.choice(_VARIANTS)
    body = (
        base
        + f", {variant}, different subject emphasis, different composition, different angle, "
          "single scene, no collage, no text, square 1:1, "
        + f"variation key {today}-{salt}"
    )

    return hero, body
