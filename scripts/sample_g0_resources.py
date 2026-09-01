#!/usr/bin/env python3
"""Sample NPU HBM/bandwidth and process-tree PSS into raw JSONL."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path


def descendants(root: int) -> set[int]:
    found, pending = {root}, [root]
    while pending:
        pid = pending.pop()
        try:
            text = Path(f"/proc/{pid}/task/{pid}/children").read_text()
        except OSError:
            continue
        for value in text.split():
            child = int(value)
            if child not in found:
                found.add(child)
                pending.append(child)
    return found


def process_tree_pss_bytes(root: int) -> int:
    total_kib = 0
    for pid in descendants(root):
        try:
            for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines():
                if line.startswith("Pss:"):
                    total_kib += int(line.split()[1])
                    break
        except OSError:
            pass
    return total_kib * 1024


def command(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=False).stdout


def npu_memory(device: int) -> tuple[int | None, int | None]:
    lines = command("npu-smi", "info").splitlines()
    for index, line in enumerate(lines[:-1]):
        fields = [part.strip() for part in line.split("|")]
        if len(fields) > 2 and re.match(rf"{device}\s+910", fields[1]):
            next_fields = [part.strip() for part in lines[index + 1].split("|")]
            matches = re.findall(r"(\d+)\s*/\s*(\d+)", " ".join(next_fields))
            if matches:
                used, capacity = matches[-1]
                return int(used) * 1024**2, int(capacity) * 1024**2
    return None, None


def usage(device: int) -> dict[str, int]:
    text = command("npu-smi", "info", "-t", "usages", "-i", str(device), "-c", "0")
    values = {}
    for key, name in (
        ("Aicore Usage Rate", "aicore_busy_pct"),
        ("HBM Bandwidth Usage Rate", "hbm_bandwidth_busy_pct"),
        ("NPU Utilization", "npu_utilization_pct"),
    ):
        match = re.search(rf"{re.escape(key)}\(%\)\s*:\s*(\d+)", text)
        if match:
            values[name] = int(match.group(1))
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", type=int, required=True)
    parser.add_argument("--root-pid", type=int, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--interval", type=float, default=0.2)
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", buffering=1) as destination:
        while Path(f"/proc/{args.root_pid}").exists():
            hbm_used, hbm_capacity = npu_memory(args.device)
            row = {
                "ts_ns": time.perf_counter_ns(),
                "wall_time_ns": time.time_ns(),
                "root_pid": args.root_pid,
                "process_tree_pss_bytes": process_tree_pss_bytes(args.root_pid),
                "hbm_used_bytes": hbm_used,
                "hbm_capacity_bytes": hbm_capacity,
                **usage(args.device),
            }
            destination.write(json.dumps(row, sort_keys=True) + "\n")
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
