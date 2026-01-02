# app/config.py
import os


def _get_env(name: str, required: bool = True, default: str = "") -> str:
    v = os.getenv(name)
    v = v.strip() if isinstance(v, str) else ""
    if required and not v:
        raise RuntimeError(f"ENV 누락: {name}")
    return v if v else default


class Settings:
    # ======================
    # API Keys
    # ======================
    OPENAI_API_KEY: str = _get_env("OPENAI_API_KEY", required=True)

    # ✅ Google/Gemini 키는 이제 선택 사항 (없어도 동작)
    GOOGLE_API_KEY: str = _get_env("GOOGLE_API_KEY", required=False, default="")

    # ======================
    # WordPress
    # ======================
    WP_URL: str = _get_env("WP_URL", required=True).rstrip("/")
    WP_USERNAME: str = _get_env("WP_USERNAME", required=True)
    WP_APP_PASSWORD: str = _get_env(
        "WP_APP_PASSWORD", required=True
    ).replace(" ", "")

    # ======================
    # Models
    # ======================
    # 글 생성 (가성비)
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip() or "gpt-5-mini"

    # 이미지 모델
    # ⚠️ 내부에서 OpenAI 이미지로 래핑해 쓰더라도 이름 유지 가능
    GEMINI_IMAGE_MODEL: str = os.getenv(
        "GEMINI_IMAGE_MODEL",
        "gpt-image-1.5"
    ).strip() or "gpt-image-1.5"

    # ======================
    # Naver Search API
    # ======================
    NAVER_CLIENT_ID: str = _get_env("NAVER_CLIENT_ID", required=True)
    NAVER_CLIENT_SECRET: str = _get_env("NAVER_CLIENT_SECRET", required=True)
