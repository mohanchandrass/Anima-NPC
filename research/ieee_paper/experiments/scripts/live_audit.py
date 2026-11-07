"""Aggregate local Skyrim console logs and captured frames into shareable, sanitized evidence.

Reads only ignored local files under output/skyrim. Player text, reply text, session IDs and frames
are never written out: the JSON holds route/receipt/timing fields and per-frame pixel statistics.

Run from the repository root:
    .venv/Scripts/python.exe -X utf8 research/ieee_paper/experiments/scripts/live_audit.py
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "research/ieee_paper/experiments/raw_results"
FIELD = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')
TERMINAL = ("completed", "interrupted", "failed")
BLACK_LEVEL = 8  # 8-bit luma below this counts as black
SAMPLE_WIDTH = 320
TIMING_FIELDS = ("total_ms", "model_ms", "tts_ms", "staging_ms", "native_start_ms", "pipeline_ms")
# The published snapshot: the first 354 log lines and frames written before 13:41:56 IST on 6 Nov 2026.
SNAPSHOT_LOG_LINES = 354
SNAPSHOT_FRAME_EPOCH = 1793952716.0


def event(line: str) -> tuple[str | None, dict[str, str]]:
    match = re.search(r"\[ANIMA\] (\w+)", line)
    return (match.group(1), dict(FIELD.findall(line))) if match else (None, {})


def number(value: str | None) -> float | None:
    return None if value in (None, "none") else float(value)


def summary(values: list[float]) -> dict | None:
    if not values:
        return None
    return {"n": len(values), "mean": round(statistics.mean(values), 1), "median": round(statistics.median(values), 1),
            "p90": round(rank(values, 0.9), 1), "min": round(min(values), 1), "max": round(max(values), 1)}


def rank(values: list[float], q: float) -> float:
    """Nearest-rank percentile, so small samples report an observed value."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def parse_log(lines) -> dict:
    """Join each timed turn to its pipeline result and terminal receipt. Text fields are never copied."""
    result, tts, terminal, turns = {}, {}, {}, []
    exchanges, startups = [], []
    for line in lines:
        kind, data = event(line)
        request = data.get("request")
        if kind == "PIPELINE_RESULT":
            result[request] = {k: data.get(k) for k in ("route", "status", "calls", "model", "error")}
        elif kind == "TTS_READY":
            tts[request] = data.get("engine")
        elif kind == "NATIVE_ACK" and data.get("status") in TERMINAL:
            terminal[request] = {"status": data["status"], "audio": data.get("audio"), "memory_committed": data.get("memory_committed")}
        elif kind == "TURN_TIMING":
            turns.append({"request": request, "source": data.get("source"), "turn_type": data.get("turn_type"), "move": data.get("move"),
                          **{field: number(data.get(field)) for field in TIMING_FIELDS}})
        elif kind == "SOCIAL_STATE" and "memory_exchanges" in data:
            exchanges.append(int(data["memory_exchanges"]))
        elif kind == "STARTUP":
            startups.append({k: data.get(k) for k in ("provider_profile", "rag", "stt", "tts")})
    rows = []
    for turn in turns:
        request = turn.pop("request")
        rows.append({**turn, **result.get(request, {}), "tts_engine": tts.get(request),
                     "receipt": terminal.get(request, {}).get("status", "none"), "audio": terminal.get(request, {}).get("audio"),
                     "memory_committed": terminal.get(request, {}).get("memory_committed")})
    generated = [r for r in rows if r.get("route") == "generate"]
    instrumented_start = next((i for i, line in enumerate(lines) if "TURN_TIMING" in line), len(lines))
    timed_requests = {event(line)[1].get("request") for line in lines if event(line)[0] == "TURN_TIMING"}
    early = {event(line)[1].get("request") for line in lines[:instrumented_start]
             if event(line)[0] == "PIPELINE_RESULT" and event(line)[1].get("route") == "generate"} - timed_requests
    replies = [r for r in generated if r.get("status") in ("model_success", "repair_success")]
    return {"generated_before_instrumentation": {"turns": len(early), "receipts": dict(Counter(terminal.get(r, {}).get("status", "none") for r in early))},
            "generated_model_replies": len(replies), "generated_model_replies_completed": sum(r["receipt"] == "completed" for r in replies),
            "authored_completed": sum(r.get("route") == "authored" and r["receipt"] == "completed" for r in rows),
            "fallback_completed": sum(r.get("status") == "error_fallback" and r["receipt"] == "completed" for r in rows),
            "generated_with_director_fields": sum(bool(r["turn_type"]) for r in generated),
            "fallbacks": [{k: r.get(k) for k in ("model", "error", "calls", "model_ms", "total_ms")} for r in generated if r.get("status") == "error_fallback"],
            "receipt_audio": dict(Counter(r["audio"] for r in rows)),
            "pipeline_ms_generated": summary([r["pipeline_ms"] for r in generated if r["pipeline_ms"] is not None]),
            "timed_turns": len(rows), "receipts": dict(Counter(r["receipt"] for r in rows)),
            "memory_committed": sum(r["memory_committed"] == "True" for r in rows),
            "pipeline_status": dict(Counter(r.get("status") for r in rows)),
            "routes": dict(Counter(r.get("route") for r in rows)), "model_calls": sum(int(r.get("calls") or 0) for r in rows),
            "input_source": dict(Counter(r["source"] for r in rows)), "tts_engine": dict(Counter(r["tts_engine"] for r in rows)),
            "turn_types": dict(Counter(r["turn_type"] for r in rows if r["turn_type"])),
            "moves": dict(Counter(r["move"] for r in rows if r["move"] not in (None, "none"))),
            "total_ms_all": summary([r["total_ms"] for r in rows if r["total_ms"] is not None]),
            "total_ms_generated": summary([r["total_ms"] for r in generated if r["total_ms"] is not None]),
            "total_ms_model_success": summary([r["total_ms"] for r in generated if r.get("status") in ("model_success", "repair_success")]),
            "model_ms": summary([r["model_ms"] for r in rows if r["model_ms"] is not None]),
            "tts_ms": summary([r["tts_ms"] for r in rows if r["tts_ms"] is not None]),
            "native_start_ms": summary([r["native_start_ms"] for r in rows if r["native_start_ms"] is not None]),
            "max_memory_exchanges": max(exchanges, default=0), "startups_with_rag": sum(s["rag"] == "True" for s in startups),
            "startups": len(startups), "rows": rows}


def black_fraction(image) -> float:
    """Share of near-black pixels, on a nearest-neighbour sample so large frames cost the same."""
    gray = image.convert("L")
    width, height = gray.size
    scale = min(1.0, SAMPLE_WIDTH / width)
    sample = gray.resize((max(1, int(width * scale)), max(1, int(height * scale))), 0)
    histogram = sample.histogram()
    return sum(histogram[:BLACK_LEVEL]) / sum(histogram)


def frame_audit(paths) -> dict:
    from PIL import Image

    rows, distinct = [], {}
    for path in sorted(paths):
        with Image.open(path) as image:
            fraction, size = black_fraction(image), image.size
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        distinct.setdefault(digest, fraction)
        rows.append({"sha256": digest, "width": size[0], "height": size[1], "black_fraction": round(fraction, 4)})
    fractions, unique = [r["black_fraction"] for r in rows], list(distinct.values())
    return {"black_level_luma": BLACK_LEVEL, "frames": len(rows), "distinct_frames": len(distinct),
            "resolutions": dict(Counter(f"{r['width']}x{r['height']}" for r in rows)),
            "frames_at_least_half_black": sum(f >= 0.5 for f in fractions), "frames_at_least_90pct_black": sum(f >= 0.9 for f in fractions),
            "distinct_at_least_90pct_black": sum(f >= 0.9 for f in unique), "min_black_fraction": min(fractions, default=None),
            "median_black_fraction": statistics.median(fractions) if fractions else None,
            "distinct_black_fractions": sorted(round(f, 3) for f in unique), "rows": rows}


def main() -> None:
    log = ROOT / "output/skyrim/anima-console.log"
    live = parse_log(log.read_text(encoding="utf-8", errors="replace").splitlines()[:SNAPSHOT_LOG_LINES])
    frames = frame_audit(p for p in (ROOT / "output/skyrim/screenshots").glob("*.png") if p.stat().st_mtime < SNAPSHOT_FRAME_EPOCH)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "skyrim_live_audit.json").write_text(json.dumps(live, indent=1), encoding="utf-8")
    (OUT / "cv_capture_audit.json").write_text(json.dumps(frames, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in live.items() if k != "rows"}, indent=1))
    print(json.dumps({k: v for k, v in frames.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main()
