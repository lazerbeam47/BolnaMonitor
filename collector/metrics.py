"""
BolnaMonitor — Metrics Collector
Plugs into Bolna's existing latency data (already emitted per-turn in meta_info)
and stores it in SQLite for analysis.

Bolna already tracks:
  - transcriber_duration
  - first_interim_to_final_ms
  - llm_latency
  - tts_latency (per synthesizer)
  - total_turn_latency

We just collect, tag, and persist it.
"""

import sqlite3
import json
import time
import logging
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "metrics.db"


@dataclass
class CallMetric:
    call_id: str
    agent_id: str
    timestamp: float
    turn_index: int

    # Provider combo
    asr_provider: str
    asr_model: str
    llm_provider: str
    llm_model: str
    tts_provider: str
    tts_model: str
    language: str

    # Latency (ms) — directly from Bolna's meta_info
    asr_latency_ms: Optional[float] = None       # transcriber_duration * 1000
    asr_first_interim_ms: Optional[float] = None  # first_interim_to_final_ms
    llm_latency_ms: Optional[float] = None
    tts_latency_ms: Optional[float] = None
    total_latency_ms: Optional[float] = None

    # Quality signals
    transcript: Optional[str] = None
    transcript_empty: bool = False               # ASR returned nothing
    interrupted: bool = False                    # User interrupted agent
    hangup_reason: Optional[str] = None
    error: Optional[str] = None


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS call_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            call_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            timestamp REAL NOT NULL,
            turn_index INTEGER NOT NULL,

            asr_provider TEXT,
            asr_model TEXT,
            llm_provider TEXT,
            llm_model TEXT,
            tts_provider TEXT,
            tts_model TEXT,
            language TEXT,

            asr_latency_ms REAL,
            asr_first_interim_ms REAL,
            llm_latency_ms REAL,
            tts_latency_ms REAL,
            total_latency_ms REAL,

            transcript TEXT,
            transcript_empty INTEGER DEFAULT 0,
            interrupted INTEGER DEFAULT 0,
            hangup_reason TEXT,
            error TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_call_metrics_combo
        ON call_metrics(asr_provider, llm_provider, tts_provider, timestamp)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_call_metrics_agent
        ON call_metrics(agent_id, timestamp)
    """)
    conn.commit()
    conn.close()
    logger.info(f"DB initialised at {DB_PATH}")


def insert_metric(m: CallMetric):
    conn = sqlite3.connect(DB_PATH)
    d = asdict(m)
    d["transcript_empty"] = int(d["transcript_empty"])
    d["interrupted"] = int(d["interrupted"])
    conn.execute("""
        INSERT INTO call_metrics (
            call_id, agent_id, timestamp, turn_index,
            asr_provider, asr_model, llm_provider, llm_model,
            tts_provider, tts_model, language,
            asr_latency_ms, asr_first_interim_ms, llm_latency_ms,
            tts_latency_ms, total_latency_ms,
            transcript, transcript_empty, interrupted,
            hangup_reason, error
        ) VALUES (
            :call_id, :agent_id, :timestamp, :turn_index,
            :asr_provider, :asr_model, :llm_provider, :llm_model,
            :tts_provider, :tts_model, :language,
            :asr_latency_ms, :asr_first_interim_ms, :llm_latency_ms,
            :tts_latency_ms, :total_latency_ms,
            :transcript, :transcript_empty, :interrupted,
            :hangup_reason, :error
        )
    """, d)
    conn.commit()
    conn.close()


def from_bolna_meta(call_id: str, agent_id: str, turn_index: int,
                    agent_config: dict, meta_info: dict) -> CallMetric:
    """
    Build a CallMetric from Bolna's existing meta_info dict.
    Drop this into Bolna's TaskManager after each turn.

    agent_config shape (from Bolna's API.md):
      tools_config.transcriber.provider / model / language
      tools_config.llm_agent.llm_config.provider / model
      tools_config.synthesizer.provider / provider_config.model
    """
    tc = agent_config.get("tools_config", {})
    tr = tc.get("transcriber", {})
    ll = tc.get("llm_agent", {}).get("llm_config", {})
    sy = tc.get("synthesizer", {})
    sy_cfg = sy.get("provider_config", {})

    asr_dur = meta_info.get("transcriber_duration")

    return CallMetric(
        call_id=call_id,
        agent_id=agent_id,
        timestamp=time.time(),
        turn_index=turn_index,

        asr_provider=tr.get("provider", "unknown"),
        asr_model=tr.get("model", "unknown"),
        llm_provider=ll.get("provider", "unknown"),
        llm_model=ll.get("model", "unknown"),
        tts_provider=sy.get("provider", "unknown"),
        tts_model=sy_cfg.get("model", "unknown"),
        language=tr.get("language", "en"),

        asr_latency_ms=asr_dur * 1000 if asr_dur else None,
        asr_first_interim_ms=meta_info.get("first_interim_to_final_ms"),
        llm_latency_ms=meta_info.get("llm_latency_ms"),
        tts_latency_ms=meta_info.get("tts_latency_ms"),
        total_latency_ms=meta_info.get("total_latency_ms"),

        transcript=meta_info.get("transcript"),
        transcript_empty=not bool(meta_info.get("transcript", "").strip()),
        interrupted=meta_info.get("interrupted", False),
        hangup_reason=meta_info.get("hangup_reason"),
        error=meta_info.get("error"),
    )
