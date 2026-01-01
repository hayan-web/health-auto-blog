import base64
import time
from typing import Any, Optional

from google import genai


def make_gemini_client(api_key: str) -> Any:
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY가 비어 있습니다.")
    return genai.Client(api_key=api_key)


def _extract_inline_image_b64(resp: Any) -> Optional[str]:
    # dict 형태(JSON)
    if isinstance(resp, dict):
        candidates = resp.get("candidates") or []
        for c in candidates:
            content = c.get("content") or {}
            parts = content.get("parts") or []
            for p in parts:
                inline = p.get("inline_data") or p.get("inlineData") or {}
                data = inline.get("data")
                if data:
                    return data

    # 객체 형태(SDK response)
    candidates = getattr(resp, "candidates", None)
    if candidates:
        for c in candidates:
            content = getattr(c, "content", None)
            parts = getattr(content, "parts", None) if content else None
            if not parts:
                continue
            for p in parts:
                inline = getattr(p, "inline_data", None) or getattr(p, "inlineData", None)
                data = getattr(inline, "data", None) if inline else None
                if data:
                    return data

    return None


def _is_png(b: bytes) -> bool:
    return len(b) >= 8 and b[:8] == b"\x89PNG\r\n\x1a\n"


def _is_jpg(b: bytes) -> bool:
    return len(b) >= 3 and b[:3] == b"\xff\xd8\xff"


def generate_nanobanana_image_png_bytes(
    gemini_client: Any,
    model: str,
    prompt: str,
    *,
    retries: int = 3,
    sleep_sec: float = 1.2,
) -> bytes:
    """
    Gemini 이미지 생성 -> 이미지 bytes 반환 (PNG/JPG 모두 허용)
    - "바이트가 작다"는 이유만으로 바로 실패시키지 않고,
      실제 PNG/JPG 매직바이트로 검증
    - 그래도 비정상이면 재시도
    """
    last_err: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            resp = gemini_client.models.generate_content(
                model=model,
                contents=prompt,
            )

            b64 = _extract_inline_image_b64(resp)
            if not b64:
                print("🧩 Gemini raw resp (head):", str(resp)[:800])
                raise RuntimeError("Gemini 응답에서 이미지 데이터(inline_data.data)를 찾지 못했습니다.")

            # base64 decode
            img_bytes = base64.b64decode(b64)

            # 아주 작은 경우는 진짜로 실패일 확률이 높아서 컷(너무 빡세게 잡지 않음)
            if not img_bytes or len(img_bytes) < 200:
                raise RuntimeError(f"Gemini 이미지 바이트가 너무 작습니다(len={len(img_bytes) if img_bytes else 0}).")

            # PNG/JPG 헤더 검증
            if not (_is_png(img_bytes) or _is_jpg(img_bytes)):
                # 텍스트/에러가 들어온 경우가 많음
                head = img_bytes[:40]
                raise RuntimeError(f"Gemini 이미지 바이트가 PNG/JPG가 아닙니다. head={head!r}")

            return img_bytes

        except Exception as e:
            last_err = e
            print(f"⚠️ Gemini 이미지 생성 실패 {attempt}/{retries}: {e}")
            if attempt < retries:
                time.sleep(sleep_sec * attempt)

    raise RuntimeError(f"Gemini 이미지 생성 최종 실패: {last_err}")
