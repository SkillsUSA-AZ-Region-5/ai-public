#!/usr/bin/env python3
"""Verify an AI-stack backup bundle is restorable. Usage: verify_backup.py <bundle-dir>"""
import sqlite3
import sys
import tarfile
from pathlib import Path

bundle = Path(sys.argv[1])
ok = True

db = bundle / "webui.db"
try:
    c = sqlite3.connect(db)
    integ = c.execute("PRAGMA integrity_check").fetchone()[0]
    tables = c.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    has_user = bool(c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user'").fetchone())
    c.close()
    print(f"webui.db        : integrity={integ}, tables={tables}, user-table={has_user}")
    ok &= integ == "ok" and has_user
except Exception as e:
    print(f"webui.db        : FAILED ({e})"); ok = False

for name in ("openwebui-files.tar.gz", "qdrant.tar.gz"):
    p = bundle / name
    try:
        with tarfile.open(p, "r:gz") as t:
            n = sum(1 for _ in t)
        print(f"{name:16}: {n} entries, {p.stat().st_size/1e6:.0f} MB OK")
        ok &= n > 0
    except Exception as e:
        print(f"{name:16}: FAILED ({e})"); ok = False

dump = bundle / "litellm-db.dump"
try:
    head = dump.read_bytes()[:5]
    print(f"litellm-db.dump : {dump.stat().st_size/1e6:.1f} MB, header={head!r}")
    ok &= head == b"PGDMP" and dump.stat().st_size > 0
except Exception as e:
    print(f"litellm-db.dump : FAILED ({e})"); ok = False

hermes = bundle / "hermes-data.tar.gz"
if hermes.exists():
    try:
        with tarfile.open(hermes, "r:gz") as t:
            names = t.getnames()
        has_config = any(n == ".hermes/config.yaml" for n in names)
        has_env = any(n == ".hermes/.env" for n in names)
        print(f"hermes-data.tar.gz: {len(names)} entries, config={has_config}, env={has_env}, {hermes.stat().st_size/1e6:.0f} MB OK")
        ok &= len(names) > 0 and has_config
    except Exception as e:
        print(f"hermes-data.tar.gz: FAILED ({e})"); ok = False
else:
    print("hermes-data.tar.gz: not present (ok if Hermes is not installed)")

env = bundle / ".env"
print(f".env            : {'present' if env.exists() and env.stat().st_size > 0 else 'MISSING/empty'}")
ok &= env.exists() and env.stat().st_size > 0

print("RESULT          :", "all checks passed" if ok else "PROBLEMS FOUND")
sys.exit(0 if ok else 1)
