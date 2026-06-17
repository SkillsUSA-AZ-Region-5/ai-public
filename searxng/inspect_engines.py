from searx import settings

engs = settings["engines"]
keyfields = ("api_key", "token", "tokens", "secret", "password",
             "api_id", "app_id", "app_key", "client_id", "client_secret")
enabled, disabled_nokey, needs_key = [], [], []
for e in engs:
    name = e.get("name")
    dis = bool(e.get("disabled", False))
    keyish = any(f in e for f in keyfields)
    if keyish:
        needs_key.append(name)
    elif dis:
        disabled_nokey.append(name)
    else:
        enabled.append(name)

print("TOTAL", len(engs), "| enabled", len(enabled),
      "| disabled_nokey", len(disabled_nokey), "| needs_key", len(needs_key))
print("NOKEY_DISABLED=" + ",".join(sorted(disabled_nokey)))
print("NEEDS_KEY=" + ",".join(sorted(needs_key)))
