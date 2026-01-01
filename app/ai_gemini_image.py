import base64
import time
from typing import Any, Optional


def _extract_inline_image_b64(resp: Any) -> Optional[str]:
    """
    Gemini 응답에서 inline_data.data (base64) 문자열을 최대한 다양한 구조로 탐색해 추출
    """
    # 1) dict 형태(JSON)
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

    # 2) 객체 형태(SDK response)
    # resp.candidates[*].content.parts[*].inline_data.data
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


def generate_nanobanana_image_png_bytes(
    gemini_client: Any,
    model: str,
    prompt: str,
    *,
    retries: int = 3,
    sleep_sec: float = 1.2,
) -> bytes:
    """
    Gemini 이미지 생성 -> PNG bytes 반환
    - 응답 구조가 달라도 inline_data(data)를 최대한 찾아서 디코딩
    - 실패 시 재시도
    """
    last_err = None

    for attempt in range(1, retries + 1):
        try:
            # ✅ SDK/버전에 따라 호출 방식이 다를 수 있어, 기존 코드의 호출을 최대한 유지합니다.
            # 프로젝트에서 쓰던 방식이 generate_content 라면 아래 그대로 동작합니다.
            resp = gemini_client.models.generate_content(
                model=model,
                contents=prompt,
            )

            b64 = _extract_inline_image_b64(resp)
            if not b64:
                # 디버그용: 응답 요약 찍기(너무 길면 잘림)
                text = str(resp)
                print("🧩 Gemini raw resp (head):", text[:800])
                raise RuntimeError("Gemini 응답에서 이미지 데이터(inline_data.data)를 찾지 못했습니다.")

            # base64 -> bytes
            img_bytes = base64.b64decode(b64)
            if not img_bytes or len(img_bytes) < 1000:
                raise RuntimeError("Gemini 이미지 바이트가 비정상적으로 작습니다.")

            return img_bytes

        except Exception as e:
            last_err = e
            print(f"⚠️ Gemini 이미지 생성 실패 {attempt}/{retries}: {e}")
            if attempt < retries:
                time.sleep(sleep_sec * attempt)

    raise RuntimeError(f"Gemini 이미지 생성 최종 실패: {last_err}")
