# app/monetize_adsense.py
from __future__ import annotations
import os


def adsense_inarticle_block() -> str:
    """
    인아티클 광고(본문 삽입형) - slot/client는 ENV에서 받습니다.
    (주의) 승인/정책/테마 설정에 따라 노출이 안 될 수 있습니다.
    """
    client = (os.getenv("ADSENSE_CLIENT", "") or "").strip()
    slot = (os.getenv("ADSENSE_SLOT_INARTICLE", "") or "").strip()

    if not client or not slot:
        return ""  # 설정 없으면 삽입하지 않음

    return f"""
    <div style="margin:18px 0;">
      <ins class="adsbygoogle"
           style="display:block; text-align:center;"
           data-ad-client="{client}"
           data-ad-slot="{slot}"
           data-ad-format="auto"
           data-full-width-responsive="true"></ins>
      <script>(adsbygoogle = window.adsbygoogle || []).push({{}});</script>
    </div>
    """.strip()


def inject_ads(html: str) -> str:
    """
    간단 규칙:
    - 첫 구분선 이후 1회
    - 중간쯤 1회
    """
    block = adsense_inarticle_block()
    if not block:
        return html

    parts = html.split("<hr")
    if len(parts) < 3:
        return html + "\n" + block

    # 첫 번째 hr 앞뒤로 복원
    rebuilt = []
    rebuilt.append(parts[0])
    rebuilt.append("<hr" + parts[1])  # 첫 hr 포함
    rebuilt.append(block)             # 첫 광고
    # 나머지 붙이기
    rest = ["<hr" + p for p in parts[2:]]
    html2 = "\n".join(rebuilt + rest)

    # 중간 광고 추가(대략)
    mid = len(html2) // 2
    return html2[:mid] + "\n" + block + "\n" + html2[mid:]
