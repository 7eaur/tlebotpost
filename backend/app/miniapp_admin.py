"""Telegram Mini App routes for fast V2 operations."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.audit import AuditRecorder
from app.telegram.miniapp import MiniAppAuthError, verify_init_data


def register_miniapp_routes(app: FastAPI, runtime: Any) -> None:
    audit = AuditRecorder(runtime.database.session_factory, runtime.settings.account_id)

    @app.get("/mini", response_class=HTMLResponse)
    async def mini_page() -> HTMLResponse:
        return HTMLResponse(mini_html())

    @app.post("/api/v1/mini/status")
    async def mini_status(request: Request) -> JSONResponse:
        verify_request(request, runtime)
        return JSONResponse(await runtime.status_snapshot())

    @app.post("/api/v1/mini/action/{action}")
    async def mini_action(request: Request, action: str) -> JSONResponse:
        user = verify_request(request, runtime)
        if action == "run":
            await runtime.resume_ingestion()
        elif action == "pause":
            await runtime.pause_ingestion()
        elif action == "reload":
            await runtime.reload_ingestion()
        else:
            raise HTTPException(404)
        await audit.record(
            f"mini.{action}",
            "runtime",
            actor=f"telegram:{user.id}",
        )
        return JSONResponse(await runtime.status_snapshot())


def verify_request(request: Request, runtime: Any) -> Any:
    owner_id = runtime.settings.owner_id
    if owner_id is None:
        raise HTTPException(403, "Mini App owner is not configured")
    init_data = request.headers.get("x-telegram-init-data", "")
    try:
        return verify_init_data(
            init_data,
            bot_token=runtime.settings.bot_token,
            expected_user_id=owner_id,
        )
    except MiniAppAuthError as exc:
        raise HTTPException(401, str(exc)) from exc


def mini_html() -> str:
    return """<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<title>Relay V2 Mini</title>
<style>
body{font-family:system-ui;margin:0;padding:18px;
background:var(--tg-theme-bg-color,#111827);
color:var(--tg-theme-text-color,#fff)}
.card{border:1px solid #ffffff25;border-radius:16px;padding:16px;margin:12px 0}
.row{display:flex;gap:8px}
button{flex:1;padding:12px;border:0;border-radius:10px;font-weight:700}
pre{white-space:pre-wrap;font-size:.85rem}
</style>
</head>
<body>
<h2>Telegram Relay V2</h2>
<div class="card"><pre id="status">جارِ التحقق…</pre></div>
<div class="row">
<button onclick="act('run')">▶ تشغيل</button>
<button onclick="act('pause')">⏸ إيقاف</button>
<button onclick="act('reload')">↻ تحديث</button>
</div>
<script>
const tg=window.Telegram.WebApp; tg.ready(); tg.expand();
async function call(path){
  const r=await fetch(path,{method:'POST',
    headers:{'X-Telegram-Init-Data':tg.initData}});
  if(!r.ok) throw new Error(await r.text());
  return r.json();
}
function show(v){
  const text='PostgreSQL: '+(v.database?'✅':'❌')+'\\n'
    +'Telegram: '+(v.telegram_connected?'✅':'❌')+'\\n'
    +'Listener: '+(v.listener_running?'✅':'⏸')+'\\n'
    +'Worker: '+(v.worker_running?'✅':'❌')+'\\n'
    +'Sources: '+String(v.source_count||0);
  document.getElementById('status').textContent=text;
}
async function refresh(){
  try{show(await call('/api/v1/mini/status'));}
  catch(e){document.getElementById('status').textContent='غير مصرح: '+e.message;}
}
async function act(a){
  try{show(await call('/api/v1/mini/action/'+a));}
  catch(e){tg.showAlert(e.message);}
}
refresh();
</script>
</body></html>"""
