# BolnaMonitor

**Real-time drift detection for Bolna's ASR+LLM+TTS provider pipelines.**

At 200K calls/day across 10+ languages and 20+ model combinations, degradation in one provider combo can affect thousands of calls before anyone notices. BolnaMonitor detects it in 30 minutes, not days.

---

## The Problem

Bolna's orchestration layer routes calls across many provider combinations — Deepgram+GPT-4o+ElevenLabs for English, Sarvam+DeepSeek+Bulbul for Hindi, etc. Each combo has different latency characteristics and failure modes. When one degrades:

- It doesn't show up in aggregate metrics (healthy combos mask it)
- Customers complain before engineering knows
- Root cause (which provider?) takes time to isolate

## What This Does

BolnaMonitor watches each combo's per-turn latency data (which Bolna **already emits** in `meta_info`) and runs statistical drift detection:

- **Z-score drift**: flags when a combo's recent latency is >2.5σ above its 24h baseline
- **Error rate monitoring**: flags when empty transcripts or errors exceed 5%  
- **Absolute threshold**: always alerts if total turn latency exceeds 2000ms
- **Sparkline trends**: 24h latency chart per combo so you see the shape, not just a number

---

## Architecture

```
Bolna call completes
        │
        ▼
  Webhook / direct ingest          ← collector/receiver.py
        │
        ▼
   SQLite metrics DB               ← data/metrics.db
        │
        ▼
  Drift detector                   ← detector/drift.py
  (rolling Z-score + EWMA)
        │
        ▼
  FastAPI + Dashboard              ← api.py + dashboard/index.html
```

Bolna already tracks `transcriber_duration`, `first_interim_to_final_ms`, `llm_latency_ms`, `tts_latency_ms`, and `total_latency_ms` in `meta_info` (added in v0.9.8). We collect it, nothing else.

---

## Setup

```bash
git clone https://github.com/yourusername/bolna-monitor
cd bolna-monitor
pip install -r requirements.txt

# Generate demo data (simulates 3 days of calls with injected drift)
python tests/seed_demo_data.py

# Start the server (API + dashboard on same port)
uvicorn api:app --port 8000 --reload
```

Open `http://localhost:8000` → dashboard loads with live data.

---

## Integrating with Bolna

### Option 1 — Webhook (zero code change to Bolna)

Set your Bolna webhook URL to `http://your-server:8000/webhook-receiver/webhook`.  
Bolna fires this after every call with `execution_details` containing per-turn `meta_info`.

### Option 2 — Direct ingest (lower latency, real-time)

Add one call to Bolna's `TaskManager` after each turn:

```python
# In bolna/agent_manager/task_manager.py, after computing latencies:
import httpx
from bolna.helpers.monitor import build_metric_payload  # helper included

payload = build_metric_payload(call_id, agent_id, turn_index, agent_config, meta_info)
httpx.post("http://localhost:8000/webhook-receiver/ingest", json=payload, timeout=0.5)
```

The ingest call is fire-and-forget with a 500ms timeout — no impact on call latency.

---

## API

```
GET  /api/summary?hours=1          Platform-wide health (last N hours)
GET  /api/combos                   All provider combos with drift status
GET  /api/combos/{key}/timeseries  15-min bucketed latency for one combo
POST /webhook-receiver/webhook     Bolna call completion webhook
POST /webhook-receiver/ingest      Direct per-turn ingest
```

---

## Drift Detection Logic

For each combo:

1. Collect all turns in the **baseline window** (last 24h) → compute mean μ and std σ
2. Collect all turns in the **recent window** (last 30min) → compute recent mean
3. Z-score = (recent_mean - μ) / σ
4. Alert if z > 2.5, error_rate > 5%, or absolute latency > 2000ms

Minimum 20 samples required before alerting (avoids false positives on new combos).

---

## Stack

- Python 3.11+, FastAPI, SQLite (no external dependencies)
- Chart.js for the dashboard (CDN, no build step)
- IBM Plex Mono — chosen because engineers read it better than anything else at small sizes

---

## Why SQLite

Bolna runs 200K calls/day = ~2M turns/day (avg 10 turns/call). SQLite handles this easily with the indexed schema. For higher volumes, swap the connection string in `collector/metrics.py` for PostgreSQL — the query layer is identical.

---

Built by [Dabbu Mothsera](https://github.com/lazerbeam47) · [github.com/lazerbeam47](https://github.com/lazerbeam47)