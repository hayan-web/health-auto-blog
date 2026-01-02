import os


def _get_env(name: str, required: bool = True) -> str:
    v = os.getenv(name, "")
    v = v.strip() if isinstance(v, str) else ""
    if required and not v:
        raise RuntimeError(f"ENV 누락: {name}")
    return v


class Settings:
    # -------------------------
    # API Keys
    # -------------------------
    OPENAI_API_KEY: str = _get_env("OPENAI_API_KEY", required=True)

    # Google(Gemini) 키는 **선택**
    # (OpenAI-only 모드에서는 없어도 동작하도록 required=False)
    GOOGLE_API_KEY: str = _get_env("GOOGLE_API_KEY", required=False)

    # 이미지 호출 키를 별도로 지정하고 싶으면 사용 (기본: OPENAI_API_KEY)
    IMAGE_API_KEY: str = _get_env("IMAGE_API_KEY", required=False) or OPENAI_API_KEY

    # -------------------------
    # WordPress
    # -------------------------
    WP_URL: str = _get_env("WP_URL", required=True).rstrip("/")
    WP_USERNAME: str = _get_env("WP_USERNAME", required=True)
    WP_APP_PASSWORD: str = _get_env("WP_APP_PASSWORD", required=True).replace(" ", "")

    # -------------------------
    # Models
    # -------------------------
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip() or "gpt-5-mini"
    GEMINI_IMAGE_MODEL: str = os.getenv("GEMINI_IMAGE_MODEL", "gpt-image-1").strip() or "gpt-image-1"

    # -------------------------
    # Naver Search API
    # -------------------------
    NAVER_CLIENT_ID: str = _get_env("NAVER_CLIENT_ID", required=True)
    NAVER_CLIENT_SECRET: str = _get_env("NAVER_CLIENT_SECRET", required=True)

    # -------------------------
    # Guardrails / Ops
    # -------------------------
    MAX_POSTS_PER_DAY: int = int(os.getenv("MAX_POSTS_PER_DAY", "3") or "3")
    MAX_USD_PER_MONTH: float = float(os.getenv("MAX_USD_PER_MONTH", "30.0") or "30.0")

    # 자동발행 우선(초과여도 진행): 1=허용, 0=차단
    ALLOW_OVER_BUDGET: int = int(os.getenv("ALLOW_OVER_BUDGET", "1") or "1")

    # -------------------------
    # AdSense (optional)
    # -------------------------
    ADSENSE_CLIENT: str = os.getenv("ADSENSE_CLIENT", "").strip()  # e.g. ca-pub-xxxxxxxxxxxx
    ADSENSE_SLOT_TOP: str = os.getenv("ADSENSE_SLOT_TOP", "").strip() or os.getenv("ADSENSE_SLOT_1", "").strip()
    ADSENSE_SLOT_MID: str = os.getenv("ADSENSE_SLOT_MID", "").strip() or os.getenv("ADSENSE_SLOT_2", "").strip()
    ADSENSE_SLOT_BOTTOM: str = os.getenv("ADSENSE_SLOT_BOTTOM", "").strip() or os.getenv("ADSENSE_SLOT_3", "").strip()

    # 본문에 adsbygoogle.js 스크립트를 같이 넣을지 (테마/플러그인에서 이미 넣었으면 0 권장)
    ADSENSE_INCLUDE_SCRIPT: int = int(os.getenv("ADSENSE_INCLUDE_SCRIPT", "0") or "0")
