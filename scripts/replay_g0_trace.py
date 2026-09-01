#!/usr/bin/env python3
"""Replay an immutable G0 trace and record per-request streaming latency."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any

import aiohttp


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    rank = (len(ordered) - 1) * q / 100
    low, high = math.floor(rank), math.ceil(rank)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - rank) + ordered[high] * (rank - low)


class ByteBudget:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.available = capacity
        self.condition = asyncio.Condition()

    async def acquire(self, amount: int) -> None:
        if amount > self.capacity:
            raise ValueError(f"request bytes {amount} exceed oracle budget {self.capacity}")
        async with self.condition:
            await self.condition.wait_for(lambda: self.available >= amount)
            self.available -= amount

    async def release(self, amount: int) -> None:
        async with self.condition:
            self.available += amount
            if self.available > self.capacity:
                raise RuntimeError("byte budget over-release")
            self.condition.notify_all()


class TokenBucket:
    def __init__(self, rate: float, capacity: float):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.updated = time.perf_counter()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self.lock:
                now = time.perf_counter()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                delay = (1 - self.tokens) / self.rate
            await asyncio.sleep(delay)


async def post_stream(
    session: aiohttp.ClientSession,
    url: str,
    model: str,
    row: dict[str, Any],
    request_id: str,
    on_first_token=None,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "prompt": row["prompt_token_ids"],
        "max_tokens": int(row["output_tokens"]),
        "temperature": 0,
        "stream": True,
        "stream_options": {"include_usage": True},
        "ignore_eos": True,
    }
    started = time.perf_counter()
    first_token_at: float | None = None
    finished_at = started
    fragments: list[str] = []
    completion_tokens = 0
    error = ""
    status = 0
    try:
        async with session.post(
            url,
            json=payload,
            headers={"X-Request-Id": request_id},
        ) as response:
            status = response.status
            if response.status != 200:
                error = (await response.text())[:1000]
            else:
                buffer = b""
                async for chunk in response.content.iter_any():
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        line = line.strip()
                        if not line.startswith(b"data:"):
                            continue
                        data = line[5:].strip()
                        if data == b"[DONE]":
                            continue
                        event = json.loads(data)
                        usage = event.get("usage")
                        if usage:
                            completion_tokens = int(usage.get("completion_tokens", completion_tokens))
                        choices = event.get("choices") or []
                        text = choices[0].get("text", "") if choices else ""
                        if text:
                            fragments.append(text)
                            if first_token_at is None:
                                first_token_at = time.perf_counter()
                                if on_first_token is not None:
                                    await on_first_token()
                finished_at = time.perf_counter()
    except Exception as exc:  # retained as raw correctness evidence
        error = f"{type(exc).__name__}: {exc}"
        finished_at = time.perf_counter()
    if completion_tokens == 0:
        completion_tokens = len(fragments)
    if first_token_at is None:
        first_token_at = finished_at
    e2e_ms = (finished_at - started) * 1000
    ttft_ms = (first_token_at - started) * 1000
    return {
        "http_status": status,
        "ok": status == 200 and not error and completion_tokens == int(row["output_tokens"]),
        "error": error,
        "ttft_ms": ttft_ms,
        "e2e_ms": e2e_ms,
        "tpot_ms": (e2e_ms - ttft_ms) / max(1, completion_tokens - 1),
        "output_tokens": completion_tokens,
        "output_sha256": hashlib.sha256("".join(fragments).encode()).hexdigest(),
    }


async def warm_cache(
    session: aiohttp.ClientSession, url: str, model: str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        prefix = row["prefix_token_ids"]
        key = hashlib.sha256(json.dumps(prefix).encode()).hexdigest()
        unique.setdefault(
            key,
            {"prompt_token_ids": prefix, "output_tokens": 1},
        )
    results = []
    for index, row in enumerate(unique.values()):
        results.append(
            await post_stream(session, url, model, row, f"g0-warm-{index:02d}")
        )
    await asyncio.sleep(1)
    return results


async def replay(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = read_jsonl(Path(args.trace))
    if not rows:
        raise ValueError("empty trace")
    timeout = aiohttp.ClientTimeout(total=args.timeout)
    connector = aiohttp.TCPConnector(limit=0)
    concurrency = asyncio.Semaphore(args.concurrency_cap)
    byte_budget = ByteBudget(args.oracle_byte_budget)
    token_bucket = TokenBucket(args.token_bucket_rate, args.token_bucket_capacity)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        warm = await warm_cache(session, args.url, args.model, rows) if args.warm_cache else []
        if any(not item["ok"] for item in warm):
            raise RuntimeError(f"cache warm-up failed: {warm}")
        epoch = time.perf_counter()

        async def run_one(row: dict[str, Any]) -> dict[str, Any]:
            await asyncio.sleep(max(0, epoch + float(row["arrival_s"]) - time.perf_counter()))
            arrival_actual = time.perf_counter()
            release_first_token = None
            held_bytes = 0
            if args.arm == "concurrency_cap":
                await concurrency.acquire()
            elif args.arm == "request_token_bucket":
                await token_bucket.acquire()
            elif args.arm == "materialization_paced_oracle":
                held_bytes = int(row["target_materialization_bytes"])
                await byte_budget.acquire(held_bytes)

                async def release_first_token() -> None:
                    nonlocal held_bytes
                    if held_bytes:
                        value, held_bytes = held_bytes, 0
                        await byte_budget.release(value)

            submitted = time.perf_counter()
            result = await post_stream(
                session,
                args.url,
                args.model,
                row,
                str(row["request_id"]),
                release_first_token,
            )
            if held_bytes:
                await byte_budget.release(held_bytes)
            if args.arm == "concurrency_cap":
                concurrency.release()
            return {
                "request_id": row["request_id"],
                "sequence_index": row["sequence_index"],
                "scheduled_arrival_s": row["arrival_s"],
                "actual_arrival_s": arrival_actual - epoch,
                "submit_s": submitted - epoch,
                "admission_wait_ms": (submitted - arrival_actual) * 1000,
                "reuse_prefix_tokens": row["reuse_prefix_tokens"],
                "target_materialization_bytes": row["target_materialization_bytes"],
                **result,
            }

        results = await asyncio.gather(*(run_one(row) for row in rows))
        duration_s = max(item["submit_s"] + item["e2e_ms"] / 1000 for item in results)
    results.sort(key=lambda item: item["sequence_index"])
    ttft = [item["ttft_ms"] for item in results]
    e2e = [item["e2e_ms"] for item in results]
    tpot = [item["tpot_ms"] for item in results]
    correct = sum(bool(item["ok"]) for item in results)
    summary = {
        "schema_version": 1,
        "arm": args.arm,
        "requests": len(results),
        "correct_requests": correct,
        "correctness_rate": correct / len(results),
        "duration_s": duration_s,
        "throughput_rps": len(results) / duration_s,
        "goodput_rps": correct / duration_s,
        "ttft_ms": {"p50": percentile(ttft, 50), "p95": percentile(ttft, 95), "p99": percentile(ttft, 99)},
        "e2e_ms": {"mean": statistics.mean(e2e), "p95": percentile(e2e, 95)},
        "tpot_ms": {"mean": statistics.mean(tpot), "p95": percentile(tpot, 95)},
        "warmup_requests": len(warm),
        "controls": {
            "concurrency_cap": args.concurrency_cap,
            "token_bucket_rate": args.token_bucket_rate,
            "token_bucket_capacity": args.token_bucket_capacity,
            "oracle_byte_budget": args.oracle_byte_budget,
            "minimum_retrieve_tokens": args.minimum_retrieve_tokens,
            "fixed_prefetch_depth": args.fixed_prefetch_depth,
        },
    }
    return results, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--arm",
        choices=(
            "no_control",
            "always_recompute",
            "concurrency_cap",
            "request_token_bucket",
            "minimum_retrieve_threshold",
            "fixed_prefetch_depth",
            "materialization_paced_oracle",
        ),
        required=True,
    )
    parser.add_argument("--concurrency-cap", type=int, default=4)
    parser.add_argument("--token-bucket-rate", type=float, default=8)
    parser.add_argument("--token-bucket-capacity", type=float, default=4)
    parser.add_argument("--oracle-byte-budget", type=int, default=31_850_496)
    parser.add_argument("--minimum-retrieve-tokens", type=int, default=256)
    parser.add_argument("--fixed-prefetch-depth", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--warm-cache", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    results, summary = asyncio.run(replay(args))
    output, summary_path = Path(args.output), Path(args.summary)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w") as destination:
        for row in results:
            destination.write(json.dumps(row, sort_keys=True) + "\n")
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return 0 if summary["correctness_rate"] == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
