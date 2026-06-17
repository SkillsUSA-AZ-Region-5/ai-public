"""
stackctl web dashboard - a host FastAPI service that exposes the same controls as the
`stack` CLI (status, models, profiles, mem0) in a browser, reusing stackctl's core
functions directly. Runs on the HOST (not Docker) so it can reach lms, nvidia-smi,
the WSL/docker control path, and host metrics.

Bound to 127.0.0.1 by default - it can load/unload models and switch profiles, so do
NOT expose it to the LAN without putting auth in front of it (e.g. the Caddy pattern).

Launched windowless via `stack web start`; visit http://localhost:8090.
"""
import os
import sys

# pythonw.exe (windowless launch) has no stdout/stderr; redirect so uvicorn doesn't crash.
if sys.stdout is None or sys.stderr is None:
    _logf = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp.log"),
                 "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stdout or _logf
    sys.stderr = sys.stderr or _logf

import secrets
import threading
import time
import json
import re

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
import uvicorn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stackctl as sc  # noqa: E402  (also runs load_dotenv, so WEB_* env is available)

# --- auth + bind, from .env (loaded by importing stackctl) ---
# WEB_AUTH_PASS unset => no auth (localhost dev). Set it + WEB_BIND=0.0.0.0 for LAN.
WEB_USER = os.environ.get("WEB_AUTH_USER", "admin")
WEB_PASS = os.environ.get("WEB_AUTH_PASS", "")
WEB_BIND = os.environ.get("WEB_BIND", "127.0.0.1")
_security = HTTPBasic(auto_error=False)


def require_auth(request: Request, creds: HTTPBasicCredentials | None = Depends(_security)):
    if request.url.path in ("/api/health", "/api/usage/event"):
        return  # health is public; usage ingest uses its own X-Usage-Token (checked in-handler)
    if not WEB_PASS:
        return  # auth disabled when no password configured
    good = creds and secrets.compare_digest(creds.username, WEB_USER) \
        and secrets.compare_digest(creds.password, WEB_PASS)
    if not good:
        raise HTTPException(401, "unauthorized", headers={"WWW-Authenticate": "Basic"})


app = FastAPI(title="stackctl web", dependencies=[Depends(require_auth)])

# --- single-action runner: profile switches / model loads take time, so run them in a
# background thread and let the UI poll. Only one action at a time. ---
_lock = threading.Lock()
_action = {"running": False, "name": None, "result": None, "ts": None}


def _start_action(name: str, fn) -> bool:
    with _lock:
        if _action["running"]:
            return False
        _action.update(running=True, name=name, result=None, ts=time.time())

    def worker():
        try:
            fn()
            res = "ok"
        except Exception as e:  # noqa: BLE001
            res = f"error: {e}"
        with _lock:
            _action.update(running=False, result=res, ts=time.time())

    threading.Thread(target=worker, daemon=True).start()
    return True


# ---------------------------------------------------------------- local usage store
# LiteLLM's callback POSTs each completion's tokens here (in addition to InfluxDB), so
# the dashboard can show usage even without Influx. SQLite, so it survives restarts.
import sqlite3  # noqa: E402

USAGE_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "usage.db")
USAGE_INGEST_TOKEN = os.environ.get("USAGE_INGEST_TOKEN", "")
_usage_lock = threading.Lock()
_usage_db = sqlite3.connect(USAGE_DB, check_same_thread=False)
_usage_db.execute("""CREATE TABLE IF NOT EXISTS events(
    ts REAL, user TEXT, model TEXT, prompt INTEGER, completion INTEGER, latency REAL)""")
_usage_db.execute("CREATE INDEX IF NOT EXISTS idx_ts ON events(ts)")
# Migration: add the `app` column (which client made the call) to existing DBs.
if "app" not in {r[1] for r in _usage_db.execute("PRAGMA table_info(events)")}:
    _usage_db.execute("ALTER TABLE events ADD COLUMN app TEXT")
_usage_db.commit()


MODELISH_APPS = {
    "chat",
    "brain",
    "embed",
    "google/gemma-4-26b-a4b-qat",
    "google/gemma-4-12b-qat",
    "qwen/qwen3.6-35b-a3b",
    "text-embedding-nomic-embed-text-v1.5",
}


def normalize_usage_user(user):
    user = str(user or "anon")
    if user.lstrip().startswith("{"):
        m = re.search(r'"(?:device_id|id)"\s*:\s*"([^"]+)', user)
        if m:
            return f"device:{m.group(1)[:12]}"
        try:
            obj = json.loads(user)
            did = obj.get("device_id") or obj.get("id") or ""
            return f"device:{str(did)[:12]}" if did else "device"
        except Exception:
            return "device"
    return user


def normalize_usage_app(app):
    app = str(app or "other")
    return "openwebui" if app in MODELISH_APPS else app


def record_usage(user, model, prompt, completion, latency, app=None):
    user = normalize_usage_user(user)
    app = normalize_usage_app(app)
    with _usage_lock:
        _usage_db.execute(
            "INSERT INTO events(ts,user,model,app,prompt,completion,latency) VALUES (?,?,?,?,?,?,?)",
            (time.time(), user, model, app, int(prompt or 0), int(completion or 0), latency))
        # opportunistic 60-day retention
        _usage_db.execute("DELETE FROM events WHERE ts < ?", (time.time() - 60 * 86400,))
        _usage_db.commit()


def usage_summary(days: int = 7) -> dict:
    since = time.time() - days * 86400
    with _usage_lock:
        cur = _usage_db.execute(
            "SELECT COALESCE(SUM(prompt),0), COALESCE(SUM(completion),0), COUNT(*) FROM events WHERE ts>?", (since,))
        tp, tc, treq = cur.fetchone()
        by_user = [{"user": u, "prompt": p, "completion": c, "requests": n} for u, p, c, n in _usage_db.execute(
            "SELECT user, SUM(prompt), SUM(completion), COUNT(*) FROM events WHERE ts>? GROUP BY user ORDER BY SUM(prompt)+SUM(completion) DESC LIMIT 12", (since,))]
        by_app = [{"app": a or "other", "prompt": p, "completion": c, "requests": n} for a, p, c, n in _usage_db.execute(
            "SELECT COALESCE(app,'other'), SUM(prompt), SUM(completion), COUNT(*) FROM events WHERE ts>? GROUP BY COALESCE(app,'other') ORDER BY SUM(prompt)+SUM(completion) DESC LIMIT 12", (since,))]
        by_model = [{"model": m, "prompt": p, "completion": c, "requests": n} for m, p, c, n in _usage_db.execute(
            "SELECT model, SUM(prompt), SUM(completion), COUNT(*) FROM events WHERE ts>? GROUP BY model ORDER BY SUM(prompt)+SUM(completion) DESC LIMIT 12", (since,))]
    return {"window_days": days, "total_prompt": tp, "total_completion": tc,
            "total_tokens": tp + tc, "total_requests": treq,
            "by_user": by_user, "by_app": by_app, "by_model": by_model}


@app.post("/api/usage/event")
async def usage_event(req: Request):
    if USAGE_INGEST_TOKEN and req.headers.get("x-usage-token") != USAGE_INGEST_TOKEN:
        raise HTTPException(401, "bad usage token")
    e = await req.json()
    record_usage(e.get("user") or "anon", e.get("model") or "unknown",
                 e.get("prompt_tokens"), e.get("completion_tokens"), e.get("latency_s"),
                 app=e.get("app") or "other")
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"ok": True, "service": "stackctl-web"}


@app.get("/api/status")
def status():
    return {
        "system": sc.system_info(extended=True),
        "models": sc.lms_ps_json(),
        "containers": sc.container_rows(),   # name/status + CPU%/mem from docker stats
        "mem0": {"up": sc.mem0_health() is not None, "instances": sc.mem0_instances()},
        "health": sc.health_checks(),
        "usage": usage_summary(),
        "profiles": {k: v["desc"] for k, v in sc.PROFILES.items()},
        "action": dict(_action),
    }


@app.post("/api/doctor/fix")
def doctor_fix():
    if not _start_action("recover", sc.recover):
        raise HTTPException(409, "another action is running")
    return {"started": True}


class LoadReq(BaseModel):
    model: str
    ctx: int | None = None
    gpu: str = "max"


@app.post("/api/model/load")
def model_load(r: LoadReq):
    if not _start_action(f"load {r.model}", lambda: sc.lms_load(r.model, r.gpu, r.ctx)):
        raise HTTPException(409, "another action is running")
    return {"started": True}


@app.post("/api/model/unload")
def model_unload(model: str | None = None):
    name = f"unload {model}" if model else "unload all"
    fn = (lambda: sc.lms_unload(model)) if model else sc.lms_unload_all
    if not _start_action(name, fn):
        raise HTTPException(409, "another action is running")
    return {"started": True}


@app.post("/api/profile/{name}")
def profile(name: str):
    if name not in sc.PROFILES:
        raise HTTPException(404, f"unknown profile '{name}'")
    if not _start_action(f"profile {name}", lambda: sc.apply_profile(name)):
        raise HTTPException(409, "another action is running")
    return {"started": True}


@app.post("/api/mem0/{action}")
def mem0(action: str):
    fns = {"start": sc.mem0_start, "stop": sc.mem0_stop, "restart": lambda: (sc.mem0_stop(), sc.mem0_start())}
    if action not in fns:
        raise HTTPException(404, f"unknown mem0 action '{action}'")
    if not _start_action(f"mem0 {action}", fns[action]):
        raise HTTPException(409, "another action is running")
    return {"started": True}


@app.get("/", response_class=HTMLResponse)
def index():
    # no-store so an open tab picks up new UI on reload (the page only re-fetches
    # data on its own; the JS itself only updates when the page is reloaded).
    return HTMLResponse(_PAGE, headers={"Cache-Control": "no-store"})


_PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>stackctl</title><style>
:root{--bg:#161310;--card:#211c16;--line:#3a2f22;--txt:#f0e6da;--dim:#b59878;
--o1:#ffa733;--o2:#ff8c00;--o3:#c8651b;--ok:#5bd66b;--bad:#e6584d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);
font:14px/1.5 ui-monospace,Consolas,monospace}h1{color:var(--o1);margin:0;font-size:20px}
h2{color:var(--o2);font-size:13px;text-transform:uppercase;letter-spacing:.08em;
margin:0 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
header{display:flex;align-items:center;gap:14px;padding:16px 22px;border-bottom:1px solid var(--line)}
.host{color:var(--dim)}
.wrap{padding:18px 22px;display:grid;gap:16px;max-width:1800px;align-items:start;
grid-template-columns:minmax(0,1fr) 340px}
#main{display:grid;gap:16px;align-content:start;grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
#side{display:grid;gap:16px;align-content:start}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.kv{display:flex;justify-content:space-between;gap:10px;padding:2px 0}.kv span:first-child{color:var(--dim)}
.bar{height:8px;border-radius:5px;background:#0d0b09;overflow:hidden;margin:4px 0 10px}
.bar>i{display:block;height:100%;background:linear-gradient(90deg,var(--o3),var(--o1))}
.row{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:7px 0;border-top:1px solid var(--line)}
.row:first-of-type{border-top:0}.muted{color:var(--dim);font-size:12px}
button{background:#2c241a;color:var(--o1);border:1px solid var(--o3);border-radius:7px;
padding:5px 11px;cursor:pointer;font:inherit;font-size:12px}button:hover{background:var(--o3);color:#fff}
button:disabled{opacity:.4;cursor:not-allowed}.pill{font-size:11px;padding:1px 8px;border-radius:20px}
.up{color:var(--ok)}.down{color:var(--bad)}.tag{background:#0d0b09;color:var(--dim);padding:1px 7px;border-radius:5px;font-size:11px}
a{color:var(--o1);text-decoration:none;font-weight:600}a:hover{text-decoration:underline}
#banner{display:none;position:sticky;top:0;z-index:9;background:var(--o3);color:#fff;padding:8px 22px;font-size:13px}
.prof{display:flex;flex-direction:column;gap:6px}.prof .row{flex-direction:column;align-items:stretch}
.prof button{align-self:flex-start}
.row>div{min-width:0;overflow-wrap:anywhere}
@media(max-width:820px){
 header{padding:12px 14px;flex-wrap:wrap}
 .wrap{padding:12px 14px;grid-template-columns:1fr}
 #main{grid-template-columns:1fr}
 button{padding:8px 14px;font-size:13px}
 .card{padding:14px}
}
</style></head><body>
<header><h1>stackctl</h1><span class=host id=host>-</span><span class=muted id=clock></span></header>
<div id=banner></div><div class=wrap><div id=main>loading...</div><div id=side></div></div>
<script>
const $=h=>{const d=document.createElement('div');d.innerHTML=h;return d.firstElementChild};
const pct=(u,t)=>t?Math.round(u/t*100):0;
let busy=false;
async function act(url,body){if(busy)return;busy=true;
 try{const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):null});
 if(!r.ok){alert('failed: '+(await r.text()))}}catch(e){alert(e)}finally{busy=false;load()}}
function bar(label,u,t,unit,extra){const p=pct(u,t);
 return `<div class=kv><span>${label}</span><span>${u} / ${t} ${unit}${extra||''} <b>${p}%</b></span></div><div class=bar><i style="width:${p}%"></i></div>`}
function pctbar(label,p,extra){p=Math.max(0,Math.min(100,Number(p)||0));
 return `<div class=kv><span>${label}</span><span><b>${p}%</b>${extra||''}</span></div><div class=bar><i style="width:${p}%"></i></div>`}
let polling=false;
async function load(){if(polling)return;polling=true;let d;try{d=await(await fetch('/api/status')).json()}catch(e){polling=false;return}
 document.getElementById('host').textContent=d.system.host;
 document.getElementById('clock').textContent='up '+d.system.uptime;
 const b=document.getElementById('banner');
 if(d.action.running){b.style.display='block';b.textContent='\\u26a1 running: '+d.action.name+' ...'}
 else{b.style.display='none'}
 const sys=d.system;
 let main='', side='';
 // system card
 const cpuName=(sys.sockets>1?sys.sockets+'\\u00d7 ':'')+sys.cpu;
 main+=`<div class=card><h2>host</h2>
  <div class=kv><span>host</span><span>${sys.host}</span></div>
  <div class=kv><span>lan ip</span><span>${sys.lan_ip||'-'}</span></div>
  <div class=kv><span>wsl ip</span><span>${sys.wsl_ip||'-'}</span></div>
  <div class=kv><span>os</span><span>${sys.os}</span></div>
  <div class=kv><span>cpu</span><span>${cpuName}</span></div>
  <div class=kv><span></span><span>${sys.sockets>1?sys.sockets+' sockets \\u00b7 ':''}${sys.cores_phys} cores / ${sys.threads} threads</span></div>
  ${pctbar('cpu load',sys.cpu_pct)}
  ${bar('ram',sys.ram_used_gb,sys.ram_total_gb,'GB')}
  ${sys.gpus.map(g=>bar('gpu'+g.gpu+' vram',Math.round(g.used_mb/1024*10)/10,Math.round(g.total_mb/1024*10)/10,'GB','  '+g.util_pct+'% util')).join('')}
  </div>`;
 // health card (doctor) with one-click recover
 const dn=d.health.filter(h=>!h.ok).length;
 main+=`<div class=card><h2>health ${dn?`<span class=down>${dn} down</span>`:`<span class=up>all ok</span>`} <button onclick="act('/api/doctor/fix')" ${d.action.running?'disabled':''}>recover</button></h2>`+
  d.health.map(h=>`<div class=row><span>${h.name}</span><span class="pill ${h.ok?'up':'down'}">${h.ok?'ok':'down'}</span></div>`).join('')+`</div>`;
 // services card (links). LAN-exposed services use the host you're viewing from;
 // internal-only ones link to localhost and are tagged (not reachable off-box).
 const H=location.hostname, cUp=n=>d.containers.some(c=>c.name.includes(n));
 const svc=[
  {n:'OpenWebUI',d:'chat UI',port:3000,path:'',host:H,up:cUp('open-webui')},
  {n:'MinerU UI',d:'PDF extract',port:7860,path:'',host:H,auth:1,up:cUp('mineru')},
  {n:'MinerU API',d:'swagger docs',port:8000,path:'/docs',host:H,auth:1,up:cUp('mineru')},
  {n:'Mem0',d:'memory API',port:8077,path:'/health',host:H,up:d.mem0.up},
  {n:'LM Studio',d:'inference API (key)',port:1234,path:'/v1/models',host:H,up:null},
  {n:'LiteLLM',d:'gateway docs',port:4000,path:'/docs',host:'localhost',local:1,up:cUp('litellm')},
  {n:'Qdrant',d:'vector dashboard',port:6333,path:'/dashboard',host:'localhost',local:1,up:cUp('qdrant')},
 ];
 main+=`<div class=card><h2>services</h2>`+svc.map(s=>{
  const url=`http://${s.host}:${s.port}${s.path}`;
  const dot=s.up===null?'':`<span class="pill ${s.up?'up':'down'}">${s.up?'up':'down'}</span>`;
  const tags=(s.auth?'<span class=tag>auth</span> ':'')+(s.local?'<span class=tag>local only</span>':'');
  return `<div class=row><div><a href="${url}" target=_blank rel=noopener>${s.n}</a> ${dot}<div class=muted>${s.d} \\u00b7 :${s.port}</div></div><div>${tags}</div></div>`;
 }).join('')+`<div class=muted style="padding-top:8px">Jupyter & SearXNG are internal (used by OpenWebUI, no direct port).</div></div>`;
 // models card
 main+=`<div class=card><h2>models <button onclick="act('/api/model/unload')" ${d.action.running?'disabled':''}>unload all</button></h2>`+
  (d.models.length?d.models.map(m=>`<div class=row><div>${m.displayName||m.identifier}
   <div class=muted>${m.type} \\u00b7 ${(m.sizeBytes/1e9).toFixed(2)} GB \\u00b7 ctx ${m.contextLength||'-'} \\u00b7 <span class=tag>${m.status}</span></div></div>
   <button onclick="act('/api/model/unload?model='+encodeURIComponent('${m.modelKey}'))" ${d.action.running?'disabled':''}>unload</button></div>`).join('')
   :'<div class=muted>none loaded</div>')+`</div>`;
 // profiles card
 main+=`<div class=card><h2>profiles</h2><div class=prof>`+
  Object.entries(d.profiles).map(([k,desc])=>`<div class=row><div><b>${k}</b><div class=muted>${desc}</div></div>
   <button onclick="if(confirm('Apply profile '+'${k}'+'? This juggles VRAM.'))act('/api/profile/${k}')" ${d.action.running?'disabled':''}>apply</button></div>`).join('')+`</div></div>`;
 // containers card (name/status + live CPU% and memory from docker stats)
 main+=`<div class=card><h2>containers (${d.containers.length})</h2>`+
  (d.containers.length?d.containers.map(c=>`<div class=row><div>${c.name}
    <div class=muted>${c.status}</div></div>
    <div style="text-align:right;white-space:nowrap"><span class=tag>cpu ${c.cpu||'-'}</span> <span class=tag>${c.mem||'-'}</span></div></div>`).join('')
   :'<div class=muted>none running</div>')+`</div>`;
 // token usage card (local SQLite store, fed by the LiteLLM callback)
 const u=d.usage;
 const fmt=n=>n.toLocaleString();
 main+=`<div class=card><h2>token usage <span class=muted>last ${u.window_days}d</span></h2>
  <div class=kv><span>total</span><span><b>${fmt(u.total_tokens)}</b> tok (${fmt(u.total_prompt)} in / ${fmt(u.total_completion)} out) \\u00b7 ${u.total_requests} req</span></div>`+
  ((u.by_app&&u.by_app.length)?`<div class=muted style="margin:8px 0 2px">by app</div>`+u.by_app.map(r=>`<div class=row><span>${r.app}</span><div style="text-align:right;white-space:nowrap"><span class=tag>${fmt(r.prompt+r.completion)} tok</span> <span class=tag>${r.requests} req</span></div></div>`).join('')
   :'<div class=muted>no usage recorded yet - chat in OpenWebUI to populate</div>')+
  (u.by_user.length?`<div class=muted style="margin:10px 0 2px">by user</div>`+u.by_user.map(r=>`<div class=row><span>${r.user}</span><div style="text-align:right;white-space:nowrap"><span class=tag>${fmt(r.prompt+r.completion)} tok</span> <span class=tag>${r.requests} req</span></div></div>`).join(''):'')+
  (u.by_model.length?`<div class=muted style="margin:10px 0 2px">by model</div>`+u.by_model.map(r=>`<div class=row><span>${r.model}</span><span class=tag>${fmt(r.prompt+r.completion)} tok</span></div>`).join(''):'')+
  `</div>`;
 // host services card -> right sidebar
 const m=d.mem0;
 side+=`<div class=card><h2>host services</h2>
  <div class=row><div>mem0 memory <span class="pill ${m.up?'up':'down'}">${m.up?'up':'down'}</span>
   ${m.instances>1?'<span class=down>('+m.instances+' instances!)</span>':''}<div class=muted>:8077</div></div>
   <div><button onclick="act('/api/mem0/restart')" ${d.action.running?'disabled':''}>restart</button>
   <button onclick="act('/api/mem0/${m.up?'stop':'start'}')" ${d.action.running?'disabled':''}>${m.up?'stop':'start'}</button></div></div>
  </div>`;
 document.getElementById('main').innerHTML=main;
 document.getElementById('side').innerHTML=side;
 polling=false;
}
load();setInterval(load,5000);
</script></body></html>"""


if __name__ == "__main__":
    uvicorn.run(app, host=WEB_BIND, port=8090)
