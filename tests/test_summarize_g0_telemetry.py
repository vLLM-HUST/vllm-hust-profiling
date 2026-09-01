import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "summarize_g0_telemetry.py"
SPEC = importlib.util.spec_from_file_location("summarize_g0", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_summary_requires_and_closes_materialization_events(tmp_path: Path) -> None:
    raw = tmp_path / "telemetry.jsonl"
    common = {
        "pid": 1,
        "thread_id": 2,
        "layer": 0,
        "request_ids": ["request-1"],
        "connector_queue_depth": 3,
        "connector_queue_wait_ns": 2_000_000,
        "requested_materialization_bytes": 4096,
        "realized_materialization_bytes": 4096,
    }
    rows = [
        {"event": "layer_load_enqueued", "ts_ns": 1, "wall_ts_ns": 1_000_000, "layer": 0, "connector_queue_depth": 3},
        {"event": "transfer_started", "ts_ns": 2, "wall_ts_ns": 2_000_000, "bytes_in_flight": 4096, **common},
        {"event": "transfer_finished", "ts_ns": 3, "wall_ts_ns": 4_000_000, "bytes_in_flight": 0, "transfer_result": 0, **common},
        {"event": "layer_ready_wait_finished", "ts_ns": 4, "layer": 0, "layer_ready_wait_ns": 1_000_000, "wait_succeeded": True},
    ]
    raw.write_text("".join(json.dumps(row) + "\n" for row in rows))
    chrome = tmp_path / "chrome.json"
    chrome.write_text(
        json.dumps(
            [
                {
                    "name": "modelExec",
                    "ph": "X",
                    "ts": 1_000.0,
                    "dur": 2_000.0,
                    "args": {"res_list": [{"rid": "cmpl-g0-request-1"}]},
                }
            ]
        )
    )
    result = MODULE.summarize(raw, chrome)
    assert result["activation"]["all_transfer_results_ok"] is True
    assert result["materialization"]["requested_bytes"] == 4096
    assert result["materialization"]["peak_connector_queue_depth"] == 3
    overlap = result["compute_transfer_overlap"]
    assert overlap["transfer_union_busy_ms"] == 2.0
    assert overlap["overlap_ms"] == 1.0
    assert overlap["transfer_overlap_ratio"] == 0.5


def test_always_recompute_requires_decisions_and_zero_transfers(tmp_path: Path) -> None:
    raw = tmp_path / "telemetry.jsonl"
    rows = [
        {
            "event": "materialization_decision",
            "ts_ns": index,
            "pid": 1,
            "request_id": request_id,
            "external_hit_tokens": 128,
            "minimum_retrieve_tokens": 512,
            "decision": "recompute_threshold",
        }
        for index, request_id in enumerate(("cmpl-g0-a", "cmpl-g0-b"), 1)
    ]
    raw.write_text("".join(json.dumps(row) + "\n" for row in rows))
    result = MODULE.summarize(
        raw,
        expected_arm="always_recompute",
        expected_requests=2,
    )
    assert result["activation"]["decision_request_ids"] == 2
    assert result["activation"]["transfer_starts"] == 0
    assert result["materialization"]["requested_bytes"] == 0
    assert result["compute_transfer_overlap"] is None


def test_threshold_matches_materialize_decisions_to_transfers(tmp_path: Path) -> None:
    raw = tmp_path / "telemetry.jsonl"
    common = {
        "pid": 1,
        "thread_id": 2,
        "layer": 0,
        "request_ids": ["cmpl-g0-load"],
        "connector_queue_depth": 0,
        "connector_queue_wait_ns": 10,
        "requested_materialization_bytes": 4096,
        "realized_materialization_bytes": 4096,
    }
    rows = [
        {
            "event": "materialization_decision",
            "ts_ns": 1,
            "pid": 1,
            "request_id": "cmpl-g0-load",
            "external_hit_tokens": 256,
            "minimum_retrieve_tokens": 256,
            "decision": "materialize",
        },
        {
            "event": "materialization_decision",
            "ts_ns": 2,
            "pid": 1,
            "request_id": "cmpl-g0-recompute",
            "external_hit_tokens": 128,
            "minimum_retrieve_tokens": 256,
            "decision": "recompute_threshold",
        },
        {
            "event": "layer_load_enqueued",
            "ts_ns": 3,
            "layer": 0,
            "connector_queue_depth": 0,
        },
        {"event": "transfer_started", "ts_ns": 4, "bytes_in_flight": 4096, **common},
        {
            "event": "transfer_finished",
            "ts_ns": 5,
            "bytes_in_flight": 0,
            "transfer_result": 0,
            **common,
        },
        {
            "event": "layer_ready_wait_finished",
            "ts_ns": 6,
            "layer": 0,
            "layer_ready_wait_ns": 10,
            "wait_succeeded": True,
        },
    ]
    raw.write_text("".join(json.dumps(row) + "\n" for row in rows))
    result = MODULE.summarize(
        raw,
        expected_arm="minimum_retrieve_threshold",
        expected_requests=2,
    )
    assert result["activation"]["decision_counts"] == {
        "materialize": 1,
        "recompute_threshold": 1,
    }
    assert result["activation"]["request_ids"] == 1
