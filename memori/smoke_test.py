"""
Quick verification that Memori works fully-locally against LM Studio.

Prereqs:
  - LM Studio serving on :1234 with google/gemma-4-26b-a4b-qat loaded on GPU
    (`lms load google/gemma-4-26b-a4b-qat --gpu max`)
  - venv created and `pip install -r requirements.txt` done

Run:
    python smoke_test.py
"""
import time
import memori_config


def main():
    m = memori_config.make_memori(user_id="smoketest")
    print("[*] Memori enabled. DB:", memori_config.DATABASE_URL)
    m.add("Smoke test fact: the local AI stack serves OpenWebUI on port 3000.")
    print("[*] recorded a fact; ingestion is async, polling for extraction...")
    for i in range(18):
        res = m.retrieve_context("what port is OpenWebUI served on?")
        if res:
            print("[*] recalled after %ds:" % (i * 5))
            for r in res:
                pd = r.get("processed_data", {}) if isinstance(r, dict) else {}
                print("   -", pd.get("content") or pd.get("summary") or str(r)[:200])
            return
        time.sleep(5)
    print("[!] nothing recalled within 90s - is the extraction model loaded on GPU?")


if __name__ == "__main__":
    main()
