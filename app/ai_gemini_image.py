# app/ai_gemini_image.py
from __future__ import annotations

import base64
import time
from typing import Any, Optional

from openai import OpenAI


def make_gemini_client(api_key: str) -> Any:
    """
    ⚠️ 이름은 유지하지만, 실제로는 OpenAI client를 반환합니다 (OpenAI-only 모드).
    """
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY가 비어 있습니다.")
    return OpenAI(api_key=api_key)


def _extract_image_b64_from_responses(resp: Any) -> Optional[str]:
    """
    Responses API 결과에서 image_generation_call의 base64 결과를 찾아 반환합니다.
    """
    # SDK 객체 형태
    output = getattr(resp, "output", None)
    if isinstance(output, list):
        for o in output:
            if getattr(o, "type", None) == "image_generation_call":
                result = getattr(o, "result", None)
                if isinstance(result, str) and result.strip():
                    return result.strip()

    # dict 형태(혹시를 대비)
    if isinstance(resp, dict):
        out = resp.get("output")
        if isinstance(out, list):
            for o in out:
                if o.get("type") == "image_generation_call":
                    result = o.get("result")
                    if isinstance(result, str) and result.strip():
                        return result.strip()

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
    size: str = "1024x1024",
    quality: str = "medium",
    output_format: str = "png",
) -> bytes:
    """
    OpenAI Responses API + image_generation tool로 이미지 생성 후 bytes 반환.
    - model 파라미터는 main.py 호환용으로 그대로 받되,
      실제 생성은 `responses.create(model=...)`에서 수행합니다.
    - 결과는 base64 -> bytes 디코드 후 PNG/JPG 매직바이트로 검증합니다.

    참고: OpenAI 문서의 image_generation tool 방식 사용. :contentReference[oaicite:0]{index=0}
    """
    last_err: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            client: OpenAI = gemini_client  # 이름만 gemini_client일 뿐, OpenAI client입니다.

            resp = client.responses.create(
                model=model or "gpt-5",
                input=prompt,
                tools=[
                    {
                        "type": "image_generation",
                        "size": size,
                        "quality": quality,
                        "format": output_format,
                    }
                ],
                tool_choice={"type": "image_generation"},
            )

            b64 = _extract_image_b64_from_responses(resp)
            if not b64:
                raise RuntimeError("OpenAI 응답에서 이미지 base64(result)를 찾지 못했습니다.")

            img_bytes = base64.b64decode(b64)

            if not img_bytes or len(img_bytes) < 200:
                raise RuntimeError(f"이미지 바이트가 너무 작습니다(len={len(img_bytes) if img_bytes else 0}).")

            if not (_is_png(img_bytes) or _is_jpg(img_bytes)):
                head = img_bytes[:40]
                raise RuntimeError(f"이미지 바이트가 PNG/JPG가 아닙니다. head={head!r}")

            return img_bytes

        except Exception as e:
            last_err = e
            print(f"⚠️ OpenAI 이미지 생성 실패 {attempt}/{retries}: {e}")
            if attempt < retries:
                time.sleep(sleep_sec * attempt)

    raise RuntimeError(f"OpenAI 이미지 생성 최종 실패: {last_err}")
