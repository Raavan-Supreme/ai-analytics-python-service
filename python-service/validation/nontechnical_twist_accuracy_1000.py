#!/usr/bin/env python3
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.intents import default_fallback_result


DATASET_COUNT = 1000
ROWS_PER_DATASET = 1500
SEED = 42

TWISTED_QUESTIONS = {
    "starts_with_10": [
        "select all records sstarts wtih 10",
        "give me rows where id starts from 10",
        "show records with id beginning 10",
        "i need data where order id prefix is 10",
        "plz list rows whose id starts with 10",
    ],
    "lookup_sarah": [
        "give all Sarah records",
        "show me rows for sarah",
        "sarah related data dikhao",
        "please list entries having name sarah",
        "fetch records where customer is sarah",
    ],
    "total_rows_count": [
        "how many rows are there",
        "count total records please",
        "dataset me total entries kitni",
        "tell me total row number",
        "what is full record count",
    ],
}


def _safe_missing_mask(series: pd.Series) -> pd.Series:
    as_text = series.astype(str).str.strip().str.lower()
    return series.isna() | as_text.eq("") | as_text.isin({"nan", "none", "null"})


def _make_dataset(seed: int, rows: int) -> tuple[pd.DataFrame, dict[str, int]]:
    rng = np.random.default_rng(seed)

    names = np.array(["Sarah", "John", "Emma", "Mike", "Ava", "Noah"], dtype=object)
    name_values = rng.choice(names, size=rows, replace=True)

    # Ensure there are enough Sarah rows each dataset.
    sarah_idx = rng.choice(rows, size=max(40, rows // 20), replace=False)
    name_values[sarah_idx] = "Sarah"

    base_ids = rng.integers(1000, 9999, size=rows)
    prefixes = rng.choice(np.array(["10", "20", "30", "40"], dtype=object), size=rows, replace=True)
    start10_idx = rng.choice(rows, size=max(60, rows // 10), replace=False)
    prefixes[start10_idx] = "10"
    order_ids = np.char.add(prefixes.astype(str), base_ids.astype(str)).astype(object)

    emails = np.char.add(np.char.lower(name_values.astype(str)), "@example.com").astype(object)
    missing_idx = rng.choice(rows, size=max(20, rows // 30), replace=False)
    emails[missing_idx] = None

    df = pd.DataFrame(
        {
            "OrderID": order_ids,
            "CustomerName": name_values,
            "Email": emails,
            "Amount": np.round(rng.gamma(shape=2.0, scale=50.0, size=rows), 2),
        }
    )

    expected = {
        "starts_with_10": int(df["OrderID"].astype(str).str.startswith("10").sum()),
        "lookup_sarah": int(df["CustomerName"].astype(str).str.lower().eq("sarah").sum()),
        "total_rows_count": int(len(df)),
        "missing_email_count": int(_safe_missing_mask(df["Email"]).sum()),
    }
    return df, expected


def _first_int(df: pd.DataFrame, summary: str) -> int | None:
    if not df.empty and len(df) == 1:
        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                return int(df[col].iloc[0])
    digits = ""
    for ch in summary:
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    return int(digits) if digits else None


def _extract_actual(intent: str, out_df: pd.DataFrame, summary: str) -> int:
    lower_cols = {str(c).lower(): str(c) for c in out_df.columns}

    if intent in {"starts_with_10", "lookup_sarah"}:
        if "matches" in lower_cols and not out_df.empty:
            return int(out_df[lower_cols["matches"]].iloc[0])
        return int(len(out_df))

    if intent == "total_rows_count":
        value = _first_int(out_df, summary)
        if value is not None:
            return value
        return int(len(out_df))

    value = _first_int(out_df, summary)
    if value is not None:
        return value
    return int(len(out_df))


def run_benchmark() -> dict:
    random.seed(SEED)

    total = 0
    passed = 0
    per_intent = {
        key: {"total": 0, "passed": 0}
        for key in TWISTED_QUESTIONS
    }

    started = time.perf_counter()

    for i in range(DATASET_COUNT):
        df, expected = _make_dataset(SEED + i * 17, ROWS_PER_DATASET)

        for intent, questions in TWISTED_QUESTIONS.items():
            for q in questions:
                out_df, summary = default_fallback_result(df, q, trace={})
                actual = _extract_actual(intent, out_df, summary)
                ok = int(actual) == int(expected[intent])

                total += 1
                per_intent[intent]["total"] += 1
                if ok:
                    passed += 1
                    per_intent[intent]["passed"] += 1

    elapsed = time.perf_counter() - started

    for intent in per_intent:
        t = per_intent[intent]["total"]
        p = per_intent[intent]["passed"]
        per_intent[intent]["accuracy"] = round((p / t) if t else 0.0, 4)

    overall = {
        "datasets": DATASET_COUNT,
        "rows_per_dataset": ROWS_PER_DATASET,
        "total_questions": total,
        "passed": passed,
        "failed": total - passed,
        "accuracy": round((passed / total) if total else 0.0, 4),
        "runtime_sec": round(elapsed, 3),
        "throughput_qps": round((total / elapsed) if elapsed > 0 else 0.0, 3),
    }

    return {
        "config": {
            "seed": SEED,
            "question_families": list(TWISTED_QUESTIONS.keys()),
            "questions_per_family": {k: len(v) for k, v in TWISTED_QUESTIONS.items()},
        },
        "overall": overall,
        "intent_metrics": per_intent,
    }


def main() -> None:
    report = run_benchmark()

    out_path = ROOT_DIR / "validation" / "reports" / "nontechnical_twist_accuracy_1000.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    overall = report["overall"]
    print("=== Non-Technical Twist Benchmark ===")
    print(f"Datasets: {overall['datasets']}")
    print(f"Questions: {overall['total_questions']}")
    print(f"Passed: {overall['passed']}")
    print(f"Failed: {overall['failed']}")
    print(f"Accuracy: {overall['accuracy']:.2%}")
    print(f"Runtime: {overall['runtime_sec']} sec")
    print(f"Throughput: {overall['throughput_qps']} q/s")

    print("\nPer-intent accuracy:")
    for intent, metrics in report["intent_metrics"].items():
        print(f"- {intent}: {metrics['accuracy']:.2%} ({metrics['passed']}/{metrics['total']})")

    print(f"\nSaved report: {out_path}")


if __name__ == "__main__":
    main()
