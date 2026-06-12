import os
import sys

import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.core import intents


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "OrderID": [1001, 1002, 991, 2100, 1010],
            "Salesperson": ["John", "Sarah", "Mike", "Emma", "John"],
            "Region": ["North", "South", "East", "West", "North"],
            "Product": ["Laptop", "Phone", "Printer", "Desk", "Monitor"],
        }
    )


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_noisy_startswith_routes_to_lookup() -> None:
    df = _sample_df()
    q = "select all records sstarts wtih 10"

    _assert(intents.wants_full_data(q) is False, "Noisy startswith query should not be treated as full-data intent")
    rules = intents.extract_text_match_rules(q)
    _assert(len(rules) > 0, "Noisy startswith query should produce text match rules")

    trace = {}
    out, summary = intents.default_fallback_result(df, q, trace=trace)

    _assert(trace.get("decisionPath") == "lookup", "Noisy startswith query should route to lookup path")
    _assert(len(out) == 3, f"Expected 3 OrderID rows starting with 10, got {len(out)}")
    _assert(all(str(v).startswith("10") for v in out["OrderID"].tolist()), "All returned OrderID values must start with 10")
    _assert("Found" in summary, "Summary should indicate filtered matches")



def test_explicit_startswith_with_column() -> None:
    df = _sample_df()
    q = "show all records where order id starts with 10"

    trace = {}
    out, _ = intents.default_fallback_result(df, q, trace=trace)

    _assert(trace.get("decisionPath") == "lookup", "Explicit startswith query should route to lookup")
    _assert(len(out) == 3, f"Expected 3 rows for explicit startswith query, got {len(out)}")



def test_plan_filter_applied_in_fallback() -> None:
    df = _sample_df()
    q = "show all records"
    trace = {
        "queryPlan": {
            "filters": [
                {"column": "OrderID", "operator": "startswith", "value": "10"},
                {"column": "Region", "operator": "=", "value": "North"},
            ]
        }
    }

    out, _ = intents.default_fallback_result(df, q, trace=trace)
    ids = out["OrderID"].tolist()

    _assert(trace.get("decisionPath") == "lookup", "Plan filters should route fallback into lookup")
    _assert(ids == [1001, 1010], f"Expected plan-filtered rows [1001, 1010], got {ids}")



def run() -> None:
    tests = [
        ("noisy_startswith_routes_to_lookup", test_noisy_startswith_routes_to_lookup),
        ("explicit_startswith_with_column", test_explicit_startswith_with_column),
        ("plan_filter_applied_in_fallback", test_plan_filter_applied_in_fallback),
    ]

    passed = 0
    for name, fn in tests:
        fn()
        print(f"PASS: {name}")
        passed += 1

    print(f"\nSemantic filter regression suite: {passed}/{len(tests)} passed")


if __name__ == "__main__":
    run()
