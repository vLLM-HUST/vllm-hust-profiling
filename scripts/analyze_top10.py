#!/usr/bin/env python3
"""Analyze the independent 2026-07-23 all-scenario profiling campaign.

The script intentionally uses the standard library plus Pillow (already used
by the report figure scripts).  It reads benchmark JSON, msServiceProfiler CSV
exports and profiler.db, then writes machine-readable tables and source-backed
figures under the campaign directory.
"""

from __future__ import annotations

import csv
import argparse
import json
import math
import os
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - figures are optional in a bare parser env
    Image = ImageDraw = ImageFont = None


ROOT = Path(os.environ.get("RUN_DIR", Path(__file__).resolve().parents[1] / "runs"))
ANALYSIS = ROOT / "analysis"
FIGURES = ROOT / "figures"
SCENARIO_ORDER = [
    "random-online",
    "logprobs-online",
    "prefix-repetition-online",
    "kv-tiering-prefix-online",
    "agent-research-online",
    "sharegpt-online",
]
SLICE_NAMES = [
    "modelExec",
    "modelRunnerExec",
    "computing_logits",
    "sample",
    "_prepare_inputs",
    "batchFrameworkProcessing",
    "_update_states",
]


def finite(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def quantile(values, p):
    values = sorted(v for v in values if v is not None and math.isfinite(v))
    if not values:
        return None
    return values[min(len(values) - 1, int(p * (len(values) - 1)))]


def read_csv_numbers(path, column):
    if not path.exists():
        return []
    result = []
    with path.open(newline="") as stream:
        for row in csv.DictReader(stream):
            value = finite(row.get(column))
            if value is not None:
                result.append(value)
    return result


def summarize(values):
    return {
        "count": len(values),
        "mean": sum(values) / len(values) if values else None,
        "p50": quantile(values, 0.50),
        "p95": quantile(values, 0.95),
        "p99": quantile(values, 0.99),
        "max": max(values) if values else None,
    }


def slice_stats(db_path):
    result = {}
    if not db_path.exists():
        return result
    with sqlite3.connect(db_path) as conn:
        names = ",".join("?" for _ in SLICE_NAMES)
        rows = conn.execute(
            f"SELECT name, duration / 1000000.0 FROM slice WHERE name IN ({names})",
            SLICE_NAMES,
        ).fetchall()
    grouped = defaultdict(list)
    for name, duration in rows:
        value = finite(duration)
        if value is not None:
            grouped[name].append(value)
    for name in SLICE_NAMES:
        values = grouped[name]
        result[name] = summarize(values)
    return result


def parse_dir_name(path):
    match = re.match(r"(.+)-eager-(true|false)(?:-32)?$", path.name)
    if not match:
        return None
    return match.group(1), match.group(2), path.name.endswith("-32")


def detect_mode(log_path, mode):
    text = log_path.read_text(errors="replace") if log_path.exists() else ""
    if mode == "true":
        path = "eager: disabled torch.compile/CUDAGraphs" if "Enforce eager set" in text else "eager: evidence missing"
    else:
        path = "ACL Graph/compile replay evidence" if ("Replaying aclgraph" in text or "AscendCompiler" in text) else "compile/ACL Graph evidence missing"
    return path


def one_run(path):
    parsed = parse_dir_name(path)
    if not parsed:
        return None
    scenario, mode, strict32 = parsed
    bench_path = path / "benchmark/random-online.json"
    if not bench_path.exists():
        return None
    bench = json.loads(bench_path.read_text())
    request = path / "parsed/request.csv"
    batch = path / "parsed/batch.csv"
    kv = path / "parsed/kvcache.csv"
    request_summaries = {
        key: summarize(read_csv_numbers(request, key))
        for key in [
            "execution_time(ms)",
            "queue_wait_time(ms)",
            "first_token_latency(ms)",
            "cache_hit_rate",
        ]
    }
    batch_duration = read_csv_numbers(batch, "during_time(ms)")
    prefill = []
    decode = []
    framework = []
    decode_model_exec = []
    if batch.exists():
        with batch.open(newline="") as stream:
            for row in csv.DictReader(stream):
                value = finite(row.get("during_time(ms)"))
                if value is None:
                    continue
                if row.get("name") == "batchFrameworkProcessing":
                    framework.append(value)
                if row.get("name") == "modelExec" and "Decode" in row.get("batch_type", ""):
                    decode_model_exec.append(value)
                if row.get("batch_type") == "Prefill":
                    prefill.append(value)
                elif row.get("batch_type") == "Decode":
                    decode.append(value)
    slices = slice_stats(path / "parsed/profiler.db")
    model_exec = slices.get("modelExec", {})
    row = {
        "run": path.name,
        "scenario": scenario,
        "mode": mode,
        "strict32": strict32,
        "num_prompts": int(bench.get("num_prompts", 0)),
        "completed": int(bench.get("completed", 0)),
        "failed": int(bench.get("failed", 0)),
        "duration_s": finite(bench.get("duration")),
        "request_throughput": finite(bench.get("request_throughput")),
        "output_throughput": finite(bench.get("output_throughput")),
        "total_token_throughput": finite(bench.get("total_token_throughput")),
        "mean_ttft_ms": finite(bench.get("mean_ttft_ms")),
        "p99_ttft_ms": finite(bench.get("p99_ttft_ms")),
        "mean_tpot_ms": finite(bench.get("mean_tpot_ms")),
        "p99_tpot_ms": finite(bench.get("p99_tpot_ms")),
        "mean_itl_ms": finite(bench.get("mean_itl_ms")),
        "p99_itl_ms": finite(bench.get("p99_itl_ms")),
        "queue_p50_ms": request_summaries["queue_wait_time(ms)"]["p50"],
        "queue_p95_ms": request_summaries["queue_wait_time(ms)"]["p95"],
        "queue_p99_ms": request_summaries["queue_wait_time(ms)"]["p99"],
        "request_exec_p95_ms": request_summaries["execution_time(ms)"]["p95"],
        "request_exec_p99_ms": request_summaries["execution_time(ms)"]["p99"],
        "cache_hit_p95": request_summaries["cache_hit_rate"]["p95"],
        "batch_p95_ms": summarize(batch_duration)["p95"],
        "batch_p99_ms": summarize(batch_duration)["p99"],
        "batch_framework_p95_ms": summarize(framework)["p95"],
        "batch_framework_p99_ms": summarize(framework)["p99"],
        "decode_model_exec_p95_ms": summarize(decode_model_exec)["p95"],
        "decode_model_exec_p99_ms": summarize(decode_model_exec)["p99"],
        "prefill_batch_p95_ms": summarize(prefill)["p95"],
        "decode_batch_p50_ms": summarize(decode)["p50"],
        "decode_batch_p95_ms": summarize(decode)["p95"],
        "model_exec_avg_ms": model_exec.get("mean"),
        "model_exec_p99_ms": model_exec.get("p99"),
        "model_exec_max_ms": model_exec.get("max"),
        "trace_present": (path / "parsed/chrome_tracing.json").exists(),
        "request_csv_present": request.exists(),
        "batch_csv_present": batch.exists(),
        "kvcache_csv_present": kv.exists(),
        "mode_evidence": detect_mode(path / "logs/vllm.log", mode),
        "slice_stats": slices,
    }
    return row


def primary_rows(rows):
    """Use the strict 32-request random eager=true row for A/B comparison."""
    selected = {}
    for row in rows:
        key = (row["scenario"], row["mode"])
        if key not in selected or row["strict32"]:
            selected[key] = row
    return [selected[key] for key in sorted(selected)]


def ratio(a, b):
    return a / b if a is not None and b not in (None, 0) else None


def ab_rows(rows):
    by = {(r["scenario"], r["mode"]): r for r in rows}
    result = []
    for scenario in SCENARIO_ORDER:
        true = by.get((scenario, "true"))
        false = by.get((scenario, "false"))
        if not true or not false:
            continue
        result.append({
            "scenario": scenario,
            "true_run": true["run"],
            "false_run": false["run"],
            "true_prompts": true["completed"],
            "false_prompts": false["completed"],
            "true_output_tok_s": true["output_throughput"],
            "false_output_tok_s": false["output_throughput"],
            "false_vs_true_output_ratio": ratio(false["output_throughput"], true["output_throughput"]),
            "true_mean_tpot_ms": true["mean_tpot_ms"],
            "false_mean_tpot_ms": false["mean_tpot_ms"],
            "false_vs_true_tpot_ratio": ratio(false["mean_tpot_ms"], true["mean_tpot_ms"]),
            "true_p99_ttft_ms": true["p99_ttft_ms"],
            "false_p99_ttft_ms": false["p99_ttft_ms"],
            "false_vs_true_p99_ttft_ratio": ratio(false["p99_ttft_ms"], true["p99_ttft_ms"]),
            "true_model_exec_avg_ms": true["model_exec_avg_ms"],
            "false_model_exec_avg_ms": false["model_exec_avg_ms"],
            "false_vs_true_model_exec_ratio": ratio(false["model_exec_avg_ms"], true["model_exec_avg_ms"]),
            "true_queue_p99_ms": true["queue_p99_ms"],
            "false_queue_p99_ms": false["queue_p99_ms"],
            "false_vs_true_queue_p99_ratio": ratio(false["queue_p99_ms"], true["queue_p99_ms"]),
        })
    return result


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    fields = list(rows[0])
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def load_font(size, bold=False):
    candidates = [
        "/workspace/vllm-hust-docs/presentations/vllm-hust-introduction/fonts/NotoSansCJKsc-Bold.otf" if bold else "/workspace/vllm-hust-docs/presentations/vllm-hust-introduction/fonts/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def grouped_bar(path, title, labels, series, unit, percent=False):
    if Image is None:
        return
    width, height = 1800, 1000
    im = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(im)
    title_font = load_font(34, True)
    label_font = load_font(20)
    small_font = load_font(17)
    draw.text((50, 30), title, fill="#102a43", font=title_font)
    draw.text((50, 82), "数据来源：本次运行目录下的 benchmark、parsed/request.csv、parsed/profiler.db", fill="#52606d", font=small_font)
    left, top, right, bottom = 110, 160, 1740, 870
    max_value = max(v for _, values, _ in series for v in values if v is not None) or 1
    max_value *= 1.18
    for tick in range(5):
        y = bottom - (bottom - top) * tick / 4
        draw.line((left, y, right, y), fill="#e6e6e6", width=2)
        value = max_value * tick / 4
        text = f"{value:.0f}%" if percent else f"{value:.0f}"
        draw.text((25, y - 12), text, fill="#52606d", font=small_font)
    colors = ["#2f80ed", "#eb5757", "#27ae60", "#f2c94c"]
    group_width = (right - left) / len(labels)
    bar_width = min(55, group_width / (len(series) + 1))
    for i, label in enumerate(labels):
        center = left + group_width * (i + 0.5)
        total = len(series) * bar_width
        start = center - total / 2
        for j, (name, values, color) in enumerate(series):
            value = values[i]
            if value is None:
                continue
            x0 = start + j * bar_width + 5
            x1 = x0 + bar_width - 10
            y = bottom - (bottom - top) * value / max_value
            draw.rectangle((x0, y, x1, bottom), fill=color)
            draw.text((x0 - 3, max(top, y - 28)), f"{value:.1f}", fill="#102a43", font=small_font)
        draw.text((center - max(30, len(label) * 7), bottom + 14), label, fill="#243b53", font=label_font)
    for j, (name, _, color) in enumerate(series):
        x = 120 + j * 230
        draw.rectangle((x, 120, x + 20, 140), fill=color)
        draw.text((x + 28, 115), name, fill="#243b53", font=small_font)
    im.save(path)


def make_figures(ab, rows):
    FIGURES.mkdir(parents=True, exist_ok=True)
    labels = [s.replace("-online", "") for s in SCENARIO_ORDER]
    by = {r["scenario"]: r for r in ab}
    grouped_bar(
        FIGURES / "throughput-ab.png",
        "六个在线场景：enforce_eager 吞吐 A/B",
        labels,
        [("eager=true", [by.get(s, {}).get("true_output_tok_s") for s in SCENARIO_ORDER], "#2f80ed"),
         ("eager=false", [by.get(s, {}).get("false_output_tok_s") for s in SCENARIO_ORDER], "#eb5757")],
        "output tok/s",
    )
    grouped_bar(
        FIGURES / "latency-ab.png",
        "六个在线场景：TPOT 与 TTFT P99 的 eager A/B",
        labels,
        [("true TPOT", [by.get(s, {}).get("true_mean_tpot_ms") for s in SCENARIO_ORDER], "#2f80ed"),
         ("false TPOT", [by.get(s, {}).get("false_mean_tpot_ms") for s in SCENARIO_ORDER], "#eb5757"),
         ("true TTFT P99", [by.get(s, {}).get("true_p99_ttft_ms") / 1000 if by.get(s, {}).get("true_p99_ttft_ms") else None for s in SCENARIO_ORDER], "#27ae60"),
         ("false TTFT P99", [by.get(s, {}).get("false_p99_ttft_ms") / 1000 if by.get(s, {}).get("false_p99_ttft_ms") else None for s in SCENARIO_ORDER], "#f2c94c")],
        "ms；TTFT P99 按秒显示",
    )
    primary = {(r["scenario"], r["mode"]): r for r in primary_rows(rows)}
    slice_names = ["computing_logits", "sample", "_prepare_inputs", "batchFrameworkProcessing", "_update_states"]
    values_true, values_false = [], []
    for name in slice_names:
        def avg_share(mode):
            shares = []
            for scenario in SCENARIO_ORDER:
                row = primary[(scenario, mode)]
                total = row["slice_stats"].get("modelExec", {}).get("mean", 0) * row["slice_stats"].get("modelExec", {}).get("count", 0)
                part = row["slice_stats"].get(name, {}).get("mean", 0) * row["slice_stats"].get(name, {}).get("count", 0)
                if total:
                    shares.append(100 * part / total)
            return sum(shares) / len(shares) if shares else None
        values_true.append(avg_share("true"))
        values_false.append(avg_share("false"))
    grouped_bar(
        FIGURES / "host-and-slice-hotspots.png",
        "Profiler 热点：辅助 slice 占 modelExec 总时长的平均比例",
        ["logits", "sample", "prepare", "batch", "states"],
        [("eager=true", values_true, "#2f80ed"), ("eager=false", values_false, "#eb5757")],
        "percent",
        percent=True,
    )


def main():
    global ROOT, ANALYSIS, FIGURES
    parser = argparse.ArgumentParser(description="Analyze one vLLM profiling run directory.")
    parser.add_argument("--data-root", type=Path, default=ROOT, help="one run root containing scenario-eager-* directories")
    parser.add_argument("--output-dir", type=Path, default=None, help="analysis output directory; defaults to <data-root>/analysis")
    args = parser.parse_args()
    ROOT = args.data_root.expanduser().resolve()
    ANALYSIS = args.output_dir.expanduser().resolve() if args.output_dir else ROOT / "analysis"
    FIGURES = ANALYSIS.parent / "figures"
    rows = [one_run(path) for path in sorted(ROOT.iterdir()) if path.is_dir()]
    rows = [row for row in rows if row]
    primary = primary_rows(rows)
    ab = ab_rows(primary)
    ANALYSIS.mkdir(parents=True, exist_ok=True)
    serializable_rows = [{k: v for k, v in row.items() if k != "slice_stats"} for row in rows]
    write_csv(ANALYSIS / "metrics-all-runs.csv", serializable_rows)
    write_csv(ANALYSIS / "metrics-primary-12-runs.csv", [{k: v for k, v in row.items() if k != "slice_stats"} for row in primary])
    write_csv(ANALYSIS / "eager-ab-comparison.csv", ab)
    (ANALYSIS / "metrics-all-runs.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    (ANALYSIS / "eager-ab-comparison.json").write_text(json.dumps(ab, indent=2, ensure_ascii=False))
    make_figures(ab, rows)
    print(json.dumps({"all_runs": len(rows), "primary_runs": len(primary), "ab_pairs": len(ab), "analysis": str(ANALYSIS), "figures": str(FIGURES)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
