"""Shared HTML helpers for the V2 administration portal."""

from __future__ import annotations

import html
import re
from typing import Any


def page(title: str, body: str) -> str:
    nav = """
    <nav>
      <a href="/">Dashboard</a><a href="/projects">Projects</a>
      <a href="/profiles">Profiles</a><a href="/scheduled">Scheduled</a>
      <a href="/published">Published</a><a href="/telegram">Telegram</a>
      <a href="/audit">Audit</a><a href="/settings">Settings</a>
    </nav>
    """
    return f"""<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} — Telegram Relay V2</title>
<style>
:root{{--bg:#080d18;--panel:#111a2b;--border:#26344f;--text:#eef3ff;
--muted:#93a2bd;--accent:#3478f6}}*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);
font-family:system-ui,-apple-system,sans-serif}}
main{{width:min(1400px,94vw);margin:auto;padding:24px 0 50px}}
nav{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:22px}}
nav a{{color:#dce7ff;text-decoration:none;background:#141f34;
border:1px solid var(--border);padding:8px 12px;border-radius:9px}}
section,.panel{{background:var(--panel);border:1px solid var(--border);
border-radius:15px;padding:16px;margin:14px 0}}
.cards{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}
.profile-card{{margin:0}}
.form-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}}
input,select,textarea{{width:100%;background:#0a1221;color:white;
border:1px solid var(--border);border-radius:8px;padding:9px}}
button{{background:var(--accent);color:white;border:0;border-radius:8px;
padding:9px 12px;font-weight:650;cursor:pointer}}
button.ghost{{background:#2a354c}}
table{{width:100%;border-collapse:collapse;min-width:650px}}
th,td{{padding:10px;border-bottom:1px solid var(--border);text-align:right}}
th{{color:var(--muted)}}.actions{{display:flex;gap:6px;flex-wrap:wrap}}
.actions form{{margin:0}}code{{direction:ltr;display:inline-block}}
li{{margin:6px 0}}
.metric-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
.metric{{background:var(--panel);border:1px solid var(--border);
padding:14px;border-radius:12px}}
.metric b{{display:block;margin-top:8px}}
@media(max-width:800px){{
.cards,.form-grid,.metric-grid{{grid-template-columns:1fr}}
}}
</style></head><body><main>{nav}<h1>{escape(title)}</h1>{body}</main></body></html>"""


def table(headers: list[str], body: str) -> str:
    head = "".join(f"<th>{escape(value)}</th>" for value in headers)
    return (
        '<div class="panel"><table><thead><tr>'
        + head
        + "</tr></thead><tbody>"
        + body
        + "</tbody></table></div>"
    )


def post_button(action: str, label: str, *, ghost: bool = False) -> str:
    css_class = ' class="ghost"' if ghost else ""
    return (
        f'<form method="post" action="{escape(action)}">'
        f"<button{css_class}>{escape(label)}</button></form>"
    )


def csv_values(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def slugify(value: str) -> str:
    clean = re.sub(r"[^a-z0-9-]+", "-", value.strip().lower())
    return clean.strip("-")[:63]


def escape(value: Any) -> str:
    return html.escape(str(value), quote=True)
