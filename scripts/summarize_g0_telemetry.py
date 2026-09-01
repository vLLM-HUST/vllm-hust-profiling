#!/usr/bin/env python3
"""Fail-closed summary of raw AscendStore G0 connector telemetry."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

REQUIRED_START = {
    "ts_ns",
    "pid",
    "thread_id",
    "layer",
    "request_ids",
    "connector_queue_depth",
    "connector_queue_wait_ns",
    "requested_materialization_bytes",
    "realized_materialization_bytes",
    "bytes_in_flight",
}
REQUIRED_FINISH = REQUIRED_START | {"transfer_result"}
REQUIRED_DECISION = {
    "ts_ns",
    "pid",
    "request_id",
    "external_hit_tokens",
    "minimum_retrieve_tokens",
    "decision",
}

MATERIALIZING_ARMS = {
    "no_control",
    "concurrency_cap",
    "request_token_bucket",
    "minimum_retrieve_threshold",
    "fixed_prefetch_depth",
    "materialization_paced_oracle",
}


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("no values")
    index = round((q / 100.0) * (len(ordered) - 1))
    return ordered[index]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, finish in sorted(intervals):
        if finish < start:
            raise ValueError("interval finishes before it starts")
        if not merged or start > merged[-1][1]:
            merged.append((start, finish))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], finish))
    return merged


def interval_duration_us(intervals: list[tuple[float, float]]) -> float:
    return sum(finish - start for start, finish in merge_intervals(intervals))


def interval_overlap_us(
    left: list[tuple[float, float]], right: list[tuple[float, float]]
) -> float:
    left, right = merge_intervals(left), merge_intervals(right)
    i = j = 0
    overlap = 0.0
    while i < len(left) and j < len(right):
        overlap += max(0.0, min(left[i][1], right[j][1]) - max(left[i][0], right[j][0]))
        if left[i][1] <= right[j][1]:
            i += 1
        else:
            j += 1
    return overlap


def summarize_compute_overlap(
    chrome_path: Path, starts: list[dict[str, Any]], finishes: list[dict[str, Any]]
) -> dict[str, Any]:
    if not all("wall_ts_ns" in row for row in starts + finishes):
        raise ValueError("connector telemetry lacks wall_ts_ns clock bridge")
    chrome = json.loads(chrome_path.read_text())
    compute_intervals = []
    for event in chrome:
        if event.get("ph") != "X" or event.get("name") != "modelExec":
            continue
        request_ids = [
            str(item.get("rid", ""))
            for item in event.get("args", {}).get("res_list", [])
        ]
        if not any("g0-" in request_id and "g0-warm-" not in request_id for request_id in request_ids):
            continue
        start_us = float(event["ts"])
        compute_intervals.append((start_us, start_us + float(event["dur"])))

    pending: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    transfer_intervals = []
    for row in sorted(starts + finishes, key=lambda item: int(item["ts_ns"])):
        key = (int(row["pid"]), int(row["thread_id"]))
        if row["event"] == "transfer_started":
            pending[key].append(row)
        else:
            if not pending[key]:
                raise ValueError(f"transfer finish without clock-paired start: {key}")
            start = pending[key].pop(0)
            transfer_intervals.append(
                (float(start["wall_ts_ns"]) / 1000.0, float(row["wall_ts_ns"]) / 1000.0)
            )
    if any(pending.values()):
        raise ValueError("unclosed wall-clock transfer interval")
    if not compute_intervals or not transfer_intervals:
        raise ValueError("no workload modelExec or transfer intervals for overlap")

    compute_us = interval_duration_us(compute_intervals)
    transfer_us = interval_duration_us(transfer_intervals)
    overlap_us = interval_overlap_us(compute_intervals, transfer_intervals)
    return {
        "definition": "wall-clock intersection of msServiceProfiler modelExec envelopes and connector transfer intervals",
        "compute_envelope_busy_ms": compute_us / 1000.0,
        "transfer_union_busy_ms": transfer_us / 1000.0,
        "overlap_ms": overlap_us / 1000.0,
        "transfer_overlap_ratio": overlap_us / transfer_us if transfer_us else 0.0,
        "compute_overlap_ratio": overlap_us / compute_us if compute_us else 0.0,
        "compute_intervals": len(compute_intervals),
        "transfer_intervals": len(transfer_intervals),
        "chrome_trace": {
            "path": str(chrome_path.resolve()),
            "bytes": chrome_path.stat().st_size,
            "sha256": sha256(chrome_path),
        },
    }


def is_workload_request_id(request_id: object) -> bool:
    value = str(request_id)
    return "g0-" in value and "g0-warm-" not in value


def summarize(
    path: Path,
    chrome_path: Path | None = None,
    expected_arm: str | None = None,
    expected_requests: int | None = None,
    expected_controls: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        raise ValueError("empty G0 connector telemetry")
    starts = [row for row in rows if row.get("event") == "transfer_started"]
    finishes = [row for row in rows if row.get("event") == "transfer_finished"]
    if expected_arm is not None:
        starts = [
            row
            for row in starts
            if any(is_workload_request_id(item) for item in row.get("request_ids", []))
        ]
        finishes = [
            row
            for row in finishes
            if any(is_workload_request_id(item) for item in row.get("request_ids", []))
        ]
    waits = [row for row in rows if row.get("event") == "layer_ready_wait_finished"]
    enqueues = [row for row in rows if row.get("event") == "layer_load_enqueued"]
    decisions = [
        row
        for row in rows
        if row.get("event") == "materialization_decision"
        and is_workload_request_id(row.get("request_id"))
    ]
    connector_configs = [
        row for row in rows if row.get("event") == "connector_config"
    ]

    decision_counts: dict[str, int] = defaultdict(int)
    decision_ids: set[str] = set()
    for decision in decisions:
        missing = REQUIRED_DECISION - decision.keys()
        if missing:
            raise ValueError(f"materialization_decision missing fields: {sorted(missing)}")
        decision_counts[str(decision["decision"])] += 1
        decision_ids.add(str(decision["request_id"]))

    if expected_arm is not None:
        if expected_arm not in MATERIALIZING_ARMS | {"always_recompute"}:
            raise ValueError(f"unsupported expected arm: {expected_arm}")
        if not decisions:
            raise ValueError(f"{expected_arm} lacks materialization decisions")
        if len(decisions) != len(decision_ids):
            raise ValueError("materialization decisions are not one-to-one with requests")
        if expected_requests is not None and len(decision_ids) != expected_requests:
            raise ValueError(
                f"decision activation mismatch: expected={expected_requests} "
                f"observed={len(decision_ids)}"
            )
        if expected_arm == "always_recompute":
            if set(decision_counts) != {"recompute_threshold"}:
                raise ValueError(
                    f"always_recompute activated unexpected decisions: {dict(decision_counts)}"
                )
            if starts or finishes or waits or enqueues:
                raise ValueError("always_recompute unexpectedly activated materialization")
        elif expected_arm == "minimum_retrieve_threshold":
            if not {"materialize", "recompute_threshold"}.issubset(decision_counts):
                raise ValueError(
                    "minimum_retrieve_threshold must activate both retrieve and recompute"
                )
        elif set(decision_counts) != {"materialize"}:
            raise ValueError(
                f"{expected_arm} activated unexpected decisions: {dict(decision_counts)}"
            )
        if expected_controls is not None:
            expected_threshold = int(expected_controls["minimum_retrieve_tokens"])
            observed_thresholds = {
                int(row["minimum_retrieve_tokens"]) for row in decisions
            }
            if observed_thresholds != {expected_threshold}:
                raise ValueError(
                    "runtime/benchmark minimum retrieve thresholds differ: "
                    f"runtime={sorted(observed_thresholds)} "
                    f"benchmark={expected_threshold}"
                )
            expected_depth = int(expected_controls["fixed_prefetch_depth"])
            observed_depths = {
                int(row["layerwise_prefetch_layers"])
                for row in connector_configs
                if row.get("use_layerwise") is True
            }
            if observed_depths != {expected_depth}:
                raise ValueError(
                    "runtime/benchmark prefetch depths differ: "
                    f"runtime={sorted(observed_depths)} benchmark={expected_depth}"
                )

    if expected_arm == "always_recompute":
        return {
            "schema_version": 1,
            "artifact": "g0-ascendstore-connector-summary",
            "raw": {
                "path": str(path.resolve()),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            },
            "activation": {
                "enqueue_events": 0,
                "transfer_starts": 0,
                "transfer_finishes": 0,
                "layer_ready_waits": 0,
                "request_ids": 0,
                "decision_request_ids": len(decision_ids),
                "decision_counts": dict(sorted(decision_counts.items())),
                "connector_config_events": len(connector_configs),
                "all_transfer_results_ok": True,
            },
            "materialization": {
                "requested_bytes": 0,
                "realized_bytes": 0,
                "peak_bytes_in_flight": 0,
                "peak_connector_queue_depth": 0,
                "connector_queue_wait_ms_p50": None,
                "connector_queue_wait_ms_p95": None,
                "layer_ready_wait_ms_p50": None,
                "layer_ready_wait_ms_p95": None,
                "transfer_busy_ms": 0.0,
            },
            "compute_transfer_overlap": None,
        }

    if not starts or not finishes or not waits or not enqueues:
        raise ValueError("missing enqueue, transfer, or layer-ready activation")
    checked_events = [(row, REQUIRED_START) for row in starts]
    checked_events.extend((row, REQUIRED_FINISH) for row in finishes)
    for event, required in checked_events:
        missing = required - event.keys()
        if missing:
            raise ValueError(f"{event.get('event')} missing fields: {sorted(missing)}")
    if len(starts) != len(finishes):
        raise ValueError(f"unclosed transfers: starts={len(starts)} finishes={len(finishes)}")
    by_stream: dict[tuple[int, int], int] = defaultdict(int)
    for row in sorted(starts + finishes, key=lambda item: int(item["ts_ns"])):
        key = (int(row["pid"]), int(row["thread_id"]))
        by_stream[key] += 1 if row["event"] == "transfer_started" else -1
        if by_stream[key] < 0:
            raise ValueError(f"finish without start on stream {key}")
    if any(value != 0 for value in by_stream.values()):
        raise ValueError("one or more transfer streams did not close")
    request_ids = {str(request_id) for row in starts for request_id in row["request_ids"]}
    if expected_arm is not None:
        materialize_ids = {
            str(row["request_id"])
            for row in decisions
            if row["decision"] == "materialize"
        }
        if request_ids != materialize_ids:
            raise ValueError(
                "materialization decision/transfer request IDs differ: "
                f"decisions={len(materialize_ids)} transfers={len(request_ids)}"
            )
    queue_wait_ms = [float(row["connector_queue_wait_ns"]) / 1e6 for row in starts]
    layer_wait_ms = [float(row["layer_ready_wait_ns"]) / 1e6 for row in waits]
    starts_ordered = sorted(starts, key=lambda row: (int(row["pid"]), int(row["thread_id"]), int(row["ts_ns"])))
    finishes_ordered = sorted(finishes, key=lambda row: (int(row["pid"]), int(row["thread_id"]), int(row["ts_ns"])))
    transfer_busy_ms = sum(
        max(0, int(finish["ts_ns"]) - int(start["ts_ns"])) / 1e6
        for start, finish in zip(starts_ordered, finishes_ordered)
    )
    result = {
        "schema_version": 1,
        "artifact": "g0-ascendstore-connector-summary",
        "raw": {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": sha256(path)},
        "activation": {
            "enqueue_events": len(enqueues),
            "transfer_starts": len(starts),
            "transfer_finishes": len(finishes),
            "layer_ready_waits": len(waits),
            "request_ids": len(request_ids),
            "decision_request_ids": len(decision_ids),
            "decision_counts": dict(sorted(decision_counts.items())),
            "connector_config_events": len(connector_configs),
            "all_transfer_results_ok": all(int(row["transfer_result"]) == 0 for row in finishes),
        },
        "materialization": {
            "requested_bytes": sum(int(row["requested_materialization_bytes"]) for row in starts),
            "realized_bytes": sum(int(row["realized_materialization_bytes"]) for row in finishes),
            "peak_bytes_in_flight": max(int(row["bytes_in_flight"]) for row in starts),
            "peak_connector_queue_depth": max(int(row["connector_queue_depth"]) for row in starts),
            "connector_queue_wait_ms_p50": percentile(queue_wait_ms, 50),
            "connector_queue_wait_ms_p95": percentile(queue_wait_ms, 95),
            "layer_ready_wait_ms_p50": percentile(layer_wait_ms, 50),
            "layer_ready_wait_ms_p95": percentile(layer_wait_ms, 95),
            "transfer_busy_ms": transfer_busy_ms,
        },
    }
    if chrome_path is not None:
        result["compute_transfer_overlap"] = summarize_compute_overlap(
            chrome_path, starts, finishes
        )
    return result


def summarize_resources(path: Path) -> dict[str, Any]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        raise ValueError("empty G0 resource telemetry")
    def numbers(field: str) -> list[float]:
        return [float(row[field]) for row in rows if isinstance(row.get(field), (int, float))]
    hbm, pss = numbers("hbm_used_bytes"), numbers("process_tree_pss_bytes")
    bandwidth, aicore = numbers("hbm_bandwidth_busy_pct"), numbers("aicore_busy_pct")
    if not hbm or not pss:
        raise ValueError("resource telemetry lacks HBM or process PSS")
    return {
        "raw": {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": sha256(path)},
        "samples": len(rows),
        "hbm_peak_bytes": int(max(hbm)),
        "dram_process_tree_pss_peak_bytes": int(max(pss)),
        "hbm_bandwidth_busy_pct_p95": percentile(bandwidth, 95) if bandwidth else None,
        "aicore_busy_pct_p95": percentile(aicore, 95) if aicore else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--resources", type=Path)
    parser.add_argument("--chrome-trace", type=Path)
    parser.add_argument("--expected-arm")
    parser.add_argument("--benchmark-summary", type=Path)
    args = parser.parse_args()
    expected_requests = None
    expected_controls = None
    if args.benchmark_summary is not None:
        benchmark = json.loads(args.benchmark_summary.read_text())
        expected_requests = int(benchmark["requests"])
        expected_controls = benchmark["controls"]
        if args.expected_arm is not None and benchmark["arm"] != args.expected_arm:
            raise ValueError(
                f"benchmark arm {benchmark['arm']} != expected {args.expected_arm}"
            )
    result = summarize(
        args.input,
        args.chrome_trace,
        expected_arm=args.expected_arm,
        expected_requests=expected_requests,
        expected_controls=expected_controls,
    )
    if args.resources is not None:
        result["resources"] = summarize_resources(args.resources)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
