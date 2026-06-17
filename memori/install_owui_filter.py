"""
Installs the Memori filter into OpenWebUI's DB (run INSIDE the open-webui container).
Reads the filter code from /tmp/memori_filter.py and registers it as an active, global
filter attributed to the admin user. Idempotent (re-run safe).
"""
import os, sqlite3, time, json

DB = "/app/backend/data/webui.db"
content = open("/tmp/memori_filter.py", encoding="utf-8").read()

c = sqlite3.connect(DB)
cur = c.cursor()
row = cur.execute("select id from user where role='admin' order by created_at limit 1").fetchone()
if not row:
    row = cur.execute("select id from user limit 1").fetchone()
uid = row[0] if row else None

now = int(time.time())
fid = "memori_memory"
meta = json.dumps({"description": "Per-account long-term memory via the local Memori service.", "manifest": {}})
valves = json.dumps({
    "memory_url": "http://HOST_LAN_IP:8077",
    "enabled": True, "inject_memories": True, "record_turns": True,
    "recall_limit": 5, "recall_timeout": 20,
    "service_token": os.environ.get("MEMORI_SERVICE_TOKEN", ""),
})
cur.execute("delete from function where id=?", (fid,))
cur.execute(
    "insert into function (id,user_id,name,type,content,meta,valves,is_active,is_global,updated_at,created_at)"
    " values (?,?,?,?,?,?,?,?,?,?,?)",
    (fid, uid, "Memori Memory", "filter", content, meta, valves, 1, 1, now, now),
)
c.commit()
print("attributed to user_id:", uid)
print("functions in DB now:", cur.execute("select count(*) from function").fetchone()[0])
