"""
BolnaMonitor — Integration Helper

Drop this file into Bolna's codebase at bolna/helpers/monitor.py
Then call send_metric() from TaskManager after each turn.

One function. One HTTP call. Fire and forget.
"""

import time
import httpx
import logging

logger = logging.getLogger(__name__)

MONITOR_URL = "http://localhost:8000/webhook-receiver/ingest"
TIMEOUT_S = 0.5  # never block a call for monitoring


def build_metric_payload(
    call_id: str,
    agent_id: str,
    turn_index: int,
    agent_config: dict,
    meta_info: dict,
) -> dict:
    """
    Builds the ingest payload from Bolna's existing agent_config + meta_info.
    Both are already available in TaskManager — no new data needed.
    """
    tc = agent_config.get("tools_config", {})
    tr = tc.get("transcriber", {})
    ll = tc.get("llm_agent", {}).get("llm_config", {})
    sy = tc.get("synthesizer", {})
    sy_cfg = sy.get("provider_config", {})

    asr_dur = meta_info.get("transcriber_duration")

    return {
        "call_id": call_id,
        "agent_id": agent_id,
        "timestamp": time.time(),
        "turn_index": turn_index,
        "asr_provider": tr.get("provider", "unknown"),
        "asr_model": tr.get("model", "unknown"),
        "llm_provider": ll.get("provider", "unknown"),
        "llm_model": ll.get("model", "unknown"),
        "tts_provider": sy.get("provider", "unknown"),
        "tts_model": sy_cfg.get("model", "unknown"),
        "language": tr.get("language", "en"),
        "asr_latency_ms": asr_dur * 1000 if asr_dur else None,
        "asr_first_interim_ms": meta_info.get("first_interim_to_final_ms"),
        "llm_latency_ms": meta_info.get("llm_latency_ms"),
        "tts_latency_ms": meta_info.get("tts_latency_ms"),
        "total_latency_ms": meta_info.get("total_latency_ms"),
        "transcript": meta_info.get("transcript"),
        "transcript_empty": not bool((meta_info.get("transcript") or "").strip()),
        "interrupted": bool(meta_info.get("interrupted", False)),
        "hangup_reason": meta_info.get("hangup_reason"),
        "error": meta_info.get("error"),
    }


def send_metric(
    call_id: str,
    agent_id: str,
    turn_index: int,
    agent_config: dict,
    meta_info: dict,
) -> None:
    """
    Fire-and-forget metric send. Call this after every turn in TaskManager.

    Usage in bolna/agent_manager/task_manager.py:
        from bolna.helpers.monitor import send_metric
        send_metric(call_id, agent_id, turn_index, agent_config, meta_info)
    """
    try:
        payload = build_metric_payload(
            call_id, agent_id, turn_index, agent_config, meta_info
        )
        httpx.post(MONITOR_URL, json=payload, timeout=TIMEOUT_S)
    except Exception as e:
        # Never crash a call because monitoring failed
        logger.debug(f"BolnaMonitor: failed to send metric: {e}")