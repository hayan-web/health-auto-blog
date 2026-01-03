# app/monetize_coupang.py
import os
import re
from typing import Tuple

from app.coupang_api import search_products

def _env(k: str, d: str = "") -> str:
    return (os.getenv(k) or d).strip()

def _disclosure_html() -> str:
    text = _env(
        "COUPANG_DISCLOSURE_TEXT",
        "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.",
    )
    return f"""
<div class="disclosure"
     style="margin:0 0 14px; padding:12px 14px; border-radius:10px;
            background:#fff3cd; border:1px solid #ffe69c;
            font-size:14px; line-height:1.6; color:#664d03;">
  <b>광고 안내</b><br/>
  {text}
</div>
""".strip()

def _coupang_cards_html(keyword: str, products: list[dict]) -> str:
    title = _env("COUPANG_BOX_TITLE", "지금 인기 상품")
    btn_text = _env("COUPANG_BOX_BUTTON", "쿠팡에서 가격/쿠폰 확인")
    note = "할인/쿠폰 적용 여부는 쿠팡 상세페이지에서 확인하실 수 있어요."

    # 카드 2열(모바일에서도 보기 좋게)
    cards = []
    for p in products:
        name = p.get("name", "")
        price = p.get("price", "")
        url = p.get("url", "")
        img = p.get("image", "")
        rocket = "🚀 로켓" if p.get("isRocket") else ""
        rating = p.get("rating", "")
        reviews = p.get("reviews", "")

        meta = []
        if price:
            meta.append(f"<span style='font-weight:800;'>₩{price}</span>")
        if rocket:
            meta.append(f"<span style='color:#0d6efd; font-weight:700;'>{rocket}</span>")
        if rating:
            rv = f"⭐ {rating}"
            if reviews:
                rv += f" ({reviews})"
            meta.append(f"<span style='color:#6c757d;'>{rv}</span>")

        meta_html = " · ".join(meta)

        cards.append(f"""
<div style="display:flex; gap:12px; border:1px solid #e9ecef; border-radius:14px; padding:12px; background:#fff;">
  <a href="{url}" target="_blank" rel="nofollow sponsored noopener"
     style="display:block; width:92px; flex:0 0 92px;">
    <img src="{img}" alt="{name}"
         style="width:92px; height:92px; object-fit:cover; border-radius:12px; background:#f1f3f5;" />
  </a>
  <div style="flex:1; min-width:0;">
    <div style="font-size:14px; font-weight:900; color:#212529; line-height:1.35; margin-bottom:6px;
                display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;">
      {name}
    </div>
    <div style="font-size:13px; color:#343a40; margin-bottom:10px;">
      {meta_html}
    </div>
    <a href="{url}" target="_blank" rel="nofollow sponsored noopener"
       style="display:inline-block; text-decoration:none; font-weight:900;
              background:#198754; color:#fff; padding:10px 12px; border-radius:10px;">
      {btn_text}
    </a>
  </div>
</div>
""".strip())

    cards_html = "\n".join(cards)

    return f"""
<div class="coupang-box"
     style="margin:18px 0; padding:16px; border-radius:16px;
            border:1px solid #e9ecef; background:#f8f9fa;">
  <div style="display:flex; justify-content:space-between; align-items:flex-end; gap:12px; margin-bottom:12px;">
    <div>
      <div style="font-size:17px; font-weight:1000; color:#212529; margin-bottom:4px;">{title}</div>
      <div style="font-size:13px; color:#495057;">‘{keyword}’ 관련 상품을 모아봤어요.</div>
    </div>
  </div>

  <div style="display:grid; grid-template-columns:1fr; gap:12px;">
    {cards_html}
  </div>

  <div style="margin-top:10px; font-size:12px; color:#6c757d; line-height:1.5;">
    {note}
  </div>
</div>
""".strip()

def inject_coupang(html: str, keyword: str) -> Tuple[str, bool]:
    """
    ✅ 반환: (html, inserted_bool)

    동작:
    - 쿠팡 API로 키워드 검색 → 상품 N개 가져옴
    - 가져온 경우에만:
      1) 최상단 wrap 바로 아래 disclosure 삽입
      2) 본문 중간(요약 다음/첫 섹션 전 등)에 쿠팡 카드 박스 삽입
    """
    if not html:
        return html, False

    # 이미 들어가 있으면 추가 삽입 안 함
    if "class=\"coupang-box\"" in html:
        return html, True

    limit = int(_env("COUPANG_PRODUCT_LIMIT", "6") or "6")

    try:
        products = search_products(keyword, limit=limit)
    except Exception as e:
        print(f"⚠️ coupang search failed: {e}")
        return html, False

    if not products:
        return html, False

    disclosure = _disclosure_html()
    box = _coupang_cards_html(keyword, products)

    out = html
    inserted_any = False

    # 1) 최상단 disclosure
    if "<div class=\"wrap\">" in out:
        if "class=\"disclosure\"" not in out:
            out = out.replace("<div class=\"wrap\">", f"<div class=\"wrap\">\n{disclosure}\n", 1)
            inserted_any = True
    else:
        if "class=\"disclosure\"" not in out:
            out = disclosure + "\n" + out
            inserted_any = True

    # 2) 본문 삽입 위치(우선순위)
    inserted = False

    # summary 끝마커가 있으면 그 직후(프로젝트에 맞춰 유연하게)
    candidates = [
        r"(<!--\s*SUMMARY\s*END\s*-->)",
        r"(</div>\s*<!--\s*SUMMARY\s*END\s*-->)",
    ]
    for pat in candidates:
        m = re.search(pat, out, flags=re.IGNORECASE | re.DOTALL)
        if m:
            idx = m.end()
            out = out[:idx] + "\n" + box + "\n" + out[idx:]
            inserted = True
            inserted_any = True
            break

    if not inserted:
        # 첫 section-card 앞
        m = re.search(
            r"<div[^>]+class=[\"'][^\"']*(section-card|content-card|card)[^\"']*[\"'][^>]*>",
            out,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if m:
            idx = m.start()
            out = out[:idx] + box + "\n" + out[idx:]
            inserted = True
            inserted_any = True

    if not inserted:
        # 첫 h2 앞
        m = re.search(r"<h2[^>]*>", out, flags=re.IGNORECASE | re.DOTALL)
        if m:
            idx = m.start()
            out = out[:idx] + box + "\n" + out[idx:]
            inserted = True
            inserted_any = True

    if not inserted:
        # 마지막에 붙이기
        out = out + "\n" + box
        inserted_any = True

    return out, inserted_any
