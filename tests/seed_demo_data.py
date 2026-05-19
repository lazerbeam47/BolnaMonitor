"""
Generate realistic demo data for BolnaMonitor.
Simulates 3 days of calls across multiple provider combos,
including a deliberate drift event in the last 30 minutes
so the detector fires and the dashboard looks real.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import random
import time
import uuid
from collector.metrics import init_db, insert_metric, CallMetric

random.seed(42)

COMBOS = [
    # (asr_provider, asr_model, llm_provider, llm_model, tts_provider, tts_model, language, base_latency_ms, stability)
    ("deepgram", "nova-2",     "openai",   "gpt-4o-mini",  "elevenlabs", "eleven_turbo_v2_5", "en",      620,  0.12),
    ("deepgram", "nova-2",     "openai",   "gpt-4o-mini",  "cartesia",   "sonic-2",           "en",      540,  0.10),
    ("sarvam",   "saaras-v2",  "openai",   "gpt-4o-mini",  "sarvam",     "bulbul-v2",         "hi",      710,  0.18),
    ("sarvam",   "saaras-v2",  "deepseek", "deepseek-v3",  "elevenlabs", "eleven_turbo_v2_5", "hi",      820,  0.22),
    ("deepgram", "nova-2",     "openai",   "gpt-4o",       "elevenlabs", "eleven_multilingual_v2", "en", 780,  0.15),
    ("azure",    "whisper",    "openai",   "gpt-4o-mini",  "azure",      "neural-v2",         "en",      950,  0.25),
    ("deepgram", "nova-2",     "groq",     "llama-3.3-70b","cartesia",   "sonic-2",           "en",      480,  0.08),
    ("sarvam",   "saarika-v2", "openai",   "gpt-4o-mini",  "sarvam",     "bulbul-v2",         "te",      890,  0.30),
]

LANGUAGES_EXTRA = ["hi", "en", "ta", "te", "mr", "kn"]
HANGUP_REASONS = [None, None, None, None, "silence", "silence", "user_hangup", "max_duration"]

def jitter(base, std_frac, count=1):
    return [max(100, random.gauss(base, base * std_frac)) for _ in range(count)]

def generate_calls(hours_back=72):
    now = time.time()
    calls = []
    
    for combo in COMBOS:
        asr_p, asr_m, llm_p, llm_m, tts_p, tts_m, lang, base_ms, stability = combo
        
        # ~50-300 calls per combo over the window
        n_calls = random.randint(50, 300)
        
        for _ in range(n_calls):
            call_id = str(uuid.uuid4())
            agent_id = f"agent_{random.randint(1, 20):03d}"
            
            # Random time in window, weighted toward recent
            ts_offset = random.expovariate(1 / (hours_back * 3600 / 2))
            ts = now - min(ts_offset, hours_back * 3600)
            
            # Is this call in the "drift zone" (last 30 min)?
            in_drift_zone = (now - ts) < 1800
            
            # Inject drift for 2 combos in the last 30 min
            drift_multiplier = 1.0
            error_rate = 0.02
            
            if in_drift_zone and asr_p == "sarvam" and llm_p == "deepseek":
                drift_multiplier = 2.8   # severe spike
                error_rate = 0.18
            elif in_drift_zone and asr_p == "azure":
                drift_multiplier = 1.6   # moderate spike
                error_rate = 0.08
            
            effective_base = base_ms * drift_multiplier
            n_turns = random.randint(3, 15)
            
            for turn_i in range(n_turns):
                total = random.gauss(effective_base, effective_base * stability)
                total = max(150, total)
                
                # Split total into ASR / LLM / TTS proportionally
                asr_frac = random.uniform(0.20, 0.35)
                llm_frac = random.uniform(0.30, 0.50)
                tts_frac = 1.0 - asr_frac - llm_frac
                
                has_error = random.random() < error_rate
                transcript_empty = has_error and random.random() < 0.6
                interrupted = (not has_error) and random.random() < 0.08
                
                m = CallMetric(
                    call_id=call_id,
                    agent_id=agent_id,
                    timestamp=ts + turn_i * random.uniform(5, 30),
                    turn_index=turn_i,
                    asr_provider=asr_p,
                    asr_model=asr_m,
                    llm_provider=llm_p,
                    llm_model=llm_m,
                    tts_provider=tts_p,
                    tts_model=tts_m,
                    language=lang,
                    asr_latency_ms=round(total * asr_frac, 1),
                    asr_first_interim_ms=round(total * asr_frac * 0.6, 1),
                    llm_latency_ms=round(total * llm_frac, 1),
                    tts_latency_ms=round(total * tts_frac, 1),
                    total_latency_ms=round(total, 1),
                    transcript="" if transcript_empty else f"[turn {turn_i} transcript]",
                    transcript_empty=transcript_empty,
                    interrupted=interrupted,
                    hangup_reason=random.choice(HANGUP_REASONS) if turn_i == n_turns - 1 else None,
                    error="ASR_CONNECTION_ERROR" if (has_error and not transcript_empty) else None,
                )
                calls.append(m)
    
    return calls


if __name__ == "__main__":
    print("Initialising database...")
    init_db()
    
    print("Generating 3 days of demo data...")
    calls = generate_calls(hours_back=72)
    
    print(f"Inserting {len(calls)} turn records...")
    for i, m in enumerate(calls):
        insert_metric(m)
        if i % 500 == 0:
            print(f"  {i}/{len(calls)}...")
    
    print(f"Done. {len(calls)} records inserted.")
    print("Drift injected into: sarvam+deepseek and azure combos (last 30min)")
    print("Run: uvicorn api:app --port 8000 to start the server")