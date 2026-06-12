#!/usr/bin/env python3
"""High-volume paraphrase stress test for NL rule-based intents.

What this does:
- Generates many synthetic datasets (supports large counts, e.g. 10,000)
- Uses 10 paraphrases per intent (non-technical wording variants)
- Verifies same-purpose paraphrases return the same deterministic answer
- Runs iterative, failure-focused re-testing loops
- Emits detailed report + optional failure corpus for later model tuning

Usage examples:
  source python-service/.venv/bin/activate
  python python-service/validation/paraphrase_stress_test.py --rows 200000 --datasets 10000 --rounds 1 --iterations 1
  python python-service/validation/paraphrase_stress_test.py --rows 200000 --datasets 10000 --rounds 1 --iterations 3 --strict-gate
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.intents import build_reasoning_trace, default_fallback_result


@dataclass(frozen=True)
class DatasetProfile:
    name: str
    id_col: str
    name_col: str
    email_col: str
    amount_col: str
    city_col: str
    date_col: str


@dataclass
class IntentAccumulator:
    total: int = 0
    passed: int = 0
    latencies_ms: list[float] = field(default_factory=list)


@dataclass
class ConsistencyAccumulator:
    actual_values: set[int] = field(default_factory=set)
    expected_values: set[int] = field(default_factory=set)
    total: int = 0
    passed: int = 0


@dataclass
class QuestionAccumulator:
    total: int = 0
    passed: int = 0


BASE_PROFILES: list[DatasetProfile] = [
    DatasetProfile(
        name="donations_style",
        id_col="order id",
        name_col="name",
        email_col="customer.email",
        amount_col="amount",
        city_col="city",
        date_col="created_on",
    ),
    DatasetProfile(
        name="retail_style",
        id_col="OrderID",
        name_col="CustomerName",
        email_col="EmailAddress",
        amount_col="TotalAmount",
        city_col="Region",
        date_col="PurchaseDate",
    ),
    DatasetProfile(
        name="transactions_style",
        id_col="transaction_id",
        name_col="buyer_name",
        email_col="buyer_email",
        amount_col="sale_value",
        city_col="zone",
        date_col="txn_date",
    ),
]


COLUMN_ALIAS_POOL: dict[str, list[str]] = {
    "id": [
        "order id",
        "OrderID",
        "transaction_id",
        "invoice_no",
        "record_key",
        "request_id",
        "ticket_id",
        "reference_no",
        "payment_id",
        "txn_ref",
        "purchase_code",
        "document_id",
    ],
    "name": [
        "name",
        "CustomerName",
        "buyer_name",
        "client_name",
        "person_name",
        "full_name",
        "contact_name",
        "customer_full_name",
        "registered_name",
    ],
    "email": [
        "customer.email",
        "EmailAddress",
        "buyer_email",
        "email",
        "contact_email",
        "mail_id",
        "primary_email",
        "user_email",
    ],
    "amount": [
        "amount",
        "TotalAmount",
        "sale_value",
        "net_amount",
        "gross_amount",
        "order_value",
        "payment_amount",
        "revenue_amount",
    ],
    "city": [
        "city",
        "Region",
        "zone",
        "location",
        "territory",
        "area",
        "branch_city",
        "state_region",
    ],
    "date": [
        "created_on",
        "PurchaseDate",
        "txn_date",
        "order_date",
        "created_at",
        "event_date",
        "booking_date",
        "transaction_date",
    ],
}


INTENT_PARAPHRASES: dict[str, list[str]] = {
    "lookup_sarah": [
        "give all Sarah records",
        "show me rows for Sarah",
        "can you pull Sarah data",
        "i need all entries with Sarah",
        "please list Sarah related rows",
        "show details for Sarah",
        "Sarah ke records dikhao",
        "where do we have Sarah in this data",
        "find records for Sarah only",
        "just Sarah data please",
    ],
    "starts_with_10": [
        "list out all records with starts order id from 10",
        "show rows where id starts with 10",
        "find entries whose order id begins 10",
        "i want records with ids starting 10",
        "which rows have id prefix 10",
        "show me id starting from 10",
        "please fetch records where order number starts 10",
        "all data where first digits of id are 10",
        "give rows with 10 at start of id",
        "records having id beginning with 10",
    ],
    "distinct_names_count": [
        "how many unique names are there",
        "count different customer names",
        "tell me number of distinct names",
        "how many separate names do we have",
        "unique people count please",
        "count unique customer entries",
        "how many different buyers",
        "give distinct name count",
        "number of non-duplicate names",
        "can you count unique names",
    ],
    "duplicate_id_count": [
        "how many duplicate order id rows are there",
        "count repeated id rows",
        "duplicate ids count please",
        "tell me repeated order number rows",
        "how many rows have duplicate ids",
        "count duplicate transaction ids",
        "do we have duplicate ids count them",
        "number of repeated id records",
        "count rows where id is duplicated",
        "how many duplicate id entries",
    ],
    "missing_email_count": [
        "count missing values in email",
        "how many email values are blank",
        "tell me email missing count",
        "number of empty emails",
        "email null count please",
        "how many records have no email",
        "count blank email rows",
        "show count of missing email ids",
        "emails not filled how many",
        "give me missing email total",
    ],
    "total_rows_count": [
        "how many rows are there",
        "count total records",
        "total row count please",
        "how many entries in dataset",
        "tell me full record count",
        "number of rows in this file",
        "count all data rows",
        "what is total records number",
        "dataset size in rows",
        "give me total entries count",
    ],
}


def _pick_alias(kind: str, dataset_idx: int, seed: int, fallback: str, used: set[str]) -> str:
    options = COLUMN_ALIAS_POOL[kind]
    rng = random.Random((seed * 1000003) + (dataset_idx * 9176) + hash(kind) % 1013)

    if rng.random() < 0.25:
        candidate = fallback
    else:
        candidate = options[rng.randrange(len(options))]

    cycle = dataset_idx // max(1, len(options))
    if cycle > 0 and rng.random() < 0.8:
        candidate = f"{candidate}_{cycle}"

    if candidate in used:
        suffix = (dataset_idx % 97) + 1
        candidate = f"{candidate}__{suffix}"

    used.add(candidate)
    return candidate


def build_profile_variant(dataset_idx: int, seed: int) -> DatasetProfile:
    if dataset_idx < len(BASE_PROFILES):
        return BASE_PROFILES[dataset_idx]

    base = BASE_PROFILES[dataset_idx % len(BASE_PROFILES)]
    used: set[str] = set()

    return DatasetProfile(
        name=f"{base.name}_d{dataset_idx + 1:05d}",
        id_col=_pick_alias("id", dataset_idx, seed, base.id_col, used),
        name_col=_pick_alias("name", dataset_idx, seed, base.name_col, used),
        email_col=_pick_alias("email", dataset_idx, seed, base.email_col, used),
        amount_col=_pick_alias("amount", dataset_idx, seed, base.amount_col, used),
        city_col=_pick_alias("city", dataset_idx, seed, base.city_col, used),
        date_col=_pick_alias("date", dataset_idx, seed, base.date_col, used),
    )


def _safe_missing_mask(series: pd.Series) -> pd.Series:
    as_text = series.astype(str).str.strip().str.lower()
    return series.isna() | as_text.eq("") | as_text.isin({"nan", "none", "null"})


def generate_dataset(profile: DatasetProfile, rows: int, seed: int) -> tuple[pd.DataFrame, dict[str, int]]:
    rng = np.random.default_rng(seed)

    base_names = np.array(
        [
            "Sarah",
            "John",
            "Emma",
            "Noah",
            "Ava",
            "Olivia",
            "Liam",
            "Mia",
            "Sophia",
            "Lucas",
            "Ethan",
            "Isla",
            "Amelia",
            "Mason",
            "Harper",
            "Aiden",
            "Aria",
            "Elijah",
            "Zoe",
            "James",
            "Benjamin",
            "Charlotte",
            "Henry",
            "Evelyn",
            "Daniel",
            "Abigail",
            "Alexander",
            "Ella",
            "Michael",
            "Scarlett",
        ],
        dtype=object,
    )

    cities = np.array(
        ["London", "Leeds", "Manchester", "Bristol", "Birmingham", "Liverpool", "Pune", "Delhi", "Bangalore", "Austin"],
        dtype=object,
    )

    statuses = np.array(["new", "active", "paused", "closed", "processing"], dtype=object)

    name_values = rng.choice(base_names, size=rows, replace=True)
    sarah_injections = max(2500, rows // 40)
    sarah_idx = rng.choice(rows, size=sarah_injections, replace=False)
    name_values[sarah_idx] = "Sarah"

    prefix_10_count = max(5000, rows // 9)
    ten_idx = rng.choice(rows, size=prefix_10_count, replace=False)
    ten_mask = np.zeros(rows, dtype=bool)
    ten_mask[ten_idx] = True

    id_num = rng.integers(100000, 999999, size=rows)
    prefixes = rng.choice(np.array(["20", "30", "40", "50", "60"], dtype=object), size=rows, replace=True)
    prefixes[ten_mask] = "10"
    ids = np.char.add(prefixes.astype(str), id_num.astype(str)).astype(object)

    duplicate_targets = max(4000, rows // 45)
    target_idx = rng.choice(rows, size=duplicate_targets, replace=False)
    source_idx = rng.choice(rows, size=duplicate_targets, replace=True)
    ids[target_idx] = ids[source_idx]

    domains = np.array(["mail.com", "example.org", "demo.net", "sample.io"], dtype=object)
    user_stems = np.char.lower(np.char.replace(name_values.astype(str), " ", ""))
    emails = np.char.add(np.char.add(user_stems, "_"), np.char.mod("%06d", rng.integers(1, 999999, size=rows)))
    emails = np.char.add(np.char.add(emails, "@"), rng.choice(domains, size=rows, replace=True))
    emails = emails.astype(object)

    missing_email_count = max(3000, rows // 35)
    missing_idx = rng.choice(rows, size=missing_email_count, replace=False)
    emails[missing_idx] = None

    amounts = np.round(rng.gamma(shape=2.2, scale=45.0, size=rows), 2)
    city_values = rng.choice(cities, size=rows, replace=True)
    status_values = rng.choice(statuses, size=rows, replace=True)

    start_date = np.datetime64("2024-01-01")
    date_offsets = rng.integers(0, 730, size=rows)
    dates = start_date + date_offsets.astype("timedelta64[D]")

    df = pd.DataFrame(
        {
            profile.id_col: ids,
            profile.name_col: name_values,
            profile.email_col: emails,
            profile.amount_col: amounts,
            profile.city_col: city_values,
            profile.date_col: dates.astype(str),
            "record_status": status_values,
        }
    )

    expected = {
        "lookup_sarah": int(df[profile.name_col].astype(str).str.lower().eq("sarah").sum()),
        "starts_with_10": int(df[profile.id_col].astype(str).str.startswith("10").sum()),
        "distinct_names_count": int(df[profile.name_col].nunique(dropna=True)),
        "duplicate_id_count": int(df[df[profile.id_col].duplicated(keep=False)].shape[0]),
        "missing_email_count": int(_safe_missing_mask(df[profile.email_col]).sum()),
        "total_rows_count": int(len(df)),
    }
    return df, expected


def first_scalar_int(df: pd.DataFrame, summary: str) -> int | None:
    if not df.empty and len(df) == 1:
        numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        if numeric_cols:
            return int(df[numeric_cols[0]].iloc[0])

    m = re.search(r"\b(\d+)\b", summary)
    if m:
        return int(m.group(1))
    return None


def extract_actual(intent: str, out_df: pd.DataFrame, summary: str) -> int:
    lower_cols = {str(c).lower(): str(c) for c in out_df.columns}

    if intent in {"lookup_sarah", "starts_with_10"}:
        if "matches" in lower_cols:
            return int(out_df[lower_cols["matches"]].iloc[0])
        return int(len(out_df))

    if intent == "distinct_names_count":
        distinct_col = next((c for c in out_df.columns if "distinct" in str(c).lower() and "count" in str(c).lower()), None)
        if distinct_col is not None and not out_df.empty:
            return int(out_df[distinct_col].iloc[0])
        scalar = first_scalar_int(out_df, summary)
        return scalar if scalar is not None else int(len(out_df))

    if intent == "duplicate_id_count":
        if "duplicate_row_count" in lower_cols:
            return int(out_df[lower_cols["duplicate_row_count"]].iloc[0])
        if "duplicate_count" in lower_cols:
            return int(pd.to_numeric(out_df[lower_cols["duplicate_count"]], errors="coerce").fillna(0).sum())
        scalar = first_scalar_int(out_df, summary)
        return scalar if scalar is not None else int(len(out_df))

    if intent == "missing_email_count":
        if "missing_or_blank_values" in lower_cols:
            return int(out_df[lower_cols["missing_or_blank_values"]].iloc[0])
        missing_col = next((c for c in out_df.columns if "missing" in str(c).lower() and "count" in str(c).lower()), None)
        if missing_col is not None and not out_df.empty:
            return int(out_df[missing_col].iloc[0])
        scalar = first_scalar_int(out_df, summary)
        return scalar if scalar is not None else int(len(out_df))

    if intent == "total_rows_count":
        scalar = first_scalar_int(out_df, summary)
        return scalar if scalar is not None else int(len(out_df))

    return int(len(out_df))


def build_recommendations(
    intent_metrics: dict[str, dict[str, Any]],
    consistency_rows: list[dict[str, Any]],
    top_failing_questions: list[dict[str, Any]],
) -> list[str]:
    recommendations: list[str] = []

    low_intents = [intent for intent, metrics in intent_metrics.items() if float(metrics.get("accuracy", 0.0)) < 1.0]
    if low_intents:
        recommendations.append(
            "Focus rule tuning on these intents first: " + ", ".join(sorted(low_intents)) + "."
        )

    inconsistent = [row for row in consistency_rows if not bool(row.get("consistent"))]
    if inconsistent:
        recommendations.append(
            f"Detected {len(inconsistent)} consistency failures where same-purpose paraphrases returned different answers."
        )

    if top_failing_questions:
        sample = "; ".join(
            f"{item['intent']} -> {item['question']}"
            for item in top_failing_questions[:5]
        )
        recommendations.append("Use these failing paraphrases as priority regression tests: " + sample + ".")

    recommendations.append(
        "Training is not performed by this script; export the failure corpus and use it for supervised prompt/model fine-tuning in your training pipeline."
    )
    return recommendations


def derive_next_intent_weights(
    intent_metrics: dict[str, dict[str, Any]],
    current_weights: dict[str, int],
    max_weight: int = 4,
) -> dict[str, int]:
    next_weights = dict(current_weights)
    for intent, metrics in intent_metrics.items():
        accuracy = float(metrics.get("accuracy", 0.0))
        if accuracy < 1.0:
            next_weights[intent] = min(max_weight, max(2, current_weights.get(intent, 1) + 1))
    return next_weights


def run_single_iteration(
    rows: int,
    datasets: int,
    rounds: int,
    seed: int,
    trace: bool,
    intent_weights: dict[str, int],
    max_failure_samples: int,
) -> dict[str, Any]:
    rng = random.Random(seed)
    start_all = time.perf_counter()

    intent_summary: dict[str, IntentAccumulator] = {
        intent: IntentAccumulator()
        for intent in INTENT_PARAPHRASES
    }
    consistency_groups: dict[tuple[str, str], ConsistencyAccumulator] = {}
    question_stats: dict[tuple[str, str], QuestionAccumulator] = {}
    failure_samples: list[dict[str, Any]] = []

    total_questions = 0
    passed_questions = 0

    for dataset_idx in range(datasets):
        profile = build_profile_variant(dataset_idx, seed)
        df, expected = generate_dataset(profile, rows=rows, seed=seed + (dataset_idx * 13))

        for round_idx in range(rounds):
            for intent, paraphrases in INTENT_PARAPHRASES.items():
                multiplier = max(1, intent_weights.get(intent, 1))
                for _ in range(multiplier):
                    shuffled = paraphrases[:]
                    rng.shuffle(shuffled)
                    for q in shuffled:
                        t0 = time.perf_counter()
                        local_trace = build_reasoning_trace(df, q) if trace else None
                        out_df, summary = default_fallback_result(df, q, local_trace)
                        latency_ms = (time.perf_counter() - t0) * 1000.0

                        decision_path: Optional[str] = None
                        plan_intent: Optional[str] = None
                        if isinstance(local_trace, dict):
                            decision_raw = local_trace.get("decisionPath")
                            decision_path = str(decision_raw) if decision_raw is not None else None
                            plan_raw = local_trace.get("queryPlan")
                            if isinstance(plan_raw, dict):
                                plan_intent_raw = plan_raw.get("intent_family")
                                plan_intent = str(plan_intent_raw) if plan_intent_raw is not None else None

                        actual = extract_actual(intent, out_df, summary)
                        exp = int(expected[intent])
                        passed = actual == exp

                        total_questions += 1
                        if passed:
                            passed_questions += 1

                        intent_acc = intent_summary[intent]
                        intent_acc.total += 1
                        if passed:
                            intent_acc.passed += 1
                        intent_acc.latencies_ms.append(latency_ms)

                        key = (profile.name, intent)
                        group = consistency_groups.setdefault(key, ConsistencyAccumulator())
                        group.actual_values.add(int(actual))
                        group.expected_values.add(exp)
                        group.total += 1
                        if passed:
                            group.passed += 1

                        q_key = (intent, q)
                        q_acc = question_stats.setdefault(q_key, QuestionAccumulator())
                        q_acc.total += 1
                        if passed:
                            q_acc.passed += 1

                        if not passed and len(failure_samples) < max_failure_samples:
                            failure_samples.append(
                                {
                                    "dataset": profile.name,
                                    "round": round_idx + 1,
                                    "intent": intent,
                                    "question": q,
                                    "expected": exp,
                                    "actual": int(actual),
                                    "latency_ms": round(latency_ms, 3),
                                    "decision_path": decision_path,
                                    "plan_intent": plan_intent,
                                    "summary": summary,
                                }
                            )

    consistency_rows: list[dict[str, Any]] = []
    for (dataset_name, intent), acc in consistency_groups.items():
        actual_values = sorted(acc.actual_values)
        expected_values = sorted(acc.expected_values)
        consistent = len(actual_values) == 1 and len(expected_values) == 1 and actual_values == expected_values
        consistency_rows.append(
            {
                "dataset": dataset_name,
                "intent": intent,
                "consistent": bool(consistent),
                "actual_values": actual_values,
                "expected": expected_values,
                "total": acc.total,
                "passed": acc.passed,
                "accuracy": round((acc.passed / acc.total) if acc.total else 0.0, 4),
            }
        )

    top_failing_questions: list[dict[str, Any]] = []
    for (intent, question), q_acc in question_stats.items():
        accuracy = (q_acc.passed / q_acc.total) if q_acc.total else 0.0
        if accuracy < 1.0:
            top_failing_questions.append(
                {
                    "intent": intent,
                    "question": question,
                    "total": q_acc.total,
                    "passed": q_acc.passed,
                    "failures": q_acc.total - q_acc.passed,
                    "accuracy": round(accuracy, 4),
                }
            )
    top_failing_questions.sort(key=lambda item: (float(item["accuracy"]), -int(item["failures"]), str(item["intent"]), str(item["question"])))

    intent_metrics: dict[str, dict[str, Any]] = {}
    for intent, acc in intent_summary.items():
        latencies = acc.latencies_ms
        intent_metrics[intent] = {
            "total": acc.total,
            "passed": acc.passed,
            "failed": acc.total - acc.passed,
            "accuracy": round((acc.passed / acc.total) if acc.total else 0.0, 4),
            "p50_ms": round(statistics.median(latencies), 3) if latencies else 0.0,
            "p95_ms": round(float(np.percentile(latencies, 95)), 3) if latencies else 0.0,
            "avg_ms": round(float(statistics.mean(latencies)), 3) if latencies else 0.0,
        }

    consistency_total = len(consistency_rows)
    consistency_pass = sum(1 for row in consistency_rows if bool(row.get("consistent")))
    total_runtime = time.perf_counter() - start_all

    overall = {
        "total_questions": total_questions,
        "passed": passed_questions,
        "failed": total_questions - passed_questions,
        "accuracy": round((passed_questions / total_questions) if total_questions else 0.0, 4),
        "consistency_groups": consistency_total,
        "consistency_pass": consistency_pass,
        "consistency_accuracy": round((consistency_pass / consistency_total) if consistency_total else 0.0, 4),
        "total_runtime_sec": round(total_runtime, 3),
        "throughput_qps": round((total_questions / total_runtime) if total_runtime > 0 else 0.0, 3),
    }

    return {
        "overall": overall,
        "intent_metrics": intent_metrics,
        "consistency": consistency_rows,
        "sample_failures": failure_samples,
        "top_failing_questions": top_failing_questions[:max_failure_samples],
        "recommendations": build_recommendations(intent_metrics, consistency_rows, top_failing_questions),
    }


def run_benchmark(
    rows: int,
    datasets: int,
    rounds: int,
    seed: int,
    trace: bool,
    iterations: int,
    target_accuracy: float,
    target_consistency: float,
    max_failure_samples: int,
) -> dict[str, Any]:
    iterations = max(1, iterations)
    intent_weights = {intent: 1 for intent in INTENT_PARAPHRASES}

    iteration_history: list[dict[str, Any]] = []
    final_report: Optional[dict[str, Any]] = None

    for iteration_idx in range(iterations):
        iteration_seed = seed + (iteration_idx * 1000003)

        iteration_report = run_single_iteration(
            rows=rows,
            datasets=datasets,
            rounds=rounds,
            seed=iteration_seed,
            trace=trace,
            intent_weights=intent_weights,
            max_failure_samples=max_failure_samples,
        )
        final_report = iteration_report

        overall = iteration_report["overall"]
        iteration_history.append(
            {
                "iteration": iteration_idx + 1,
                "seed": iteration_seed,
                "accuracy": overall["accuracy"],
                "consistency_accuracy": overall["consistency_accuracy"],
                "intent_weights": dict(intent_weights),
            }
        )

        if float(overall["accuracy"]) >= target_accuracy and float(overall["consistency_accuracy"]) >= target_consistency:
            break

        intent_weights = derive_next_intent_weights(iteration_report["intent_metrics"], intent_weights)

    if final_report is None:
        final_report = run_single_iteration(
            rows=rows,
            datasets=datasets,
            rounds=rounds,
            seed=seed,
            trace=trace,
            intent_weights=intent_weights,
            max_failure_samples=max_failure_samples,
        )

    final_overall = final_report["overall"]
    strict_gate_passed = bool(
        float(final_overall["accuracy"]) >= target_accuracy
        and float(final_overall["consistency_accuracy"]) >= target_consistency
    )

    report: dict[str, Any] = {
        "config": {
            "rows_per_dataset": rows,
            "dataset_count": datasets,
            "rounds": rounds,
            "questions_per_round_per_dataset": sum(len(v) for v in INTENT_PARAPHRASES.values()),
            "trace_enabled": trace,
            "seed": seed,
            "iterations_requested": iterations,
            "iterations_executed": len(iteration_history),
            "target_accuracy": target_accuracy,
            "target_consistency": target_consistency,
        },
        "overall": final_overall,
        "intent_metrics": final_report["intent_metrics"],
        "consistency": final_report["consistency"],
        "sample_failures": final_report["sample_failures"],
        "top_failing_questions": final_report["top_failing_questions"],
        "recommendations": final_report["recommendations"],
        "iteration_history": iteration_history,
        "strict_gate_passed": strict_gate_passed,
    }
    return report


def save_report(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")


def save_failure_corpus(samples: list[dict[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for item in samples:
            handle.write(json.dumps(item, ensure_ascii=True) + "\n")


def print_report(report: dict[str, Any]) -> None:
    cfg = report["config"]
    overall = report["overall"]

    print("=== Paraphrase Stress Test Report ===")
    print(
        f"Datasets: {cfg['dataset_count']} | Rows per dataset: {cfg['rows_per_dataset']} | Rounds: {cfg['rounds']} | Iterations: {cfg['iterations_executed']}/{cfg['iterations_requested']}"
    )
    print(
        f"Questions: {overall['total_questions']} | Passed: {overall['passed']} | Failed: {overall['failed']}"
    )
    print(
        f"Accuracy: {overall['accuracy']:.2%} | Consistency: {overall['consistency_accuracy']:.2%} | Throughput: {overall['throughput_qps']} q/s"
    )
    print(f"Runtime: {overall['total_runtime_sec']} sec")
    print(f"Strict gate passed: {report.get('strict_gate_passed')}")

    print("\nPer-intent metrics:")
    for intent, metrics in report["intent_metrics"].items():
        print(
            f"- {intent}: accuracy={metrics['accuracy']:.2%}, p50={metrics['p50_ms']} ms, p95={metrics['p95_ms']} ms, total={metrics['total']}"
        )

    top_failing = report.get("top_failing_questions", [])
    if top_failing:
        print("\nTop failing paraphrases:")
        for row in top_failing[:10]:
            print(
                f"- {row['intent']} | failures={row['failures']} | accuracy={row['accuracy']:.2%} | q={row['question']}"
            )

    recs = report.get("recommendations", [])
    if recs:
        print("\nRecommendations:")
        for rec in recs:
            print(f"- {rec}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run large-scale paraphrase consistency stress testing.")
    parser.add_argument("--rows", type=int, default=200000, help="Rows per synthetic dataset.")
    parser.add_argument("--datasets", type=int, default=100, help="Number of datasets/schemas to test.")
    parser.add_argument("--rounds", type=int, default=1, help="Repetitions per dataset per iteration.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    parser.add_argument("--trace", action="store_true", help="Enable reasoning trace for every question.")
    parser.add_argument("--iterations", type=int, default=1, help="Adaptive re-test iterations.")
    parser.add_argument("--target-accuracy", type=float, default=1.0, help="Target overall accuracy to stop iterations.")
    parser.add_argument("--target-consistency", type=float, default=1.0, help="Target consistency accuracy to stop iterations.")
    parser.add_argument("--max-failure-samples", type=int, default=200, help="Maximum failure rows to store in report/corpus.")
    parser.add_argument("--strict-gate", action="store_true", help="Exit non-zero when targets are not met.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("python-service/validation/reports/paraphrase_stress_report.json"),
        help="Output JSON report path.",
    )
    parser.add_argument(
        "--failure-corpus",
        type=Path,
        default=Path("python-service/validation/reports/paraphrase_failure_corpus.jsonl"),
        help="Optional JSONL file containing sampled failed cases.",
    )
    parser.add_argument(
        "--no-failure-corpus",
        action="store_true",
        help="Disable writing failure corpus JSONL.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    report = run_benchmark(
        rows=args.rows,
        datasets=args.datasets,
        rounds=args.rounds,
        seed=args.seed,
        trace=args.trace,
        iterations=args.iterations,
        target_accuracy=args.target_accuracy,
        target_consistency=args.target_consistency,
        max_failure_samples=args.max_failure_samples,
    )

    save_report(report, args.output)
    print_report(report)
    print(f"\nSaved report: {args.output}")

    if not args.no_failure_corpus:
        samples = report.get("sample_failures", [])
        if isinstance(samples, list) and samples:
            save_failure_corpus(samples, args.failure_corpus)
            print(f"Saved failure corpus: {args.failure_corpus}")

    if args.strict_gate and not bool(report.get("strict_gate_passed")):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
