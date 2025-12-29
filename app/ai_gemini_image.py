from google import genai
from google.genai import types


def make_gemini_client(google_api_key: str) -> genai.Client:
    return genai.Client(api_key=google_api_key)


def generate_nanobanana_image_png_bytes(
    client: genai.Client,
    model: str,
    prompt: str,
) -> bytes:
    """
    NanoBanana(Gemini) 이미지 생성 → PNG bytes 반환
    - 콜라주/분할 방지
    - 1:1 정사각을 강하게 요구 (그래도 후처리에서 강제 고정 권장)
    """
    img_prompt = f"""
Create ONE single scene illustration for a blog thumbnail.
Hard constraints:
- square (1:1)
- SINGLE scene, SINGLE frame
- NO collage, NO triptych, NO split panels, NO multiple images
- NO grid, NO montage, NO storyboard
- centered subject, clean background
- no text, no watermark, no logo
Style: clean minimal, soft light, high clarity
Prompt: {prompt}
"""

    resp = client.models.generate_content(
        model=model,
        contents=[img_prompt],
        config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
    )

    # candidates 경로
    candidates = getattr(resp, "candidates", None)
    if candidates:
        for cand in candidates:
            content = getattr(cand, "content", None)
            if not content:
                continue
            parts = getattr(content, "parts", None) or []
            for part in parts:
                inline = getattr(part, "inline_data", None)
                if inline and getattr(inline, "data", None):
                    data = inline.data
                    if isinstance(data, (bytes, bytearray)):
                        return bytes(data)
                    if isinstance(data, str):
                        import base64
                        return base64.b64decode(data)

    # 혹시 resp.parts 형태
    parts = getattr(resp, "parts", None)
    if parts:
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                data = inline.data
                if isinstance(data, (bytes, bytearray)):
                    return bytes(data)
                if isinstance(data, str):
                    import base64
                    return base64.b64decode(data)

    raise RuntimeError("Gemini 응답에서 이미지 데이터를 찾지 못했습니다.")
