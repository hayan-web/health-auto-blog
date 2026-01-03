# app/monetize_coupang.py
import os
import re
import time
import hashlib
from typing import Tuple, Dict, Any, List

from app.coupang_api import search_products

def _env(k: str, d: str = "") -> str:
    return (os.getenv(k) or d).strip()

# -------------------------
# 1) 키워드 매핑(정확도 업)
# -------------------------
_KEYWORD_MAP = [
    # (패턴, 대체/보강 키워드)
    (r"(식단|다이어트|혈당|혈압|콜레스테롤|고지혈증|당뇨)", "식단 관리 영양제 건강식품"),
    (r"(수면|불면|잠)", "수면 보조 멜라토닌 마그네슘"),
    (r"(관절|무릎|허리|근육)", "관절 건강 MSM 오메가3"),
    (r"(욕실|위생|세정|청소)", "욕실 청소용품 세정제"),
    (r"(정리|수납|정돈)", "수납 정리함 정리용품"),
    (r"(주방|설거지|세척)", "주방 세척용품 수세미 세제"),
]

def _map_keyword(keyword: str) -> str:
    kw = (keyword or "").strip()
    if not kw:
        return kw
    for pat, addon in _KEYWORD_MAP:
        if re.search(pat, kw, flags=re.IGNORECASE):
            return f"{kw} {addon}".strip()
    return kw

# -------------------------
# 2) 대가성 문구(최상단)
# -------------------------
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

# -------------------------
# 3) 카드 박스(클릭 유도)
# -------------------------
def _box_html(keyword: str, products: List[dict], box_id: str) -> str:
    title = _env("COUPANG_BOX_TITLE", "지금 인기 상품")
    btn_text = _env("COUPANG_BOX_BUTTON", "쿠팡에서 가격/쿠폰 확인")
    note = "할인/쿠폰 적용 여부는 쿠팡 상세페이지에서 확인하실 수 있어요."

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
            meta.append(f"<span style='font-weight:900;'>₩{price}</span>")
        if rocket:
            meta.append(f"<span style='color:#0d6efd; font-weight:800;'>{rocket}</span>")
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
    <div style="font-size:14px; font-weight:950; color:#212529; line-height:1.35; margin-bottom:6px;
                display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;">
      {name}
    </div>
    <div style="font-size:13px; color:#343a40; margin-bottom:10px;">
      {meta_html}
    </div>
    <a href="{url}" target="_blank" rel="nofollow sponsored noopener"
       style="display:inline-block; text-decoration:none; font-weight:950;
              background:#198754; color:#fff; padding:10px 12px; border-radius:10px;">
      {btn_text}
    </a>
  </div>
</div>
""".strip())

    cards_html = "\n".join(cards)

    return f"""
<div class="coupang-box" data-box="{box_id}"
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

# -------------------------
# 4) 7일 중복 방지 캐시(state.json)
# -------------------------
def _now_ts() -> int:
    return int(time.time())

def _get_cache(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    cache = state.get("coupang_recent_products")
    if isinstance(cache, list):
        return cache
    return []

def _prune_cache(cache: List[Dict[str, Any]], dedupe_days: int) -> List[Dict[str, Any]]:
    keep_sec = max(1, dedupe_days) * 86400
    cut = _now_ts() - keep_sec
    out = []
    for item in cache:
        try:
            ts = int(item.get("ts", 0))
            pid = str(item.get("id", "")).strip()
            if pid and ts >= cut:
                out.append({"id": pid, "ts": ts})
        except Exception:
            continue
    return out

def _filter_by_cache(products: List[dict], cache: List[Dict[str, Any]]) -> List[dict]:
    seen = set(str(x.get("id")) for x in cache if x.get("id"))
    out = []
    for p in products:
        pid = str(p.get("id", "")).strip()
        if pid and pid in seen:
            continue
        out.append(p)
    return out

def _update_cache(state: Dict[str, Any], used_products: List[dict], dedupe_days: int) -> Dict[str, Any]:
    cache = _get_cache(state)
    cache = _prune_cache(cache, dedupe_days)

    for p in used_products:
        pid = str(p.get("id", "")).strip()
        if not pid:
            # id 없는 경우는 url 해시로 대체
            url = (p.get("url") or "").strip()
            if url:
                pid = "u_" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:14]
        if pid:
            cache.append({"id": pid, "ts": _now_ts()})

    state["coupang_recent_products"] = cache
    return state

# -------------------------
# 5) 삽입 유틸(3곳)
# -------------------------
def _insert_after_summary(html: str, box: str) -> Tuple[str, bool]:
    candidates = [
        r"(<!--\s*SUMMARY\s*END\s*-->)",
        r"(</div>\s*<!--\s*SUMMARY\s*END\s*-->)",
    ]
    for pat in candidates:
        m = re.search(pat, html, flags=re.IGNORECASE | re.DOTALL)
        if m:
            idx = m.end()
            return html[:idx] + "\n" + box + "\n" + html[idx:], True

    # summary 마커가 없다면 첫 섹션/첫 h2 앞
    m = re.search(r"<h2[^>]*>", html, flags=re.IGNORECASE | re.DOTALL)
    if m:
        idx = m.start()
        return html[:idx] + box + "\n" + html[idx:], True

    return html, False

def _insert_mid(html: str, box: str) -> Tuple[str, bool]:
    # 두 번째 섹션 카드 앞(대략 중단)
    # section-card/content-card/card를 2번째로 찾기
    matches = list(re.finditer(
        r"<div[^>]+class=[\"'][^\"']*(section-card|content-card|card)[^\"']*[\"'][^>]*>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ))
    if len(matches) >= 2:
        idx = matches[1].start()
        return html[:idx] + box + "\n" + html[idx:], True

    # fallback: 첫 h2 두 번째 앞
    h2s = list(re.finditer(r"<h2[^>]*>", html, flags=re.IGNORECASE | re.DOTALL))
    if len(h2s) >= 2:
        idx = h2s[1].start()
        return html[:idx] + box + "\n" + html[idx:], True

    return html, False

def _insert_bottom(html: str, box: str) -> Tuple[str, bool]:
    # 댓글/코멘트 섹션 앞(가능하면)
    candidates = [
        r"(<h2[^>]*>\s*댓글[^<]*</h2>)",
        r"(<div[^>]+id=[\"']comments[\"'][^>]*>)",
        r"(<div[^>]+class=[\"'][^\"']*comments[^\"']*[\"'][^>]*>)",
    ]
    for pat in candidates:
        m = re.search(pat, html, flags=re.IGNORECASE | re.DOTALL)
        if m:
            idx = m.start()
            return html[:idx] + box + "\n" + html[idx:], True

    # wrap 끝나기 전
    m = re.search(r"</div>\s*$", html, flags=re.IGNORECASE | re.DOTALL)
    if m:
        idx = m.start()
        return html[:idx] + box + "\n" + html[idx:], True

    return html + "\n" + box, True

# -------------------------
# 6) 메인 함수: (html, inserted, state)
# -------------------------
def inject_coupang(html: str, keyword: str, state: Dict[str, Any]) -> Tuple[str, bool, Dict[str, Any]]:
    """
    ✅ 반환: (html, inserted_bool, state)

    동작:
    - 키워드 매핑 → 쿠팡 검색
    - 7일 중복 제거 후 상품 선택
    - 3곳(상/중/하) 분산 삽입(상품도 분할)
    - 실제 삽입된 경우에만 disclosure 최상단 삽입 + state 캐시 업데이트
    """
    if not html:
        return html, False, state

    # 이미 들어가 있으면 스킵
    if "class=\"coupang-box\"" in html:
        return html, True, state

    limit = int(_env("COUPANG_PRODUCT_LIMIT", "8") or "8")
    dedupe_days = int(_env("COUPANG_DEDUPE_DAYS", "7") or "7")

    mapped_kw = _map_keyword(keyword)

    try:
        products = search_products(mapped_kw, limit=limit)
    except Exception as e:
        print(f"⚠️ coupang search failed: {e}")
        return html, False, state

    if not products:
        return html, False, state

    # 중복 방지
    cache = _prune_cache(_get_cache(state), dedupe_days)
    fresh = _filter_by_cache(products, cache)

    # 신선한게 너무 적으면(예: 계속 같은 키워드) 원본도 일부 허용
    if len(fresh) < 4:
        fresh = products

    # 3곳 분산: 3/3/2 (총 8 기준)
    top_items = fresh[:3]
    mid_items = fresh[3:6] if len(fresh) > 3 else []
    bot_items = fresh[6:8] if len(fresh) > 6 else []

    # 최소 1개는 있어야
    used = top_items + mid_items + bot_items
    used = [p for p in used if p.get("url")]
    if not used:
        return html, False, state

    out = html
    inserted_any = False

    # (1) 최상단 disclosure (실제 삽입될 때만)
    disclosure = _disclosure_html()
    if "<div class=\"wrap\">" in out:
        if "class=\"disclosure\"" not in out:
            out = out.replace("<div class=\"wrap\">", f"<div class=\"wrap\">\n{disclosure}\n", 1)
    else:
        if "class=\"disclosure\"" not in out:
            out = disclosure + "\n" + out

    # (2) 상단 삽입
    if top_items:
        box_top = _box_html(keyword, top_items, box_id="top")
        out, ok = _insert_after_summary(out, box_top)
        inserted_any = inserted_any or ok

    # (3) 중단 삽입
    if mid_items:
        box_mid = _box_html(keyword, mid_items, box_id="mid")
        out, ok = _insert_mid(out, box_mid)
        inserted_any = inserted_any or ok

    # (4) 하단 삽입
    if bot_items:
        box_bot = _box_html(keyword, bot_items, box_id="bottom")
        out, ok = _insert_bottom(out, box_bot)
        inserted_any = inserted_any or ok

    if inserted_any:
        state = _update_cache(state, used, dedupe_days)

    return out, inserted_any, state
