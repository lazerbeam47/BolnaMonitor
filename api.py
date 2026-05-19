"""
BolnaMonitor — API Server
Serves drift analysis and metrics to the dashboard.
Run: uvicorn api:app --port 8000 --reload
"""

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
import dataclasses
import json

from detector.drift import (
    compute_combo_stats,
    get_latency_timeseries,
    get_summary_stats,
)
from collector.receiver import app as receiver_app
from collector.metrics import init_db

init_db()

app = FastAPI(title="BolnaMonitor", version="1.0.0")
app.mount("/webhook-receiver", receiver_app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _to_json(obj):
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    raise TypeError(f"Not serializable: {type(obj)}")


@app.get("/api/combos")
async def get_combos():
    """All provider combos with drift status. Powers the main table."""
    combos = compute_combo_stats()
    return [dataclasses.asdict(c) for c in combos]


@app.get("/api/combos/{combo_key}/timeseries")
async def get_timeseries(
    combo_key: str,
    hours: int = Query(default=24, ge=1, le=168),
    bucket_minutes: int = Query(default=15, ge=5, le=60),
):
    """Latency timeseries for a specific combo. Powers the chart."""
    return get_latency_timeseries(combo_key, hours=hours, bucket_minutes=bucket_minutes)


@app.get("/api/summary")
async def get_summary(hours: int = Query(default=1, ge=1, le=24)):
    """Platform-wide health summary."""
    return get_summary_stats(hours=hours)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# Serve dashboard
DASHBOARD_PATH = Path(__file__).parent / "dashboard"
if DASHBOARD_PATH.exists():
    app.mount("/", StaticFiles(directory=str(DASHBOARD_PATH), html=True), name="dashboard")