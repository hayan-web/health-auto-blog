import os
import re
from typing import Optional


# formatter_v2.py 에서 넣어둔 마커를 치환합니다.
MARK_TOP = "<!--AD_TOP-->"
MARK_MID = "<!--AD_MID-->"
MARK_BOTTOM = "<!--AD_BOTTOM-->"


def _env(name: str) -> str:
    v = os.getenv(name, "")
    return v.strip() if isinstance(v, str) else ""


def _is_full_snippet(v: str) -> bool:
    low = (v or "").lower()
    return ("<ins" in low) or ("adsbygoogle" in low) or ("data-ad-slot" in low)


def _render_adsense(slot_value: str) -> str:
    """slot_value:
    - 숫자만 들어오면(예: 1234567890) => ins+script로 감쌉니다.
    - 이미 <ins ...> 형태면 그대로 사용합니다.
    """
    v = (slot_value or "").strip()
    if not v:
        return ""

    if _is_full_snippet(v):
        # 사용자가 전체 코드를 넣었으면 그대로 사용
        return v

    # 숫자만 들어온 경우: 표준 in-article 광고 코드로 감쌉니다.
    slot = re.sub(r"[^0-9]", "", v)
    if not slot:
        return ""

    client = _env("ADSENSE_CLIENT") or _env("GOOGLE_ADSENSE_CLIENT")
    if not client:
        # client가 없으면 숫자가 그대로 노출될 수 있으니 비워버립니다.
        print("⚠️ ADSENSE_CLIENT 누락: 광고 삽입 스킵(슬롯만 있으면 숫자가 노출될 수 있어 비웁니다).")
        return ""

    ins = (
        f'<ins class="adsbygoogle" style="display:block" '
        f'data-ad-client="{client}" data-ad-slot="{slot}" '
        f'data-ad-format="auto" data-full-width-responsive="true"></ins>'
        f'\n<script>(adsbygoogle = window.adsbygoogle || []).push({{}});</script>'
    )
    return ins


def _maybe_include_script() -> str:
    # 기본값은 "1"로 두어서(=포함) 헤더에 스크립트를 아직 못 넣은 상태에서도
    # 본문 삽입 광고가 동작하게 합니다.
    # 이미 테마/플러그인으로 <head>에 넣었다면 ADSENSE_INCLUDE_SCRIPT=0 으로 꺼주세요.
    include = _env("ADSENSE_INCLUDE_SCRIPT")
    if include == "":
        include = "1"

    if include not in ("0", "false", "False", "FALSE"):
        client = _env("ADSENSE_CLIENT") or _env("GOOGLE_ADSENSE_CLIENT")
        if not client:
            return ""
        return (
            f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={client}" '
            f'crossorigin="anonymous"></script>'
        )
    return ""


def inject_adsense_slots(html: str) -> str:
    """본문 HTML에 3개 슬롯(상/중/하)을 자연스럽게 삽입합니다.
    - format_post_v2가 넣어둔 <!--AD_TOP-->, <!--AD_MID-->, <!--AD_BOTTOM--> 마커를 사용합니다.
    - 슬롯 값은 env로 받습니다:
      - ADSENSE_SLOT_TOP / MID / BOTTOM
      - 또는 ADSENSE_SLOT_1 / 2 / 3 (호환)
    """
    if not html:
        return html

    top_v = _env("ADSENSE_SLOT_TOP") or _env("ADSENSE_SLOT_1")
    mid_v = _env("ADSENSE_SLOT_MID") or _env("ADSENSE_SLOT_2")
    bot_v = _env("ADSENSE_SLOT_BOTTOM") or _env("ADSENSE_SLOT_3")

    top = _render_adsense(top_v)
    mid = _render_adsense(mid_v)
    bot = _render_adsense(bot_v)

    # 광고 설정이 하나도 없으면 마커만 제거
    if not (top or mid or bot):
        return html.replace(MARK_TOP, "").replace(MARK_MID, "").replace(MARK_BOTTOM, "")

    script_tag = _maybe_include_script()

    # 마커 치환
    out = html
    out = out.replace(MARK_TOP, (script_tag + "\n" + top).strip() if top else script_tag)
    out = out.replace(MARK_MID, mid or "")
    out = out.replace(MARK_BOTTOM, bot or "")

    return out
