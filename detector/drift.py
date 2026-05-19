"""
BolnaMonitor — Drift Detector

Statistical drift detection per ASR+LLM+TTS provider combination.
Uses rolling Z-score + EWMA (Exponentially Weighted Moving Average) 
to detect when a combo starts degrading — same approach as DriftGuard
but applied to voice pipeline latency.

Key insight: we're not comparing combos to each other.
We're comparing each combo to its OWN recent baseline.
A slow combo that's consistently slow is fine.
A combo that was fast and suddenly got slow — that's an alert.
"""

import sqlite3
import math
import time
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "metrics.db"

# Thresholds
DRIFT_Z_SCORE_THRESHOLD = 2.5    # standard deviations from rolling mean
MIN_SAMPLES_FOR_DRIFT = 20       # need enough data before alerting
BASELINE_WINDOW_HOURS = 24       # rolling baseline window
RECENT_WINDOW_MINUTES = 30       # "current" window to compare against baseline
ERROR_RATE_ALERT_THRESHOLD = 0.05  # 5% empty transcripts = alert
LATENCY_ABSOLUTE_ALERT_MS = 2000   # always alert if >2s total latency


@dataclass
class ComboStats:
    combo_key: str          # "deepgram|nova-2|openai|gpt-4o-mini|elevenlabs|eleven_turbo_v2_5"
    asr_provider: str
    asr_model: str
    llm_provider: str
    llm_model: str
    tts_provider: str
    tts_model: str

    # Baseline (last 24h)
    baseline_count: int
    baseline_mean_ms: float
    baseline_std_ms: float

    # Recent (last 30min)
    recent_count: int
    recent_mean_ms: float
    recent_error_rate: float       # % of turns with empty transcript or error

    # Drift signal
    z_score: float
    is_drifting: bool
    alert_level: str               # "ok" | "warning" | "critical"
    alert_reasons: list


def _get_conn():
    return sqlite3.connect(DB_PATH)


def _combo_key(row) -> str:
    return f"{row[0]}|{row[1]}|{row[2]}|{row[3]}|{row[4]}|{row[5]}"


def compute_combo_stats() -> list[ComboStats]:
    """
    For every distinct ASR+LLM+TTS combo seen in the last 24h,
    compute baseline stats and compare to the last 30 minutes.
    """
    now = time.time()
    baseline_cutoff = now - (BASELINE_WINDOW_HOURS * 3600)
    recent_cutoff = now - (RECENT_WINDOW_MINUTES * 60)

    conn = _get_conn()

    # Get all distinct combos seen in baseline window
    combos = conn.execute("""
        SELECT DISTINCT asr_provider, asr_model, llm_provider, llm_model,
               tts_provider, tts_model
        FROM call_metrics
        WHERE timestamp > ? AND total_latency_ms IS NOT NULL
    """, (baseline_cutoff,)).fetchall()

    results = []

    for combo in combos:
        asr_p, asr_m, llm_p, llm_m, tts_p, tts_m = combo

        # --- Baseline stats (last 24h) ---
        baseline_rows = conn.execute("""
            SELECT total_latency_ms
            FROM call_metrics
            WHERE asr_provider=? AND asr_model=?
              AND llm_provider=? AND llm_model=?
              AND tts_provider=? AND tts_model=?
              AND timestamp > ?
              AND total_latency_ms IS NOT NULL
        """, (*combo, baseline_cutoff)).fetchall()

        baseline_latencies = [r[0] for r in baseline_rows]
        baseline_count = len(baseline_latencies)

        if baseline_count < 2:
            continue

        baseline_mean = sum(baseline_latencies) / baseline_count
        variance = sum((x - baseline_mean) ** 2 for x in baseline_latencies) / baseline_count
        baseline_std = math.sqrt(variance) if variance > 0 else 1.0

        # --- Recent stats (last 30min) ---
        recent_rows = conn.execute("""
            SELECT total_latency_ms, transcript_empty, error
            FROM call_metrics
            WHERE asr_provider=? AND asr_model=?
              AND llm_provider=? AND llm_model=?
              AND tts_provider=? AND tts_model=?
              AND timestamp > ?
        """, (*combo, recent_cutoff)).fetchall()

        recent_count = len(recent_rows)
        if recent_count == 0:
            recent_mean = baseline_mean
            recent_error_rate = 0.0
        else:
            recent_latencies = [r[0] for r in recent_rows if r[0] is not None]
            recent_mean = sum(recent_latencies) / len(recent_latencies) if recent_latencies else baseline_mean
            error_count = sum(1 for r in recent_rows if r[1] or r[2])
            recent_error_rate = error_count / recent_count

        # --- Z-score: how many std devs is recent mean from baseline? ---
        z_score = (recent_mean - baseline_mean) / baseline_std if baseline_std > 0 else 0.0

        # --- Alert logic ---
        alert_reasons = []
        alert_level = "ok"

        if baseline_count >= MIN_SAMPLES_FOR_DRIFT:
            if z_score > DRIFT_Z_SCORE_THRESHOLD:
                alert_reasons.append(
                    f"Latency spiked {z_score:.1f}σ above baseline "
                    f"({recent_mean:.0f}ms vs {baseline_mean:.0f}ms baseline)"
                )
                alert_level = "warning"

            if recent_mean > LATENCY_ABSOLUTE_ALERT_MS:
                alert_reasons.append(
                    f"Total latency exceeds {LATENCY_ABSOLUTE_ALERT_MS}ms threshold "
                    f"(current: {recent_mean:.0f}ms)"
                )
                alert_level = "critical"

            if recent_error_rate > ERROR_RATE_ALERT_THRESHOLD and recent_count >= 5:
                alert_reasons.append(
                    f"Error rate {recent_error_rate:.1%} exceeds {ERROR_RATE_ALERT_THRESHOLD:.0%} threshold"
                )
                alert_level = "critical" if recent_error_rate > 0.15 else max(alert_level, "warning")

        is_drifting = bool(alert_reasons)

        results.append(ComboStats(
            combo_key=_combo_key(combo),
            asr_provider=asr_p,
            asr_model=asr_m,
            llm_provider=llm_p,
            llm_model=llm_m,
            tts_provider=tts_p,
            tts_model=tts_m,
            baseline_count=baseline_count,
            baseline_mean_ms=round(baseline_mean, 1),
            baseline_std_ms=round(baseline_std, 1),
            recent_count=recent_count,
            recent_mean_ms=round(recent_mean, 1),
            recent_error_rate=round(recent_error_rate, 4),
            z_score=round(z_score, 2),
            is_drifting=is_drifting,
            alert_level=alert_level,
            alert_reasons=alert_reasons,
        ))

    conn.close()
    results.sort(key=lambda x: (x.alert_level == "critical", x.alert_level == "warning", x.z_score), reverse=True)
    return results


def get_latency_timeseries(combo_key: str, hours: int = 24, bucket_minutes: int = 15) -> list[dict]:
    """
    Returns time-bucketed latency averages for a specific combo.
    Used to power the sparkline charts in the dashboard.
    """
    parts = combo_key.split("|")
    if len(parts) != 6:
        return []

    asr_p, asr_m, llm_p, llm_m, tts_p, tts_m = parts
    now = time.time()
    cutoff = now - (hours * 3600)
    bucket_seconds = bucket_minutes * 60

    conn = _get_conn()
    rows = conn.execute("""
        SELECT timestamp, total_latency_ms, asr_latency_ms, llm_latency_ms, tts_latency_ms
        FROM call_metrics
        WHERE asr_provider=? AND asr_model=?
          AND llm_provider=? AND llm_model=?
          AND tts_provider=? AND tts_model=?
          AND timestamp > ?
          AND total_latency_ms IS NOT NULL
        ORDER BY timestamp ASC
    """, (asr_p, asr_m, llm_p, llm_m, tts_p, tts_m, cutoff)).fetchall()
    conn.close()

    if not rows:
        return []

    # Bucket into time windows
    buckets = {}
    for ts, total, asr, llm, tts in rows:
        bucket = int(ts / bucket_seconds) * bucket_seconds
        if bucket not in buckets:
            buckets[bucket] = {"total": [], "asr": [], "llm": [], "tts": []}
        buckets[bucket]["total"].append(total)
        if asr: buckets[bucket]["asr"].append(asr)
        if llm: buckets[bucket]["llm"].append(llm)
        if tts: buckets[bucket]["tts"].append(tts)

    result = []
    for bucket_ts in sorted(buckets.keys()):
        b = buckets[bucket_ts]
        result.append({
            "timestamp": bucket_ts * 1000,  # JS expects ms
            "total_ms": round(sum(b["total"]) / len(b["total"]), 1),
            "asr_ms": round(sum(b["asr"]) / len(b["asr"]), 1) if b["asr"] else None,
            "llm_ms": round(sum(b["llm"]) / len(b["llm"]), 1) if b["llm"] else None,
            "tts_ms": round(sum(b["tts"]) / len(b["tts"]), 1) if b["tts"] else None,
            "count": len(b["total"]),
        })

    return result


def get_summary_stats(hours: int = 1) -> dict:
    """Overall platform health for the past N hours."""
    cutoff = time.time() - (hours * 3600)
    conn = _get_conn()

    row = conn.execute("""
        SELECT
            COUNT(*) as total_turns,
            AVG(total_latency_ms) as avg_latency,
            SUM(transcript_empty) as empty_transcripts,
            SUM(interrupted) as interruptions,
            SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) as errors
        FROM call_metrics
        WHERE timestamp > ?
    """, (cutoff,)).fetchone()

    combos_active = conn.execute("""
        SELECT COUNT(DISTINCT asr_provider || llm_provider || tts_provider)
        FROM call_metrics WHERE timestamp > ?
    """, (cutoff,)).fetchone()[0]

    conn.close()

    total = row[0] or 0
    return {
        "total_turns": total,
        "avg_latency_ms": round(row[1], 1) if row[1] else None,
        "error_rate": round((row[4] or 0) / total, 4) if total > 0 else 0,
        "empty_transcript_rate": round((row[2] or 0) / total, 4) if total > 0 else 0,
        "interruption_rate": round((row[3] or 0) / total, 4) if total > 0 else 0,
        "active_combos": combos_active,
        "window_hours": hours,
    }