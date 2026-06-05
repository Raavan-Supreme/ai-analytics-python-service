import re
from typing import Optional

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

LOOKUP_KEYWORDS = ["for", "where", "with", "whose"]


def wants_full_data(question: str) -> bool:
    q = question.lower()
    keywords = ["all", "full", "entire", "complete", "everything", "whole", "show all", "all rows"]
    return any(k in q for k in keywords)


def asks_existence(question: str) -> bool:
    q = question.lower()
    markers = ["is there", "are there", "any", "exists", "exist", "contains", "like", "present"]
    return any(m in q for m in markers)


def asks_count(question: str) -> bool:
    q = question.lower()
    markers = ["how many", "count", "number of", "total"]
    return any(m in q for m in markers)


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
    if re.search(r"\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b", q):
        return True
    if any(k in q for k in ["status of", "details of", "record for", "for email", "for id", "where ", " with "]):
        return True
    return False


def should_use_rule_based(question: str) -> bool:
    return (
        wants_full_data(question)
        or asks_existence(question)
        or asks_count(question)
        or asks_schema(question)
        or asks_id_lookup(question)
        or asks_specific_lookup(question)
    )


def should_render_chart(question: str, chart_type: str, result_df: pd.DataFrame) -> bool:
    if result_df.empty:
        return False

    if chart_type and chart_type != "auto":
        return chart_type != "none"

    if asks_count(question) or asks_schema(question) or asks_existence(question) or asks_specific_lookup(question):
        return False

    if asks_comparison(question):
        return True

    # Auto mode: only chart if multi-row and there is at least one numeric column.
    numeric_cols = [c for c in result_df.columns if pd.api.types.is_numeric_dtype(result_df[c])]
    return len(result_df) > 1 and bool(numeric_cols)


def extract_search_term(question: str) -> Optional[str]:
    quoted = re.findall(r"['\"]([^'\"]{1,120})['\"]", question)
    if quoted:
        return quoted[0].strip()

    q = question.lower().strip()

    id_match = re.search(r"(?:for\s+id|id)\s+([a-z0-9_\-./]+)", q)
    if id_match:
        return id_match.group(1).strip(" ?.,")

    patterns = [
        r"(?:like|contains|contain|with|where|named|called)\s+(.+)$",
        r"(?:is there|are there|any)\s+(.+)$",
    ]
    for pattern in patterns:
        m = re.search(pattern, q)
        if m:
            term = m.group(1).strip(" ?.,")
            if term:
                return term
    return None


def extract_lookup_conditions(question: str) -> list[tuple[Optional[str], str]]:
    q = question.strip()
    q_lower = q.lower()
    conditions: list[tuple[Optional[str], str]] = []

    def clean_value(value: str) -> str:
        trimmed = re.split(r"\b(if|then|and|or|tell|show|please|thanks|for|because|but)\b", value, maxsplit=1)[0]
        return trimmed.strip(" '\".,?!")

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

    id_match = re.search(r"\b(?:for\s+id|where\s+id|with\s+id|id)\s*(?:=|is)?\s*([a-z0-9_\-./]{2,80})", q_lower)
    if id_match:
        id_value = clean_value(id_match.group(1))
        if id_value:
            conditions.append(("id", id_value))

    # Clause-based comparisons: split conditions like
    # "where source is organization and eventName is IP INTERIORS LTD".
    clause_text = q_lower
    for starter in [" where ", " with ", " for "]:
        marker = f" {q_lower} "
        if starter in marker:
            clause_text = marker.split(starter, 1)[1].strip()
            break

    for segment in re.split(r"\band\b", clause_text):
        seg = segment.strip()
        if not seg:
            continue
        m = re.search(r"([a-z][a-z0-9_\- ]{1,40})\s*(?:=|\bis\b|\bequals\b)\s*(.+)", seg)
        if not m:
            continue
        field = m.group(1).strip()
        val = clean_value(m.group(2))
        if field and val and field not in {"is", "for", "with", "where"} and val not in {"is", "for", "with", "where"}:
            conditions.append((field, val))

    # Fallback to existing search term if nothing explicit found
    if not conditions:
        term = extract_search_term(question)
        if term:
            conditions.append((None, term))

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
    token = re.sub(r"[^a-z0-9]", "", str(value).lower())
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
    for hint, raw_value in conditions:
        value = raw_value.strip()
        if not value or matched.empty:
            continue

        value_norm = normalize_token(value)
        exact_mask = pd.Series(False, index=matched.index)
        contains_mask = pd.Series(False, index=matched.index)

        target_col = choose_best_column(matched, hint)
        candidate_cols = [target_col] if target_col else [str(c) for c in matched.columns]

        for col in candidate_cols:
            series = matched[col].astype(str)
            norm = series.map(normalize_token)
            exact_mask = exact_mask | (norm == value_norm)
            contains_mask = contains_mask | norm.str.contains(value_norm, na=False)

        if exact_mask.any():
            matched = matched[exact_mask]
        elif contains_mask.any():
            matched = matched[contains_mask]
        else:
            return matched.head(0)

    return matched


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
            elif alias in col_norm:
                score = max(score, 2)
        if score > best_score:
            best_col = str(col)
            best_score = score
    return best_col if best_score > 0 else None


def requested_projection_columns(df: pd.DataFrame, question: str) -> list[str]:
    q = question.lower()
    aliases: dict[str, list[str]] = {
        "amount": ["amount", "amt", "price", "value", "total"],
        "source_code": ["sourcecode", "source code", "source", "src", "sku", "code"],
        "id": ["id", "productid", "itemid", "webid", "ean", "code"],
        "status": ["status", "state", "result", "active", "inactive", "giftaid", "gift_aid"],
        "email": ["email", "mail", "emailaddress", "e-mail"],
    }

    requested: list[str] = []
    if "amount" in q or "price" in q or "total" in q:
        requested.append("amount")
    if "source code" in q or "source" in q or "code" in q:
        requested.append("source_code")
    if re.search(r"\bid\b", q):
        requested.append("id")
    if "status" in q or "giftaid" in q or "gift aid" in q:
        requested.append("status")
    if "email" in q:
        requested.append("email")

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
        elif key in aliases:
            col = pick_column_by_aliases(df, aliases[key])
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


def find_matching_rows(df: pd.DataFrame, term: str) -> pd.DataFrame:
    needle = term.strip().lower()
    if not needle:
        return df.head(0)

    mask = pd.Series(False, index=df.index)
    for col in df.columns:
        values = df[col].astype(str).str.lower()
        mask = mask | values.str.contains(needle, na=False)
    return df[mask]


def default_fallback_result(df: pd.DataFrame, question: str) -> tuple[pd.DataFrame, str]:
    if wants_full_data(question):
        return df.reset_index(drop=True), "Returned full dataset because question requested all data."

    if asks_schema(question):
        cols_df = pd.DataFrame({"column": [str(c) for c in df.columns], "dtype": [str(t) for t in df.dtypes]})
        return cols_df, "Returned schema information (columns and dtypes)."

    if asks_count(question):
        return pd.DataFrame({"count": [int(len(df))]}), "Returned total row count."

    if asks_existence(question) or asks_specific_lookup(question):
        conditions = extract_lookup_conditions(question)
        if conditions:
            matched = filter_rows_by_conditions(df, conditions)
            term_display = " and ".join([f"{h}={v}" if h else v for h, v in conditions])
            if matched.empty:
                return (
                    pd.DataFrame({"exists": [False], "query": [term_display], "matches": [0]}),
                    f"No matching data found for '{term_display}'.",
                )

            projection = requested_projection_columns(matched, question)
            should_single = should_return_single_row(question, conditions)
            final_df = matched.head(1) if should_single else matched
            if projection:
                final_df = final_df[projection]
                if not should_single:
                    final_df = add_identifying_columns(matched, final_df)
                return (
                    final_df.reset_index(drop=True),
                    f"Found {len(matched)} matching rows for '{term_display}'. Returned requested fields only.",
                )

            return final_df.reset_index(drop=True), f"Found {len(matched)} matching rows for '{term_display}'."

    return df.head(100).reset_index(drop=True), "Returned first 100 rows as fallback answer."
