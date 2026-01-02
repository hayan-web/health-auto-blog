import base64
from typing import Optional


def _extract_b64_image(resp) -> str:
    """
    OpenAI images.generate 응답에서 b64를 최대한 안전하게 꺼냅니다.
    (openai python 버전 차이를 흡수)
    """
    # 최신: resp.data[0].b64_json
    if hasattr(resp, "data") and resp.data:
        item = resp.data[0]
        if hasattr(item, "b64_json") and item.b64_json:
            return item.b64_json
        if hasattr(item, "b64") and item.b64:
            return item.b64

    # dict 형태
    if isinstance(resp, dict):
        data = resp.get("data") or []
        if data:
            item = data[0] or {}
            b64 = item.get("b64_json") or item.get("b64")
            if b64:
                return b64

    raise RuntimeError("OpenAI image response에서 base64 이미지를 찾지 못했습니다.")


def generate_openai_image_png_bytes(
    openai_client,
    model: str,
    prompt: str,
    size: str = "1024x1024",
    quality: Optional[str] = None,
) -> bytes:
    """
    OpenAI로 1:1 PNG 생성 후 bytes 반환
    """
    kwargs = {
        "model": model,
        "prompt": prompt,
        "size": size,
    }
    # 일부 환경에서 quality 지원
    if quality:
        kwargs["quality"] = quality

    resp = openai_client.images.generate(**kwargs)
    b64 = _extract_b64_image(resp)
    return base64.b64decode(b64)
