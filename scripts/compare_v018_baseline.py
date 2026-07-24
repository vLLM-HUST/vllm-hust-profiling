#!/usr/bin/env python3
"""Compare the 2026-07-23 measurements with official v0.18.0 manifests."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    Image = ImageDraw = ImageFont = None


TASK = Path(__file__).resolve().parents[1]
DATA = TASK / "outputs/top10-all-scenes-20260723"
BASE = Path("/workspace/vllm-hust-benchmark")
SCENARIOS = ["random-online", "prefix-repetition-online", "agent-research-online", "sharegpt-online"]
BASE_FILES = {
    "random-online": "official-ascend-jan-2026-v0.18.0-random-online-qwen25-14b-910b2",
    "prefix-repetition-online": "official-ascend-jan-2026-v0.18.0-prefix-repetition-online-qwen25-14b-910b2",
    "agent-research-online": "official-ascend-jan-2026-v0.18.0-agent-research-online-qwen25-14b-910b2",
    "sharegpt-online": "official-ascend-jan-2026-v0.18.0-sharegpt-online-qwen25-14b-910b2",
}


def load_current():
    rows = list(csv.DictReader((DATA / "analysis/metrics-primary-12-runs.csv").open()))
    result = {}
    for row in rows:
        result[(row["scenario"], row["mode"])] = row
    return result


def load_baseline(scenario):
    name = BASE_FILES[scenario]
    path = BASE / "submissions" / name / "run_leaderboard.json"
    data = json.loads(path.read_text())
    workload = data["workload"]
    metrics = data["metrics"]
    return {
        "baseline_run": name,
        "baseline_path": str(path),
        "baseline_engine": data.get("engine_version"),
        "baseline_vllm_commit": data.get("metadata", {}).get("runtime_provenance", {}).get("engine", {}).get("commit"),
        "baseline_ascend_commit": data.get("metadata", {}).get("runtime_provenance", {}).get("plugin", {}).get("commit"),
        "baseline_model": data.get("model", {}).get("parameters"),
        "baseline_chip": data.get("hardware", {}).get("chip_model"),
        "baseline_prompts": workload.get("input_length"),
        "baseline_num_prompts": data.get("same_spec", {}).get("resolved_client_parameters", {}).get("num_prompts", 200),
        "baseline_input_len": workload.get("input_length"),
        "baseline_output_len": workload.get("output_length"),
        "baseline_request_rate": data.get("same_spec", {}).get("resolved_client_parameters", {}).get("request_rate", 1),
        "baseline_throughput_tok_s": metrics.get("throughput_tps"),
        "baseline_ttft_ms": metrics.get("ttft_ms"),
        "baseline_tpot_ms": metrics.get("tbt_ms"),
        "baseline_error_rate": metrics.get("error_rate"),
    }


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compare():
    current = load_current()
    rows = []
    for scenario in SCENARIOS:
        base = load_baseline(scenario)
        for mode in ("true", "false"):
            cur = current[(scenario, mode)]
            throughput = number(cur["output_throughput"])
            ttft = number(cur["mean_ttft_ms"])
            tpot = number(cur["mean_tpot_ms"])
            bthroughput = number(base["baseline_throughput_tok_s"])
            bttft = number(base["baseline_ttft_ms"])
            btpot = number(base["baseline_tpot_ms"])
            rows.append({
                "scenario": scenario,
                "mode": mode,
                "current_run": cur["run"],
                "current_completed": cur["completed"],
                "current_dtype_observed": "bfloat16",
                "current_output_tok_s": throughput,
                "current_mean_ttft_ms": ttft,
                "current_mean_tpot_ms": tpot,
                "baseline_output_tok_s": bthroughput,
                "baseline_ttft_ms": bttft,
                "baseline_tpot_ms": btpot,
                "throughput_ratio_current_vs_baseline": throughput / bthroughput,
                "throughput_change_pct": (throughput / bthroughput - 1) * 100,
                "ttft_change_pct": (ttft / bttft - 1) * 100,
                "tpot_change_pct": (tpot / btpot - 1) * 100,
                "baseline_run": base["baseline_run"],
                "baseline_path": base["baseline_path"],
                "baseline_vllm_commit": base["baseline_vllm_commit"],
                "baseline_ascend_commit": base["baseline_ascend_commit"],
                "baseline_model": base["baseline_model"],
                "baseline_chip": base["baseline_chip"],
                "baseline_input_len": base["baseline_input_len"],
                "baseline_output_len": base["baseline_output_len"],
                "baseline_request_rate": base["baseline_request_rate"],
                "baseline_error_rate": base["baseline_error_rate"],
            })
    return rows


def write_csv(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def font(size, bold=False):
    candidates = [
        "/workspace/vllm-hust-docs/presentations/vllm-hust-introduction/fonts/NotoSansCJKsc-Bold.otf" if bold else "/workspace/vllm-hust-docs/presentations/vllm-hust-introduction/fonts/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def make_figure(rows):
    if Image is None:
        return
    out = DATA / "figures/v018-baseline-comparison.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    width, height = 1900, 1100
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.text((50, 30), "当前测量 vs 官方 v0.18.0 基线（单芯片 910B2）", fill="#102a43", font=font(34, True))
    draw.text((50, 82), "吞吐按基线=100%归一化；TTFT/TPOT 为当前 mean 与官方 TTFT/TBT 的比值", fill="#52606d", font=font(19))
    left, top, right, bottom = 125, 220, 1800, 930
    labels = ["random", "prefix", "agent", "sharegpt"]
    group_width = (right - left) / len(labels)
    by = {(r["scenario"], r["mode"]): r for r in rows}
    panels = [
        ("吞吐 / 基线", lambda s, m: float(by[(s, m)]["throughput_ratio_current_vs_baseline"]) * 100, False),
        ("TPOT / 基线", lambda s, m: float(by[(s, m)]["current_mean_tpot_ms"]) / float(by[(s, m)]["baseline_tpot_ms"]) * 100, False),
        ("TTFT / 基线（对数轴）", lambda s, m: float(by[(s, m)]["current_mean_ttft_ms"]) / float(by[(s, m)]["baseline_ttft_ms"]) * 100, True),
    ]
    panel_lefts = [125, 680, 1235]
    colors = {"baseline": "#27ae60", "true": "#2f80ed", "false": "#eb5757"}
    for (title, getter, log_scale), x0 in zip(panels, panel_lefts):
        pw = 480
        draw.text((x0, 150), title, fill="#243b53", font=font(24, True))
        raw_values = [100] + [getter(s, m) for s in SCENARIOS for m in ("true", "false")]
        maxv = max(raw_values) * 1.12
        minv = 50 if log_scale else 0
        if log_scale:
            maxv = max(maxv, 100000)
            ticks = [100, 1000, 10000, 100000]
        else:
            maxv = max(maxv, 120)
            ticks = [maxv * tick / 4 for tick in range(5)]
        for value in ticks:
            if log_scale and not (minv <= value <= maxv):
                continue
            if log_scale:
                frac = (math.log10(value) - math.log10(minv)) / (math.log10(maxv) - math.log10(minv))
            else:
                frac = value / maxv
            y = bottom - (bottom - top) * frac
            draw.line((x0, y, x0 + pw, y), fill="#e6e6e6", width=2)
            draw.text((x0 - 60, y - 10), f"{value:.0f}%", fill="#52606d", font=font(15))
        gw = pw / len(labels)
        bw = 25
        for i, (scenario, label) in enumerate(zip(SCENARIOS, labels)):
            center = x0 + gw * (i + 0.5)
            vals = [("baseline", 100), ("true", getter(scenario, "true")), ("false", getter(scenario, "false"))]
            start = center - len(vals) * bw / 2
            for j, (mode, value) in enumerate(vals):
                bx = start + j * bw
                if log_scale:
                    frac = (math.log10(max(value, minv)) - math.log10(minv)) / (math.log10(maxv) - math.log10(minv))
                else:
                    frac = value / maxv
                byy = bottom - (bottom - top) * frac
                draw.rectangle((bx, byy, bx + bw - 5, bottom), fill=colors[mode])
                draw.text((bx - 3, max(top, byy - 22)), f"{value:.0f}", fill="#102a43", font=font(13))
            draw.text((center - max(25, len(label) * 6), bottom + 14), label, fill="#243b53", font=font(15))
        for j, (name, color) in enumerate((("baseline", colors["baseline"]), ("eager=true", colors["true"]), ("eager=false", colors["false"]))):
            lx = x0 + j * 150
            draw.rectangle((lx, 108, lx + 16, 124), fill=color)
            draw.text((lx + 22, 104), name, fill="#243b53", font=font(14))
    image.save(out)


def main():
    rows = compare()
    outdir = DATA / "analysis"
    outdir.mkdir(parents=True, exist_ok=True)
    write_csv(outdir / "v018-baseline-comparison.csv", rows)
    (outdir / "v018-baseline-comparison.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    make_figure(rows)
    print(json.dumps({"rows": len(rows), "scenarios": SCENARIOS, "output": str(outdir / "v018-baseline-comparison.csv")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
