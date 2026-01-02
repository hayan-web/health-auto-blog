# app/config.py
import os


def _get_env(name: str, required: bool = True, default: str = "") -> str:
    v = os.getenv(name, default)
    v = v.strip() if isinstance(v, str) else ""
    if required and not v:
        raise RuntimeError(f"ENV 누락: {name}")
    return v


class Settings:
    # -------------------------
    # API Keys (OpenAI-only)
    # -------------------------
    OPENAI_API_KEY: str = _get_env("OPENAI_API_KEY", required=True)

    # ✅ 이미지도 OpenAI로 통일: 따로 키를 쓰고 싶으면 IMAGE_API_KEY에 넣을 수 있게만 해둠(선택)
    # - 미설정이면 OPENAI_API_KEY를 그대로 사용
    IMAGE_API_KEY: str = _get_env("IMAGE_API_KEY", required=False, default="") or OPENAI_API_KEY

    # -------------------------
    # WordPress
    # -------------------------
    WP_URL: str = _get_env("WP_URL", required=True).rstrip("/")
    WP_USERNAME: str = _get_env("WP_USERNAME", required=True)
    WP_APP_PASSWORD: str = _get_env("WP_APP_PASSWORD", required=True).replace(" ", "")

    # -------------------------
    # Models
    # -------------------------
    # 글 모델 (가성비 기본값)
    OPENAI_MODEL: str = _get_env("OPENAI_MODEL", required=False, default="gpt-5-mini") or "gpt-5-mini"

    # ✅ 이미지 모델: 프로젝트 내부(ai_gemini_image 래퍼)가 OpenAI 이미지 호출을 하도록 되어 있다는 전제
    # 예) "gpt-image-1" / "gpt-image-1.5" 등
    IMAGE_MODEL: str = _get_env("IMAGE_MODEL", required=False, default="gpt-image-1") or "gpt-image-1"

    # (호환) 기존 코드가 GEMINI_IMAGE_MODEL을 참조할 수도 있으니 alias로 유지
    GEMINI_IMAGE_MODEL: str = IMAGE_MODEL

    # -------------------------
    # Naver Search API
    # -------------------------
    NAVER_CLIENT_ID: str = _get_env("NAVER_CLIENT_ID", required=True)
    NAVER_CLIENT_SECRET: str = _get_env("NAVER_CLIENT_SECRET", required=True)

    # -------------------------
    # Guardrail (선택)
    # -------------------------
    MAX_POSTS_PER_DAY: int = int(_get_env("MAX_POSTS_PER_DAY", required=False, default="3") or "3")
    MAX_USD_PER_MONTH: float = float(_get_env("MAX_USD_PER_MONTH", required=False, default="30") or "30")
    ALLOW_OVER_BUDGET: int = int(_get_env("ALLOW_OVER_BUDGET", required=False, default="1") or "1")
