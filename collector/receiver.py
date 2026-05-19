"""
BolnaMonitor — Webhook Receiver
Receives call completion webhooks from Bolna's existing webhook system.
Bolna fires webhooks after each call (docs: /docs/polling-call-status-webhooks)

Run: uvicorn collector.receiver:app --port 8001
Then set webhook URL in Bolna dashboard to: http://your-server:8001/webhook
"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import json
import logging
import time

from collector.metrics import init_db, insert_metric, CallMetric

logger = logging.getLogger(__name__)
app = FastAPI(title="BolnaMonitor Receiver", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()


@app.post("/webhook")
async def receive_bolna_webhook(request: Request):
    """
    Receives Bolna's call completion webhook.
    Bolna sends per-call data including call_id, agent_id, and execution details.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    call_id = body.get("call_id", "unknown")
    agent_id = body.get("agent_id", "unknown")
    agent_config = body.get("agent_config", {})
    # Bolna sends per-turn execution data in "execution_details"
    turns = body.get("execution_details", [])

    inserted = 0
    for i, turn in enumerate(turns):
        meta = turn.get("meta_info", {})
        if not meta:
            continue
        try:
            m = CallMetric(
                call_id=call_id,
                agent_id=agent_id,
                timestamp=turn.get("timestamp", time.time()),
                turn_index=i,
                asr_provider=agent_config.get("tools_config", {}).get("transcriber", {}).get("provider", "unknown"),
                asr_model=agent_config.get("tools_config", {}).get("transcriber", {}).get("model", "unknown"),
                llm_provider=agent_config.get("tools_config", {}).get("llm_agent", {}).get("llm_config", {}).get("provider", "unknown"),
                llm_model=agent_config.get("tools_config", {}).get("llm_agent", {}).get("llm_config", {}).get("model", "unknown"),
                tts_provider=agent_config.get("tools_config", {}).get("synthesizer", {}).get("provider", "unknown"),
                tts_model=agent_config.get("tools_config", {}).get("synthesizer", {}).get("provider_config", {}).get("model", "unknown"),
                language=agent_config.get("tools_config", {}).get("transcriber", {}).get("language", "en"),
                asr_latency_ms=(meta.get("transcriber_duration") or 0) * 1000 or None,
                asr_first_interim_ms=meta.get("first_interim_to_final_ms"),
                llm_latency_ms=meta.get("llm_latency_ms"),
                tts_latency_ms=meta.get("tts_latency_ms"),
                total_latency_ms=meta.get("total_latency_ms"),
                transcript=meta.get("transcript"),
                transcript_empty=not bool((meta.get("transcript") or "").strip()),
                interrupted=bool(meta.get("interrupted", False)),
                hangup_reason=meta.get("hangup_reason"),
                error=meta.get("error"),
            )
            insert_metric(m)
            inserted += 1
        except Exception as e:
            logger.error(f"Failed to insert turn {i} for call {call_id}: {e}")

    return {"status": "ok", "call_id": call_id, "turns_stored": inserted}


@app.post("/ingest")
async def direct_ingest(request: Request):
    """
    Direct ingest endpoint — call this from your Bolna TaskManager patch
    instead of waiting for webhook. Lower latency, good for real-time monitoring.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    try:
        m = CallMetric(**body)
        insert_metric(m)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": time.time()}