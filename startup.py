import sys, os, sqlite3
from pathlib import Path
sys.path.insert(0, os.path.dirname(__file__))
from collector.metrics import init_db, DB_PATH

def needs_seeding():
    init_db()
    try:
        conn = sqlite3.connect(DB_PATH)
        count = conn.execute("SELECT COUNT(*) FROM call_metrics").fetchone()[0]
        conn.close()
        return count == 0
    except:
        return True

if needs_seeding():
    print("Seeding demo data...")
    exec(open("tests/seed_demo_data.py").read())
    print("Done.")
else:
    print("Data exists, skipping seed.")
