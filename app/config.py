import os


def _get_env(name: str, required: bool = True) -> str:
    v = os.getenv(name, "")
    v = v.strip() if isinstance(v, str) else ""
    if required and not v:
        raise RuntimeError(f"ENV 누락: {name}")
    return v


class Settings:
    # API Keys
    OPENAI_API_KEY: str = _get_env("OPENAI_API_KEY", required=True)
    GOOGLE_API_KEY: str = _get_env("GOOGLE_API_KEY", required=True)

    # WordPress
    WP_URL: str = _get_env("WP_URL", required=True).rstrip("/")
    WP_USERNAME: str = _get_env("WP_USERNAME", required=True)
    WP_APP_PASSWORD: str = _get_env("WP_APP_PASSWORD", required=True).replace(" ", "")

    # Models
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip() or "gpt-5-mini"
    GEMINI_IMAGE_MODEL: str = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image").strip() or "gemini-2.5-flash-image"
    
    NAVER_CLIENT_ID: str
    NAVER_CLIENT_SECRET: str



