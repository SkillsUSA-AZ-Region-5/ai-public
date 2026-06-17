"""
Installs the Web Fetch tool into OpenWebUI's DB (run INSIDE the open-webui container).
Reads /tmp/web_fetch_tool.py and registers it in the `tool` table with a function spec
so the model can call fetch_url. Idempotent.
"""
import sqlite3, time, json

DB = "/app/backend/data/webui.db"
content = open("/tmp/web_fetch_tool.py", encoding="utf-8").read()

c = sqlite3.connect(DB)
cur = c.cursor()
row = cur.execute("select id from user where role='admin' order by created_at limit 1").fetchone()
if not row:
    row = cur.execute("select id from user limit 1").fetchone()
uid = row[0] if row else None

now = int(time.time())
tid = "web_fetch"
specs = json.dumps([{
    "name": "fetch_url",
    "description": "Fetch a web page and return its readable text content. Use when the user "
                   "gives a URL or asks you to read/summarize a specific page.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The full URL to fetch (http:// or https://)."}
        },
        "required": ["url"],
    },
}])
meta = json.dumps({"description": "Fetch and read the text of a specific URL."})
cur.execute("delete from tool where id=?", (tid,))
cur.execute(
    "insert into tool (id,user_id,name,content,specs,meta,valves,updated_at,created_at)"
    " values (?,?,?,?,?,?,?,?,?)",
    (tid, uid, "Web Fetch", content, specs, meta, json.dumps({}), now, now),
)
c.commit()
print("tools in DB now:", cur.execute("select count(*) from tool").fetchone()[0])
