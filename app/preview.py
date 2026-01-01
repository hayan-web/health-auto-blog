# app/preview.py
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Optional


def _safe_slug(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", "-", t)
    t = re.sub(r"[^a-z0-9_-]+", "", t)
    return t[:60] or "post"


def save_html_preview(html: str, title: str, out_dir: str = "previews") -> str:
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    fname = f"{ts}_{_safe_slug(title)}.html"
    path = os.path.join(out_dir, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html or "")
    return path
