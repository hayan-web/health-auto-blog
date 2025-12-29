# app/monetize_coupang.py
from __future__ import annotations
import os
import urllib.parse


def coupang_search_url(keyword: str) -> str:
    """
    가장 안정적인 방식: 쿠팡 검색결과로 유도(파트너 트래킹 붙이기)
    ※ 실제 파트너 링크 포맷은 계정/정책에 따라 다를 수 있어,
      일단 '키워드 기반 검색'으로 안전하게 시작합니다.
    """
    q = urllib.parse.quote((keyword or "").strip())
    base = f"https://www.coupang.com/np/search?component=&q={q}&channel=user"

    # 트래킹용 파라미터(옵션)
    pid = (os.getenv("COUPANG_PARTNER_ID", "") or "").strip()
    tc = (os.getenv("COUPANG_TRACKING_CODE", "") or "").strip()

    params = {}
    if pid:
        params["subId"] = pid
    if tc:
        params["traceId"] = tc

    if params:
        return base + "&" + urllib.parse.urlencode(params)
    return base


def coupang_box(keyword: str) -> str:
    url = coupang_search_url(keyword)
    if not url:
        return ""

    return f"""
    <div style="border:1px solid #e9e9e9;border-radius:14px;padding:14px 16px;background:#ffffff;margin:18px 0;">
      <div style="font-weight:700;margin-bottom:8px;">추천 쇼핑(쿠팡)</div>
      <div style="font-size:15px;line-height:1.65;">
        아래 키워드로 관련 제품을 한 번에 확인하실 수 있어요:
        <span style="font-weight:700;">{keyword}</span>
      </div>
      <div style="margin-top:10px;">
        <a href="{url}" target="_blank" rel="nofollow sponsored noopener"
           style="display:inline-block;padding:10px 14px;border-radius:10px;background:#111;color:#fff;text-decoration:none;">
          쿠팡에서 관련 제품 보기
        </a>
      </div>
      <div style="font-size:12px;color:#777;margin-top:10px;line-height:1.5;">
        이 포스팅은 제휴마케팅이 포함되어, 링크 클릭 후 구매 시 일정 수수료를 제공받을 수 있습니다.
      </div>
    </div>
    """.strip()


def inject_coupang(html: str, keyword: str) -> str:
    box = coupang_box(keyword)
    if not box:
        return html

    # 첫 번째 h2 앞에 1회 + 하단 1회
    idx = html.find("<h2")
    if idx != -1:
        html = html[:idx] + box + "\n" + html[idx:]
    return html + "\n" + box
