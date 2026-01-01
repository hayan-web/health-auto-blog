# app/monetize_adsense.py
from __future__ import annotations
import os


def _get(name: str) -> str:
    return (os.getenv(name, "") or "").strip()


def inject_adsense_slots(html: str) -> str:
    """
    formatter_v2가 넣어둔 마커 3곳을 수동 광고코드로 치환
      - <!--AD_SLOT_TOP-->    : 요약박스 위
      - <!--AD_SLOT_MID-->    : 첫 소제목카드 위
      - <!--AD_SLOT_BOTTOM--> : 맨 아래
    광고코드가 없으면 해당 슬롯은 제거(빈 div도 제거)
    """
    top = _get("ADSENSE_BLOCK_TOP")
    mid = _get("ADSENSE_BLOCK_MID")
    bottom = _get("ADSENSE_BLOCK_BOTTOM")

    def repl(marker: str, code: str) -> str:
        if code:
            return code
        return ""  # 없으면 슬롯 자체 제거

    html = html.replace("<!--AD_SLOT_TOP-->", repl("TOP", top))
    html = html.replace("<!--AD_SLOT_MID-->", repl("MID", mid))
    html = html.replace("<!--AD_SLOT_BOTTOM-->", repl("BOTTOM", bottom))

    # 남은 빈 ads div 정리(대충)
    html = html.replace("<div class='ads'></div>", "")
    html = html.replace('<div class="ads"></div>', "")

    return html
