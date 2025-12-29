import os
import re
import json
import uuid
import textwrap
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont
import requests
from openai import OpenAI
from google import genai
from google.genai import types

from app.config import Settings
from app.wp_client import upload_media_to_wp, publish_to_wp
from app.ai_openai import make_openai_client, generate_blog_post, generate_thumbnail_title
from app.ai_gemini_image import make_gemini_client, generate_nanobanana_image_png_bytes
from app.thumb_overlay import to_square_1024, add_title_to_image

S = Settings()

OPENAI_API_KEY = S.OPENAI_API_KEY
GOOGLE_API_KEY = S.GOOGLE_API_KEY

WP_URL = S.WP_URL
WP_USER = S.WP_USERNAME
WP_PW = S.WP_APP_PASSWORD

OPENAI_MODEL = S.OPENAI_MODEL
GEMINI_IMAGE_MODEL = S.GEMINI_IMAGE_MODEL

if not OPENAI_API_KEY:
    print("❌ OPENAI_API_KEY 누락")
    raise SystemExit(1)

if not GOOGLE_API_KEY:
    print("❌ GOOGLE_API_KEY 누락")
    raise SystemExit(1)

if not (WP_URL and WP_USER and WP_PW):
    print("❌ WP_URL / WP_USERNAME / WP_APP_PASSWORD 중 누락")
    raise SystemExit(1)

openai_client = make_openai_client(OPENAI_API_KEY)
gemini_client = make_gemini_client(GOOGLE_API_KEY)

OPENAI_MODEL = "gpt-5-mini"
GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"


# =========================
# 1) Helpers
# =========================

def _safe_slug_filename(name: str, fallback: str) -> str:
    s = (name or "").strip()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-zA-Z0-9가-힣\-_]", "", s)
    s = s[:60].strip("-") or fallback
    return s

from PIL import Image
from io import BytesIO

# =========================
# 5) WordPress: Media Upload (RAW binary) + Post Publish
# =========================
def make_ascii_filename(prefix: str, ext: str = "png") -> str:
    """
    헤더에 넣어도 안전한 ASCII 파일명 생성 (한글/특수문자 절대 없음)
    """
    uid = uuid.uuid4().hex[:10]
    prefix = re.sub(r"[^a-zA-Z0-9_-]+", "-", (prefix or "img")).strip("-")
    if not prefix:
        prefix = "img"
    return f"{prefix}-{uid}.{ext}"


def force_ascii(s: str) -> str:
    """
    혹시라도 남아있는 비ASCII 제거
    """
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", (s or "file")).strip("-") or "file"

    top_html = f"""
<div style="margin-bottom:28px;">
  <img src="{hero_url}" alt="{data["title"]}" style="width:100%; border-radius:14px; box-shadow:0 4px 14px rgba(0,0,0,0.14);" />
</div>
"""

    mid_img_html = f"""
<div style="margin:28px 0;">
  <img src="{body_url}" alt="{data["title"]} 관련 이미지" style="width:100%; border-radius:14px; box-shadow:0 4px 14px rgba(0,0,0,0.12);" />
</div>
"""

    body_parts = []
    for i, p in enumerate(raw_paras):
        if i == 0:
            # 첫 문단 전에 이미 top 이미지가 있으니 그대로 문단부터
            pass
        if i == mid_idx:
            body_parts.append(mid_img_html)
        body_parts.append(ptag(p))

    final_html = f"""
{top_html}
<div style="line-height:1.9; font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;">
  {''.join(body_parts)}
</div>
"""

    api_endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    payload = {
        "title": data["title"],
        "content": final_html,
        "status": "publish",
        "featured_media": featured_media_id,
    }

    print("📝 POST ->", api_endpoint)
    print("📝 title ->", payload["title"][:80])

    res = requests.post(api_endpoint, auth=(WP_USER, WP_PW), json=payload, timeout=60)
    print("📝 WP status:", res.status_code)
    print("📝 WP resp:", (res.text or "")[:500])

    if res.status_code != 201:
        raise RuntimeError(f"워드프레스 글 발행 실패: {res.status_code} / {res.text}")

    return res.json()["id"]


# =========================
# 6) MAIN
# =========================
if __name__ == "__main__":
    try:
        # 1) 글 생성 (OpenAI)
        post = generate_blog_post(openai_client, OPENAI_MODEL)

        # 2) 썸네일용 짧은 타이틀 (OpenAI)
        thumb_title = generate_thumbnail_title(openai_client, OPENAI_MODEL, post["title"])
        print("🏷️ 썸네일 타이틀:", thumb_title)

        # 3) 이미지 2장 생성 (Gemini NanoBanana)
        print("🎨 Gemini 이미지(상단/대표) 생성 중...")
        hero_img = generate_nanobanana_image_png_bytes(gemini_client, GEMINI_IMAGE_MODEL, post["img_prompt"])
        body_img = generate_nanobanana_image_png_bytes(
        gemini_client, GEMINI_IMAGE_MODEL,
        post["img_prompt"] + ", different composition, different angle, no text"
        )

        # ✅ 이미지 생성 직후 무조건 1:1 정사각 고정
        hero_img = to_square_1024(hero_img)
        body_img = to_square_1024(body_img)


        # 4) 대표 이미지에 타이틀 오버레이
        hero_img_titled = add_title_to_image(hero_img, thumb_title)

        # ✅ 오버레이 후에도 혹시 비율 깨질 수 있으니 다시 1:1 고정
        hero_img_titled = to_square_1024(hero_img_titled)


        # 5) WP 미디어 업로드(대표/중간)
        hero_name = make_ascii_filename("featured")
        body_name = make_ascii_filename("body")
        
        hero_url, hero_media_id = upload_media_to_wp(WP_URL, WP_USER, WP_PW, hero_img_titled, hero_name)
        body_url, _ = upload_media_to_wp(WP_URL, WP_USER, WP_PW, body_img, body_name)
        
        post_id = publish_to_wp(
            WP_URL, WP_USER, WP_PW,
            post, hero_url, body_url,
            featured_media_id=hero_media_id
        )

        print(f"✅ 완료! post_id={post_id}")

    except Exception as e:
        print(f"❌ 시스템 중단: {e}")
        raise
