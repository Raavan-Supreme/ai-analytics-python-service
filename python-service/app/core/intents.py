import re
import unicodedata
import warnings
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Optional, cast

import duckdb
import pandas as pd


COMPARISON_MARKERS = [
    "compare",
    "comparison",
    "vs",
    "versus",
    "by",
    "between",
    "trend",
    "over time",
    "distribution",
    "top",
    "bottom",
    "highest",
    "lowest",
    "increase",
    "decrease",
    "correlation",
]

VISUALIZATION_MARKERS = [
    "chart",
    "graph",
    "plot",
    "visual",
    "visualize",
    "visualise",
    "distribution",
    "trend",
    "histogram",
    "breakdown",
]

MAX_MARKERS = ["highest", "max", "maximum", "top", "most", "best", "largest", "greatest"]
MIN_MARKERS = ["lowest", "min", "minimum", "bottom", "least", "worst", "smallest"]

LOOKUP_KEYWORDS = ["for", "where", "with", "whose"]

LOOKUP_VALUE_STOPWORDS = {
    "from",
    "to",
    "with",
    "where",
    "for",
    "and",
    "or",
    "is",
    "are",
    "equals",
    "equal",
    "between",
}

ALL_RECORDS_VALUE_STOPWORDS = {
    "all",
    "full",
    "entire",
    "complete",
    "everything",
    "whole",
    "data",
    "dataset",
    "record",
    "records",
    "row",
    "rows",
    "result",
    "results",
    "entry",
    "entries",
}

OUT_OF_SCOPE_MARKERS = [
    "write script",
    "generate script",
    "complete script",
    "full script",
    "build app",
    "create app",
    "develop app",
    "build website",
    "create website",
    "backend code",
    "frontend code",
    "dockerfile",
    "kubernetes",
    "terraform",
]

DATA_QUERY_MARKERS = [
    "show",
    "list",
    "find",
    "count",
    "total",
    "average",
    "avg",
    "sum",
    "min",
    "max",
    "trend",
    "over time",
    "compare",
    "comparison",
    "top",
    "bottom",
    "highest",
    "lowest",
    "rows",
    "row",
    "records",
    "record",
    "column",
    "columns",
    "schema",
    "chart",
    "plot",
    "graph",
    "where",
    "contains",
    "by",
    "per",
]

QUESTION_ENTITY_SYNONYMS: dict[str, set[str]] = {
    "order": {"orders", "orderid", "orderno", "purchase", "transaction", "invoice", "po"},
    "product": {
        "products",
        "item",
        "items",
        "sku",
        "material",
        "model",
        "part",
        "article",
        "catalog",
        "code",
        "productname",
        "product name",
        "productid",
        "product id",
        "productcode",
        "product code",
    },
    "category": {
        "categories",
        "type",
        "types",
        "segment",
        "segments",
        "class",
        "classes",
        "group",
        "groups",
        "productcategory",
        "product category",
    },
    "region": {
        "regions",
        "area",
        "areas",
        "territory",
        "territories",
        "zone",
        "zones",
        "location",
        "locations",
        "salesregion",
        "sale region",
    },
    "salesperson": {
        "salespersons",
        "salesperson",
        "seller",
        "sellers",
        "rep",
        "reps",
        "agent",
        "agents",
        "salesrep",
        "sales rep",
        "salesagent",
        "salespeople",
        "sales people",
    },
    "customer": {"customers", "client", "clients", "buyer", "buyers", "account", "accounts", "user", "users"},
    "sales": {"sale", "revenue", "amount", "turnover"},
    "date": {"day", "month", "year", "time", "timestamp"},
}

INTENT_PHRASE_NORMALIZATION_RULES: list[tuple[str, str]] = [
    (r"\bmonth\s+on\s+month\b|\bmonth-over-month\b|\bmom\b", " mom "),
    (r"\byear\s+over\s+year\b|\byear-on-year\b|\byoy\b", " yoy "),
    (r"\bbreak\s*down\s+by\b|\bsplit\s+by\b", " by "),
    (r"\bhow\s+much\b", " total "),
    (r"\bother\s+than\b|\bexcept\b|\bexcluding\b", " not "),
    (r"\bnull\s+values?\b|\bmissing\s+values?\b", " missing values "),
    (r"\bblank\s+values?\b|\bempty\s+values?\b", " blank values "),
    (r"\bcount\s+distinct\b", " distinct count "),
]

INTENT_FAMILY_KEYWORDS: dict[str, set[str]] = {
    "schema": {"schema", "column", "columns", "field", "fields", "header", "datatype", "data type"},
    "count": {"count", "how many", "number of", "total records", "row count"},
    "lookup": {"where", "with", "for", "whose", "contains", "starts with", "ends with", "named", "called"},
    "aggregation": {"sum", "average", "avg", "mean", "minimum", "maximum", "median", "std", "variance", "percent", "share", "contribution"},
    "trend": {"trend", "over time", "time series", "mom", "yoy", "growth", "decline", "projection", "forecast", "predict"},
    "comparison": {"compare", "comparison", "vs", "versus", "rank", "ranking", "top", "bottom", "highest", "lowest"},
    "relationship": {"correlation", "relationship", "impact", "affect", "influence", "associated"},
    "chart": {"chart", "graph", "plot", "visualize", "visualise", "heatmap", "histogram", "scatter", "bar", "line", "pie"},
    "data_quality": {"duplicate", "duplicates", "missing", "blank", "null", "invalid", "outlier", "anomaly", "inconsistent"},
}

DISTINCT_MARKERS = {
    "distinct",
    "unique",
    "different",
    "dedupe",
    "de-duplicate",
    "non-duplicate",
}

DATA_QUALITY_MARKERS = {
    "duplicate",
    "duplicates",
    "repeated",
    "repeat",
    "missing",
    "blank",
    "null",
    "invalid",
    "outlier",
    "anomaly",
    "inconsistent",
}

SORT_DIRECTION_HINTS = {
    "desc": ["desc", "descending", "highest", "largest", "top", "max"],
    "asc": ["asc", "ascending", "lowest", "smallest", "bottom", "min"],
}

_DF_PARSE_CACHE_LIMIT = 8
_DF_DATE_PARSE_CACHE: "OrderedDict[int, dict[str, Any]]" = OrderedDict()
_DATE_HINT_TOKENS = (
    "date",
    "time",
    "day",
    "month",
    "year",
    "created",
    "updated",
    "purchase",
    "txn",
)

_CURRENCY_TOKEN_PATTERN = re.compile(
    r"\b(?:usd|eur|gbp|inr|aud|cad|jpy|chf|sgd|aed|zar|rs|rupees?|dollars?|euros?|pounds?)\b",
    flags=re.IGNORECASE,
)
_CURRENCY_SYMBOL_PATTERN = re.compile(r"[$€£¥₹]")


def normalize_question_text(question: str) -> str:
    q = unicodedata.normalize("NFKC", str(question or "")).casefold()
    q = q.replace("\u2019", "'")
    for pattern, replacement in INTENT_PHRASE_NORMALIZATION_RULES:
        q = re.sub(pattern, replacement, q)
    q = re.sub(r"\s+", " ", q).strip()
    return q


def normalize_question_advanced(question: str) -> str:
    return normalize_question_text(question)


def asks_distinct(question: str) -> bool:
    q = normalize_question_text(question)
    return any(marker in q for marker in DISTINCT_MARKERS) or bool(re.search(r"\bhow\s+many\s+unique\b", q))


def asks_duplicate_check(question: str) -> bool:
    q = normalize_question_text(question)
    if "duplicate" in q or "duplicates" in q:
        return True
    return bool(re.search(r"\b(repeated|repeat(?:ed)?)\b", q))


def asks_null_blank_check(question: str) -> bool:
    q = normalize_question_text(question)
    if any(marker in q for marker in ["missing", "blank", "null", "empty"]):
        return True
    return bool(
        re.search(r"\b(not\s+filled|not\s+provided|not\s+available|no\s+email|without\s+email|email\s+not\s+filled)\b", q)
    )


def asks_outlier_check(question: str) -> bool:
    q = normalize_question_text(question)
    return any(marker in q for marker in ["outlier", "outliers", "anomaly", "anomalies"])


def asks_invalid_format_check(question: str) -> bool:
    q = normalize_question_text(question)
    return "invalid" in q and any(token in q for token in ["date", "dates", "number", "numeric", "email", "format", "value"])


def asks_data_quality_question(question: str) -> bool:
    q = normalize_question_text(question)
    return any(marker in q for marker in DATA_QUALITY_MARKERS) or asks_duplicate_check(q) or asks_null_blank_check(q) or asks_outlier_check(q) or asks_invalid_format_check(q)


def asks_distinct_question(question: str) -> bool:
    return asks_distinct(question)


def asks_missing_question(question: str) -> bool:
    q = normalize_question_text(question)
    return "missing" in q or "not available" in q or asks_null_blank_check(q)


def asks_duplicate_question(question: str) -> bool:
    return asks_duplicate_check(question)


def asks_null_question(question: str) -> bool:
    q = normalize_question_text(question)
    return "null" in q or "blank" in q or asks_null_blank_check(q)


def asks_outlier_question(question: str) -> bool:
    return asks_outlier_check(question)


def asks_percent_question(question: str) -> bool:
    q = normalize_question_text(question)
    return any(token in q for token in ["percent", "percentage", "%", "share", "contribution", "ratio"])


def asks_growth_question(question: str) -> bool:
    q = normalize_question_text(question)
    return any(token in q for token in ["growth", "increase", "decrease", "change", "delta", "trend change"])


def asks_yoy_question(question: str) -> bool:
    q = normalize_question_text(question)
    return any(token in q for token in ["yoy", "year over year", "year-on-year", "year on year"])


def asks_mom_question(question: str) -> bool:
    q = normalize_question_text(question)
    return any(token in q for token in ["mom", "month over month", "month-on-month", "month on month"])


def asks_nth_rank_question(question: str) -> bool:
    q = normalize_question_text(question)
    return bool(re.search(r"\b(\d+)(?:st|nd|rd|th)\s+(highest|lowest|largest|smallest|top|bottom)\b", q))


def needs_clarification(plan: Optional[dict[str, Any]]) -> bool:
    plan_data = plan if isinstance(plan, dict) else {}
    return bool(plan_data.get("clarification_needed") or plan_data.get("needs_clarification") or plan_data.get("scope") == "clarify")


def build_clarification_from_plan(df: pd.DataFrame, question: str, plan: Optional[dict[str, Any]]) -> str:
    plan_data = plan if isinstance(plan, dict) else {}
    reason = str(plan_data.get("clarification_question") or plan_data.get("clarification_reason") or "").strip()
    if reason:
        return reason
    return build_scope_guidance(df, question, escalation=True, out_of_scope=str(plan_data.get("scope") or "") == "out_of_scope")


def _choose_time_column_for_plan(df: pd.DataFrame, question: str) -> Optional[str]:
    date_cols = _date_like_columns(df)
    if not date_cols:
        return None
    mentioned = requested_projection_columns(df, question)
    for col in mentioned:
        if col in date_cols:
            return col
    return date_cols[0]


def percent_contribution_result(df: pd.DataFrame, question: str) -> Optional[tuple[pd.DataFrame, str]]:
    if df.empty or not asks_percent_question(question):
        return None

    group_col = choose_group_column(df, question)
    metric_col = choose_metric_column(df, question, exclude_column=group_col)
    if not group_col or not metric_col:
        return None

    group_series = _first_series_for_column_name(df, group_col)
    metric_series = _first_series_for_column_name(df, metric_col)
    if group_series is None or metric_series is None:
        return None

    working = pd.DataFrame({"__group__": group_series, "__metric__": _coerce_numeric_series(metric_series)}).dropna(subset=["__metric__"])
    if working.empty:
        return None

    grouped = (
        working.groupby("__group__", dropna=False, as_index=False)["__metric__"]
        .sum()
        .rename(columns={"__group__": group_col, "__metric__": metric_col})
    )
    total = float(grouped[metric_col].sum())
    if total == 0:
        grouped["percent_contribution"] = 0.0
    else:
        grouped["percent_contribution"] = (grouped[metric_col] / total) * 100.0
    grouped = grouped.sort_values(by="percent_contribution", ascending=False).reset_index(drop=True)
    return grouped, f"Computed percent contribution of {metric_col} by {group_col}."


def timeseries_comparison_result(df: pd.DataFrame, question: str) -> Optional[tuple[pd.DataFrame, str]]:
    if df.empty or not (asks_yoy_question(question) or asks_mom_question(question) or asks_growth_question(question)):
        return None

    time_col = _choose_time_column_for_plan(df, question)
    metric_col = choose_metric_column(df, question, exclude_column=time_col)
    if not time_col or not metric_col:
        return None

    time_series = _first_series_for_column_name(df, time_col)
    metric_series = _first_series_for_column_name(df, metric_col)
    if time_series is None or metric_series is None:
        return None

    parsed_time = _parse_datetime_series_cached(df, time_col, time_series)
    parsed_metric = _coerce_numeric_series(metric_series)
    working = pd.DataFrame({"__time__": parsed_time, "__metric__": parsed_metric}).dropna()
    if working.empty:
        return None

    if asks_yoy_question(question):
        working["__period__"] = working["__time__"].dt.to_period("Y").astype(str)
        label = "YoY"
    else:
        working["__period__"] = working["__time__"].dt.to_period("M").astype(str)
        label = "MoM" if asks_mom_question(question) else "period-over-period"

    grouped = working.groupby("__period__", as_index=False)["__metric__"].sum().rename(columns={"__period__": "period", "__metric__": metric_col})
    grouped = grouped.sort_values(by="period").reset_index(drop=True)
    grouped["previous_value"] = grouped[metric_col].shift(1)
    grouped["absolute_change"] = grouped[metric_col] - grouped["previous_value"]
    grouped["percent_change"] = grouped["absolute_change"] / grouped["previous_value"].replace({0: pd.NA}) * 100.0
    return grouped, f"Computed {label} comparison for {metric_col} over {time_col}."


def growth_result(df: pd.DataFrame, question: str) -> Optional[tuple[pd.DataFrame, str]]:
    return timeseries_comparison_result(df, question)


def running_total_result(df: pd.DataFrame, question: str) -> Optional[tuple[pd.DataFrame, str]]:
    q = normalize_question_text(question)
    if df.empty or not any(token in q for token in ["running total", "cumulative", "cum sum", "cumsum"]):
        return None

    time_col = _choose_time_column_for_plan(df, question)
    metric_col = choose_metric_column(df, question, exclude_column=time_col)
    if not time_col or not metric_col:
        return None

    time_series = _first_series_for_column_name(df, time_col)
    metric_series = _first_series_for_column_name(df, metric_col)
    if time_series is None or metric_series is None:
        return None

    parsed_time = _parse_datetime_series_cached(df, time_col, time_series)
    parsed_metric = _coerce_numeric_series(metric_series)
    working = pd.DataFrame({"time": parsed_time, "value": parsed_metric}).dropna()
    if working.empty:
        return None

    grouped = working.groupby("time", as_index=False)["value"].sum().sort_values(by="time").reset_index(drop=True)
    grouped["running_total"] = grouped["value"].cumsum()
    grouped = grouped.rename(columns={"time": time_col, "value": metric_col})
    return grouped, f"Computed running total for {metric_col} over {time_col}."


def nth_rank_result(df: pd.DataFrame, question: str) -> Optional[tuple[pd.DataFrame, str]]:
    if df.empty or not asks_nth_rank_question(question):
        return None

    q = normalize_question_text(question)
    m = re.search(r"\b(\d+)(?:st|nd|rd|th)\s+(highest|lowest|largest|smallest|top|bottom)\b", q)
    if not m:
        return None
    rank_n = max(1, int(m.group(1)))
    is_desc = m.group(2) in {"highest", "largest", "top"}

    group_col = choose_group_column(df, question)
    metric_col = choose_metric_column(df, question, exclude_column=group_col)
    if not group_col or not metric_col:
        return None

    group_series = _first_series_for_column_name(df, group_col)
    metric_series = _first_series_for_column_name(df, metric_col)
    if group_series is None or metric_series is None:
        return None

    working = pd.DataFrame({"__group__": group_series, "__metric__": _coerce_numeric_series(metric_series)}).dropna(subset=["__metric__"])
    if working.empty:
        return None

    grouped = (
        working.groupby("__group__", dropna=False, as_index=False)["__metric__"]
        .sum()
        .rename(columns={"__group__": group_col, "__metric__": metric_col})
        .sort_values(by=metric_col, ascending=not is_desc)
        .reset_index(drop=True)
    )

    if rank_n > len(grouped):
        rank_n = len(grouped)
    picked = grouped.iloc[[rank_n - 1]].reset_index(drop=True)
    label = f"{rank_n}{'th' if rank_n > 3 else {1:'st',2:'nd',3:'rd'}.get(rank_n,'th')}"
    direction = "highest" if is_desc else "lowest"
    return picked, f"Returned {label} {direction} {group_col} by {metric_col}."


def distinct_result(df: pd.DataFrame, question: str) -> Optional[tuple[pd.DataFrame, str]]:
    return distinct_intent_result(df, question)


def duplicate_result(df: pd.DataFrame, question: str) -> Optional[tuple[pd.DataFrame, str]]:
    if not asks_duplicate_question(question):
        return None
    return data_quality_intent_result(df, question)


def missing_result(df: pd.DataFrame, question: str) -> Optional[tuple[pd.DataFrame, str]]:
    if not asks_missing_question(question):
        return None
    return data_quality_intent_result(df, question)


def null_summary_result(df: pd.DataFrame, question: str) -> Optional[tuple[pd.DataFrame, str]]:
    if not asks_null_question(question):
        return None
    return data_quality_intent_result(df, question)


def outlier_result(df: pd.DataFrame, question: str) -> Optional[tuple[pd.DataFrame, str]]:
    if not asks_outlier_question(question):
        return None
    return data_quality_intent_result(df, question)


def detect_chart_preference(question: str) -> str:
    q = normalize_question_text(question)
    if "heatmap" in q:
        return "heatmap"
    if "histogram" in q:
        return "hist"
    if "scatter" in q:
        return "scatter"
    if "pie" in q:
        return "pie"
    if "line" in q:
        return "line"
    if "bar" in q:
        return "bar"
    if "area" in q:
        return "area"
    return "auto" if any(word in q for word in ["chart", "graph", "plot", "visual"]) else "none"


def detect_output_mode(question: str, chart_pref: str) -> str:
    q = normalize_question_text(question)
    wants_chart = chart_pref != "none"
    wants_table = any(word in q for word in ["show", "list", "rows", "records", "table", "details", "data"])
    if wants_chart and wants_table:
        return "table_and_chart"
    if wants_chart:
        return "chart"
    if any(word in q for word in ["count", "total", "sum", "average", "mean", "median", "min", "max"]):
        return "scalar"
    return "table"


def detect_sheet_mode(question: str) -> str:
    q = normalize_question_text(question)
    if any(marker in q for marker in ["all sheets", "all tabs", "entire workbook", "whole workbook", "across sheets"]):
        return "all_sheets"
    if any(marker in q for marker in ["this sheet", "current sheet", "selected sheet"]):
        return "single"
    return "auto"


def extract_sort_spec(question: str) -> list[dict[str, str]]:
    q = normalize_question_text(question)
    specs: list[dict[str, str]] = []

    m = re.search(r"\bsort(?:ed)?\s+by\s+([a-z0-9_\- ]{1,80})(?:\s+(asc|ascending|desc|descending))?\b", q)
    if m:
        direction = "asc"
        raw_dir = (m.group(2) or "").strip()
        if raw_dir in {"desc", "descending"}:
            direction = "desc"
        specs.append({"column": m.group(1).strip(), "direction": direction})
        return specs

    for direction, hints in SORT_DIRECTION_HINTS.items():
        for hint in hints:
            pattern = rf"\b{re.escape(hint)}\s+([a-z0-9_\- ]{{1,80}})\b"
            match = re.search(pattern, q)
            if match:
                specs.append({"column": match.group(1).strip(), "direction": direction})
                return specs

    return specs


def _extract_distinct_hint(question: str) -> Optional[str]:
    q = normalize_question_text(question)
    patterns = [
        r"\b(?:unique|distinct|different)\s+([a-z0-9_\.\- ]{1,80})\b",
        r"\b(?:unique|distinct|different)\s+(?:values?|entries?)\s+of\s+([a-z0-9_\.\- ]{1,80})\b",
        r"\bhow\s+many\s+different\s+([a-z0-9_\.\- ]{1,80})\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, q)
        if m:
            hint = m.group(1).strip(" ?.,")
            hint = re.sub(r"\b(count|records?|rows?|entries?|values?|please|total|non\s*duplicate|separate|do\s+we\s+have)\b", "", hint).strip()
            hint = re.sub(r"\s+", " ", hint).strip()
            if hint:
                return hint
    return None


def _extract_quality_column_hint(question: str) -> Optional[str]:
    q = normalize_question_text(question)
    patterns = [
        r"\b(?:duplicate|duplicates|missing|blank|null|invalid|outlier|outliers)(?:\s+values?)?\s+(?:in|for|of)\s+([a-z0-9_\.\- ]{1,80})\b",
        r"\b(?:duplicate|duplicates|repeated|repeat(?:ed)?)\s+([a-z0-9_\.\- ]{1,80})\b",
        r"\b([a-z0-9_\.\- ]{1,80})\s+(?:duplicates?|missing|blank|null|invalid|outliers?)\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, q)
        if not m:
            continue
        hint = m.group(1).strip(" ?.,")
        hint = re.sub(r"^(?:count|show|list|find|give|return|display)\s+", "", hint).strip()
        hint = re.sub(r"^(?:values?|rows?|records?|columns?|fields?)\s+", "", hint).strip()
        hint = re.sub(r"\s+(?:values?|rows?|records?)$", "", hint).strip()
        hint_tokens = [tok for tok in re.split(r"\s+", hint) if tok]
        invalid_hint_tokens = {
            "count",
            "rows",
            "records",
            "values",
            "data",
            "dataset",
            "me",
            "please",
            "total",
            "how",
            "many",
            "give",
            "show",
            "list",
            "find",
            "tell",
        }
        if hint and hint_tokens and not all(tok in invalid_hint_tokens for tok in hint_tokens):
            return hint
    return None


def extract_filter_specs(question: str) -> list[dict[str, Any]]:
    q = normalize_question_text(question)
    filters: list[dict[str, Any]] = []

    for hint, value in extract_lookup_conditions(q):
        filters.append({"column": hint, "op": "contains", "value": value})

    for hint, value, op in extract_text_match_rules(q):
        op_name = "startswith" if op == "startswith" else "endswith" if op == "endswith" else "contains"
        filters.append({"column": hint, "op": op_name, "value": value})

    if any(token in f" {q} " for token in [" not in ", " not ", " excluding ", " except ", " != "]):
        filters.append({"column": None, "op": "negation", "value": True})

    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in filters:
        key = (str(item.get("column") or "").lower(), str(item.get("op") or "").lower(), str(item.get("value") or "").lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def extract_time_range_spec(question: str) -> Optional[dict[str, str]]:
    start, end = _parse_question_date_bounds(normalize_question_text(question))
    if start is None or end is None:
        return None
    return {
        "from": pd.Timestamp(start).isoformat(),
        "to": pd.Timestamp(end).isoformat(),
    }


def split_compound_question(question: str) -> list[str]:
    q = normalize_question_text(question)
    # Keep date ranges intact: "between x and y" should not split.
    if re.search(r"\bbetween\b.+\band\b", q):
        return [q]

    parts = [p.strip(" ,.?") for p in re.split(r"\b(?:and then|then|also|and)\b", q) if p.strip(" ,.?")]
    meaningful_parts = [p for p in parts if len(re.findall(r"[a-z0-9]+", p)) >= 3]
    return meaningful_parts if len(meaningful_parts) >= 2 else [q]


def _to_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items = cast(list[Any], value)
    out: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _to_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items = cast(list[Any], value)
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            typed_item = cast(dict[str, Any], item)
            out.append({str(k): v for k, v in typed_item.items()})
    return out


def _new_str_list() -> list[str]:
    return []


def _new_dict_list() -> list[dict[str, Any]]:
    return []


@dataclass
class QueryPlan:
    normalized_question: str
    scope: str
    intent_family: str
    confidence: float = 0.0
    measures: list[str] = field(default_factory=_new_str_list)
    dimensions: list[str] = field(default_factory=_new_str_list)
    filters: list[dict[str, Any]] = field(default_factory=_new_dict_list)
    time_range: Optional[dict[str, str]] = None
    sort: list[dict[str, Any]] = field(default_factory=_new_dict_list)
    limit: Optional[int] = None
    distinct: bool = False
    sheet_mode: str = "auto"
    output_mode: str = "table"
    chart_pref: str = "auto"
    subqueries: list[str] = field(default_factory=_new_str_list)
    explanation: str = ""
    operation: Optional[str] = None

    @classmethod
    def from_dict(cls, payload: Optional[dict[str, Any]], question: str = "") -> "QueryPlan":
        plan = payload if isinstance(payload, dict) else {}
        normalized_question = str(plan.get("normalized_question") or normalize_question_text(question))
        scope = str(plan.get("scope") or "in_scope")
        intent_family = str(plan.get("intent_family") or detect_intent_family(normalized_question))
        confidence = float(plan.get("confidence") or 0.0)
        raw_time_range = plan.get("time_range")
        time_range = (
            {str(key): str(val) for key, val in cast(dict[str, Any], raw_time_range).items()}
            if isinstance(raw_time_range, dict)
            else None
        )
        limit = plan.get("limit") if isinstance(plan.get("limit"), int) else None

        return cls(
            normalized_question=normalized_question,
            scope=scope,
            intent_family=intent_family,
            confidence=round(min(max(confidence, 0.0), 1.0), 3),
            measures=_to_str_list(plan.get("measures")),
            dimensions=_to_str_list(plan.get("dimensions")),
            filters=_to_dict_list(plan.get("filters")),
            time_range=time_range,
            sort=_to_dict_list(plan.get("sort")),
            limit=limit,
            distinct=bool(plan.get("distinct")),
            sheet_mode=str(plan.get("sheet_mode") or "auto"),
            output_mode=str(plan.get("output_mode") or "table"),
            chart_pref=str(plan.get("chart_pref") or "auto"),
            subqueries=_to_str_list(plan.get("subqueries")),
            explanation=str(plan.get("explanation") or ""),
            operation=str(plan.get("operation")) if plan.get("operation") is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "normalized_question": self.normalized_question,
            "scope": self.scope,
            "intent_family": self.intent_family,
            "confidence": round(min(max(float(self.confidence), 0.0), 1.0), 3),
            "measures": list(self.measures),
            "dimensions": list(self.dimensions),
            "filters": [dict(item) for item in self.filters],
            "time_range": dict(self.time_range) if isinstance(self.time_range, dict) else None,
            "sort": [dict(item) for item in self.sort],
            "limit": self.limit,
            "distinct": bool(self.distinct),
            "sheet_mode": self.sheet_mode,
            "output_mode": self.output_mode,
            "chart_pref": self.chart_pref,
            "subqueries": list(self.subqueries),
            "explanation": self.explanation,
            "operation": self.operation,
        }


def detect_intent_family(question: str) -> str:
    q = normalize_question_text(question)
    if asks_schema(q):
        return "schema"
    if asks_data_quality_question(q):
        return "data_quality"
    if asks_nth_rank_question(q):
        return "ranking"
    if asks_compare_question(q):
        return "comparison"
    if asks_trend_question(q):
        return "trend"
    if asks_relationship_question(q):
        return "relationship"
    if asks_group_aggregation(q) or asks_scalar_aggregation(q) or asks_superlative_aggregation(q) or asks_metric_by_group(q):
        return "aggregation"
    if asks_distinct(q):
        return "distinct"
    if asks_count(q):
        return "count"
    if asks_specific_lookup(q) or asks_id_lookup(q) or asks_existence(q):
        return "lookup"
    if asks_chart_request(q):
        return "chart"
    if wants_full_data(q):
        return "raw_rows"

    keyword_hits: list[tuple[int, str]] = []
    for family, keywords in INTENT_FAMILY_KEYWORDS.items():
        score = sum(1 for keyword in keywords if keyword in q)
        if score > 0:
            keyword_hits.append((score, family))
    if keyword_hits:
        keyword_hits.sort(key=lambda item: (-item[0], item[1]))
        return keyword_hits[0][1]

    return "raw_rows"


def rank_and_repair_query_plan(df: pd.DataFrame, question: str, plan: dict[str, Any]) -> dict[str, Any]:
    repaired = dict(plan)

    # If we found explicit filters, treat as lookup-like intent over raw full-data.
    raw_filters = repaired.get("filters")
    filters = raw_filters if isinstance(raw_filters, list) else []
    if repaired.get("intent_family") == "raw_rows" and filters:
        repaired["intent_family"] = "lookup"

    # Data-quality intents should not request charts by default.
    if repaired.get("intent_family") == "data_quality" and repaired.get("chart_pref") == "auto":
        repaired["chart_pref"] = "none"
        repaired["output_mode"] = "table"

    # Confidence boost for well-anchored filters and explicit operations.
    confidence = float(repaired.get("confidence") or 0.0)
    if filters:
        confidence += 0.15
    if repaired.get("measures"):
        confidence += 0.08
    if repaired.get("dimensions"):
        confidence += 0.08
    repaired["confidence"] = round(min(1.0, confidence), 3)
    return repaired


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_plan_intent(value: Any) -> str:
    intent = str(value or "").strip().lower()
    allowed = {
        "count",
        "lookup",
        "schema",
        "raw_rows",
        "aggregation",
        "group_aggregation",
        "comparison",
        "trend",
        "relationship",
        "chart",
        "quality",
        "ranking",
        "unknown",
    }
    return intent if intent in allowed else "unknown"


def _normalize_plan_output_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    allowed = {"scalar", "rows", "table", "chart", "table_and_chart", "clarify"}
    return mode if mode in allowed else "table"


def _normalize_workbook_scope(value: Any) -> str:
    scope = str(value or "").strip().lower()
    allowed = {"single_sheet", "all_sheets", "unknown"}
    return scope if scope in allowed else "unknown"


def _normalize_filter_operator(value: Any) -> str:
    op = str(value or "").strip().lower()
    aliases = {
        "equals": "=",
        "equal": "=",
        "eq": "=",
        "not equal": "!=",
        "notequals": "!=",
        "includes": "contains",
        "has": "contains",
        "prefix": "startswith",
        "suffix": "endswith",
        "blank": "is_null",
        "null": "is_null",
        "not blank": "not_null",
        "not null": "not_null",
    }
    normalized = aliases.get(op, op)
    allowed = {
        "=",
        "!=",
        ">",
        ">=",
        "<",
        "<=",
        "contains",
        "startswith",
        "endswith",
        "between",
        "in",
        "not_in",
        "is_null",
        "not_null",
    }
    return normalized if normalized in allowed else "contains"


def _coerce_llm_understanding(df: pd.DataFrame, llm_understanding: Optional[dict[str, Any]]) -> dict[str, Any]:
    payload = llm_understanding if isinstance(llm_understanding, dict) else {}

    raw_columns = payload.get("columnCandidates")
    column_candidates: list[dict[str, Any]] = []
    if isinstance(raw_columns, list):
        for item in raw_columns:
            if not isinstance(item, dict):
                continue
            raw_col = str(item.get("column") or "").strip()
            mapped_col = raw_col if raw_col in df.columns else choose_best_column(df, raw_col)
            if not mapped_col:
                continue
            role = str(item.get("role") or "target").strip().lower()
            if role not in {"filter", "group", "metric", "time", "identifier", "target"}:
                role = "target"
            column_candidates.append(
                {
                    "column": mapped_col,
                    "role": role,
                    "confidence": max(0.0, min(1.0, _safe_float(item.get("confidence"), 0.0))),
                }
            )

    raw_filters = payload.get("filters") if isinstance(payload.get("filters"), list) else payload.get("filterCandidates")
    filters: list[dict[str, Any]] = []
    if isinstance(raw_filters, list):
        for item in raw_filters:
            if not isinstance(item, dict):
                continue
            raw_col = str(item.get("column") or "").strip()
            mapped_col = raw_col if raw_col in df.columns else choose_best_column(df, raw_col)
            if not mapped_col:
                continue
            filters.append(
                {
                    "column": mapped_col,
                    "operator": _normalize_filter_operator(item.get("operator")),
                    "value": item.get("value"),
                    "confidence": max(0.0, min(1.0, _safe_float(item.get("confidence"), 0.0))),
                }
            )

    return {
        "enabled": bool(payload.get("enabled", True)),
        "canonicalQuestion": str(payload.get("canonicalQuestion") or "").strip(),
        "alternativeInterpretations": [
            str(x).strip() for x in (payload.get("alternativeInterpretations") or payload.get("alternatives") or []) if str(x).strip()
        ][:5],
        "confidence": max(0.0, min(1.0, _safe_float(payload.get("confidence"), 0.0))),
        "reasoning": str(payload.get("reasoning") or "").strip(),
        "language": str(payload.get("language") or "").strip(),
        "intentFamily": _normalize_plan_intent(payload.get("intentFamily")),
        "outputMode": _normalize_plan_output_mode(payload.get("outputMode")),
        "workbookScope": _normalize_workbook_scope(payload.get("workbookScope")),
        "columnCandidates": column_candidates,
        "filters": filters,
        "timeRange": payload.get("timeRange") if isinstance(payload.get("timeRange"), dict) else {},
        "sort": payload.get("sort") if isinstance(payload.get("sort"), list) else [],
        "limit": payload.get("limit"),
        "distinct": bool(payload.get("distinct", False)),
        "chartPreference": detect_chart_preference(str(payload.get("chartPreference") or "")),
        "qualityChecks": payload.get("qualityChecks") if isinstance(payload.get("qualityChecks"), list) else [],
        "clarificationNeeded": bool(payload.get("clarificationNeeded", False)),
        "clarificationQuestion": str(payload.get("clarificationQuestion") or "").strip(),
    }


def fused_question_schema_relation(
    df: pd.DataFrame,
    question: str,
    understanding: Optional[dict[str, Any]] = None,
    max_items: int = 8,
) -> dict[str, Any]:
    base = question_schema_relation(df, question, max_items=max_items)
    if not isinstance(understanding, dict) or not understanding.get("enabled", True):
        return base

    scored: dict[str, float] = {str(col): 0.0 for col in df.columns}
    for idx, col in enumerate(base.get("relevantColumns", [])):
        col_name = str(col)
        if col_name in scored:
            scored[col_name] += max(0.1, 1.0 - idx * 0.12)

    for item in understanding.get("columnCandidates", []):
        if not isinstance(item, dict):
            continue
        col = str(item.get("column") or "").strip()
        conf = max(0.0, min(1.0, _safe_float(item.get("confidence"), 0.0)))
        if col in scored:
            scored[col] += 2.0 * conf
        else:
            mapped = choose_best_column(df, col)
            if mapped:
                scored[mapped] += 1.5 * conf

    ranked = sorted(
        [(score, col) for col, score in scored.items() if score > 0],
        key=lambda item: (-item[0], item[1].lower()),
    )
    relevant = [col for _, col in ranked[:max_items]]

    base_score = _safe_float(base.get("score"), 0.0)
    llm_conf = max(0.0, min(1.0, _safe_float(understanding.get("confidence"), 0.0)))
    merged_score = round(min(1.0, base_score * 0.55 + llm_conf * 0.45 + (0.06 * len(relevant))), 3)

    return {
        "questionTokens": base.get("questionTokens", []),
        "relevantColumns": relevant,
        "matchedEntities": base.get("matchedEntities", []),
        "score": merged_score,
        "llmIntentFamily": understanding.get("intentFamily", "unknown"),
        "llmColumnCandidates": understanding.get("columnCandidates", []),
        "llmFilterCandidates": understanding.get("filters", []),
        "workbookScope": understanding.get("workbookScope", "unknown"),
    }


def classify_question_scope_hybrid(
    df: pd.DataFrame,
    question: str,
    understanding: Optional[dict[str, Any]] = None,
) -> str:
    q = normalize_question_text(question)
    if not q:
        return "clarify"

    token_count = len(re.findall(r"[a-z0-9]+", q))
    if token_count < 2:
        return "clarify"

    if any(marker in q for marker in OUT_OF_SCOPE_MARKERS):
        return "out_of_scope"

    if should_use_rule_based(q):
        return "in_scope"

    relation = fused_question_schema_relation(df, q, understanding=understanding)
    score = _safe_float(relation.get("score"), 0.0)
    relevant = relation.get("relevantColumns", []) if isinstance(relation.get("relevantColumns"), list) else []
    if relevant:
        return "in_scope"
    if score >= 0.35:
        return "in_scope"

    if isinstance(understanding, dict) and understanding.get("enabled", True):
        intent_family = str(understanding.get("intentFamily") or "unknown")
        col_cands = understanding.get("columnCandidates") if isinstance(understanding.get("columnCandidates"), list) else []
        filter_cands = understanding.get("filters") if isinstance(understanding.get("filters"), list) else []
        if intent_family != "unknown" and (col_cands or filter_cands or score >= 0.22):
            return "in_scope"
        if understanding.get("clarificationNeeded"):
            return "clarify"

    has_data_marker = any(marker in q for marker in DATA_QUERY_MARKERS)
    return "clarify" if has_data_marker else "out_of_scope"


def build_query_plan(df: pd.DataFrame, question: str, llm_understanding: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    normalized = normalize_question_text(question)
    llm_map = _coerce_llm_understanding(df, llm_understanding)
    relation = fused_question_schema_relation(df, normalized, understanding=llm_map)
    raw_relevance = relation.get("relevantColumns")
    relevance_cols = [str(col) for col in raw_relevance] if isinstance(raw_relevance, list) else []
    confidence = _safe_float(relation.get("score"), 0.0)

    chart_pref = llm_map.get("chartPreference") or detect_chart_preference(normalized)
    intent_family = detect_intent_family(normalized)
    subqueries = split_compound_question(normalized)

    measures: list[str] = []
    dimensions: list[str] = []

    group_hint, metric_hint = _extract_superlative_hints(normalized)
    op, agg_group_hint, agg_metric_hint = _extract_group_aggregation_hints(normalized)
    metric_by_group_hint, group_by_metric_hint = _extract_metric_by_group_hints(normalized)

    group_col = choose_group_column(df, normalized, group_hint=group_hint or agg_group_hint or group_by_metric_hint)
    metric_col = choose_metric_column(
        df,
        normalized,
        exclude_column=group_col,
        metric_hint=metric_hint or agg_metric_hint or metric_by_group_hint,
    )

    if metric_col:
        measures.append(metric_col)
    if group_col:
        dimensions.append(group_col)

    if not measures:
        llm_metric = next(
            (
                str(item.get("column") or "")
                for item in llm_map.get("columnCandidates", [])
                if isinstance(item, dict) and str(item.get("role") or "").strip().lower() == "metric"
            ),
            "",
        )
        mapped_metric = llm_metric if llm_metric in df.columns else choose_metric_column(df, llm_metric) if llm_metric else None
        if mapped_metric and mapped_metric not in measures:
            measures.append(mapped_metric)

    if not dimensions:
        llm_group = next(
            (
                str(item.get("column") or "")
                for item in llm_map.get("columnCandidates", [])
                if isinstance(item, dict) and str(item.get("role") or "").strip().lower() == "group"
            ),
            "",
        )
        mapped_group = llm_group if llm_group in df.columns else choose_group_column(df, llm_group) if llm_group else None
        if mapped_group and mapped_group not in dimensions:
            dimensions.append(mapped_group)

    if not measures:
        for col in _numeric_like_columns(df):
            if col in relevance_cols and col not in measures:
                measures.append(col)
                break

    if not dimensions:
        for col in relevance_cols:
            if col not in _numeric_like_columns(df):
                dimensions.append(col)
                break

    row_limit, limit_direction = extract_requested_row_limit(normalized)
    if row_limit is None and isinstance(llm_map.get("limit"), int):
        row_limit = max(1, min(int(llm_map.get("limit")), 5000))
    sort_spec = extract_sort_spec(normalized)
    if not sort_spec and isinstance(llm_map.get("sort"), list):
        for item in llm_map.get("sort", []):
            if not isinstance(item, dict):
                continue
            mapped_col = choose_best_column(df, str(item.get("column") or ""))
            if not mapped_col:
                continue
            direction = str(item.get("direction") or "desc").strip().lower()
            if direction not in {"asc", "descending", "desc", "ascending"}:
                direction = "desc"
            sort_spec = [{"column": mapped_col, "direction": "asc" if direction.startswith("asc") else "desc"}]
            break
    if row_limit is not None and not sort_spec:
        sort_spec = [{"column": measures[0] if measures else (dimensions[0] if dimensions else ""), "direction": "desc" if limit_direction == "head" else "asc"}]

    scope = classify_question_scope_hybrid(df, normalized, understanding=llm_map)

    merged_filters: list[dict[str, Any]] = []
    for f in llm_map.get("filters", []):
        if isinstance(f, dict):
            merged_filters.append({**f, "source": "llm"})
    for f in extract_filter_specs(normalized):
        if isinstance(f, dict):
            merged_filters.append({**f, "source": "rule"})

    llm_family = _normalize_plan_intent(llm_map.get("intentFamily"))
    if llm_family != "unknown":
        intent_family = llm_family

    output_mode = detect_output_mode(normalized, chart_pref)
    llm_mode = _normalize_plan_output_mode(llm_map.get("outputMode"))
    if llm_mode != "table":
        output_mode = llm_mode
    elif asks_chart_request(normalized) and output_mode == "table":
        output_mode = "table_and_chart"

    workbook_scope = _normalize_workbook_scope(llm_map.get("workbookScope") or detect_sheet_mode(normalized))
    clarification_needed = bool(llm_map.get("clarificationNeeded", False))
    if scope != "in_scope":
        clarification_needed = True

    quality_checks: list[str] = []
    if asks_duplicate_question(normalized):
        quality_checks.append("duplicates")
    if asks_missing_question(normalized) or asks_null_question(normalized):
        quality_checks.append("missing")
    if asks_outlier_question(normalized):
        quality_checks.append("outliers")

    sub_intents: list[str] = []
    if asks_compare_question(normalized):
        sub_intents.append("comparison")
    if asks_chart_request(normalized):
        sub_intents.append("chart")
    if asks_distinct(normalized):
        sub_intents.append("distinct")

    comparison_payload: dict[str, Any] = {}
    if asks_compare_question(normalized):
        group_hint, metric_hint = _extract_superlative_hints(normalized)
        comparison_payload = {
            "compare_by": group_hint or (dimensions[0] if dimensions else ""),
            "values": [metric_hint] if metric_hint else [],
        }

    plan: dict[str, Any] = {
        "normalized_question": normalized,
        "scope": scope,
        "intent_family": intent_family,
        "confidence": round(confidence, 3),
        "measures": measures,
        "dimensions": dimensions,
        "filters": merged_filters,
        "time_range": llm_map.get("timeRange") or extract_time_range_spec(normalized),
        "sort": sort_spec,
        "limit": row_limit,
        "distinct": bool(llm_map.get("distinct")) or asks_distinct(normalized),
        "sheet_mode": workbook_scope,
        "output_mode": output_mode,
        "chart_pref": chart_pref,
        "subqueries": subqueries if len(subqueries) > 1 else [],
        "explanation": f"Detected {intent_family} intent with confidence {round(confidence, 3)}.",
        "operation": op,
        "relevant_columns": relevance_cols,
        "schema_relation": relation,
        "llm_understanding": llm_map,
        "clarification_needed": clarification_needed,
        "clarification_question": str(llm_map.get("clarificationQuestion") or "").strip(),
        # planner-first aliases
        "intent_family_normalized": intent_family,
        "sub_intents": sub_intents,
        "workbook_scope": workbook_scope,
        "target_sheet": None,
        "columns": {
            "group": list(dimensions),
            "metric": list(measures),
            "time": [_choose_time_column_for_plan(df, normalized)] if _choose_time_column_for_plan(df, normalized) else [],
            "filter": [str(f.get("column")) for f in merged_filters if isinstance(f, dict) and f.get("column")],
            "identifier": [str(c) for c in df.columns if any(tok in normalize_token(c) for tok in ["id", "key", "code", "uuid", "ref"])][:3],
        },
        "text_rules": [{"hint": hint, "value": value, "operator": op} for hint, value, op in extract_text_match_rules(normalized)],
        "date_range": llm_map.get("timeRange") or extract_time_range_spec(normalized),
        "aggregation": op,
        "comparison": comparison_payload,
        "quality_checks": quality_checks,
        "needs_clarification": clarification_needed,
        "clarification_reason": str(llm_map.get("clarificationQuestion") or "").strip(),
    }
    return rank_and_repair_query_plan(df, normalized, plan)


def build_canonical_query_plan(
    df: pd.DataFrame,
    question: str,
    plan: Optional[dict[str, Any]] = None,
) -> QueryPlan:
    base_plan = plan if isinstance(plan, dict) else build_query_plan(df, question)
    canonical = QueryPlan.from_dict(base_plan, question=question)
    repaired = rank_and_repair_query_plan(df, canonical.normalized_question, canonical.to_dict())
    return QueryPlan.from_dict(repaired, question=canonical.normalized_question)


def _planner_route_candidates(question: str, plan: QueryPlan) -> list[str]:
    q = normalize_question_text(question)
    detected_family = str(plan.intent_family or "").strip().lower()

    requested: list[str] = []
    if detected_family:
        requested.append(detected_family)
    if asks_schema(q):
        requested.append("schema")
    if asks_data_quality_question(q):
        requested.append("data_quality")
    if asks_percent_question(q) or asks_growth_question(q) or asks_nth_rank_question(q):
        requested.append("aggregation")
    if asks_distinct(q):
        requested.append("distinct")
    if asks_compare_question(q):
        requested.append("comparison")
    if asks_group_aggregation(q) or asks_superlative_aggregation(q) or asks_metric_by_group(q) or asks_scalar_aggregation(q):
        requested.append("aggregation")
    if asks_relationship_question(q):
        requested.append("relationship")
    if asks_trend_question(q):
        requested.append("trend")
    if asks_chart_request(q):
        requested.append("chart")
    if asks_count(q):
        requested.append("count")
    if (
        detected_family == "lookup"
        or asks_specific_lookup(q)
        or asks_id_lookup(q)
        or bool(extract_text_match_rules(q))
        or any(
            isinstance(f, dict)
            and str(f.get("op") or f.get("operator") or "").lower() in {"startswith", "endswith", "contains", "=", "=="}
            for f in plan.filters
        )
    ):
        requested.append("lookup")

    priority = [
        "schema",
        "data_quality",
        "distinct",
        "lookup",
        "comparison",
        "aggregation",
        "relationship",
        "trend",
        "chart",
        "count",
    ]

    ordered: list[str] = []
    for route in priority:
        key = str(route or "").strip().lower()
        if key and key in requested and key not in ordered:
            ordered.append(key)

    for route in requested:
        key = str(route or "").strip().lower()
        if key and key not in ordered:
            ordered.append(key)
    return ordered


def _execute_planner_route(
    df: pd.DataFrame,
    question: str,
    plan_dict: dict[str, Any],
    route: str,
) -> Optional[tuple[pd.DataFrame, str]]:
    if route == "schema":
        return (
            pd.DataFrame({"column": [str(c) for c in df.columns], "dtype": [str(t) for t in df.dtypes]}),
            "Returned schema information (columns and dtypes).",
        )

    if route == "data_quality":
        return data_quality_intent_result(df, question, plan=plan_dict)

    if route == "distinct":
        return distinct_intent_result(df, question, plan=plan_dict)

    if route == "comparison":
        comparison = comparison_result(df, question)
        if comparison is None:
            return None
        result_df, summary = comparison
        result_df = apply_query_plan_post_processing(result_df, question, plan_dict)
        return result_df, summary

    if route == "aggregation":
        for resolver in (
            nth_rank_result,
            percent_contribution_result,
            growth_result,
            running_total_result,
            superlative_aggregation_result,
            group_aggregation_result,
            metric_by_group_result,
            scalar_aggregation_result,
        ):
            outcome = resolver(df, question)
            if outcome is None:
                continue
            result_df, summary = outcome
            result_df = apply_query_plan_post_processing(result_df, question, plan_dict)
            return result_df, summary
        return None

    if route == "relationship":
        outcome = relationship_insight_result(df, question)
        if outcome is None:
            return None
        result_df, summary = outcome
        result_df = apply_query_plan_post_processing(result_df, question, plan_dict)
        return result_df, summary

    if route == "trend":
        outcome = trend_result(df, question)
        if outcome is None:
            return None
        result_df, summary = outcome
        result_df = apply_query_plan_post_processing(result_df, question, plan_dict)
        return result_df, summary

    if route == "chart":
        outcome = chart_intent_result(df, question)
        if outcome is None:
            return None
        result_df, summary = outcome
        result_df = apply_query_plan_post_processing(result_df, question, plan_dict)
        return result_df, summary

    if route == "count" and asks_count(question):
        return build_count_result(df, question)

    if route == "lookup":
        route_trace: dict[str, Any] = {"queryPlan": plan_dict}
        result_df, summary = default_fallback_result(df, question, trace=route_trace)
        if result_df.empty:
            has_text_filters = any(
                isinstance(item, dict)
                and str(item.get("op") or item.get("operator") or "").lower() in {"startswith", "endswith", "contains", "=", "=="}
                for item in plan_dict.get("filters", [])
            )
            if not has_text_filters:
                return None
        return result_df, summary

    return None


def execute_query_plan_deterministic(
    df: pd.DataFrame,
    question: str,
    plan: Optional[dict[str, Any]] = None,
    trace: Optional[dict[str, Any]] = None,
) -> Optional[tuple[pd.DataFrame, str]]:
    if trace is not None and isinstance(plan, dict):
        trace["planBeforeRepair"] = dict(plan)
    canonical = build_canonical_query_plan(df, question, plan=plan)
    plan_dict = canonical.to_dict()
    routes = _planner_route_candidates(question, canonical)

    if trace is not None:
        trace["queryPlan"] = plan_dict
        trace["planAfterRepair"] = plan_dict
        trace["plannerRoutes"] = routes

    for route in routes:
        outcome = _execute_planner_route(df, question, plan_dict, route)
        if outcome is None:
            continue
        if trace is not None:
            trace["plannerRoute"] = route
            trace["decisionPath"] = route
            trace["executionModule"] = route
        return outcome

    return None


def execute_query_plan(
    df: pd.DataFrame,
    question: str,
    plan: Optional[dict[str, Any]] = None,
    trace: Optional[dict[str, Any]] = None,
) -> Optional[tuple[pd.DataFrame, str]]:
    return execute_query_plan_deterministic(df, question, plan=plan, trace=trace)


def extract_requested_row_limit(question: str) -> tuple[Optional[int], Optional[str]]:
    q = question.lower()

    # Handles patterns such as "top 10", "bottom 5", "first 20", "last 15".
    directional = re.search(r"\b(top|bottom|first|last)\s+(\d{1,4})\b", q)
    if directional:
        keyword = directional.group(1)
        count = int(directional.group(2))
        direction = "tail" if keyword in {"bottom", "last"} else "head"
        return count, direction

    # Handles generic "show 10 rows/records/results" style questions.
    generic = re.search(r"\b(\d{1,4})\s+(?:rows|row|records|record|results|result|items|item)\b", q)
    if generic and any(k in q for k in ["show", "list", "give", "return", "display"]):
        return int(generic.group(1)), "head"

    return None, None


def apply_requested_row_limit(
    result_df: pd.DataFrame,
    question: str,
    default_limit: int = 5000,
) -> tuple[pd.DataFrame, Optional[int]]:
    requested_limit, direction = extract_requested_row_limit(question)
    if requested_limit is not None:
        requested_limit = max(1, min(requested_limit, default_limit))
        if direction == "tail":
            return result_df.tail(requested_limit).reset_index(drop=True), requested_limit
        return result_df.head(requested_limit).reset_index(drop=True), requested_limit

    max_rows = len(result_df) if wants_full_data(question) else default_limit
    return result_df.head(max_rows).reset_index(drop=True), None


def wants_full_data(question: str) -> bool:
    q = question.lower()
    has_all_intent = any(k in q for k in ["all", "full", "entire", "complete", "everything", "whole", "show all", "all rows"]) \
        or bool(re.search(r"\b(?:list|show|display|return)\b.*\b(all|everything|entire|complete)\b", q))
    if not has_all_intent:
        return False

    # Guardrail: treat "all <value> records/rows" as a filtered lookup intent,
    # e.g., "give all Sarah records".
    all_value_match = re.search(
        r"\b(?:give|show|list|display|return|get)?\s*all\s+([a-z0-9][a-z0-9 ._\-/]{1,80}?)\s+(?:records?|rows?|entries?|results?)\b",
        q,
    )
    if all_value_match:
        candidate = all_value_match.group(1).strip(" ?.,")
        candidate_tokens = [token for token in re.split(r"[^a-z0-9]+", candidate) if token]
        if candidate_tokens and not all(token in ALL_RECORDS_VALUE_STOPWORDS for token in candidate_tokens):
            return False

    semantic_filter_markers = [
        " where ",
        " with ",
        " whose ",
        " starts",
        " sstarts",
        " begins",
        " ends",
        " contains",
        " equals",
        " equal ",
        " matching",
    ]
    if any(marker in f" {q} " for marker in semantic_filter_markers):
        return False

    if extract_text_match_rules(question):
        return False

    # Treat as explicit full-dataset request only when no filtering intent exists.
    filter_patterns = [
        r"\bwhere\b",
        r"\bwith\b",
        r"\bwhose\b",
        r"\bbetween\b",
        r"\bfrom\b",
        r"\bto\b",
        r"\bnot\b",
        r"\bby\b",
        r"\bequal(?:s)?\b",
        r"\bsold\s+by\b",
    ]
    return not any(re.search(pattern, q) for pattern in filter_patterns)


def asks_existence(question: str) -> bool:
    q = question.lower()
    # Structured filter queries should return matching rows, not a boolean exists table.
    if extract_text_match_rules(question) or extract_filter_specs(question):
        return False
    markers = ["is there", "are there", "any", "exists", "exist", "contains", "like", "present"]
    return any(m in q for m in markers)


def asks_count(question: str) -> bool:
    def _levenshtein(a: str, b: str) -> int:
        if a == b:
            return 0
        if not a:
            return len(b)
        if not b:
            return len(a)
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, start=1):
            curr = [i]
            for j, cb in enumerate(b, start=1):
                insert_cost = curr[j - 1] + 1
                delete_cost = prev[j] + 1
                replace_cost = prev[j - 1] + (0 if ca == cb else 1)
                curr.append(min(insert_cost, delete_cost, replace_cost))
            prev = curr
        return prev[-1]

    q = question.lower()
    markers = ["how many", "count", "number of", "total"]
    if any(m in q for m in markers):
        return True

    # Handle typo-like total phrases and record/row count wording.
    typo_patterns = [
        r"\btot[a-z]*\s+(?:records?|rows?)\b",
        r"\b(?:records?|rows?)\s+tot[a-z]*\b",
        r"\b(?:record|row)\s+count\b",
    ]
    if any(re.search(pattern, q) for pattern in typo_patterns):
        return True

    # Fuzzy fallback for misspellings like "toatal records".
    tokens = re.findall(r"[a-z]+", q)
    has_record_or_row = any(t in {"record", "records", "row", "rows"} for t in tokens)
    if has_record_or_row and any(_levenshtein(t, "total") <= 2 for t in tokens):
        return True

    return False


def asks_row_count(question: str) -> bool:
    q = question.lower()
    return bool(re.search(r"\b(rows?|records?|entries?)\b", q))


def extract_count_entity_hint(question: str) -> Optional[str]:
    q = re.sub(r"\s+", " ", question.lower().strip())
    if asks_row_count(q):
        return None

    patterns = [
        r"\bhow\s+many\s+([a-z0-9_\- ]{1,80}?)\s+(?:are\s+there|exist|do\s+we\s+have|were|was)\b",
        r"\bhow\s+many\s+([a-z0-9_\- ]{1,80})\b",
        r"\b(?:number|count)\s+of\s+([a-z0-9_\- ]{1,80})\b",
        r"\btotal\s+([a-z0-9_\- ]{1,80})\b",
    ]

    for pattern in patterns:
        m = re.search(pattern, q)
        if not m:
            continue
        hint = m.group(1).strip(" ?.,")
        hint = re.sub(r"\b(are|is|were|was|there)\b.*$", "", hint).strip()
        hint = re.sub(r"\b(all|the|total)\b", "", hint).strip()
        if hint and hint not in {"data", "dataset", "result", "results", "rows", "records", "entries"}:
            return hint
    return None


def _count_entity_aliases(entity_hint: Optional[str]) -> list[str]:
    if not entity_hint:
        return []

    tokens = _tokens(entity_hint)
    if not tokens:
        return [entity_hint]

    aliases: set[str] = {entity_hint.strip()}
    for token in tokens:
        base = token.rstrip("s") or token
        aliases.add(base)
        aliases.add(base + "s")
        aliases.add(token)

        for entity, synonyms in QUESTION_ENTITY_SYNONYMS.items():
            bucket = {entity, *synonyms}
            if base not in bucket:
                continue
            for alt in bucket:
                aliases.add(alt)
                alt_base = alt.rstrip("s") or alt
                aliases.add(alt_base)
                aliases.add(alt_base + "s")

    return sorted({a for a in aliases if a.strip()}, key=len, reverse=True)


def _is_categorical_column(series: pd.Series) -> bool:
    non_null = series.dropna()
    if non_null.empty:
        return False

    unique_count = int(non_null.nunique(dropna=True))
    if unique_count < 2:
        return False

    parsed = _coerce_numeric_series(non_null)
    numeric_ratio = float(parsed.notna().mean()) if len(non_null) else 0.0
    if numeric_ratio > 0.85 and unique_count > max(20, int(len(non_null) * 0.25)):
        return False
    return True


def _score_count_column(
    col_name: str,
    series: pd.Series,
    aliases: list[str],
    date_like: set[str],
    numeric_like: set[str],
) -> float:
    if col_name in date_like:
        return -99.0

    non_null = series.dropna()
    if non_null.empty:
        return -99.0

    col_norm = normalize_token(col_name)
    unique_count = int(non_null.nunique(dropna=True))
    row_count = len(non_null)
    unique_ratio = float(unique_count) / float(row_count) if row_count else 0.0

    hint_score = 0.0
    normalized_aliases = [normalize_token(alias) for alias in aliases if normalize_token(alias)]
    for alias_norm in normalized_aliases:
        if col_norm == alias_norm:
            hint_score = max(hint_score, 10.0)
        elif alias_norm in col_norm or col_norm in alias_norm:
            hint_score = max(hint_score, 7.0)
        elif any(part in col_norm for part in alias_norm.split() if len(part) > 2):
            hint_score = max(hint_score, 4.0)

    if unique_ratio <= 0.02:
        cardinality_score = 3.5
    elif unique_ratio <= 0.10:
        cardinality_score = 3.0
    elif unique_ratio <= 0.35:
        cardinality_score = 2.0
    elif unique_ratio <= 0.60:
        cardinality_score = 0.75
    else:
        cardinality_score = -1.0

    id_like_tokens = ["id", "key", "uuid", "guid", "ref", "number", "no", "code"]
    has_id_like_name = any(token in col_norm for token in id_like_tokens)
    explicit_id_requested = any(alias in col_norm or col_norm in alias for alias in normalized_aliases)
    id_penalty = -5.0 if has_id_like_name and not explicit_id_requested else 0.0

    categorical_bonus = 2.0 if _is_categorical_column(series) else -1.0
    numeric_penalty = -1.5 if (col_name in numeric_like and not explicit_id_requested) else 0.0

    return hint_score + cardinality_score + id_penalty + categorical_bonus + numeric_penalty


def choose_count_entity_column(df: pd.DataFrame, entity_hint: Optional[str]) -> Optional[str]:
    if df.empty:
        return None

    date_like = set(_date_like_columns(df))
    numeric_like = set(_numeric_like_columns(df))
    aliases = _count_entity_aliases(entity_hint)

    if aliases:
        alias_match = pick_column_by_aliases(df, aliases)
        if alias_match and alias_match not in date_like:
            return alias_match

    best_col: Optional[str] = None
    best_score = -99.0

    for col in df.columns:
        col_name = str(col)
        series = _first_series_for_column_name(df, col_name)
        if series is None:
            continue

        score = _score_count_column(
            col_name=col_name,
            series=series,
            aliases=aliases,
            date_like=date_like,
            numeric_like=numeric_like,
        )
        if score > best_score:
            best_score = score
            best_col = col_name

    if best_col and best_score > 0.0:
        return best_col

    for col in df.columns:
        col_name = str(col)
        if col_name in date_like or col_name in numeric_like:
            continue
        series = _first_series_for_column_name(df, col_name)
        if series is None:
            continue
        if _is_categorical_column(series):
            return col_name

    return None


def build_count_result(df: pd.DataFrame, question: str) -> tuple[pd.DataFrame, str]:
    entity_hint = extract_count_entity_hint(question)
    if not entity_hint or asks_row_count(question):
        count_value = int(len(df))
        return pd.DataFrame({"count": [count_value]}), f"Returned total row count: {count_value}."

    entity_col = choose_count_entity_column(df, entity_hint)
    if entity_col:
        entity_series = _first_series_for_column_name(df, entity_col)
        if entity_series is not None:
            cleaned = entity_series.astype(str).str.strip()
            lower_cleaned = cleaned.str.lower()
            cleaned = cleaned.mask(lower_cleaned.isin({"", "nan", "none", "null"}), pd.NA).dropna()
            distinct_count = int(cleaned.nunique(dropna=True))
            label = re.sub(r"[^a-z0-9 ]", "", normalize_token(entity_hint) or "entities").strip() or "entities"
            return (
                pd.DataFrame({f"distinct {label}": [distinct_count]}),
                f"Returned distinct {entity_hint} count: {distinct_count} (from column '{entity_col}').",
            )

    count_value = int(len(df))
    return (
        pd.DataFrame({"count": [count_value]}),
        f"Could not find a column matching '{entity_hint}'. Returned total row count: {count_value}.",
    )


def asks_schema(question: str) -> bool:
    q = question.lower()
    markers = ["column", "columns", "schema", "fields", "header"]
    return any(m in q for m in markers)


def asks_comparison(question: str) -> bool:
    q = question.lower()
    return any(marker in q for marker in COMPARISON_MARKERS)


def asks_id_lookup(question: str) -> bool:
    q = question.lower()
    return " id " in f" {q} " or q.startswith("id ") or "for id" in q


def asks_specific_lookup(question: str) -> bool:
    q = question.lower().strip()
    if re.search(
        r"\b(?:give|show|list|display|return|get)?\s*all\s+[a-z0-9][a-z0-9 ._\-/]{1,80}\s+(?:records?|rows?|entries?|results?)\b",
        q,
    ):
        return True
    if re.search(r"\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b", q):
        return True
    if re.search(
        r"\b(?:whose|where|with|for)\s+[a-z][a-z0-9_\- ]{0,60}\s+(?:starts?\s+(?:with|from)|starting\s+with|begins?\s+with|prefix(?:ed)?\s+with)\s+[a-z0-9][a-z0-9 ._\-/]{0,80}\b",
        q,
    ):
        return True
    if re.search(r"\b(?:sold|created|handled|managed)\s+by\s+[a-z0-9][a-z0-9 ._\-/]{1,80}\b", q):
        return True
    if re.search(r"\b(?:rows?|records?|entries?|details?|data)\s+(?:for|of)\s+[a-z][a-z0-9 ._\-/]{1,80}\b", q):
        return True
    if re.search(r"\b(?:where\s+do\s+we\s+have|do\s+we\s+have)\s+[a-z][a-z0-9 ._\-/]{1,80}\b", q):
        return True
    if re.search(r"\bjust\s+[a-z][a-z0-9 ._\-/]{1,80}\s+data\b", q):
        return True
    if re.search(r"\bpull\s+[a-z][a-z0-9 ._\-/]{1,80}\s+data\b", q):
        return True
    if re.search(r"\b[a-z][a-z0-9 ._\-/]{1,80}\s+related\s+(?:rows?|records?|data)\b", q):
        return True
    if re.search(r"\b[a-z][a-z0-9 ._\-/]{1,80}\s+ke\s+records\b", q):
        return True
    if "for this " in q:
        return True
    if re.search(r"\b[a-z][a-z0-9_\- ]{1,60}\s*(?:=|\bequals\b)\s+[^\s].+", q):
        return True
    if any(k in q for k in ["status of", "details of", "record for", "for email", "for id"]):
        return True
    return False


def _contains_date_fragment(question: str) -> bool:
    q = question.lower()
    month_tokens = r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    patterns = [
        rf"\b\d{{1,2}}\s*(?:{month_tokens})\b",
        rf"\b(?:{month_tokens})\s*\d{{1,2}}(?:,\s*\d{{2,4}})?\b",
        r"\b\d{4}-\d{1,2}-\d{1,2}\b",
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    ]
    return any(re.search(pattern, q) for pattern in patterns)


def asks_date_range(question: str) -> bool:
    q = re.sub(r"\s+", " ", question.lower().strip())
    between_pattern = r"\bbetween\s+.+?\s+(?:and|to)\s+.+"
    from_to_pattern = r"\bfrom\s+.+?\s+to\s+.+"
    if re.search(between_pattern, q) and _contains_date_fragment(q):
        return True
    if re.search(from_to_pattern, q) and _contains_date_fragment(q):
        return True
    return False


def should_use_rule_based(question: str) -> bool:
    q = normalize_question_text(question)
    return (
        wants_full_data(q)
        or asks_existence(q)
        or asks_count(q)
        or asks_schema(q)
        or asks_date_range(q)
        or asks_id_lookup(q)
        or asks_specific_lookup(q)
        or asks_superlative_aggregation(q)
        or asks_group_aggregation(q)
        or asks_relationship_question(q)
        or asks_metric_by_group(q)
        or asks_scalar_aggregation(q)
        or asks_trend_question(q)
        or asks_chart_request(q)
        or asks_compare_question(q)
        or asks_distinct(q)
        or asks_data_quality_question(q)
    )


def classify_question_scope(df: pd.DataFrame, question: str) -> str:
    return classify_question_scope_hybrid(df, question, understanding=None)


def _question_column_relevance(df: pd.DataFrame, question: str, max_items: int = 4) -> list[str]:
    q = question.lower().strip()
    q_tokens = _tokens(q)
    if not q_tokens:
        return []

    q_expanded = _expand_tokens_with_synonyms(q_tokens)
    q_norm = normalize_token(q)
    count_or_full_intent = asks_count(question) or wants_full_data(question)

    scored: list[tuple[float, str]] = []
    for col in df.columns:
        col_name = str(col)
        col_tokens = _tokens(col_name)
        col_expanded = _expand_tokens_with_synonyms(col_tokens)

        direct_overlap = len(q_tokens.intersection(col_tokens))
        semantic_overlap = len(q_expanded.intersection(col_expanded))
        score = float((direct_overlap * 4) + (semantic_overlap * 2))

        # Keep substring matching as a soft signal for compact identifiers.
        norm_col = normalize_token(col_name)
        if norm_col:
            if norm_col in q_norm:
                score += 2.5
            elif any(t and t in norm_col for t in q_expanded if len(t) > 3):
                score += 1.25

        if count_or_full_intent and any(
            token in norm_col
            for token in ["id", "key", "code", "number", "name", "order", "product", "customer", "client", "item"]
        ):
            score += 0.75

        if score > 0:
            scored.append((score, col_name))

    scored.sort(key=lambda item: (-item[0], item[1].lower()))
    return [name for _, name in scored[:max_items]]


def _expected_output_hint(question: str) -> str:
    q = question.lower().strip()
    if asks_date_range(q):
        return "rows"
    if asks_chart_request(q):
        return "chart"
    if asks_count(q):
        return "count"
    if asks_scalar_aggregation(q) or asks_group_aggregation(q) or asks_superlative_aggregation(q):
        return "aggregation"
    if asks_specific_lookup(q) or asks_existence(q):
        return "rows"
    return "rows, count, aggregation, or chart"


def _scope_rejection_reason(df: pd.DataFrame, question: str, relevant_columns: list[str]) -> str:
    q = question.lower().strip()
    if any(marker in q for marker in OUT_OF_SCOPE_MARKERS):
        return "Detected non-dataset intent (software/deployment/code generation request)."

    if asks_date_range(question):
        date_like = _date_like_columns(df)
        if date_like:
            return (
                "Detected date-range intent, but routing could not confidently bind to date fields. "
                f"Date-like columns available: {', '.join(date_like[:6])}."
            )
        return "Detected date-range intent, but no date-like columns were found in this dataset."

    if relevant_columns:
        return "Query is partially grounded but missing clear filter/output intent."

    has_data_marker = any(marker in q for marker in DATA_QUERY_MARKERS)
    if has_data_marker:
        return "Query uses generic data words but does not map clearly to dataset fields or operations."

    return "Could not map query entities/intent to uploaded dataset schema."


def build_scope_guidance(
    df: pd.DataFrame,
    question: str,
    escalation: bool = False,
    out_of_scope: bool = False,
) -> str:
    relevant_columns = _question_column_relevance(df, question)
    relevant_hint = ", ".join(relevant_columns)
    output_hint = _expected_output_hint(question)
    scope_reason = _scope_rejection_reason(df, question, relevant_columns)

    if out_of_scope or escalation:
        if relevant_columns:
            return (
                "I found partially relevant fields but the request is still ambiguous. "
                f"Potentially relevant columns: {relevant_hint}. "
                f"Please confirm: target column(s), filter condition(s), and expected output ({output_hint}). "
                f"Scope reason: {scope_reason}"
            )
        return (
            "This request is outside what I can answer from the current dataset. "
            "I can only answer questions that are based on the uploaded data. "
            f"Scope reason: {scope_reason}"
        )

    if relevant_columns:
        return (
            "I can help with this, but I need one clarification to proceed accurately. "
            f"Detected relevant columns: {relevant_hint}. "
            f"Please specify the exact filter(s) and the expected output ({output_hint})."
        )

    return (
        "I cannot answer this from the current dataset information. "
        "Please ask a question that is directly based on the uploaded data."
    )


def should_render_chart(question: str, chart_type: str, result_df: pd.DataFrame) -> bool:
    if result_df.empty:
        return False

    plan = build_query_plan(result_df, question)
    if plan.get("output_mode") == "scalar":
        return False
    if plan.get("intent_family") in {"schema", "count", "lookup", "data_quality"} and not asks_chart_request(question):
        return False

    # Avoid noisy charts for pure count/schema/lookup answers unless user explicitly asks for a chart.
    if asks_schema(question) or asks_existence(question) or asks_specific_lookup(question):
        return False
    if asks_count(question) and not asks_chart_request(question):
        return False

    if chart_type and chart_type != "auto":
        return chart_type != "none"

    if asks_comparison(question):
        return any(marker in question.lower() for marker in VISUALIZATION_MARKERS)

    # Auto mode should be conservative: render only when user signals visual intent.
    q = question.lower()
    has_visual_intent = any(marker in q for marker in VISUALIZATION_MARKERS)
    if not has_visual_intent:
        return False

    numeric_cols = [c for c in result_df.columns if pd.api.types.is_numeric_dtype(result_df[c])]
    return len(result_df) > 1 and bool(numeric_cols)


def asks_superlative_aggregation(question: str) -> bool:
    q = question.lower()
    return any(marker in q for marker in MAX_MARKERS + MIN_MARKERS)


def asks_group_aggregation(question: str) -> bool:
    q = question.lower()
    agg_markers = ["average", "avg", "mean", "sum", "total", "count", "min", "max", "minimum", "maximum"]
    return any(marker in q for marker in agg_markers) and any(token in q for token in [" by ", " per ", " across ", " among "])


def asks_relationship_question(question: str) -> bool:
    q = question.lower()
    if re.search(r"\brelated\s+(?:rows?|records?|data)\b", q):
        return False
    if "related" in q and "related to" not in q and "relationship" not in q and "correlation" not in q:
        return False
    markers = [
        "improve",
        "increase",
        "decrease",
        "impact",
        "affect",
        "influence",
        "related",
        "relationship",
        "correlation",
        "depend",
        "associate",
    ]
    return any(m in q for m in markers)


def asks_metric_by_group(question: str) -> bool:
    q = question.lower().strip()
    if asks_specific_lookup(q):
        return False
    if re.search(r"\b(?:sold|created|handled|managed)\s+by\b", q):
        return False
    return bool(re.search(r"\b([a-z0-9_\- ]{1,80})\s+(?:by|per)\s+([a-z0-9_\- ]{1,80})\b", q))


def asks_scalar_aggregation(question: str) -> bool:
    q = question.lower().strip()
    return any(k in q for k in ["average", "avg", "mean", "sum", "total", "count", "minimum", "min", "maximum", "max"])


def asks_trend_question(question: str) -> bool:
    q = question.lower().strip()
    return any(k in q for k in ["trend", "over time", "time series", "forecast", "predict", "projection", "projected"])


def asks_chart_request(question: str) -> bool:
    q = question.lower().strip()
    return any(k in q for k in ["bar chart", "pie chart", "line chart", "area chart", "scatter", "histogram", "heatmap", "chart", "plot", "graph"])


def asks_compare_question(question: str) -> bool:
    q = question.lower().strip()
    return any(k in q for k in ["compare", "comparison", "vs", "versus", "ranking", "rank"])


def _expand_tokens_with_synonyms(tokens: set[str]) -> set[str]:
    expanded = set(tokens)
    for token in list(tokens):
        base = token[:-1] if len(token) > 3 and token.endswith("s") else token
        for entity, synonyms in QUESTION_ENTITY_SYNONYMS.items():
            bucket = {entity, *synonyms}
            if base in bucket:
                expanded.update(bucket)
    return expanded


def _tokens(text: str) -> set[str]:
    raw = str(text)
    # Split camelCase/PascalCase so headers like ProductName -> product, name.
    raw = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", raw)
    raw = raw.lower()
    base_tokens = {t for t in re.split(r"[^a-z0-9]+", raw) if t}
    expanded = set(base_tokens)
    for token in list(base_tokens):
        if len(token) > 3 and token.endswith("ies"):
            expanded.add(token[:-3] + "y")
        if len(token) > 3 and token.endswith("s"):
            expanded.add(token[:-1])
        elif len(token) > 3:
            expanded.add(token + "s")
    return expanded


def question_schema_relation(df: pd.DataFrame, question: str, max_items: int = 6) -> dict[str, Any]:
    q = question.lower().strip()
    q_tokens = _tokens(q)
    if not q_tokens:
        return {
            "questionTokens": [],
            "relevantColumns": [],
            "matchedEntities": [],
            "score": 0.0,
        }

    q_expanded = _expand_tokens_with_synonyms(q_tokens)
    relevant_columns = _question_column_relevance(df, question, max_items=max_items)

    all_column_tokens: set[str] = set()
    for col in df.columns:
        all_column_tokens.update(_expand_tokens_with_synonyms(_tokens(str(col))))

    matched_entities: list[str] = []
    for entity, synonyms in QUESTION_ENTITY_SYNONYMS.items():
        bucket = {entity, *synonyms}
        if q_expanded.intersection(bucket) and all_column_tokens.intersection(bucket):
            matched_entities.append(entity)

    column_score = min(0.7, 0.14 * len(relevant_columns))
    entity_score = min(0.3, 0.1 * len(matched_entities))
    score = round(min(1.0, column_score + entity_score), 3)

    return {
        "questionTokens": sorted(q_tokens),
        "relevantColumns": relevant_columns,
        "matchedEntities": matched_entities,
        "score": score,
    }


def _token_overlap_score(a: str, b: str) -> int:
    return len(_tokens(a).intersection(_tokens(b)))


def _extract_superlative_hints(question: str) -> tuple[Optional[str], Optional[str]]:
    q = question.lower().strip()
    q = re.sub(r"\s+", " ", q)

    # Pattern: "top product by revenue" (group by metric)
    m = re.search(
        r"\b(?:top|bottom|best|worst|largest|smallest|most|least)\s+([a-z0-9_\- ]{1,80}?)\s+(?:by|per)\s+([a-z0-9_\- ]{1,80})\b",
        q,
    )
    if m:
        group_hint = m.group(1).strip()
        metric_hint = m.group(2).strip()
        return group_hint, metric_hint

    # Pattern: "highest revenue by region" (metric by group)
    m = re.search(
        r"\b(?:highest|lowest|max(?:imum)?|min(?:imum)?|top|bottom|most|least)\s+([a-z0-9_\- ]{1,80}?)\s+(?:by|per)\s+([a-z0-9_\- ]{1,80})\b",
        q,
    )
    if m:
        metric_hint = m.group(1).strip()
        group_hint = m.group(2).strip()
        return group_hint, metric_hint

    return None, None


def _extract_group_aggregation_hints(question: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    q = question.lower().strip()
    q = re.sub(r"\s+", " ", q)

    op_map = {
        "average": "mean",
        "avg": "mean",
        "mean": "mean",
        "sum": "sum",
        "total": "sum",
        "count": "count",
        "minimum": "min",
        "min": "min",
        "maximum": "max",
        "max": "max",
    }

    m = re.search(
        r"\b(average|avg|mean|sum|total|count|minimum|min|maximum|max)\s+([a-z0-9_\- ]{1,80}?)\s+(?:by|per|across|among)\s+([a-z0-9_\- ]{1,80})\b",
        q,
    )
    if not m:
        return None, None, None
    op = op_map.get(m.group(1), None)
    metric_hint = m.group(2).strip()
    group_hint = m.group(3).strip()
    return op, group_hint, metric_hint


def _extract_relationship_hints(question: str) -> tuple[Optional[str], Optional[str]]:
    q = question.lower().strip()
    q = re.sub(r"\s+", " ", q)

    # Examples: "does study hours improve marks", "how does attendance affect score"
    m = re.search(r"\b(?:does|do|can|will|is)\s+(.+?)\s+(?:improve|increase|decrease|impact|affect|influence)\s+(.+?)\??$", q)
    if m:
        return m.group(1).strip(), m.group(2).strip()

    # Examples: "relationship between attendance and marks", "correlation between x and y"
    m = re.search(r"\b(?:relationship|correlation|association)\s+between\s+(.+?)\s+and\s+(.+?)\??$", q)
    if m:
        return m.group(1).strip(), m.group(2).strip()

    # Examples: "is marks related to study hours"
    m = re.search(r"\b(.+?)\s+(?:related to|dependent on|depends on|associated with)\s+(.+?)\??$", q)
    if m:
        return m.group(2).strip(), m.group(1).strip()

    return None, None


def _extract_metric_by_group_hints(question: str) -> tuple[Optional[str], Optional[str]]:
    q = question.lower().strip()
    q = re.sub(r"\s+", " ", q)
    m = re.search(r"\b([a-z0-9_\- ]{1,80})\s+(?:by|per)\s+([a-z0-9_\- ]{1,80})\b", q)
    if not m:
        return None, None
    metric_hint = m.group(1).strip(" ?")
    group_hint = m.group(2).strip(" ?")
    return metric_hint, group_hint


def _extract_scalar_aggregation_hints(question: str) -> tuple[Optional[str], Optional[str]]:
    q = question.lower().strip()
    q = re.sub(r"\s+", " ", q)

    # Count intent should win when user asks about records/rows/items,
    # including typo-like total variants.
    has_count_entity = bool(re.search(r"\b(rows?|records?|items?)\b", q))
    has_count_word = "count" in q or bool(re.search(r"\btot[a-z]*\b", q))
    if has_count_entity and has_count_word:
        return "count", None

    m = re.search(r"\b(average|avg|mean|sum|total|count|minimum|min|maximum|max)\s+([a-z0-9_\- ]{1,80})\??$", q)
    if not m:
        if "total" in q and ("revenue" in q or "sales" in q):
            return "sum", "revenue" if "revenue" in q else "sales"
        if "count" in q and ("row" in q or "record" in q or "item" in q):
            return "count", None
        return None, None
    op = m.group(1)
    metric = m.group(2).strip(" ?")

    # "total <entity>" usually means count (e.g., total orders/products/customers),
    # while "total <metric>" means sum (e.g., total revenue/sales/amount).
    if op == "total":
        metric_tokens = _tokens(metric)
        metric_like_terms = {
            "revenue",
            "sales",
            "amount",
            "value",
            "price",
            "cost",
            "profit",
            "income",
            "expense",
            "qty",
            "quantity",
            "score",
            "marks",
            "rate",
            "discount",
        }
        if not metric_tokens.intersection(metric_like_terms):
            return "count", metric

    op_map = {"average": "mean", "avg": "mean", "mean": "mean", "sum": "sum", "total": "sum", "count": "count", "minimum": "min", "min": "min", "maximum": "max", "max": "max"}
    return op_map.get(op), metric


def choose_numeric_column_by_hint(df: pd.DataFrame, hint: Optional[str], exclude_column: Optional[str] = None) -> Optional[str]:
    numeric_cols = [c for c in _numeric_like_columns(df) if c != exclude_column]
    if not numeric_cols:
        return None
    if not hint:
        return numeric_cols[0]

    ranked = sorted(numeric_cols, key=lambda c: _token_overlap_score(c, hint), reverse=True)
    best = ranked[0]
    return best if _token_overlap_score(best, hint) > 0 else None


def build_reasoning_trace(df: pd.DataFrame, question: str) -> dict[str, Any]:
    q = question.strip()
    normalized_q = normalize_question_text(q)
    schema_relation = question_schema_relation(df, q)
    query_plan = build_query_plan(df, q)
    group_hint, metric_hint = _extract_superlative_hints(q)
    agg_op, agg_group_hint, agg_metric_hint = _extract_group_aggregation_hints(q)
    rel_x_hint, rel_y_hint = _extract_relationship_hints(q)
    superlative = asks_superlative_aggregation(q)
    group_agg = asks_group_aggregation(q)
    relation_q = asks_relationship_question(q)
    group_candidate = choose_group_column(df, q, group_hint=group_hint) if superlative else None
    metric_candidate = (
        choose_metric_column(df, q, exclude_column=group_candidate, metric_hint=metric_hint)
        if superlative
        else None
    )
    agg_group_candidate = choose_group_column(df, q, group_hint=agg_group_hint) if group_agg else None
    agg_metric_candidate = (
        choose_metric_column(df, q, exclude_column=agg_group_candidate, metric_hint=agg_metric_hint)
        if group_agg
        else None
    )
    rel_x_candidate = choose_numeric_column_by_hint(df, rel_x_hint) if relation_q else None
    rel_y_candidate = choose_numeric_column_by_hint(df, rel_y_hint, exclude_column=rel_x_candidate) if relation_q else None

    trace: dict[str, Any] = {
        "question": q,
        "normalizedQuestion": normalized_q,
        "rowCount": int(len(df)),
        "columns": [str(c) for c in df.columns],
        "numericLikeColumns": _numeric_like_columns(df),
        "questionSchemaRelation": schema_relation,
        "queryPlan": query_plan,
        "flags": {
            "wantsFullData": wants_full_data(q),
            "asksExistence": asks_existence(q),
            "asksCount": asks_count(q),
            "asksSchema": asks_schema(q),
            "asksDateRange": asks_date_range(q),
            "asksSpecificLookup": asks_specific_lookup(q),
            "asksDistinct": asks_distinct(q),
            "asksDataQuality": asks_data_quality_question(q),
            "asksComparison": asks_comparison(q),
            "asksSuperlativeAggregation": superlative,
            "asksGroupAggregation": group_agg,
            "asksRelationshipQuestion": relation_q,
            "shouldUseRuleBased": should_use_rule_based(q),
        },
        "superlative": {
            "groupHint": group_hint,
            "metricHint": metric_hint,
            "groupCandidate": group_candidate,
            "metricCandidate": metric_candidate,
        },
        "groupAggregation": {
            "op": agg_op,
            "groupHint": agg_group_hint,
            "metricHint": agg_metric_hint,
            "groupCandidate": agg_group_candidate,
            "metricCandidate": agg_metric_candidate,
        },
        "relationship": {
            "xHint": rel_x_hint,
            "yHint": rel_y_hint,
            "xCandidate": rel_x_candidate,
            "yCandidate": rel_y_candidate,
        },
    }
    return trace


def build_reasoning_trace_with_plan(
    df: pd.DataFrame,
    question: str,
    llm_understanding: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    trace = build_reasoning_trace(df, question)
    trace["queryPlan"] = build_query_plan(df, question, llm_understanding=llm_understanding)
    return trace


def build_reasoning_trace_v2(
    df: pd.DataFrame,
    question: str,
    llm_understanding: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    plan = build_query_plan(df, question, llm_understanding=llm_understanding)
    return {
        "question": question,
        "normalizedQuestion": normalize_question_advanced(question),
        "rowCount": int(len(df)),
        "columns": [str(c) for c in df.columns],
        "numericLikeColumns": _numeric_like_columns(df),
        "dateLikeColumns": _date_like_columns(df),
        "questionSchemaRelation": question_schema_relation(df, question),
        "queryPlan": plan,
    }


def should_render_chart_from_plan(plan: Optional[dict[str, Any]], result_df: pd.DataFrame) -> bool:
    if result_df.empty:
        return False
    plan_data = plan if isinstance(plan, dict) else {}
    output_mode = str(plan_data.get("output_mode") or "table").strip().lower()
    if output_mode not in {"chart", "table_and_chart"}:
        return False
    chart_pref = str(plan_data.get("chart_pref") or "auto").strip().lower()
    if chart_pref == "none":
        return False
    numeric_cols = [c for c in result_df.columns if pd.api.types.is_numeric_dtype(result_df[c])]
    return bool(numeric_cols)


def _first_series_for_column_name(df: pd.DataFrame, col_name: str) -> Optional[pd.Series]:
    for idx, col in enumerate(df.columns):
        if str(col) == col_name:
            return df.iloc[:, idx]
    return None


def _df_cache_signature(df: pd.DataFrame) -> tuple[int, tuple[str, ...]]:
    return (int(len(df)), tuple(str(col) for col in df.columns))


def _get_df_parse_cache(df: pd.DataFrame) -> dict[str, Any]:
    key = id(df)
    signature = _df_cache_signature(df)
    cached = _DF_DATE_PARSE_CACHE.get(key)
    if cached is not None and cached.get("signature") == signature:
        _DF_DATE_PARSE_CACHE.move_to_end(key)
        return cached

    cache_state: dict[str, Any] = {
        "signature": signature,
        "date_cols": None,
        "parsed_date_columns": {},
    }
    _DF_DATE_PARSE_CACHE[key] = cache_state
    _DF_DATE_PARSE_CACHE.move_to_end(key)
    while len(_DF_DATE_PARSE_CACHE) > _DF_PARSE_CACHE_LIMIT:
        _DF_DATE_PARSE_CACHE.popitem(last=False)
    return cache_state


def _sample_non_null_strings(series: pd.Series, sample_size: int = 600) -> pd.Series:
    non_null = series.dropna()
    if non_null.empty:
        return pd.Series(dtype="object")

    as_text = non_null.astype(str).str.strip()
    as_text = as_text[as_text != ""]
    if as_text.empty:
        return pd.Series(dtype="object")

    if len(as_text) > sample_size:
        return as_text.sample(n=sample_size, random_state=0, replace=False)
    return as_text


def _looks_iso_ymd_strings(sample: pd.Series, threshold: float = 0.8) -> bool:
    if sample.empty:
        return False
    s = sample.astype(str)
    pattern_like = (
        s.str.len().ge(10)
        & s.str.slice(4, 5).eq("-")
        & s.str.slice(7, 8).eq("-")
        & s.str.slice(0, 4).str.isdigit()
        & s.str.slice(5, 7).str.isdigit()
        & s.str.slice(8, 10).str.isdigit()
    )
    return bool(pattern_like.mean() >= threshold)


def _mostly_digit_strings(sample: pd.Series, threshold: float = 0.9) -> bool:
    if sample.empty:
        return False
    compact = sample.astype(str).str.replace(" ", "", regex=False)
    return bool(compact.str.isdigit().mean() >= threshold)


def _parse_datetime_series_cached(df: pd.DataFrame, col_name: str, series: pd.Series) -> pd.Series:
    cache = _get_df_parse_cache(df)
    parsed_cache = cache.get("parsed_date_columns")
    if isinstance(parsed_cache, dict) and col_name in parsed_cache:
        return parsed_cache[col_name]

    if pd.api.types.is_datetime64_any_dtype(series):
        parsed = pd.to_datetime(series, errors="coerce")
    else:
        sample = _sample_non_null_strings(series)
        use_dayfirst = False
        if not sample.empty:
            parsed_default_sample = pd.to_datetime(sample, errors="coerce", format="mixed")
            default_ratio = float(parsed_default_sample.notna().mean())
            if not _looks_iso_ymd_strings(sample) and default_ratio < 0.75:
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore",
                        message=r"Parsing dates in %Y-%m-%d format when dayfirst=True was specified.*",
                        category=UserWarning,
                    )
                    parsed_dayfirst_sample = pd.to_datetime(sample, errors="coerce", format="mixed", dayfirst=True)
                dayfirst_ratio = float(parsed_dayfirst_sample.notna().mean())
                use_dayfirst = dayfirst_ratio > (default_ratio + 0.05)

        if use_dayfirst:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message=r"Parsing dates in %Y-%m-%d format when dayfirst=True was specified.*",
                    category=UserWarning,
                )
                parsed = pd.to_datetime(series.astype(str), errors="coerce", format="mixed", dayfirst=True)
        else:
            parsed = pd.to_datetime(series.astype(str), errors="coerce", format="mixed")

    if isinstance(parsed_cache, dict):
        parsed_cache[col_name] = parsed
    return parsed


def _coerce_numeric_series(series: Any) -> pd.Series:
    if series is None:
        return pd.Series(dtype="float64")
    if not isinstance(series, pd.Series):
        series = pd.Series(series)

    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")

    text = series.astype(str).str.strip()
    text = text.replace({"": pd.NA, "nan": pd.NA, "none": pd.NA, "null": pd.NA})
    text = text.str.replace(_CURRENCY_TOKEN_PATTERN, "", regex=True)
    text = text.str.replace(_CURRENCY_SYMBOL_PATTERN, "", regex=True)
    text = text.str.replace(",", "", regex=False)
    # Treat accounting negatives like (123.45) as -123.45.
    text = text.str.replace(r"^\((.*)\)$", r"-\1", regex=True)
    return pd.to_numeric(text, errors="coerce")


def _numeric_like_columns(df: pd.DataFrame, threshold: float = 0.7) -> list[str]:
    names: list[str] = []
    for col in df.columns:
        col_name = str(col)
        if col_name in names:
            continue
        series = _first_series_for_column_name(df, col_name)
        if series is None:
            continue
        if pd.api.types.is_numeric_dtype(series):
            names.append(col_name)
            continue
        parsed = _coerce_numeric_series(series)
        if len(series) > 0 and parsed.notna().mean() >= threshold:
            names.append(col_name)
    return names


def _date_like_columns(df: pd.DataFrame, threshold: float = 0.7) -> list[str]:
    cache = _get_df_parse_cache(df)
    cached_cols = cache.get("date_cols")
    if isinstance(cached_cols, list):
        return list(cached_cols)

    cols: list[str] = []
    for col in df.columns:
        name = str(col)
        series = _first_series_for_column_name(df, name)
        if series is None:
            continue
        if pd.api.types.is_datetime64_any_dtype(series):
            cols.append(name)
            continue

        sample = _sample_non_null_strings(series)
        if sample.empty:
            continue

        norm_name = normalize_token(name)
        has_date_hint = any(token in norm_name for token in _DATE_HINT_TOKENS)
        if _mostly_digit_strings(sample) and not has_date_hint:
            continue

        parsed_sample = pd.to_datetime(sample, errors="coerce", format="mixed")
        sample_ratio = float(parsed_sample.notna().mean()) if len(sample) else 0.0
        if sample_ratio >= threshold or (has_date_hint and sample_ratio >= 0.35):
            cols.append(name)

    cache["date_cols"] = list(cols)
    return cols


def choose_group_column(df: pd.DataFrame, question: str, group_hint: Optional[str] = None) -> Optional[str]:
    q = question.lower()
    numeric_like = set(_numeric_like_columns(df))
    group_candidates = [str(c) for c in df.columns if str(c) not in numeric_like]
    if not group_candidates:
        return None

    by_match = re.search(r"\b(?:by|per|across|among)\s+([a-z0-9_\- ]{1,80})", q)
    by_phrase = ""
    if by_match:
        by_phrase = re.split(r"\b(and|for|where|with|whose|which|what)\b", by_match.group(1), maxsplit=1)[0].strip()

    def score(col_name: str) -> int:
        col_tokens = _tokens(col_name)
        if not col_tokens:
            return 0
        score_val = 0
        if group_hint:
            score_val += 6 * _token_overlap_score(col_name, group_hint)
        if by_phrase:
            score_val += 4 * len(col_tokens.intersection(_tokens(by_phrase)))
        score_val += len(col_tokens.intersection(_tokens(q)))

        # Avoid picking synthetic row identifiers unless question asks for it.
        if any(k in normalize_token(col_name) for k in ["id", "key", "code", "uuid"]) and "id" not in q:
            score_val -= 2
        return score_val

    ranked = sorted(group_candidates, key=score, reverse=True)
    return ranked[0] if ranked else group_candidates[0]


def choose_metric_column(
    df: pd.DataFrame,
    question: str,
    exclude_column: Optional[str] = None,
    metric_hint: Optional[str] = None,
) -> Optional[str]:
    q = question.lower()
    numeric_cols = [c for c in _numeric_like_columns(df) if c != exclude_column]
    if not numeric_cols:
        return None

    q_tokens = _tokens(q)
    overlap_ranked = sorted(
        numeric_cols,
        key=lambda col: (
            6 * _token_overlap_score(col, metric_hint or ""),
            len(_tokens(col).intersection(q_tokens)),
        ),
        reverse=True,
    )
    if overlap_ranked:
        return overlap_ranked[0]

    return numeric_cols[0]


def superlative_aggregation_result(df: pd.DataFrame, question: str) -> Optional[tuple[pd.DataFrame, str]]:
    if df.empty or not asks_superlative_aggregation(question):
        return None

    group_hint, metric_hint = _extract_superlative_hints(question)
    group_col = choose_group_column(df, question, group_hint=group_hint)
    metric_col = choose_metric_column(df, question, exclude_column=group_col, metric_hint=metric_hint)
    if not group_col or not metric_col:
        return None

    # If explicit hints are provided but cannot be mapped to schema meaningfully, avoid wrong answers.
    if group_hint and _token_overlap_score(group_col, group_hint) == 0:
        return None
    if metric_hint and _token_overlap_score(metric_col, metric_hint) == 0:
        return None

    # Joined DataFrames can contain duplicate column names; select by first index to force 1-D Series.
    group_positions = [idx for idx, col in enumerate(df.columns) if str(col) == group_col]
    metric_positions = [idx for idx, col in enumerate(df.columns) if str(col) == metric_col]
    if not group_positions or not metric_positions:
        return None

    group_series = df.iloc[:, group_positions[0]]
    metric_series = df.iloc[:, metric_positions[0]]

    working = pd.DataFrame({"__group__": group_series, "__metric__": metric_series}).copy()
    working = working.dropna(subset=["__group__"])
    working["__metric__"] = _coerce_numeric_series(working["__metric__"])
    working = working.dropna(subset=["__metric__"])
    grouped = working.groupby("__group__", dropna=False, as_index=False)["__metric__"].sum()
    grouped = grouped.rename(columns={"__group__": group_col, "__metric__": metric_col})
    if grouped.empty:
        return None

    q = question.lower()
    wants_min = any(marker in q for marker in MIN_MARKERS)
    picked_idx = grouped[metric_col].idxmin() if wants_min else grouped[metric_col].idxmax()
    best_row = grouped.loc[[picked_idx]].reset_index(drop=True)

    qualifier = "Lowest" if wants_min else "Highest"
    group_val = str(best_row.loc[0, group_col])
    metric_val = best_row.loc[0, metric_col]
    summary = f"{qualifier} {metric_col} is in {group_col} '{group_val}' with value {metric_val}."
    return best_row, summary


def group_aggregation_result(df: pd.DataFrame, question: str) -> Optional[tuple[pd.DataFrame, str]]:
    if df.empty or not asks_group_aggregation(question):
        return None

    op, group_hint, metric_hint = _extract_group_aggregation_hints(question)
    if not op:
        return None

    group_col = choose_group_column(df, question, group_hint=group_hint)
    metric_col = None if op == "count" else choose_metric_column(df, question, exclude_column=group_col, metric_hint=metric_hint)
    if not group_col:
        return None
    if op != "count" and not metric_col:
        return None

    group_series = _first_series_for_column_name(df, group_col)
    if group_series is None:
        return None

    if op == "count":
        working = pd.DataFrame({"__group__": group_series}).dropna(subset=["__group__"])
        grouped = working.groupby("__group__", dropna=False, as_index=False).size()
        grouped = grouped.rename(columns={"__group__": group_col, "size": "count"})
        value_col = "count"
    else:
        metric_series = _first_series_for_column_name(df, metric_col)
        if metric_series is None:
            return None
        working = pd.DataFrame({"__group__": group_series, "__metric__": metric_series}).dropna(subset=["__group__"])
        working["__metric__"] = _coerce_numeric_series(working["__metric__"])
        working = working.dropna(subset=["__metric__"])
        if working.empty:
            return None

        agg_name = {"mean": "mean", "sum": "sum", "min": "min", "max": "max"}[op]
        grouped = (
            working.groupby("__group__", dropna=False, as_index=False)["__metric__"]
            .agg(agg_name)
            .rename(columns={"__group__": group_col, "__metric__": metric_col})
        )
        value_col = metric_col

    if grouped.empty:
        return None

    grouped = grouped.sort_values(by=value_col, ascending=False).reset_index(drop=True)
    summary = f"Computed {op} of {value_col} by {group_col}."
    return grouped, summary


def relationship_insight_result(df: pd.DataFrame, question: str) -> Optional[tuple[pd.DataFrame, str]]:
    if df.empty or not asks_relationship_question(question):
        return None

    x_hint, y_hint = _extract_relationship_hints(question)
    x_col = choose_numeric_column_by_hint(df, x_hint)
    y_col = choose_numeric_column_by_hint(df, y_hint, exclude_column=x_col)

    numeric_cols = _numeric_like_columns(df)
    if not x_col and numeric_cols:
        x_col = numeric_cols[0]
    if not y_col and len(numeric_cols) > 1:
        y_col = numeric_cols[1] if numeric_cols[1] != x_col else (numeric_cols[0] if len(numeric_cols) > 0 else None)

    if not x_col or not y_col or x_col == y_col:
        return None

    x_series = _coerce_numeric_series(_first_series_for_column_name(df, x_col))
    y_series = _coerce_numeric_series(_first_series_for_column_name(df, y_col))
    if x_series is None or y_series is None:
        return None

    working = pd.DataFrame({"x": x_series, "y": y_series}).dropna()
    if len(working) < 3:
        return None

    corr = float(working["x"].corr(working["y"]))
    if pd.isna(corr):
        return None

    if corr >= 0.25:
        verdict = "Yes"
        relation = "positive"
        explanation = f"higher {x_col} tends to be associated with higher {y_col}"
    elif corr <= -0.25:
        verdict = "No"
        relation = "negative"
        explanation = f"higher {x_col} tends to be associated with lower {y_col}"
    else:
        verdict = "No clear"
        relation = "weak"
        explanation = f"there is no strong relationship between {x_col} and {y_col}"

    out = pd.DataFrame(
        [
            {
                "predictor": x_col,
                "target": y_col,
                "correlation": round(corr, 4),
                "relationship": relation,
                "samples": int(len(working)),
            }
        ]
    )
    summary = f"{verdict}, {explanation} (correlation {corr:.2f}, n={len(working)})."
    return out, summary


def metric_by_group_result(df: pd.DataFrame, question: str) -> Optional[tuple[pd.DataFrame, str]]:
    if df.empty or not asks_metric_by_group(question):
        return None

    metric_hint, group_hint = _extract_metric_by_group_hints(question)
    if not metric_hint or not group_hint:
        return None

    group_col = choose_group_column(df, question, group_hint=group_hint)
    metric_col = choose_metric_column(df, question, exclude_column=group_col, metric_hint=metric_hint)
    if not group_col or not metric_col:
        return None

    group_series = _first_series_for_column_name(df, group_col)
    metric_series = _first_series_for_column_name(df, metric_col)
    if group_series is None or metric_series is None:
        return None

    working = pd.DataFrame({"__group__": group_series, "__metric__": metric_series}).dropna(subset=["__group__"])
    working["__metric__"] = _coerce_numeric_series(working["__metric__"])
    working = working.dropna(subset=["__metric__"])
    if working.empty:
        return None

    grouped = (
        working.groupby("__group__", dropna=False, as_index=False)["__metric__"]
        .sum()
        .rename(columns={"__group__": group_col, "__metric__": metric_col})
        .sort_values(by=metric_col, ascending=False)
        .reset_index(drop=True)
    )
    return grouped, f"Computed {metric_col} by {group_col}."


def scalar_aggregation_result(df: pd.DataFrame, question: str) -> Optional[tuple[pd.DataFrame, str]]:
    if df.empty or not asks_scalar_aggregation(question):
        return None

    op, metric_hint = _extract_scalar_aggregation_hints(question)
    if not op:
        return None

    if op == "count":
        return build_count_result(df, question)

    metric_col = choose_numeric_column_by_hint(df, metric_hint)
    # Keep this generic: when question asks a scalar (e.g., "average value") but
    # does not map to a specific metric token, use the first numeric-like column.
    if not metric_col:
        numeric_cols = _numeric_like_columns(df)
        metric_col = numeric_cols[0] if numeric_cols else None
    if not metric_col:
        return None
    series = _coerce_numeric_series(_first_series_for_column_name(df, metric_col)).dropna()
    if series.empty:
        return None

    if op == "mean":
        val = float(series.mean())
    elif op == "sum":
        val = float(series.sum())
    elif op == "min":
        val = float(series.min())
    elif op == "max":
        val = float(series.max())
    else:
        return None

    out = pd.DataFrame({f"{op}_{metric_col}": [val]})
    return out, f"Computed {op} of {metric_col}: {val:.4f}."


def trend_result(df: pd.DataFrame, question: str) -> Optional[tuple[pd.DataFrame, str]]:
    if df.empty or not asks_trend_question(question):
        return None

    date_cols = _date_like_columns(df)
    numeric_cols = _numeric_like_columns(df)
    if not numeric_cols:
        return None

    metric_col = choose_metric_column(df, question, metric_hint=None)
    time_col = date_cols[0] if date_cols else choose_group_column(df, question)
    if not time_col or not metric_col:
        return None

    t_series = _first_series_for_column_name(df, time_col)
    m_series = _first_series_for_column_name(df, metric_col)
    if t_series is None or m_series is None:
        return None

    working = pd.DataFrame({"__time__": t_series, "__metric__": m_series})
    working["__metric__"] = _coerce_numeric_series(working["__metric__"])
    working = working.dropna(subset=["__time__", "__metric__"])
    if working.empty:
        return None

    trend_df = (
        working.groupby("__time__", dropna=False, as_index=False)["__metric__"]
        .sum()
        .rename(columns={"__time__": time_col, "__metric__": metric_col})
    )

    q = question.lower()
    if any(k in q for k in ["forecast", "predict", "projection", "projected"]):
        y = _coerce_numeric_series(trend_df[metric_col]).dropna().reset_index(drop=True)
        if len(y) >= 2:
            # Lightweight, schema-agnostic one-step linear projection.
            slope = float(y.diff().dropna().mean())
            next_val = float(y.iloc[-1] + slope)
            projected = trend_df.copy()
            last_time = str(projected.iloc[-1][time_col])
            projected_next = pd.DataFrame([{time_col: f"next_after_{last_time}", metric_col: next_val}])
            projected = pd.concat([projected, projected_next], ignore_index=True)
            return projected, f"Computed {metric_col} trend by {time_col} with a one-step projection."

    return trend_df, f"Computed {metric_col} trend by {time_col}."


def comparison_result(df: pd.DataFrame, question: str) -> Optional[tuple[pd.DataFrame, str]]:
    if df.empty or not asks_compare_question(question):
        return None

    group_col = choose_group_column(df, question)
    metric_col = choose_metric_column(df, question, exclude_column=group_col)
    if not group_col or not metric_col:
        return None

    g_series = _first_series_for_column_name(df, group_col)
    m_series = _first_series_for_column_name(df, metric_col)
    if g_series is None or m_series is None:
        return None

    working = pd.DataFrame({"__group__": g_series, "__metric__": m_series})
    working["__metric__"] = _coerce_numeric_series(working["__metric__"])
    working = working.dropna(subset=["__group__", "__metric__"])
    if working.empty:
        return None

    out = (
        working.groupby("__group__", dropna=False, as_index=False)["__metric__"]
        .sum()
        .rename(columns={"__group__": group_col, "__metric__": metric_col})
        .sort_values(by=metric_col, ascending=False)
        .reset_index(drop=True)
    )
    return out, f"Computed comparison of {metric_col} across {group_col}."


def chart_intent_result(df: pd.DataFrame, question: str) -> Optional[tuple[pd.DataFrame, str]]:
    if df.empty or not asks_chart_request(question):
        return None
    metric_col = choose_metric_column(df, question)
    group_col = choose_group_column(df, question)
    if not metric_col:
        return None

    if group_col:
        g_series = _first_series_for_column_name(df, group_col)
        m_series = _first_series_for_column_name(df, metric_col)
        if g_series is None or m_series is None:
            return None
        working = pd.DataFrame({"__group__": g_series, "__metric__": m_series})
        working["__metric__"] = _coerce_numeric_series(working["__metric__"])
        working = working.dropna(subset=["__group__", "__metric__"])
        if working.empty:
            return None
        out = (
            working.groupby("__group__", dropna=False, as_index=False)["__metric__"]
            .sum()
            .rename(columns={"__group__": group_col, "__metric__": metric_col})
            .sort_values(by=metric_col, ascending=False)
            .reset_index(drop=True)
        )
        return out, f"Prepared chart data for {metric_col} by {group_col}."

    series = _coerce_numeric_series(_first_series_for_column_name(df, metric_col)).dropna()
    if series.empty:
        return None
    out = pd.DataFrame({metric_col: series})
    return out, f"Prepared chart data for {metric_col}."


def extract_search_term(question: str) -> Optional[str]:
    quoted = re.findall(r"['\"]([^'\"]{1,120})['\"]", question)
    if quoted:
        return quoted[0].strip()

    q = question.lower().strip()

    id_match = re.search(r"\b(?:for|where|with)\s+id(?:entifier)?\b\s*(?:=|is)?\s*([a-z0-9_\-./]+)\b", q)
    if not id_match:
        id_match = re.search(r"\bid(?:entifier)?\b\s*(?:=|is)\s*([a-z0-9_\-./]+)\b", q)
    if id_match:
        candidate = id_match.group(1).strip(" ?.,")
        if candidate and candidate not in LOOKUP_VALUE_STOPWORDS:
            return candidate

    patterns = [
        r"\b(?:rows?|records?|entries?|details?|data)\s+(?:for|of)\s+([a-z0-9][a-z0-9 ._\-/]{1,80}?)\b",
        r"\b(?:where\s+do\s+we\s+have|do\s+we\s+have)\s+([a-z0-9][a-z0-9 ._\-/]{1,80}?)(?:\s+in\b|$)",
        r"\bjust\s+([a-z0-9][a-z0-9 ._\-/]{1,80}?)\s+data\b",
        r"\bpull\s+([a-z0-9][a-z0-9 ._\-/]{1,80}?)\s+data\b",
        r"\b([a-z0-9][a-z0-9 ._\-/]{1,80}?)\s+related\s+(?:rows?|records?|data)\b",
        r"\b([a-z0-9][a-z0-9 ._\-/]{1,80}?)\s+ke\s+records\b",
        r"\b(?:give|show|list|display|return|get)?\s*all\s+([a-z0-9][a-z0-9 ._\-/]{1,80}?)\s+(?:records?|rows?|entries?|results?)\b",
        r"\b(?:like|contains|contain|with|where|named|called)\b\s+(.+)$",
        r"\b(?:is there|are there|any)\b\s+(.+)$",
    ]
    for pattern in patterns:
        m = re.search(pattern, q)
        if m:
            term = m.group(1).strip(" ?.,")
            if term:
                term = re.sub(r"^(?:please|kindly|can\s+you|could\s+you)\s+", "", term).strip()
                term = re.sub(r"^(?:show|list|give|find|fetch|pull|get|display|return)\s+(?:me\s+)?", "", term).strip()
                term = re.sub(r"\s+(?:records?|rows?|entries?|details?|data)$", "", term).strip()
                tokens = [token for token in re.split(r"[^a-z0-9]+", term) if token]
                if tokens and all(token in ALL_RECORDS_VALUE_STOPWORDS for token in tokens):
                    continue
                return term
    return None


def extract_lookup_conditions(question: str) -> list[tuple[Optional[str], str]]:
    q = question.strip()
    q_lower = q.lower()
    conditions: list[tuple[Optional[str], str]] = []
    semantic_text_rules = extract_text_match_rules(question)

    def clean_value(value: str) -> str:
        trimmed = re.split(r"\b(if|then|and|or|tell|show|please|thanks|for|because|but)\b", value, maxsplit=1)[0]
        return trimmed.strip(" '\".,?!")

    def clean_field(field: str) -> str:
        cleaned = field.strip(" '\".,?!")
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = re.sub(r"^this\s+", "", cleaned)
        cleaned = re.sub(r"^(?:show|list|give|find|fetch|display|return|get)\s+(?:me\s+)?", "", cleaned)
        cleaned = re.sub(r"^(?:rows?|records?|entries?|details?|data)\s+(?:where|with|for)\s+", "", cleaned)
        cleaned = re.sub(r"^(?:where|with|for|whose)\s+", "", cleaned)
        return cleaned

    # Email condition
    email_patterns = [
        r"\b(?:for|where|with)\s+email\s*(?:=|is)?\s*([a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})\b",
        r"\b([a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})\b",
    ]
    for pattern in email_patterns:
        m = re.search(pattern, q_lower)
        if m:
            conditions.append(("email", m.group(1)))
            break

    # Avoid forcing generic id extraction when a richer semantic text rule
    # (e.g., starts-with / ends-with) has already been identified.
    if not semantic_text_rules:
        id_match = re.search(r"\b(?:for|where|with)\s+id(?:entifier)?\b\s*(?:=|is)?\s*([\w\-./]{2,80})", q_lower)
        if not id_match:
            id_match = re.search(r"\bid(?:entifier)?\b\s*(?:=|is)\s*([\w\-./]{2,80})", q_lower)
        if id_match:
            id_value = clean_value(id_match.group(1))
            if id_value and (looks_like_identifier(id_value) or any(ch.isdigit() for ch in id_value)):
                conditions.append(("id", id_value))

    # Generic direct assignment patterns from plain-language questions:
    # "OrderID = ORD00001", "for this OrderID = ORD00001", "Unit Price is 1200".
    assignment_patterns = [
        # Explicit natural-language form: "for this OrderID = ORD00001"
        r"\bfor\s+this\s+([a-z][a-z0-9_\- ]{1,50}?)\s*(?:=|is|equals)\s*([a-z0-9@._\-/]{1,120})",
        # Generic assignment form: "OrderID = ORD00001" / "order id equals 123"
        r"\b([a-z][a-z0-9_\-]*(?:\s+[a-z0-9_\-]+){0,3})\s*(?:=|equals)\s*([a-z0-9@._\-/]{1,120})",
    ]
    invalid_field_tokens = {
        "what",
        "which",
        "who",
        "where",
        "when",
        "why",
        "how",
        "is",
        "are",
        "the",
        "this",
        "that",
        "for",
    }
    for pattern in assignment_patterns:
        for m in re.finditer(pattern, q_lower):
            field = clean_field(m.group(1))
            val = clean_value(m.group(2))
            field_tokens = [tok for tok in re.split(r"\s+", field) if tok]
            if field and val and not any(tok in invalid_field_tokens for tok in field_tokens):
                conditions.append((field, val))

    # Clause-based comparisons: split conditions like
    # "where source is organization and eventName is IP INTERIORS LTD".
    clause_text = q_lower
    has_clause_starter = False
    for starter in [" where ", " with ", " for "]:
        marker = f" {q_lower} "
        if starter in marker:
            clause_text = marker.split(starter, 1)[1].strip()
            has_clause_starter = True
            break

    if has_clause_starter:
        for segment in re.split(r"\band\b", clause_text):
            seg = segment.strip()
            if not seg:
                continue
            m = re.search(r"([a-z][a-z0-9_\- ]{1,40})\s*(?:=|\bis\b|\bequals\b)\s*(.+)", seg)
            if not m:
                continue
            field = clean_field(m.group(1))
            val = clean_value(m.group(2))
            if field and val and field not in {"is", "for", "with", "where"} and val not in {"is", "for", "with", "where"}:
                conditions.append((field, val))

    # Fallback to existing search term if nothing explicit found
    if not conditions:
        term = extract_search_term(question)
        # Use semantic parse first; only fall back to loose term search when
        # no structured semantic rule was detected.
        if term and not semantic_text_rules and normalize_token(term):
            # For plain count prompts (e.g., "how many products are there"),
            # avoid turning noun phrases into accidental lookup filters.
            if asks_count(question):
                has_explicit_filter_language = bool(
                    re.search(r"\b(where|with|for|whose|named|called|contains|contain|like)\b", q_lower)
                )
                if has_explicit_filter_language:
                    conditions.append((None, term))
            else:
                conditions.append((None, term))

    # Natural phrasing: "... sold by Sarah", "... by John".
    by_actor_match = re.search(r"\b(?:sold|created|handled|managed)?\s*by\s+([a-z0-9][a-z0-9 ._\-/]{1,80})\b", q_lower)
    if by_actor_match:
        actor = clean_value(by_actor_match.group(1))
        if actor:
            # Field hint is semantic and later resolved to best matching column.
            conditions.append(("salesperson", actor))

    # Region-like phrasing: "in Region North" / "in North Region" / "not in North Region".
    region_like_match = re.search(r"\b(not\s+in|in)\s+([a-z0-9][a-z0-9 _\-]{1,80})\b", q_lower)
    if region_like_match:
        phrase = clean_value(region_like_match.group(2))
        phrase_tokens = [t for t in re.split(r"\s+", phrase) if t]
        if len(phrase_tokens) >= 2:
            if "region" in phrase_tokens:
                value_tokens = [t for t in phrase_tokens if t != "region"]
                if value_tokens:
                    conditions.append(("region", " ".join(value_tokens)))
            elif phrase_tokens[0] in {"department", "state", "city", "country", "zone"}:
                conditions.append((phrase_tokens[0], " ".join(phrase_tokens[1:])))

    # De-duplicate preserving order.

    unique_conditions: list[tuple[Optional[str], str]] = []
    seen: set[tuple[Optional[str], str]] = set()
    for cond in conditions:
        key = (cond[0], cond[1].lower())
        if key not in seen:
            seen.add(key)
            unique_conditions.append(cond)
    return unique_conditions


def looks_like_identifier(term: str) -> bool:
    t = term.strip()
    # Heuristic: alnum with at least one digit and no spaces.
    return bool(t) and (" " not in t) and any(ch.isdigit() for ch in t) and any(ch.isalpha() for ch in t)


def normalize_token(value: object) -> str:
    raw = unicodedata.normalize("NFKD", str(value)).casefold()
    no_marks = "".join(ch for ch in raw if not unicodedata.combining(ch))
    token = "".join(ch for ch in no_marks if ch.isalnum())
    # Normalize common UK/US spelling differences for better matching.
    token = token.replace("organisation", "organization")
    token = token.replace("organisations", "organizations")
    return token


def choose_best_column(df: pd.DataFrame, hint: Optional[str]) -> Optional[str]:
    if not hint:
        return None

    hint_norm = normalize_token(hint)
    if not hint_norm:
        return None

    best: Optional[str] = None
    best_score = -1
    for col in df.columns:
        col_name = str(col)
        col_norm = normalize_token(col_name)
        score = 0
        if col_norm == hint_norm:
            score = 4
        elif hint_norm in col_norm or col_norm in hint_norm:
            score = 3
        elif any(part and part in col_norm for part in hint_norm.split()):
            score = 2
        if score > best_score:
            best = col_name
            best_score = score
    return best if best_score > 0 else None


def find_identifier_rows(df: pd.DataFrame, identifier: str) -> pd.DataFrame:
    needle = normalize_token(identifier)
    if not needle:
        return df.head(0)

    exact_mask = pd.Series(False, index=df.index)
    contains_mask = pd.Series(False, index=df.index)
    for col in df.columns:
        series = df[col].astype(str)
        norm = series.map(normalize_token)
        exact_mask = exact_mask | (norm == needle)
        contains_mask = contains_mask | norm.str.contains(needle, na=False)

    exact_rows = df[exact_mask]
    if not exact_rows.empty:
        return exact_rows
    return df[contains_mask]


def filter_rows_by_conditions(df: pd.DataFrame, conditions: list[tuple[Optional[str], str]]) -> pd.DataFrame:
    if not conditions:
        return df

    matched = df.copy()
    if matched.empty:
        return matched

    con = duckdb.connect(database=":memory:")
    try:
        def qident(name: str) -> str:
            return '"' + str(name).replace('"', '""') + '"'

        con.register("working_df", matched)
        working_table = "working_df"

        for hint, raw_value in conditions:
            value = raw_value.strip()
            if not value:
                continue

            value_norm = normalize_token(value)
            if not value_norm:
                continue

            target_col = choose_best_column(matched, hint)
            candidate_cols = [target_col] if target_col else [str(c) for c in matched.columns]
            if not candidate_cols:
                return matched.head(0)

            # First pass: exact normalized match.
            exact_predicates: list[str] = []
            for col in candidate_cols:
                col_expr = f"regexp_replace(lower(cast({qident(col)} as varchar)), '[^a-z0-9]', '', 'g')"
                exact_predicates.append(f"{col_expr} = ?")

            exact_sql = f"SELECT * FROM {working_table} WHERE " + " OR ".join(exact_predicates)
            exact_params = [value_norm] * len(exact_predicates)
            exact_df = con.execute(exact_sql, exact_params).df()

            if not exact_df.empty:
                matched = exact_df
                con.unregister(working_table)
                con.register("working_df", matched)
                working_table = "working_df"
                continue

            # Second pass: contains normalized value.
            contains_predicates: list[str] = []
            for col in candidate_cols:
                col_expr = f"regexp_replace(lower(cast({qident(col)} as varchar)), '[^a-z0-9]', '', 'g')"
                contains_predicates.append(f"{col_expr} LIKE ?")

            contains_sql = f"SELECT * FROM {working_table} WHERE " + " OR ".join(contains_predicates)
            contains_params = [f"%{value_norm}%"] * len(contains_predicates)
            contains_df = con.execute(contains_sql, contains_params).df()

            if contains_df.empty:
                return matched.head(0)

            matched = contains_df
            con.unregister(working_table)
            con.register("working_df", matched)
            working_table = "working_df"

        return matched
    finally:
        con.close()


def _parse_loose_date_token(raw: str, reference_year: Optional[int] = None) -> Optional[pd.Timestamp]:
    candidate = (raw or "").strip(" ,.")
    if not candidate:
        return None

    parsed = pd.to_datetime(candidate, errors="coerce", dayfirst=True)
    if pd.notna(parsed):
        return parsed

    has_year = bool(re.search(r"\b\d{4}\b", candidate))
    if not has_year:
        year = int(reference_year or pd.Timestamp.utcnow().year)
        parsed_with_year = pd.to_datetime(f"{candidate} {year}", errors="coerce", dayfirst=True)
        if pd.notna(parsed_with_year):
            return parsed_with_year

    month_map = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    day_first = re.search(r"\b(\d{1,2})\s+([a-z]{3,9})\b", candidate.lower())
    month_first = re.search(r"\b([a-z]{3,9})\s+(\d{1,2})\b", candidate.lower())
    if day_first or month_first:
        day = int((day_first.group(1) if day_first else month_first.group(2)))
        month_token = (day_first.group(2) if day_first else month_first.group(1)).lower()
        month = month_map.get(month_token)
        year = int(reference_year or pd.Timestamp.utcnow().year)
        if month:
            try:
                return pd.Timestamp(year=year, month=month, day=day)
            except ValueError:
                return None

    return None


def _parse_question_date_bounds(question: str, reference_year: Optional[int] = None) -> tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]]:
    q = question.lower().strip()

    range_match = re.search(r"\bbetween\s+(.+?)\s+(?:and|to)\s+(.+?)(?:\?|$)", q)
    if range_match:
        start_raw = range_match.group(1).strip(" ,.")
        end_raw = range_match.group(2).strip(" ,.")
        start = _parse_loose_date_token(start_raw, reference_year=reference_year)
        end = _parse_loose_date_token(end_raw, reference_year=reference_year)
        if pd.notna(start) and pd.notna(end):
            return start, end

    from_to_match = re.search(r"\bfrom\s+(.+?)\s+to\s+(.+?)(?:\?|$)", q)
    if from_to_match:
        start_raw = from_to_match.group(1).strip(" ,.")
        end_raw = from_to_match.group(2).strip(" ,.")
        start = _parse_loose_date_token(start_raw, reference_year=reference_year)
        end = _parse_loose_date_token(end_raw, reference_year=reference_year)
        if pd.notna(start) and pd.notna(end):
            return start, end

    return None, None


def filter_rows_by_date_range(df: pd.DataFrame, question: str) -> tuple[pd.DataFrame, bool]:
    if df.empty:
        return df, False

    date_cols = _date_like_columns(df)
    if not date_cols:
        return df, False

    inferred_year: Optional[int] = None
    year_samples: list[int] = []
    for col in date_cols[:3]:
        series = _first_series_for_column_name(df, col)
        if series is None:
            continue
        parsed = _parse_datetime_series_cached(df, col, series)
        year_samples.extend(parsed.dropna().dt.year.tolist())
    if year_samples:
        inferred_year = int(pd.Series(year_samples).mode().iloc[0])

    start, end = _parse_question_date_bounds(question, reference_year=inferred_year)
    if start is None or end is None:
        return df, False

    if end < start:
        start, end = end, start

    # Prefer date columns mentioned in the question; otherwise test all date-like columns.
    mentioned = requested_projection_columns(df, question)
    candidate_cols = [c for c in date_cols if c in mentioned] or date_cols

    matched = pd.DataFrame()
    for col in candidate_cols:
        series = _first_series_for_column_name(df, col)
        if series is None:
            continue
        parsed = _parse_datetime_series_cached(df, col, series)
        mask = parsed.notna() & (parsed >= start) & (parsed <= end)
        slice_df = df[mask]
        if not slice_df.empty:
            matched = slice_df
            break

    if matched.empty:
        return df.head(0), True
    return matched, True


def pick_column_by_aliases(df: pd.DataFrame, aliases: list[str]) -> Optional[str]:
    normalized_aliases = [normalize_token(a) for a in aliases]
    best_col: Optional[str] = None
    best_score = -1
    for col in df.columns:
        col_norm = normalize_token(col)
        score = 0
        for alias in normalized_aliases:
            if not alias:
                continue
            if col_norm == alias:
                score = max(score, 3)
            elif alias in col_norm or col_norm in alias:
                score = max(score, 2)
            elif any(part in col_norm for part in re.split(r"[^a-z0-9]+", alias) if len(part) > 2):
                score = max(score, 1)
        if score > best_score:
            best_col = str(col)
            best_score = score
    return best_col if best_score > 0 else None


def requested_projection_columns(df: pd.DataFrame, question: str) -> list[str]:
    q = question.lower()

    requested: list[str] = []

    q_norm = normalize_token(q)
    for col in df.columns:
        col_name = str(col)
        col_norm = normalize_token(col_name)
        if not col_norm:
            continue
        if len(col_norm) <= 3:
            is_mentioned = bool(re.search(rf"\b{re.escape(col_name.lower())}\b", q))
        else:
            is_mentioned = col_norm in q_norm
        if is_mentioned:
            if col_name not in requested:
                # Direct mention of column names in question should be honored.
                requested.append(col_name)

    projected: list[str] = []
    for key in requested:
        if key in df.columns:
            col = key
        else:
            col = choose_best_column(df, key)
        if col and col not in projected:
            projected.append(col)
    return projected


def should_return_single_row(question: str, conditions: list[tuple[Optional[str], str]]) -> bool:
    q = question.lower()
    if any(marker in q for marker in ["status of", "record for", "for email", "for id", "this id", "this email", "tell me the"]):
        return True
    if len(conditions) >= 1 and any("@" in val or looks_like_identifier(val) for _, val in conditions):
        return True
    return False


def add_identifying_columns(full_df: pd.DataFrame, projected_df: pd.DataFrame) -> pd.DataFrame:
    if projected_df.empty or len(projected_df) <= 1:
        return projected_df

    def duplicate_count(frame: pd.DataFrame) -> int:
        return int(len(frame) - len(frame.drop_duplicates()))

    enriched = projected_df.copy()
    if duplicate_count(enriched) == 0:
        return enriched

    candidate_priority = [
        "id",
        "event",
        "name",
        "email",
        "reference",
        "ref",
        "uuid",
        "number",
        "code",
        "key",
        "date",
        "time",
    ]

    remaining = [c for c in full_df.columns if c not in enriched.columns]

    def score(col: str) -> tuple[int, float]:
        col_name = str(col).lower()
        keyword_score = max((1 for token in candidate_priority if token in col_name), default=0)
        unique_ratio = float(full_df[col].nunique(dropna=False)) / float(max(1, len(full_df)))
        return (keyword_score, unique_ratio)

    ranked = sorted(remaining, key=score, reverse=True)
    for col in ranked:
        enriched[str(col)] = full_df[str(col)].values
        if duplicate_count(enriched) == 0:
            return enriched

    # Last resort: attach stable row number so each row is uniquely identifiable.
    enriched["row_number"] = list(range(1, len(enriched) + 1))
    return enriched


def _normalize_blank_markers(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.strip()
    lowered = text.str.lower()
    markers = {"", "-", "--", "nan", "none", "null", "nat"}
    return series.mask(series.isna() | lowered.isin(markers), pd.NA)


def compact_lookup_result_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()
    cols = [str(c) for c in out.columns]
    meta_cols = {"__sheet_name", "__source_file"}

    # Coalesce families like OrderID, OrderID2, OrderID3 into one base column.
    families: dict[str, list[str]] = {}
    for col in cols:
        m = re.match(r"^(.*?)(\d+)$", col)
        if not m:
            continue
        base = m.group(1)
        if base and base in out.columns:
            families.setdefault(base, []).append(col)

    for base, variants in families.items():
        merged = _normalize_blank_markers(out[base])
        for col in sorted(variants):
            merged = merged.combine_first(_normalize_blank_markers(out[col]))
        out[base] = merged
        out = out.drop(columns=variants, errors="ignore")

    # For identifier columns, convert date-like artifacts (e.g., 2001-01-01)
    # into stable ID tokens (e.g., 2001) to keep answer formatting readable.
    for col in [str(c) for c in out.columns]:
        col_norm = normalize_token(col)
        if not any(token in col_norm for token in ["id", "order", "code", "ref", "number"]):
            continue
        series = _normalize_blank_markers(out[col]).astype(str).str.strip()
        date_like = series.str.match(r"^\d{4}-\d{2}-\d{2}(?:[ tT].*)?$", na=False)
        if bool(date_like.any()):
            out[col] = series.where(~date_like, series.str.slice(0, 4))

    # Drop columns that are entirely blank after normalization (except meta).
    keep_cols: list[str] = []
    for col in [str(c) for c in out.columns]:
        norm = _normalize_blank_markers(out[col])
        if col in meta_cols or bool(norm.notna().any()):
            keep_cols.append(col)
    out = out[keep_cols]

    return out


def find_matching_rows(df: pd.DataFrame, term: str) -> pd.DataFrame:
    needle = term.strip().lower()
    if not needle:
        return df.head(0)

    mask = pd.Series(False, index=df.index)
    for col in df.columns:
        values = df[col].astype(str).str.lower()
        mask = mask | values.str.contains(needle, na=False)
    return df[mask]


def extract_text_match_rules(question: str) -> list[tuple[Optional[str], str, str]]:
    q = question.lower().strip()
    rules: list[tuple[Optional[str], str, str]] = []

    def clean_value(value: str) -> str:
        trimmed = re.split(r"\b(if|then|and|or|tell|show|please|thanks|because|but)\b", value, maxsplit=1)[0]
        trimmed = trimmed.strip(" '\".,?!")
        trimmed = re.sub(r"^(?:with|from|is|are)\s+", "", trimmed)
        return trimmed.strip(" '\".,?!")

    def clean_field(field: str) -> str:
        cleaned = field.strip(" '\".,?!")
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = re.sub(r"^this\s+", "", cleaned)
        cleaned = re.sub(r"^(?:show|list|give|find|fetch|display|return|get)\s+(?:me\s+)?", "", cleaned)
        cleaned = re.sub(r"^(?:rows?|records?|entries?|details?|data)\s+whose\s+", "", cleaned)
        cleaned = re.sub(r"^(?:rows?|records?|entries?|details?|data)\s+(?:where|with|for)\s+", "", cleaned)
        cleaned = re.sub(r"^(?:where|with|for|whose)\s+", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    invalid_field_tokens = {
        "select",
        "show",
        "list",
        "give",
        "find",
        "fetch",
        "display",
        "return",
        "get",
        "all",
        "this",
        "me",
        "at",
        "of",
        "in",
        "on",
        "to",
        "from",
        "with",
        "where",
        "rows",
        "row",
        "records",
        "record",
        "entries",
        "entry",
        "data",
    }

    def is_plausible_field(field: str) -> bool:
        tokens = [tok for tok in re.split(r"\s+", field.lower().strip()) if tok]
        if not tokens:
            return False
        if len(tokens) > 4 and not any(tok in {"id", "code", "number", "ref", "name"} for tok in tokens):
            return False
        noisy_leads = {"all", "the", "orders", "rows", "records", "entries", "data", "that"}
        if len(tokens) >= 3 and all(tok in noisy_leads for tok in tokens[:3]):
            return False
        return True

    starts_patterns = [
        r"\b(?:with|where|for)\s+starts?\s+([a-z][a-z0-9_\- ]{1,60}?)\s+from\s+([a-z0-9][a-z0-9 ._\-/]{0,100})",
        r"\b(?:whose|where|with|for)\s+([a-z][a-z0-9_\- ]{1,60}?)\s+(?:starts?\s+with|starts?\s+from|starting\s+with|starting\s+from|beginning\s+with|beginning\s+from|begins?\s+with|begins?\s+from|begins?|prefix(?:ed)?\s+with|prefix)\s+([a-z0-9][a-z0-9 ._\-/]{0,100})",
        r"\b([a-z][a-z0-9_\-]*(?:\s+[a-z0-9_\-]+){0,3})\s+(?:starts?\s+with|starts?\s+from|starting\s+with|starting\s+from|beginning\s+with|beginning\s+from|begins?\s+with|begins?\s+from|begins?|prefix(?:ed)?\s+with|prefix)\s+([a-z0-9][a-z0-9 ._\-/]{0,100})",
        r"\bstarts?\s+with\s+([a-z][a-z0-9_\- ]{1,60}?)\s+([a-z0-9][a-z0-9 ._\-/]{0,100})",
        r"\b([a-z][a-z0-9_\-]*(?:\s+[a-z0-9_\-]+){0,3})\s+starts?\s+([a-z0-9][a-z0-9 ._\-/]{0,100})",
        r"\bstarts?\s+([a-z][a-z0-9_\- ]{1,60}?)\s+from\s+([a-z0-9][a-z0-9 ._\-/]{0,100})",
        r"\bfirst\s+(?:digit|digits|character|characters)\s+of\s+([a-z][a-z0-9_\- ]{1,60}?)\s+(?:is|are)\s+([a-z0-9][a-z0-9 ._\-/]{0,100})",
    ]
    ends_patterns = [
        r"\b(?:whose|where|with|for)\s+([a-z][a-z0-9_\- ]{1,60}?)\s+(?:ends?\s+with|ending\s+with|suffix(?:ed)?\s+with)\s+([a-z0-9][a-z0-9 ._\-/]{0,100})",
        r"\b([a-z][a-z0-9_\-]*(?:\s+[a-z0-9_\-]+){0,3})\s+(?:ends?\s+with|ending\s+with|suffix(?:ed)?\s+with)\s+([a-z0-9][a-z0-9 ._\-/]{0,100})",
    ]

    for pattern in starts_patterns:
        for m in re.finditer(pattern, q):
            field = clean_field(m.group(1))
            value = clean_value(m.group(2))
            field_tokens = [tok for tok in re.split(r"\s+", field) if tok]
            if field and value and is_plausible_field(field) and not all(tok in invalid_field_tokens for tok in field_tokens):
                rules.append((field, value, "startswith"))

    # Reverse phrasing: "10 at start of id".
    reverse_start_pattern = r"\b([a-z0-9][a-z0-9_\-./]{0,40})\s+at\s+start\s+of\s+([a-z][a-z0-9_\- ]{1,60})\b"
    for m in re.finditer(reverse_start_pattern, q):
        value = clean_value(m.group(1))
        field = clean_field(m.group(2))
        field_tokens = [tok for tok in re.split(r"\s+", field) if tok]
        if field and value and not all(tok in invalid_field_tokens for tok in field_tokens):
            rules.append((field, value, "startswith"))

    end_rules: list[tuple[Optional[str], str, str]] = []
    for m in re.finditer(ends_patterns[0], q):
        field = clean_field(m.group(1))
        value = clean_value(m.group(2))
        if field and value:
            end_rules.append((field, value, "endswith"))

    if not end_rules:
        for m in re.finditer(ends_patterns[1], q):
            field = clean_field(m.group(1))
            value = clean_value(m.group(2))
            if field and value:
                end_rules.append((field, value, "endswith"))

    rules.extend(end_rules)

    # Typos/noisy phrasing fallback: emit a generic text-match rule and let
    # schema-aware column inference decide the target field.
    if not rules:
        has_start_intent = any(marker in q for marker in ["start", "sstart", "begin", "prefix"])
        has_end_intent = any(marker in q for marker in ["end", "suffix"])
        has_contains_intent = any(marker in q for marker in ["contain", "includes", "including", "match"])

        if has_start_intent or has_end_intent or has_contains_intent:
            tokens = [tok for tok in re.findall(r"[a-z0-9][a-z0-9._\-/]{0,40}", q)]
            ignored = {
                "select",
                "show",
                "list",
                "give",
                "find",
                "fetch",
                "display",
                "return",
                "get",
                "all",
                "records",
                "record",
                "rows",
                "row",
                "entries",
                "entry",
                "data",
                "details",
                "starts",
                "start",
                "sstarts",
                "with",
                "wtih",
                "begin",
                "begins",
                "prefix",
                "end",
                "ends",
                "suffix",
                "contains",
                "contain",
                "matching",
                "match",
            }
            filtered = [tok for tok in tokens if tok not in ignored]
            chosen_value = ""
            numeric_tokens = [tok for tok in filtered if any(ch.isdigit() for ch in tok)]
            if numeric_tokens:
                chosen_value = numeric_tokens[-1]
            elif filtered:
                chosen_value = filtered[-1]

            if chosen_value:
                op = "startswith" if has_start_intent else "endswith" if has_end_intent else "contains"
                inferred_hint: Optional[str] = None
                if "orderid" in q or "order id" in q:
                    inferred_hint = "orderid"
                elif "customerid" in q or "customer id" in q:
                    inferred_hint = "customerid"
                elif "productid" in q or "product id" in q:
                    inferred_hint = "productid"
                else:
                    m = re.search(r"\b([a-z][a-z0-9_]{1,24})\s*id\b", q)
                    if m:
                        inferred_hint = f"{m.group(1)}id"
                rules.append((inferred_hint, chosen_value, op))

    unique: list[tuple[Optional[str], str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for hint, value, op in rules:
        key = (str(hint or "").lower(), value.lower(), op)
        if key not in seen:
            seen.add(key)
            unique.append((hint, value, op))
    return unique


def infer_implicit_pattern_columns(
    df: pd.DataFrame,
    question: str,
    operator: str = "contains",
    understanding: Optional[dict[str, Any]] = None,
) -> list[str]:
    q = question.lower().strip()
    if not any(k in q for k in ["starts with", "starting with", "begins with", "ends with", "ending with", "contains"]):
        return []

    candidates: list[str] = []
    if isinstance(understanding, dict):
        for item in understanding.get("columnCandidates", []):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            col = str(item.get("column") or "").strip()
            if col in df.columns and role in {"filter", "identifier", "target"}:
                candidates.append(col)
    if candidates:
        unique: list[str] = []
        for col in candidates:
            if col not in unique:
                unique.append(col)
        return unique

    preferred: list[str] = []
    for col in df.columns:
        col_name = str(col)
        norm = normalize_token(col_name)
        if any(tok in norm for tok in ["id", "code", "number", "ref", "name", "product", "customer", "order"]):
            preferred.append(col_name)
    # For prefix/suffix operators, avoid broad all-column scans which can cause
    # false positives (e.g., matching year tokens in date columns).
    if operator in {"startswith", "endswith"}:
        return preferred
    return preferred or [str(c) for c in df.columns]


def _column_name_is_datetime_like(column_name: str) -> bool:
    norm = normalize_token(column_name)
    return any(token in norm for token in ["date", "time", "month", "year", "timestamp"])


def _series_is_datetime_like(series: pd.Series, sample_size: int = 240, threshold: float = 0.75) -> bool:
    if series is None or series.empty:
        return False
    sample = series.dropna().astype(str).str.strip()
    if sample.empty:
        return False
    sample = sample[sample.ne("")].head(sample_size)
    if sample.empty:
        return False
    # Pure short numeric tokens are more likely identifiers (e.g., OrderID=2001)
    # than datetimes; avoid classifying them as date-like.
    normalized = sample.str.lower()
    numeric_only = normalized.str.fullmatch(r"\d{1,6}")
    if bool(numeric_only.mean() >= 0.8):
        return False
    has_datetime_tokens = normalized.str.contains(r"[-/:t]|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec", regex=True)
    if bool(has_datetime_tokens.mean() < 0.2):
        return False
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        parsed = pd.to_datetime(sample, errors="coerce", utc=False)
    ratio = float(parsed.notna().mean()) if len(sample) else 0.0
    return ratio >= threshold


def _series_is_identifier_like(series: pd.Series, sample_size: int = 240, threshold: float = 0.45) -> bool:
    if series is None or series.empty:
        return False
    sample = series.dropna().astype(str).str.strip()
    if sample.empty:
        return False
    sample = sample[sample.ne("")].head(sample_size)
    if sample.empty:
        return False
    norm = sample.map(normalize_token)
    if norm.empty:
        return False
    id_like = norm.map(lambda item: bool(item) and bool(re.search(r"\d", item)) and len(item) <= 36)
    ratio = float(id_like.mean()) if len(id_like) else 0.0
    return ratio >= threshold


def _is_id_prefix_query(hint: Optional[str], operator: str, value_norm: str, question: str = "") -> bool:
    if operator != "startswith":
        return False
    if not bool(re.fullmatch(r"0*\d{1,8}", value_norm)):
        return False
    hint_norm = normalize_token(hint) if hint else ""
    question_norm = normalize_question_text(question)
    id_tokens = ["id", "order", "code", "number", "ref", "invoice", "transaction"]
    return any(token in hint_norm for token in id_tokens) or any(token in question_norm for token in id_tokens)


def _is_identifier_semantic_column(column_name: str) -> bool:
    col_norm = normalize_token(column_name)
    return any(token in col_norm for token in ["id", "order", "code", "number", "ref", "invoice", "transaction"])


def _normalize_for_id_prefix_match(series: pd.Series, column_name: str) -> pd.Series:
    normalized = series.astype(str).map(normalize_token)
    if not _is_identifier_semantic_column(column_name):
        return normalized
    raw = series.astype(str).str.strip().str.lower()
    date_like = raw.str.match(r"^\d{4}-\d{2}-\d{2}(?:[ tT].*)?$", na=False)
    if bool(date_like.any()):
        years = raw.str.slice(0, 4).map(normalize_token)
        normalized = normalized.where(~date_like, years)
    return normalized


def filter_rows_by_text_rules(
    df: pd.DataFrame,
    rules: list[tuple[Optional[str], str, str]],
    question: str = "",
    understanding: Optional[dict[str, Any]] = None,
) -> pd.DataFrame:
    if not rules or df.empty:
        return df

    matched = df.copy()
    for hint, raw_value, operator in rules:
        value_norm = normalize_token(raw_value)
        if not value_norm:
            continue

        id_prefix_query = _is_id_prefix_query(hint, operator, value_norm, question)
        target_col = choose_best_column(matched, hint)
        if target_col:
            candidate_cols = [target_col]
            if id_prefix_query:
                hint_norm = normalize_token(hint) if hint else ""
                for col in matched.columns:
                    col_name = str(col)
                    if col_name == target_col:
                        continue
                    col_norm = normalize_token(col_name)
                    if hint_norm:
                        if hint_norm in col_norm or col_norm in hint_norm:
                            candidate_cols.append(col_name)
                            continue
                        if hint_norm.endswith("id"):
                            base_hint = hint_norm[:-2]
                            if base_hint and base_hint in col_norm and "id" in col_norm:
                                candidate_cols.append(col_name)
                                continue
                    else:
                        if any(token in col_norm for token in ["id", "code", "number", "ref", "order"]):
                            candidate_cols.append(col_name)
                            continue
                        if _series_is_identifier_like(matched[col_name]):
                            candidate_cols.append(col_name)
                # Preserve order while de-duplicating.
                seen_cols: set[str] = set()
                candidate_cols = [c for c in candidate_cols if not (c in seen_cols or seen_cols.add(c))]
        else:
            inferred_cols = infer_implicit_pattern_columns(matched, question, operator=operator, understanding=understanding)
            if inferred_cols:
                candidate_cols = inferred_cols
            elif operator in {"startswith", "endswith"}:
                candidate_cols = []
            else:
                candidate_cols = [str(c) for c in matched.columns]
        if not candidate_cols:
            return matched.head(0)

        combined_mask = pd.Series(False, index=matched.index)
        for col in candidate_cols:
            if id_prefix_query:
                col_name = str(col)
                id_semantic = _is_identifier_semantic_column(col_name)
                if (_column_name_is_datetime_like(col_name) or _series_is_datetime_like(matched[col])) and not id_semantic:
                    continue
                normalized_col = _normalize_for_id_prefix_match(matched[col], col_name)
            else:
                normalized_col = matched[col].astype(str).map(normalize_token)
            if operator == "startswith":
                col_mask = normalized_col.str.startswith(value_norm, na=False)
                if not bool(col_mask.any()):
                    # Handle identifier-like user inputs such as "01" for IDs like 1001.
                    col_norm = normalize_token(col)
                    id_like_column = any(token in col_norm for token in ["id", "code", "number", "ref", "order"])
                    id_like_value = bool(re.fullmatch(r"0*\d+", value_norm))
                    if id_like_column and id_like_value:
                        value_trimmed = value_norm.lstrip("0")
                        if value_trimmed:
                            col_mask = normalized_col.str.startswith(value_trimmed, na=False)
            elif operator == "endswith":
                col_mask = normalized_col.str.endswith(value_norm, na=False)
            else:
                col_mask = normalized_col.str.contains(value_norm, na=False)
            combined_mask = combined_mask | col_mask

        # Last-mile recovery: when user asked an ID prefix lookup and hinted
        # columns produced zero matches, scan all non-datetime columns once.
        if id_prefix_query and not bool(combined_mask.any()):
            for col in [str(c) for c in matched.columns]:
                id_semantic = _is_identifier_semantic_column(col)
                if not id_semantic and not _series_is_identifier_like(matched[col]):
                    continue
                if (_column_name_is_datetime_like(col) or _series_is_datetime_like(matched[col])) and not id_semantic:
                    continue
                normalized_col = _normalize_for_id_prefix_match(matched[col], col)
                col_mask = normalized_col.str.startswith(value_norm, na=False)
                if not bool(col_mask.any()):
                    value_trimmed = value_norm.lstrip("0")
                    if value_trimmed:
                        col_mask = normalized_col.str.startswith(value_trimmed, na=False)
                combined_mask = combined_mask | col_mask

        matched = matched[combined_mask]
        if matched.empty:
            return matched

    return matched


def filter_rows_by_plan_filters(
    df: pd.DataFrame,
    filters: list[dict[str, Any]],
) -> pd.DataFrame:
    if not filters or df.empty:
        return df

    matched = df.copy()
    for item in filters:
        if not isinstance(item, dict):
            continue

        col_hint = str(item.get("column") or "").strip()
        operator = _normalize_filter_operator(item.get("operator") or item.get("op"))
        value = item.get("value")

        target_col = choose_best_column(matched, col_hint) if col_hint else None
        if not target_col or target_col not in matched.columns:
            continue

        series = matched[target_col]
        normalized_col = series.astype(str).map(normalize_token)

        if operator == "is_null":
            mask = _null_or_blank_mask(series)
        elif operator == "not_null":
            mask = ~_null_or_blank_mask(series)
        else:
            value_text = "" if value is None else str(value)
            value_norm = normalize_token(value_text)
            if not value_norm:
                continue

            if operator == "startswith":
                if _is_id_prefix_query(col_hint, operator, value_norm):
                    if _column_name_is_datetime_like(target_col) or _series_is_datetime_like(series):
                        continue
                mask = normalized_col.str.startswith(value_norm, na=False)
                if not bool(mask.any()):
                    target_norm = normalize_token(target_col)
                    id_like_column = any(token in target_norm for token in ["id", "code", "number", "ref", "order"])
                    id_like_value = bool(re.fullmatch(r"0*\d+", value_norm))
                    if id_like_column and id_like_value:
                        value_trimmed = value_norm.lstrip("0")
                        if value_trimmed:
                            mask = normalized_col.str.startswith(value_trimmed, na=False)
            elif operator == "endswith":
                mask = normalized_col.str.endswith(value_norm, na=False)
            elif operator in {"=", "=="}:
                mask = normalized_col.eq(value_norm)
            elif operator in {"!=", "<>"}:
                mask = ~normalized_col.eq(value_norm)
            elif operator == "in":
                raw_values = value if isinstance(value, list) else re.split(r"\s*,\s*", value_text)
                choices = {normalize_token(str(v)) for v in raw_values if normalize_token(str(v))}
                if not choices:
                    continue
                mask = normalized_col.isin(choices)
            elif operator == "not_in":
                raw_values = value if isinstance(value, list) else re.split(r"\s*,\s*", value_text)
                choices = {normalize_token(str(v)) for v in raw_values if normalize_token(str(v))}
                if not choices:
                    continue
                mask = ~normalized_col.isin(choices)
            else:
                mask = normalized_col.str.contains(value_norm, na=False)

        matched = matched[mask]
        if matched.empty:
            return matched

    return matched


def _null_or_blank_mask(series: pd.Series) -> pd.Series:
    as_text = series.astype(str).str.strip()
    lowered = as_text.str.lower()
    return series.isna() | as_text.eq("") | lowered.isin({"nan", "none", "null"})


def apply_query_plan_post_processing(
    result_df: pd.DataFrame,
    question: str,
    plan: Optional[dict[str, Any]],
    default_limit: int = 5000,
) -> pd.DataFrame:
    if result_df.empty:
        return result_df

    plan_data = plan or {}
    final_df = result_df.copy()

    sort_specs = plan_data.get("sort") if isinstance(plan_data.get("sort"), list) else []
    for spec in sort_specs:
        if not isinstance(spec, dict):
            continue
        col_hint = str(spec.get("column") or "").strip()
        direction = str(spec.get("direction") or "desc").lower()
        resolved_col = choose_best_column(final_df, col_hint) if col_hint else None
        if not resolved_col or resolved_col not in final_df.columns:
            continue
        ascending = direction in {"asc", "ascending"}
        try:
            final_df = final_df.sort_values(by=resolved_col, ascending=ascending, na_position="last", kind="mergesort")
        except Exception:
            pass
        break

    if bool(plan_data.get("distinct")) and not asks_count(question):
        final_df = final_df.drop_duplicates()

    final_df, _ = apply_requested_row_limit(final_df, question, default_limit=default_limit)
    return final_df.reset_index(drop=True)


def distinct_intent_result(
    df: pd.DataFrame,
    question: str,
    plan: Optional[dict[str, Any]] = None,
) -> Optional[tuple[pd.DataFrame, str]]:
    if df.empty or not asks_distinct(question):
        return None

    q = normalize_question_text(question)
    hint = _extract_distinct_hint(q) or extract_count_entity_hint(q)
    target_col = choose_count_entity_column(df, hint)
    if target_col is None and hint:
        target_col = choose_best_column(df, hint)

    if not target_col:
        if asks_count(q):
            count = int(len(df.drop_duplicates()))
            return pd.DataFrame({"distinct_row_count": [count]}), f"Found {count} distinct rows."
        deduped = apply_query_plan_post_processing(df.drop_duplicates().reset_index(drop=True), question, plan)
        return deduped, f"Returned {len(deduped)} distinct rows."

    series = _first_series_for_column_name(df, target_col)
    if series is None:
        return None

    clean_values = series[~_null_or_blank_mask(series)].astype(str).str.strip()
    if asks_count(q):
        distinct_count = int(clean_values.nunique(dropna=True))
        label = normalize_token(target_col) or "values"
        return (
            pd.DataFrame({f"distinct_{label}_count": [distinct_count]}),
            f"Found {distinct_count} distinct values in '{target_col}'.",
        )

    unique_values = sorted(clean_values.drop_duplicates().tolist())
    result_df = pd.DataFrame({target_col: unique_values})
    result_df = apply_query_plan_post_processing(result_df, question, plan)
    return result_df, f"Returned {len(result_df)} distinct values for '{target_col}'."


def data_quality_intent_result(
    df: pd.DataFrame,
    question: str,
    plan: Optional[dict[str, Any]] = None,
) -> Optional[tuple[pd.DataFrame, str]]:
    if df.empty or not asks_data_quality_question(question):
        return None

    q = normalize_question_text(question)
    col_hint = _extract_quality_column_hint(q)
    target_col = choose_best_column(df, col_hint) if col_hint else None

    if not target_col:
        if "email" in q or "mail" in q:
            target_col = choose_best_column(df, "email")
        elif "name" in q or "customer" in q or "buyer" in q or "client" in q:
            target_col = choose_best_column(df, "name")

    if asks_duplicate_check(q):
        if not target_col:
            target_col = choose_best_column(df, "id")

        if target_col:
            series = _first_series_for_column_name(df, target_col)
            if series is not None:
                values = series[~_null_or_blank_mask(series)].astype(str).str.strip()
                dup_counts = values.value_counts(dropna=False)
                dup_counts = dup_counts[dup_counts > 1]

                if asks_count(q):
                    duplicate_row_count = int(dup_counts.sum())
                    duplicate_value_count = int(len(dup_counts))
                    return (
                        pd.DataFrame({
                            "duplicate_value_count": [duplicate_value_count],
                            "duplicate_row_count": [duplicate_row_count],
                        }),
                        f"Found {duplicate_value_count} duplicate values in '{target_col}' across {duplicate_row_count} rows.",
                    )

                if dup_counts.empty:
                    return pd.DataFrame({target_col: [], "duplicate_count": []}), f"No duplicate values found in '{target_col}'."

                result_df = dup_counts.rename_axis(target_col).reset_index(name="duplicate_count")
                result_df = apply_query_plan_post_processing(result_df, question, plan)
                return result_df, f"Found {len(result_df)} duplicate values in '{target_col}'."

        duplicate_rows = df[df.duplicated(keep=False)].reset_index(drop=True)
        if asks_count(q):
            return pd.DataFrame({"duplicate_row_count": [int(len(duplicate_rows))]}), f"Found {len(duplicate_rows)} duplicate rows."
        if duplicate_rows.empty:
            return pd.DataFrame({"duplicate_row_count": [0]}), "No duplicate rows found."
        duplicate_rows = apply_query_plan_post_processing(duplicate_rows, question, plan)
        return duplicate_rows, f"Found {len(duplicate_rows)} duplicate rows."

    if asks_null_blank_check(q):
        if target_col:
            series = _first_series_for_column_name(df, target_col)
            if series is not None:
                mask = _null_or_blank_mask(series)
                count = int(mask.sum())
                if asks_count(q):
                    label = normalize_token(target_col) or "values"
                    return pd.DataFrame({f"missing_{label}_count": [count]}), f"Found {count} missing/blank values in '{target_col}'."
                rows = df[mask].reset_index(drop=True)
                rows = apply_query_plan_post_processing(rows, question, plan)
                if rows.empty:
                    return pd.DataFrame({f"missing_{target_col}": [0]}), f"No missing/blank values found in '{target_col}'."
                return rows, f"Found {len(rows)} rows with missing/blank values in '{target_col}'."

        missing_counts: list[tuple[str, int]] = []
        for col in df.columns:
            series = _first_series_for_column_name(df, str(col))
            if series is None:
                continue
            count = int(_null_or_blank_mask(series).sum())
            if count > 0:
                missing_counts.append((str(col), count))

        if not missing_counts:
            return pd.DataFrame({"missing_or_blank_values": [0]}), "No missing or blank values found."

        result_df = pd.DataFrame(missing_counts, columns=["column", "missing_or_blank_count"]).sort_values(
            by="missing_or_blank_count", ascending=False
        )
        if asks_count(q):
            total = int(result_df["missing_or_blank_count"].sum())
            return pd.DataFrame({"missing_or_blank_values": [total]}), f"Found {total} missing/blank values across all columns."
        result_df = apply_query_plan_post_processing(result_df, question, plan)
        return result_df, f"Found missing/blank values in {len(result_df)} columns."

    if asks_invalid_format_check(q):
        if "date" in q:
            date_cols = _date_like_columns(df)
            preferred_col = choose_best_column(df, col_hint) if col_hint else None
            target_date_col = preferred_col if preferred_col in date_cols else (date_cols[0] if date_cols else preferred_col)
            if target_date_col:
                series = _first_series_for_column_name(df, target_date_col)
                if series is not None:
                    raw = series.astype(str).str.strip()
                    non_blank = ~(raw.eq("") | raw.str.lower().isin({"nan", "none", "null"}))
                    parsed = pd.to_datetime(raw, errors="coerce", format="mixed")
                    invalid_mask = non_blank & parsed.isna()
                    invalid_count = int(invalid_mask.sum())
                    if asks_count(q):
                        return (
                            pd.DataFrame({f"invalid_{normalize_token(target_date_col) or 'date'}_count": [invalid_count]}),
                            f"Found {invalid_count} invalid date values in '{target_date_col}'.",
                        )
                    rows = df[invalid_mask].reset_index(drop=True)
                    rows = apply_query_plan_post_processing(rows, question, plan)
                    return rows, f"Found {len(rows)} rows with invalid date values in '{target_date_col}'."

        numeric_target = choose_numeric_column_by_hint(df, col_hint)
        if numeric_target:
            series = _first_series_for_column_name(df, numeric_target)
            if series is not None:
                raw = series.astype(str).str.strip()
                non_blank = ~(raw.eq("") | raw.str.lower().isin({"nan", "none", "null"}))
                parsed = _coerce_numeric_series(raw)
                invalid_mask = non_blank & parsed.isna()
                invalid_count = int(invalid_mask.sum())
                if asks_count(q):
                    return (
                        pd.DataFrame({f"invalid_{normalize_token(numeric_target) or 'numeric'}_count": [invalid_count]}),
                        f"Found {invalid_count} invalid numeric values in '{numeric_target}'.",
                    )
                rows = df[invalid_mask].reset_index(drop=True)
                rows = apply_query_plan_post_processing(rows, question, plan)
                return rows, f"Found {len(rows)} rows with invalid numeric values in '{numeric_target}'."

    if asks_outlier_check(q):
        metric_col = choose_numeric_column_by_hint(df, col_hint)
        if metric_col is None:
            numeric_cols = _numeric_like_columns(df)
            metric_col = numeric_cols[0] if numeric_cols else None
        if metric_col:
            series = _first_series_for_column_name(df, metric_col)
            if series is not None:
                numeric_series = _coerce_numeric_series(series)
                valid = numeric_series.dropna()
                if len(valid) >= 4:
                    q1 = float(valid.quantile(0.25))
                    q3 = float(valid.quantile(0.75))
                    iqr = q3 - q1
                    if iqr > 0:
                        lower = q1 - (1.5 * iqr)
                        upper = q3 + (1.5 * iqr)
                        outlier_mask = (numeric_series < lower) | (numeric_series > upper)
                        outlier_count = int(outlier_mask.sum())
                        if asks_count(q):
                            return (
                                pd.DataFrame({f"outlier_{normalize_token(metric_col) or 'value'}_count": [outlier_count]}),
                                f"Found {outlier_count} outliers in '{metric_col}' using IQR bounds [{lower:.3f}, {upper:.3f}].",
                            )
                        rows = df[outlier_mask].reset_index(drop=True)
                        rows = apply_query_plan_post_processing(rows, question, plan)
                        return rows, f"Found {len(rows)} outlier rows in '{metric_col}' using IQR bounds [{lower:.3f}, {upper:.3f}]."

        return pd.DataFrame({"outlier_row_count": [0]}), "No outliers found (or no numeric column available)."

    return None


def default_fallback_result(
    df: pd.DataFrame,
    question: str,
    trace: Optional[dict[str, Any]] = None,
) -> tuple[pd.DataFrame, str]:
    existing_plan = trace.get("queryPlan") if isinstance(trace, dict) else None
    query_plan = existing_plan if isinstance(existing_plan, dict) else build_query_plan(df, question)
    if trace is not None:
        trace["queryPlan"] = query_plan

    if asks_schema(question):
        cols_df = pd.DataFrame({"column": [str(c) for c in df.columns], "dtype": [str(t) for t in df.dtypes]})
        if trace is not None:
            trace["decisionPath"] = "schema"
        return cols_df, "Returned schema information (columns and dtypes)."

    data_quality = data_quality_intent_result(df, question, plan=query_plan)
    if data_quality is not None:
        if trace is not None:
            trace["decisionPath"] = "data_quality"
        return data_quality

    # Apply entity/date filters early so natural-language lookups like
    # "all products sold by Sarah" do not get misrouted to aggregation logic.
    conditions = extract_lookup_conditions(question)
    text_rules = extract_text_match_rules(question)
    plan_filters = query_plan.get("filters") if isinstance(query_plan.get("filters"), list) else []
    plan_text_rules: list[tuple[Optional[str], str, str]] = []
    for item in plan_filters:
        if not isinstance(item, dict):
            continue
        op = _normalize_filter_operator(item.get("operator"))
        if op not in {"contains", "startswith", "endswith"}:
            continue
        value = str(item.get("value") or "").strip()
        if not value:
            continue
        hint = str(item.get("column") or "").strip() or None
        plan_text_rules.append((hint, value, op))
    if not text_rules and plan_text_rules:
        text_rules = plan_text_rules
    date_filtered_df, has_date_filter = filter_rows_by_date_range(df, question)
    base_df = date_filtered_df if has_date_filter else df

    q = question.lower()
    has_negation = any(token in q for token in [" not in ", " not ", "!=", " not equal", " excluding ", " except "])

    has_plan_filters = any(isinstance(item, dict) for item in plan_filters)

    if conditions or has_date_filter or text_rules or has_plan_filters:
        matched = filter_rows_by_plan_filters(base_df, plan_filters) if has_plan_filters else base_df
        if has_plan_filters and matched.empty and text_rules:
            # Some LLM-derived plan filters can be over-constrained. When
            # structured text rules exist, retry from base dataframe.
            matched = base_df
        if conditions and not matched.empty:
            matched = filter_rows_by_conditions(matched, conditions)
        if text_rules and not matched.empty:
            llm_understanding = query_plan.get("llm_understanding") if isinstance(query_plan.get("llm_understanding"), dict) else None
            matched = filter_rows_by_text_rules(matched, text_rules, question=question, understanding=llm_understanding)
        if has_negation and not matched.empty:
            matched = base_df.loc[~base_df.index.isin(matched.index)]

        if asks_count(question):
            if trace is not None:
                trace["decisionPath"] = "filtered_count" if conditions or has_date_filter else "count"
            return build_count_result(matched, question)

        if asks_distinct(question):
            distinct_filtered = distinct_intent_result(matched, question, plan=query_plan)
            if distinct_filtered is not None:
                if trace is not None:
                    trace["decisionPath"] = "filtered_distinct"
                return distinct_filtered

        if conditions or text_rules or has_plan_filters:
            condition_display = [f"{h}={v}" if h else v for h, v in conditions]
            rule_display = [
                f"{(hint or 'value')} starts with {value}" if op == "startswith" else f"{(hint or 'value')} ends with {value}" if op == "endswith" else f"{(hint or 'value')} contains {value}"
                for hint, value, op in text_rules
            ]
            plan_display = []
            for item in plan_filters:
                if not isinstance(item, dict):
                    continue
                col = str(item.get("column") or "").strip()
                op = _normalize_filter_operator(item.get("operator"))
                val = str(item.get("value") or "").strip()
                if col and val:
                    plan_display.append(f"{col} {op} {val}")

            term_display = " and ".join([part for part in [*condition_display, *rule_display] if part])
            if not term_display and plan_display:
                term_display = " and ".join(plan_display)
            if not term_display:
                term_display = "applied semantic filters"
            if matched.empty:
                if trace is not None:
                    trace["decisionPath"] = "lookup_no_match"
                    trace["queryUsed"] = term_display
                return (
                    pd.DataFrame({"exists": [False], "query": [term_display], "matches": [0]}),
                    f"No matching data found for '{term_display}'.",
                )

            projection = requested_projection_columns(matched, question)
            q_lower = question.lower()
            wants_full_row_context = (
                any(token in q_lower for token in ["list", "show", "display", "return", "rows", "records", "all ", "orders", "items", "products"])
                and " only " not in f" {q_lower} "
            )
            if text_rules and any(op in {"startswith", "endswith"} for _, _, op in text_rules):
                wants_full_row_context = True
            should_single = should_return_single_row(question, conditions)
            final_df = matched.head(1) if should_single else matched
            if projection and not wants_full_row_context:
                final_df = final_df[projection]
                if not should_single:
                    final_df = add_identifying_columns(matched, final_df)
                    final_df = apply_query_plan_post_processing(final_df, question, query_plan)
                    final_df = compact_lookup_result_columns(final_df)
                if trace is not None:
                    trace["decisionPath"] = "lookup_projection"
                    trace["queryUsed"] = term_display
                return (
                    final_df.reset_index(drop=True),
                    f"Found {len(matched)} matching rows for '{term_display}'. Returned requested fields only.",
                )

            if not should_single:
                final_df = apply_query_plan_post_processing(final_df, question, query_plan)
                final_df = compact_lookup_result_columns(final_df)

            if trace is not None:
                trace["decisionPath"] = "lookup"
                trace["queryUsed"] = term_display
            return final_df.reset_index(drop=True), f"Found {len(matched)} matching rows for '{term_display}'."

        if has_date_filter:
            matched = apply_query_plan_post_processing(matched, question, query_plan)
            if trace is not None:
                trace["decisionPath"] = "date_range_filter"
            return matched.reset_index(drop=True), f"Returned {len(matched)} rows after applying date range filter."

    distinct_result = distinct_intent_result(df, question, plan=query_plan)
    if distinct_result is not None:
        if trace is not None:
            trace["decisionPath"] = "distinct"
        return distinct_result

    superlative = superlative_aggregation_result(df, question)
    if superlative is not None:
        if trace is not None:
            trace["decisionPath"] = "superlative_aggregation"
        return superlative

    grouped_agg = group_aggregation_result(df, question)
    if grouped_agg is not None:
        if trace is not None:
            trace["decisionPath"] = "group_aggregation"
        return grouped_agg

    metric_group = metric_by_group_result(df, question)
    if metric_group is not None:
        if trace is not None:
            trace["decisionPath"] = "metric_by_group"
        return metric_group

    scalar_agg = scalar_aggregation_result(df, question)
    if scalar_agg is not None:
        if trace is not None:
            trace["decisionPath"] = "scalar_aggregation"
        return scalar_agg

    relationship = relationship_insight_result(df, question)
    if relationship is not None:
        if trace is not None:
            trace["decisionPath"] = "relationship_insight"
        return relationship

    trend = trend_result(df, question)
    if trend is not None:
        if trace is not None:
            trace["decisionPath"] = "trend"
        return trend

    comparison = comparison_result(df, question)
    if comparison is not None:
        if trace is not None:
            trace["decisionPath"] = "comparison"
        return comparison

    chart_intent = chart_intent_result(df, question)
    if chart_intent is not None:
        if trace is not None:
            trace["decisionPath"] = "chart_intent"
        return chart_intent

    if asks_count(question):
        if trace is not None:
            trace["decisionPath"] = "count"
        return build_count_result(df, question)

    if wants_full_data(question):
        if trace is not None:
            trace["decisionPath"] = "full_data"
        return df.reset_index(drop=True), "Returned full dataset because question requested all data."

    if trace is not None:
        trace["decisionPath"] = "clarification_needed"
    return df.head(0).reset_index(drop=True), build_scope_guidance(df, question)
