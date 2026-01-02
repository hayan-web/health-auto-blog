import base64
import os
import time
from typing import Any, Optional

from openai import OpenAI


def make_gemini_client(api_key: str) -> Any:
    """
    main.py의 기존 흐름을 깨지 않기 위해 함수명 유지.
    - 기존에는 GOOGLE_API_KEY로 Gemini client를 만들었지만,
      이제는 OpenAI 이미지 client를 만들어 반환합니다.
    """
    # 기존 코드가 S.GOOGLE_API_KEY를 넘기는 구조라 비어있을 수 있음
    key = (api_key or "").strip() or (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY가 비어 있습니다. (GOOGLE_API_KEY를 쓰지 않아도 됩니다)")
    return OpenAI(api_key=key)


def _is_png(b: bytes) -> bool:
    return len(b) >= 8 and b[:8] == b"\x89PNG\r\n\x1a\n"


def _is_jpg(b: bytes) -> bool:
    return len(b) >= 3 and b[:3] == b"\xff\xd8\xff"


def _pick_openai_image_model(model: str) -> str:
    """
    Settings에서 S.GEMINI_IMAGE_MODEL에 뭐가 들어오든,
    OpenAI 이미지 모델명으로 안전 매핑.
    """
    m = (model or "").strip()

    # 사용자가 이미 OpenAI 이미지 모델명을 넣은 경우 그대로 사용
    if m.startswith("gpt-image-"):
        return m

    # 기존 'nano-banana' 등 Gemini 모델명일 가능성 → 기본값으로 대체
    # (가성비 최우선이면 mini 추천)
    return "gpt-image-1-mini"


def generate_nanobanana_image_png_bytes(
    gemini_client: Any,
    model: str,
    prompt: str,
    *,
    retries: int = 3,
    sleep_sec: float = 1.2,
) -> bytes:
    """
    (함수명 유지) OpenAI 이미지 생성 -> 이미지 bytes 반환
    - PNG로 받되, 혹시 JPG가 오면 JPG도 허용
    - 바이트 매직헤더로 검증하고 실패 시 재시도
    """
    last_err: Optional[Exception] = None
    client: OpenAI = gemini_client  # make_gemini_client가 OpenAI 클라이언트를 반환

    image_model = _pick_openai_image_model(model)

    for attempt in range(1, retries + 1):
        try:
            # OpenAI Images API (base64 반환)
            resp = client.images.generate(
                model=image_model,
                prompt=prompt,
                size="1024x1024",
            )

            # SDK 응답: resp.data[0].b64_json
            b64 = getattr(resp.data[0], "b64_json", None) if resp and resp.data else None
            if not b64:
                raise RuntimeError("OpenAI 이미지 응답에서 b64_json을 찾지 못했습니다.")

            img_bytes = base64.b64decode(b64)

            # 최소 크기 체크 (너무 작으면 에러 텍스트/깨짐일 확률 높음)
            if not img_bytes or len(img_bytes) < 500:
                raise RuntimeError(f"OpenAI 이미지 바이트가 너무 작습니다(len={len(img_bytes) if img_bytes else 0}).")

            # PNG/JPG 헤더 검증
            if not (_is_png(img_bytes) or _is_jpg(img_bytes)):
                head = img_bytes[:40]
                raise RuntimeError(f"OpenAI 이미지 바이트가 PNG/JPG가 아닙니다. head={head!r}")

            return img_bytes

        except Exception as e:
            last_err = e
            print(f"⚠️ OpenAI 이미지 생성 실패 {attempt}/{retries}: {e}")
            if attempt < retries:
                time.sleep(sleep_sec * attempt)

    raise RuntimeError(f"OpenAI 이미지 생성 최종 실패: {last_err}")
