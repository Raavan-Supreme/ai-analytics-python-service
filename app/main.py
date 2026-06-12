import ast
import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import uuid
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx
import matplotlib
import pandas as pd
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core import charts as core_charts
from app.core import intents as core_intents
from app.core.paths import normalize_input_path as core_normalize_input_path

matplotlib.use("Agg")
import matplotlib.pyplot as plt


BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")
load_dotenv()
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
CHART_DIR = DATA_DIR / "charts"
DB_PATH = DATA_DIR / "app.db"

SUPPORTED_CHART_TYPES = ["line", "bar", "scatter", "hist", "box", "area", "pie", "violin", "heatmap"]

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
CHART_DIR.mkdir(parents=True, exist_ok=True)

TOKEN_STORE: dict[str, int] = {}
UNRESOLVED_QUESTION_COUNTS: dict[str, int] = {}
QUERY_TRACE_ENABLED = os.getenv("APP_QUERY_TRACE", "true").strip().lower() in {"1", "true", "yes", "on"}
QUERY_TRACE_INCLUDE_IN_RESPONSE = os.getenv("APP_QUERY_TRACE_INCLUDE_RESPONSE", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LLM_TRACE_ENABLED = os.getenv("APP_LLM_TRACE", "true").strip().lower() in {"1", "true", "yes", "on"}
LLM_TRACE_PROMPT_MAX = int(os.getenv("APP_LLM_TRACE_PROMPT_MAX", "4000"))
LLM_TRACE_RESPONSE_MAX = int(os.getenv("APP_LLM_TRACE_RESPONSE_MAX", "2000"))
logger = logging.getLogger("python_service.query_trace")


app = FastAPI(title="Python NL Query Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=6, max_length=128)


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthResponse(BaseModel):
    token: str


class RelationshipRequest(BaseModel):
    leftDatasetId: int
    rightDatasetId: int
    leftKey: str
    rightKey: str
    joinType: str = "inner"


class NLQueryRequest(BaseModel):
    datasetId: Optional[int] = None
    relationshipId: Optional[int] = None
    question: str
    chartType: str = "auto"


class JavaRelationshipSpec(BaseModel):
    leftPath: str
    rightPath: str
    leftKey: str
    rightKey: str
    joinType: str = "inner"


class JavaNLQueryRequest(BaseModel):
    sessionId: Optional[int] = None
    filePath: Optional[str] = None
    sheetName: Optional[str] = None
    filePaths: list[str] = Field(default_factory=list)
    relationships: list[JavaRelationshipSpec] = Field(default_factory=list)
    question: str
    chartType: str = "auto"


class NLQueryResponse(BaseModel):
    rows: list[dict[str, Any]]
    columns: list[str]
    chart: dict[str, Any]
    charts: list[dict[str, Any]] = Field(default_factory=list)
    summary: str
    generated_code: str
    attempts: int
    debugTrace: Optional[dict[str, Any]] = None


class DashboardRequest(BaseModel):
    name: str
    config: dict[str, Any]


class DatasetPreviewResponse(BaseModel):
    datasetId: int
    name: str
    sourcePath: str
    sheetName: Optional[str]
    rows: list[dict[str, Any]]
    columns: list[str]
    columnProfile: dict[str, str]


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS datasets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            sheet_name TEXT,
            source_type TEXT NOT NULL,
            column_profile_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            left_dataset_id INTEGER NOT NULL,
            right_dataset_id INTEGER NOT NULL,
            left_key TEXT NOT NULL,
            right_key TEXT NOT NULL,
            join_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS query_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            dataset_id INTEGER,
            relationship_id INTEGER,
            question TEXT NOT NULL,
            generated_code TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            result_preview_json TEXT NOT NULL,
            chart_file_name TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS dashboards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            config_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    conn.commit()
    conn.close()


init_db()


def utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def get_bearer_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization header format")
    return parts[1].strip()


def get_current_user_id(authorization: Optional[str] = Header(default=None)) -> int:
    token = get_bearer_token(authorization)
    user_id = TOKEN_STORE.get(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user_id


def detect_column_kind(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "date"

    non_null = series.dropna().astype(str)
    if non_null.empty:
        return "text"

    try:
        parsed_dates = pd.to_datetime(non_null, errors="coerce", utc=False, format="mixed")
    except TypeError:
        # pandas < 2.0 does not support format="mixed".
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            parsed_dates = pd.to_datetime(non_null, errors="coerce", utc=False)
    if parsed_dates.notna().mean() > 0.8:
        return "date"

    parsed_nums = pd.to_numeric(non_null.str.replace(",", "", regex=False), errors="coerce")
    if parsed_nums.notna().mean() > 0.8:
        return "numeric"

    return "text"


def profile_dataframe(df: pd.DataFrame) -> dict[str, str]:
    return {str(col): detect_column_kind(df[col]) for col in df.columns}


def normalize_input_path(raw_path: str) -> str:
    return core_normalize_input_path(raw_path)


def to_plot_label(value: Any) -> str:
    if hasattr(value, "tolist") and not isinstance(value, (str, bytes)):
        value = value.tolist()
    if isinstance(value, (list, tuple, set, dict)):
        return json.dumps(value, default=str, sort_keys=True)
    try:
        if pd.isna(value):
            return "null"
    except Exception:
        pass
    return str(value)


def to_1d_series(df: pd.DataFrame, field: str) -> pd.Series:
    raw = df[field]
    if isinstance(raw, pd.DataFrame):
        if raw.shape[1] == 0:
            return pd.Series([], dtype="object")
        raw = raw.iloc[:, 0]
    if isinstance(raw, pd.Series):
        return raw
    return pd.Series(raw)


def read_csv_robust(file_path: str) -> pd.DataFrame:
    try:
        df = pd.read_csv(file_path)
    except Exception:
        df = pd.read_csv(file_path, engine="python", on_bad_lines="skip", encoding_errors="ignore")
    return normalize_uploaded_dataframe(df)


def _normalize_column_name(name: Any, fallback_index: int) -> str:
    cleaned = re.sub(r"\s+", " ", str(name or "").replace("\r", " ").replace("\n", " ")).strip()
    return cleaned or f"column_{fallback_index}"


def _normalize_fragmented_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    cleaned = value.replace("\r", " ").replace("\n", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _date_parse_score(sample: pd.Series, *, dayfirst: bool) -> float:
    if sample.empty:
        return 0.0
    try:
        parsed = pd.to_datetime(sample, errors="coerce", format="mixed", dayfirst=dayfirst)
    except TypeError:
        parsed = pd.to_datetime(sample, errors="coerce", dayfirst=dayfirst)
    return float(parsed.notna().mean())


def _coerce_date_like_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    hint_tokens = ("date", "time", "day", "month", "year", "created", "updated", "purchase", "txn")
    for col in out.columns:
        series = to_1d_series(out, str(col))
        if pd.api.types.is_datetime64_any_dtype(series):
            continue
        sample = series.dropna().astype(str).str.strip()
        sample = sample[sample != ""]
        if sample.empty:
            continue
        if len(sample) > 600:
            sample = sample.sample(n=600, random_state=0, replace=False)
        col_norm = core_intents.normalize_token(col)
        has_date_hint = any(token in col_norm for token in hint_tokens)
        score_default = _date_parse_score(sample, dayfirst=False)
        score_dayfirst = _date_parse_score(sample, dayfirst=True)
        best_dayfirst = score_dayfirst > (score_default + 0.05)
        best_score = score_dayfirst if best_dayfirst else score_default
        if best_score >= 0.8 or (has_date_hint and best_score >= 0.45):
            source = series.astype(str).str.strip()
            source = source.replace({"": pd.NA})
            try:
                out[str(col)] = pd.to_datetime(source, errors="coerce", format="mixed", dayfirst=best_dayfirst)
            except TypeError:
                out[str(col)] = pd.to_datetime(source, errors="coerce", dayfirst=best_dayfirst)
    return out


def normalize_uploaded_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    out = df.copy()
    normalized_cols: list[str] = []
    seen: dict[str, int] = {}
    for idx, col in enumerate(out.columns):
        base = _normalize_column_name(col, idx)
        count = seen.get(base, 0)
        seen[base] = count + 1
        normalized_cols.append(base if count == 0 else f"{base}_{count + 1}")
    out.columns = normalized_cols

    for col in out.columns:
        series = to_1d_series(out, str(col))
        if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
            out[str(col)] = series.map(_normalize_fragmented_text)

    out = _coerce_date_like_columns(out)
    return out


def load_dataframe(file_path: str, sheet_name: Optional[str]) -> pd.DataFrame:
    if file_path.lower().endswith(".csv"):
        return read_csv_robust(file_path)
    if sheet_name:
        return normalize_uploaded_dataframe(pd.read_excel(file_path, sheet_name=sheet_name))
    return normalize_uploaded_dataframe(pd.read_excel(file_path))


def load_dataframe_from_path(file_path: str) -> pd.DataFrame:
    lower_path = file_path.lower()
    if lower_path.endswith(".csv"):
        return read_csv_robust(file_path)
    if lower_path.endswith(".xlsx") or lower_path.endswith(".xls"):
        return normalize_uploaded_dataframe(pd.read_excel(file_path))
    raise HTTPException(status_code=400, detail=f"Unsupported file type: {Path(file_path).suffix}")


def _is_excel_path(file_path: str) -> bool:
    lower = file_path.lower()
    return lower.endswith(".xlsx") or lower.endswith(".xls")


def _question_tokens_for_schema(text: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", str(text or "").lower()) if token}


def _sheet_name_score(sheet_name: str, question: str) -> float:
    question_norm = core_intents.normalize_token(question)
    sheet_norm = core_intents.normalize_token(sheet_name)
    if not question_norm or not sheet_norm:
        return 0.0

    score = 0.0
    if sheet_norm in question_norm:
        score += 0.45

    question_tokens = _question_tokens_for_schema(question)
    sheet_tokens = _question_tokens_for_schema(sheet_name)
    overlap = len(question_tokens.intersection(sheet_tokens))
    if overlap > 0:
        score += min(0.35, 0.12 * overlap)
    return score


def _sheet_schema_score(sheet_df: pd.DataFrame, question: str) -> tuple[float, list[str]]:
    relation = core_intents.question_schema_relation(sheet_df, question)
    try:
        score = float(relation.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    relevant_cols_raw = relation.get("relevantColumns")
    relevant_cols = [str(col) for col in relevant_cols_raw] if isinstance(relevant_cols_raw, list) else []
    return score, relevant_cols


def _is_all_sheets_request(question: str) -> bool:
    q = question.lower()
    markers = [
        "all sheets",
        "all sheet",
        "all tabs",
        "across sheets",
        "across all sheets",
        "all subsheets",
        "all sub sheets",
        "all worksheets",
        "entire workbook",
        "whole workbook",
    ]
    return any(marker in q for marker in markers)


def _resolve_requested_sheet_name(requested_sheet: str, available_sheets: list[str]) -> Optional[str]:
    requested_raw = str(requested_sheet or "").strip()
    if not requested_raw:
        return None

    for sheet_name in available_sheets:
        if sheet_name == requested_raw:
            return sheet_name

    requested_lower = requested_raw.casefold()
    for sheet_name in available_sheets:
        if str(sheet_name).casefold() == requested_lower:
            return sheet_name

    requested_norm = core_intents.normalize_token(requested_raw)
    if requested_norm:
        for sheet_name in available_sheets:
            if core_intents.normalize_token(sheet_name) == requested_norm:
                return sheet_name

    return None


def _combine_sheets_with_origin(file_path: str, sheets: dict[str, pd.DataFrame]) -> pd.DataFrame:
    file_name = Path(file_path).name
    frames: list[pd.DataFrame] = []
    for sheet_name, sheet_df in sheets.items():
        tagged = sheet_df.copy()
        tagged["__sheet_name"] = str(sheet_name)
        tagged["__source_file"] = file_name
        frames.append(tagged)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


def _combine_frames_with_file_origin(frames: list[tuple[str, pd.DataFrame]]) -> pd.DataFrame:
    tagged_frames: list[pd.DataFrame] = []
    for source_path, frame in frames:
        tagged = frame.copy()
        if "__source_file" not in tagged.columns:
            tagged["__source_file"] = Path(source_path).name
        tagged_frames.append(tagged)
    if not tagged_frames:
        return pd.DataFrame()
    return pd.concat(tagged_frames, ignore_index=True, sort=False)


def load_dataframe_from_path_with_question(
    file_path: str,
    question: str,
    *,
    preferred_key: Optional[str] = None,
    requested_sheet: Optional[str] = None,
    force_all_sheets: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    lower_path = file_path.lower()
    if lower_path.endswith(".csv"):
        df = read_csv_robust(file_path)
        return df, {
            "sourceType": "csv",
            "filePath": file_path,
            "mode": "single_file",
        }

    if not _is_excel_path(file_path):
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {Path(file_path).suffix}")

    try:
        workbook = pd.read_excel(file_path, sheet_name=None)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to read workbook: {Path(file_path).name}. {exc}") from exc

    if not isinstance(workbook, dict) or not workbook:
        raise HTTPException(status_code=400, detail=f"Workbook has no readable sheets: {Path(file_path).name}")

    sheets: dict[str, pd.DataFrame] = {
        str(sheet_name): normalize_uploaded_dataframe(frame.reset_index(drop=True))
        for sheet_name, frame in workbook.items()
    }
    available_sheets = list(sheets.keys())
    candidate_sheets = sheets

    if requested_sheet:
        resolved_requested_sheet = _resolve_requested_sheet_name(requested_sheet, available_sheets)
        if resolved_requested_sheet:
            selected_df = sheets[resolved_requested_sheet]
            return selected_df, {
                "sourceType": "excel",
                "filePath": file_path,
                "mode": "requested_sheet",
                "selectedSheet": resolved_requested_sheet,
                "requestedSheet": requested_sheet,
                "selectionScore": 1.0,
                "relevantColumns": [],
                "availableSheets": available_sheets,
                "preferredKey": preferred_key,
            }

    if preferred_key:
        key_candidates = {name: frame for name, frame in sheets.items() if preferred_key in frame.columns}
        if key_candidates:
            candidate_sheets = key_candidates

    if force_all_sheets or (_is_all_sheets_request(question) and preferred_key is None and len(candidate_sheets) > 1):
        combined = _combine_sheets_with_origin(file_path, candidate_sheets)
        return combined, {
            "sourceType": "excel",
            "filePath": file_path,
            "mode": "all_sheets",
            "sheetCount": len(candidate_sheets),
            "availableSheets": available_sheets,
            "selectedSheets": list(candidate_sheets.keys()),
            "preferredKey": preferred_key,
        }

    ranked: list[tuple[float, str, list[str]]] = []
    for sheet_name, frame in candidate_sheets.items():
        name_score = _sheet_name_score(sheet_name, question)
        schema_score, relevant_cols = _sheet_schema_score(frame, question)
        key_bonus = 0.5 if preferred_key and preferred_key in frame.columns else 0.0
        ranked.append((name_score + schema_score + key_bonus, sheet_name, relevant_cols))

    ranked.sort(key=lambda item: (-item[0], item[1].lower()))
    selected_score, selected_sheet, relevant_columns = ranked[0]
    selected_df = candidate_sheets[selected_sheet]

    # If sheet confidence is weak, combine sheets to avoid missing answers hidden in other subsheets.
    if (
        selected_score < 0.25
        and len(candidate_sheets) > 1
        and preferred_key is None
    ):
        combined = _combine_sheets_with_origin(file_path, candidate_sheets)
        return combined, {
            "sourceType": "excel",
            "filePath": file_path,
            "mode": "all_sheets_low_confidence",
            "sheetCount": len(candidate_sheets),
            "availableSheets": available_sheets,
            "selectedSheets": list(candidate_sheets.keys()),
            "preferredKey": preferred_key,
        }

    return selected_df, {
        "sourceType": "excel",
        "filePath": file_path,
        "mode": "selected_sheet",
        "selectedSheet": selected_sheet,
        "selectionScore": round(float(selected_score), 3),
        "relevantColumns": relevant_columns,
        "availableSheets": available_sheets,
        "preferredKey": preferred_key,
    }


def summarize_data_context(data_context: dict[str, Any]) -> str:
    sources = data_context.get("selectedSources")
    if not isinstance(sources, list) or not sources:
        return ""

    selected_sheets: list[str] = []
    combined_sheets = 0
    for source in sources:
        if not isinstance(source, dict):
            continue
        if str(source.get("sourceType")) != "excel":
            continue
        mode = str(source.get("mode") or "")
        if mode.startswith("all_sheets"):
            try:
                combined_sheets += int(source.get("sheetCount") or 0)
            except (TypeError, ValueError):
                pass
            continue
        selected = source.get("selectedSheet")
        if selected:
            selected_sheets.append(str(selected))

    if combined_sheets > 0:
        return f"Workbook context: combined {combined_sheets} sheet(s)."

    if selected_sheets:
        unique: list[str] = []
        for sheet in selected_sheets:
            if sheet not in unique:
                unique.append(sheet)
        shown = ", ".join(unique[:3])
        if len(unique) > 3:
            shown += f" (+{len(unique) - 3} more)"
        return f"Workbook context: using sheet(s) {shown}."

    return ""


def get_dataset_row(user_id: int, dataset_id: int) -> sqlite3.Row:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM datasets WHERE id = ? AND user_id = ?",
        (dataset_id, user_id),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail=f"Dataset {dataset_id} not found")
    return row


def load_dataset_df(user_id: int, dataset_id: int) -> pd.DataFrame:
    row = get_dataset_row(user_id, dataset_id)
    return load_dataframe(row["file_path"], row["sheet_name"])


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def call_local_llm(
    prompt: str,
    *,
    system_prompt: Optional[str] = None,
    model_override: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout_sec: Optional[float] = None,
) -> str:
    provider = os.getenv("LOCAL_LLM_PROVIDER", "none").strip().lower()
    base_url = os.getenv("LOCAL_LLM_BASE_URL", "").strip()
    model = (model_override or os.getenv("LOCAL_LLM_MODEL", "gamma13b")).strip()

    if provider in {"", "none", "off", "disabled"}:
        return ""

    resolved_system_prompt = system_prompt or "Write safe pandas code only. Use df as dataframe and assign output to result."
    resolved_temperature = temperature if temperature is not None else _env_float("LOCAL_LLM_TEMPERATURE", 0.1)
    resolved_max_tokens = max_tokens if max_tokens is not None else _env_int("LOCAL_LLM_MAX_TOKENS", 2000)
    resolved_timeout = timeout_sec if timeout_sec is not None else _env_float("LOCAL_LLM_TIMEOUT_SEC", 60.0)

    try:
        if provider in {"lmstudio", "openai", "openai_compatible"}:
            effective_base_url = base_url or "http://localhost:1234"
            url = effective_base_url.rstrip("/") + "/v1/chat/completions"
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": resolved_system_prompt,
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": resolved_temperature,
            }
            if resolved_max_tokens and resolved_max_tokens > 0:
                payload["max_tokens"] = resolved_max_tokens
            with httpx.Client(timeout=resolved_timeout) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]

        if provider == "ollama":
            effective_base_url = base_url or "http://localhost:11434"
            url = effective_base_url.rstrip("/") + "/api/chat"
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": resolved_system_prompt,
                    },
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
            }
            options: dict[str, Any] = {"temperature": resolved_temperature}
            if resolved_max_tokens and resolved_max_tokens > 0:
                options["num_predict"] = resolved_max_tokens
            payload["options"] = options
            with httpx.Client(timeout=resolved_timeout) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                return data.get("message", {}).get("content", "")
    except Exception as exc:
        logger.warning("Local LLM call failed. Falling back to rule-based path. provider=%s error=%s", provider, exc)
        return ""

    return ""


def extract_code(text: str) -> str:
    if "```" not in text:
        return text.strip()

    blocks = text.split("```")
    if len(blocks) >= 3:
        code = blocks[1].strip()
        if code.lower().startswith("python"):
            code = code[6:].strip()
        return code
    return text.strip()


def validate_generated_code(code: str) -> None:
    forbidden_calls = {
        "__import__",
        "eval",
        "exec",
        "open",
        "compile",
        "input",
        "globals",
        "locals",
        "getattr",
        "setattr",
        "delattr",
        "os",
        "sys",
        "subprocess",
    }

    forbidden_nodes = (
        ast.Import,
        ast.ImportFrom,
        ast.Global,
        ast.Nonlocal,
        ast.Try,
        ast.With,
        ast.AsyncFunctionDef,
        ast.ClassDef,
    )

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f"Generated code has syntax error: {exc}") from exc

    has_result_assignment = False

    for node in ast.walk(tree):
        if isinstance(node, forbidden_nodes):
            raise ValueError("Generated code includes forbidden statement")

        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "result":
                    has_result_assignment = True

        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in forbidden_calls:
                raise ValueError(f"Forbidden call used: {node.func.id}")
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                if node.func.value.id in {"os", "sys", "subprocess"}:
                    raise ValueError("Forbidden module call used")

    if not has_result_assignment:
        raise ValueError("Generated code must assign the answer to `result`")


def execute_query_code(df: pd.DataFrame, code: str) -> pd.DataFrame:
    validate_generated_code(code)

    safe_globals = {
        "__builtins__": {
            "len": len,
            "sum": sum,
            "min": min,
            "max": max,
            "abs": abs,
            "round": round,
            "sorted": sorted,
            "range": range,
            "enumerate": enumerate,
            "list": list,
            "dict": dict,
            "set": set,
            "float": float,
            "int": int,
            "str": str,
            "bool": bool,
        }
    }
    local_vars = {"df": df.copy(), "pd": pd}

    exec(code, safe_globals, local_vars)

    result = local_vars.get("result")
    if result is None:
        raise ValueError("Generated code did not produce `result`")

    if isinstance(result, pd.Series):
        result = result.to_frame()
    elif not isinstance(result, pd.DataFrame):
        result = pd.DataFrame({"result": [result]})

    return result.reset_index(drop=True)


def _sample_schema_for_query_prompt(df: pd.DataFrame, max_cols: int = 28, max_values: int = 4) -> str:
    profile = profile_dataframe(df)
    lines: list[str] = []
    for col in list(df.columns)[:max_cols]:
        series = to_1d_series(df, str(col))
        non_null = series.dropna().astype(str)
        sample_values = list(dict.fromkeys(non_null.tolist()))[:max_values]
        sample_text = ", ".join(sample_values)
        inferred_kind = profile.get(str(col), "text")
        lines.append(f"- {col} ({series.dtype}, inferred={inferred_kind}) sample=[{sample_text}]")
    return "\n".join(lines)


def _format_data_context_for_prompt(data_context: Optional[dict[str, Any]]) -> str:
    if not isinstance(data_context, dict):
        return ""

    lines: list[str] = []
    context_note = summarize_data_context(data_context)
    if context_note:
        lines.append(context_note)

    selected_sources = data_context.get("selectedSources")
    if isinstance(selected_sources, list):
        for source in selected_sources[:12]:
            if not isinstance(source, dict):
                continue
            source_path = str(source.get("filePath") or "")
            file_name = Path(source_path).name if source_path else "unknown"
            mode = str(source.get("mode") or "")
            selected_sheet = str(source.get("selectedSheet") or "").strip()
            if selected_sheet:
                lines.append(f"- file={file_name}, mode={mode}, sheet={selected_sheet}")
            else:
                selected_sheets = source.get("selectedSheets")
                if isinstance(selected_sheets, list) and selected_sheets:
                    shown = ", ".join(str(item) for item in selected_sheets[:4])
                    if len(selected_sheets) > 4:
                        shown += f" (+{len(selected_sheets) - 4} more)"
                    lines.append(f"- file={file_name}, mode={mode}, sheets={shown}")
                else:
                    lines.append(f"- file={file_name}, mode={mode}")

    return "\n".join(lines)


def build_query_prompt(df: pd.DataFrame, question: str, data_context: Optional[dict[str, Any]] = None) -> str:
    schema_details = _sample_schema_for_query_prompt(df)
    context_text = _format_data_context_for_prompt(data_context)
    context_block = f"Data context:\n{context_text}\n\n" if context_text else ""

    return (
        "You are given a pandas DataFrame named df.\n"
        + f"Rows: {len(df)}\n"
        + f"Columns: {len(df.columns)}\n\n"
        + context_block
        + "Schema with inferred types and sample values:\n"
        + schema_details
        + "\n\n"
        + f"Question: {question}\n\n"
        + "Rules:\n"
        + "1) Use the existing DataFrame variable df.\n"
        + "2) Do not import anything.\n"
        + "3) Assign final answer to variable result.\n"
        + "4) Return code only.\n"
        + "5) If user asks for all/full data, return all rows (result = df).\n"
        + "6) If user asks whether data exists, return boolean existence and matching rows.\n"
        + "7) If user asks for top/bottom/first/last N, return only N rows.\n"
        + "8) Date filters should support mixed formats (for example dd/mm/yy and yyyy-mm-dd).\n"
        + "9) Numeric values may include currency symbols (for example $, GBP, EUR, INR, £, €, Rs); normalize before numeric operations.\n"
        + "10) Text fields may contain fragmented whitespace/newlines; normalize with .str.replace and .str.strip before text filtering when needed.\n"
    )


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


def should_use_rule_based(question: str) -> bool:
    return wants_full_data(question) or asks_existence(question) or asks_count(question) or asks_schema(question)


def extract_search_term(question: str) -> Optional[str]:
    quoted = re.findall(r"['\"]([^'\"]{1,120})['\"]", question)
    if quoted:
        return quoted[0].strip()

    q = question.lower().strip()
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
    q = question.lower().strip()

    if wants_full_data(question):
        return df.reset_index(drop=True), "Returned full dataset because question requested all data."

    if asks_schema(question):
        cols_df = pd.DataFrame({"column": [str(c) for c in df.columns], "dtype": [str(t) for t in df.dtypes]})
        return cols_df, "Returned schema information (columns and dtypes)."

    if asks_count(question):
        return pd.DataFrame({"count": [int(len(df))]}), "Returned total row count."

    if asks_existence(question):
        term = extract_search_term(question)
        if term:
            matched = find_matching_rows(df, term)
            if matched.empty:
                return pd.DataFrame({"exists": [False], "term": [term], "matches": [0]}), f"No matching data found for '{term}'."
            return matched.reset_index(drop=True), f"Found {len(matched)} matching rows for '{term}'."

    return df.head(100).reset_index(drop=True), "Returned first 100 rows as fallback answer."


def choose_chart_specs(result_df: pd.DataFrame, chart_type: str) -> list[dict[str, Any]]:
    if result_df.empty or len(result_df.columns) == 0:
        return [{"type": "none", "xField": None, "yField": None}]

    cols = list(result_df.columns)
    numeric_cols = [c for c in cols if pd.api.types.is_numeric_dtype(result_df[c])]
    text_cols = [c for c in cols if c not in numeric_cols]

    if chart_type == "all":
        selected_types = SUPPORTED_CHART_TYPES
    elif chart_type == "auto":
        selected_types = ["bar" if text_cols and numeric_cols else "line"]
    elif chart_type in SUPPORTED_CHART_TYPES:
        selected_types = [chart_type]
    else:
        selected_types = ["line"]

    x_field = text_cols[0] if text_cols else cols[0]
    y_field = numeric_cols[0] if numeric_cols else (cols[1] if len(cols) > 1 else cols[0])

    specs: list[dict[str, Any]] = []
    for selected_type in selected_types:
        specs.append(
            {
                "type": selected_type,
                "xField": x_field,
                "yField": y_field,
            }
        )
    return specs


def render_chart_png(result_df: pd.DataFrame, chart_spec: dict[str, Any]) -> Optional[str]:
    if chart_spec.get("type") == "none":
        return None

    x_field = chart_spec.get("xField")
    y_field = chart_spec.get("yField")
    if not x_field or not y_field:
        return None
    if x_field not in result_df.columns or y_field not in result_df.columns:
        return None

    chart_type = chart_spec.get("type")
    plot_df = result_df[[x_field, y_field]].head(200)
    if plot_df.empty:
        return None

    x_series = to_1d_series(plot_df, x_field)
    y_series = to_1d_series(plot_df, y_field)

    x_values = x_series.map(to_plot_label)
    y_numeric = pd.to_numeric(y_series, errors="coerce")

    chart_id = f"chart_{uuid.uuid4().hex}.png"
    chart_path = CHART_DIR / chart_id

    plt.figure(figsize=(10, 5))
    if chart_type == "line":
        if y_numeric.dropna().empty:
            plt.close()
            return None
        plt.plot(x_values, y_numeric.fillna(0), marker="o")
    elif chart_type == "bar":
        if y_numeric.dropna().empty:
            plt.close()
            return None
        plt.bar(x_values, y_numeric.fillna(0))
    elif chart_type == "scatter":
        if y_numeric.dropna().empty:
            plt.close()
            return None
        plt.scatter(x_values, y_numeric.fillna(0), alpha=0.8)
    elif chart_type == "hist":
        hist_series = y_numeric.dropna()
        if hist_series.empty:
            plt.close()
            return None
        plt.hist(hist_series, bins=20)
        plt.xlabel(str(y_field))
        plt.ylabel("Frequency")
    elif chart_type == "box":
        box_series = y_numeric.dropna()
        if box_series.empty:
            plt.close()
            return None
        plt.boxplot(box_series)
        plt.xticks([1], [str(y_field)])
    elif chart_type == "area":
        area_series = y_numeric.fillna(0)
        plt.fill_between(range(len(area_series)), area_series)
        plt.xticks(range(len(x_values)), x_values, rotation=45, ha="right")
    elif chart_type == "pie":
        pie_source = pd.DataFrame({"x": x_values, "y": y_numeric.fillna(0)})
        pie_df = pie_source.groupby("x", dropna=False)["y"].sum().head(20)
        if pie_df.empty:
            plt.close()
            return None
        plt.pie(pie_df.values, labels=pie_df.index.astype(str), autopct="%1.1f%%")
    elif chart_type == "violin":
        violin_series = y_numeric.dropna()
        if violin_series.empty:
            plt.close()
            return None
        plt.violinplot(violin_series)
        plt.xticks([1], [str(y_field)])
    elif chart_type == "heatmap":
        numeric = result_df.select_dtypes(include="number")
        if numeric.shape[1] < 2:
            plt.close()
            return None
        corr = numeric.corr().fillna(0)
        plt.imshow(corr, cmap="viridis", aspect="auto")
        plt.colorbar()
        plt.xticks(range(len(corr.columns)), corr.columns, rotation=45, ha="right")
        plt.yticks(range(len(corr.index)), corr.index)
    else:
        if y_numeric.dropna().empty:
            plt.close()
            return None
        plt.bar(x_values, y_numeric.fillna(0))

    plt.title(f"{chart_type}: {y_field} by {x_field}")
    if chart_type not in {"hist", "pie", "heatmap", "violin"}:
        plt.xlabel(str(x_field))
        plt.ylabel(str(y_field))
        plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    plt.savefig(chart_path)
    plt.close()

    return chart_id


def render_charts(result_df: pd.DataFrame, chart_type: str) -> list[dict[str, Any]]:
    specs = choose_chart_specs(result_df, chart_type)
    rendered: list[dict[str, Any]] = []
    for spec in specs:
        chart_file_name = render_chart_png(result_df, spec)
        if chart_file_name:
            item = dict(spec)
            item["downloadUrl"] = f"/charts/{chart_file_name}"
            rendered.append(item)

    if not rendered:
        return [{"type": "none", "xField": None, "yField": None}]
    return rendered


def coerce_json_safe(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe_records: list[dict[str, Any]] = []
    for row in records:
        safe_row: dict[str, Any] = {}
        for key, val in row.items():
            if isinstance(val, (pd.Timestamp, datetime)):
                safe_row[key] = val.isoformat()
            elif pd.isna(val):
                safe_row[key] = None
            else:
                safe_row[key] = val
        safe_records.append(safe_row)
    return safe_records


def build_joined_dataframe(user_id: int, relationship_id: int) -> pd.DataFrame:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM relationships WHERE id = ? AND user_id = ?",
        (relationship_id, user_id),
    ).fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail=f"Relationship {relationship_id} not found")

    left_df = load_dataset_df(user_id, row["left_dataset_id"])
    right_df = load_dataset_df(user_id, row["right_dataset_id"])

    if row["left_key"] not in left_df.columns:
        raise HTTPException(status_code=400, detail=f"leftKey {row['left_key']} not in left dataset")
    if row["right_key"] not in right_df.columns:
        raise HTTPException(status_code=400, detail=f"rightKey {row['right_key']} not in right dataset")

    return pd.merge(
        left_df,
        right_df,
        left_on=row["left_key"],
        right_on=row["right_key"],
        how=row["join_type"],
    )


def build_dataframe_from_java_request(
    req: JavaNLQueryRequest,
    question: str,
    force_all_sheets: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    all_paths = [p for p in req.filePaths if p]
    if req.filePath:
        all_paths.append(req.filePath)

    unique_paths: list[str] = []
    for path in all_paths:
        normalized = core_normalize_input_path(path)
        if normalized not in unique_paths:
            unique_paths.append(normalized)

    if not unique_paths:
        raise HTTPException(status_code=400, detail="Provide filePath or filePaths")

    for file_path in unique_paths:
        if not Path(file_path).exists():
            raise HTTPException(status_code=400, detail=f"File not found: {file_path}")

    if not req.relationships:
        selected_sources: list[dict[str, Any]] = []
        if len(unique_paths) == 1:
            selected_df, source_context = load_dataframe_from_path_with_question(
                unique_paths[0],
                question,
                requested_sheet=req.sheetName,
                force_all_sheets=force_all_sheets,
            )
            return selected_df, {
                "mode": "single_source",
                "selectedSources": [source_context],
            }

        selected_frames: list[tuple[str, pd.DataFrame]] = []
        primary_path = core_normalize_input_path(req.filePath) if req.filePath else unique_paths[0]
        for path in unique_paths:
            requested_sheet = req.sheetName if path == primary_path else None
            frame, source_context = load_dataframe_from_path_with_question(
                path,
                question,
                requested_sheet=requested_sheet,
                force_all_sheets=force_all_sheets,
            )
            selected_frames.append((path, frame))
            selected_sources.append(source_context)

        combined = _combine_frames_with_file_origin(selected_frames)
        return combined, {
            "mode": "multi_source",
            "sourceCount": len(selected_frames),
            "selectedSources": selected_sources,
        }

    frame_cache: dict[tuple[str, str], tuple[pd.DataFrame, dict[str, Any]]] = {}
    selected_sources: list[dict[str, Any]] = []
    primary_path = core_normalize_input_path(req.filePath) if req.filePath else unique_paths[0]

    def get_frame(path: str, preferred_key: Optional[str]) -> pd.DataFrame:
        cache_key = (path, preferred_key or "")
        if cache_key not in frame_cache:
            requested_sheet = req.sheetName if path == primary_path else None
            frame_cache[cache_key] = load_dataframe_from_path_with_question(
                path,
                question,
                preferred_key=preferred_key,
                requested_sheet=requested_sheet,
                force_all_sheets=force_all_sheets,
            )
            selected_sources.append(frame_cache[cache_key][1])
        return frame_cache[cache_key][0]

    current_df: Optional[pd.DataFrame] = None
    covered_paths: set[str] = set()

    for relation in req.relationships:
        left_path = core_normalize_input_path(relation.leftPath)
        right_path = core_normalize_input_path(relation.rightPath)

        if left_path not in unique_paths or right_path not in unique_paths:
            raise HTTPException(status_code=400, detail="Relationship file paths must exist in filePaths/filePath")

        left_df = current_df if current_df is not None and left_path in covered_paths else get_frame(left_path, relation.leftKey)
        right_df = current_df if current_df is not None and right_path in covered_paths else get_frame(right_path, relation.rightKey)

        if relation.leftKey not in left_df.columns:
            raise HTTPException(status_code=400, detail=f"leftKey {relation.leftKey} not found")
        if relation.rightKey not in right_df.columns:
            raise HTTPException(status_code=400, detail=f"rightKey {relation.rightKey} not found")

        if relation.joinType not in {"inner", "left", "right", "outer"}:
            raise HTTPException(status_code=400, detail="joinType must be one of inner/left/right/outer")

        current_df = pd.merge(
            left_df,
            right_df,
            left_on=relation.leftKey,
            right_on=relation.rightKey,
            how=relation.joinType,
        )
        covered_paths.add(left_path)
        covered_paths.add(right_path)

    if current_df is not None:
        return current_df, {
            "mode": "relationship_merge",
            "relationshipsApplied": len(req.relationships),
            "selectedSources": selected_sources,
        }

    selected_df, source_context = load_dataframe_from_path_with_question(
        unique_paths[0],
        question,
        requested_sheet=req.sheetName,
        force_all_sheets=force_all_sheets,
    )
    return selected_df, {
        "mode": "single_source",
        "selectedSources": [source_context],
    }


def log_query_trace(endpoint: str, trace: dict[str, Any]) -> None:
    if not QUERY_TRACE_ENABLED:
        return
    try:
        logger.info("QUERY_TRACE %s", json.dumps({"endpoint": endpoint, **trace}, default=str))
    except Exception:
        logger.info("QUERY_TRACE endpoint=%s trace=%s", endpoint, trace)

def unresolved_question_key(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip().lower())


def shorten_text(value: str, max_len: int) -> str:
    if len(value) <= max_len:
        return value
    return value[:max_len] + f" ...[truncated {len(value) - max_len} chars]"


def log_llm_trace(endpoint: str, payload: dict[str, Any]) -> None:
    if not LLM_TRACE_ENABLED:
        return
    try:
        logger.info("LLM_TRACE %s", json.dumps({"endpoint": endpoint, **payload}, default=str))
    except Exception:
        logger.info("LLM_TRACE endpoint=%s payload=%s", endpoint, payload)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _extract_json_object(text: str) -> dict[str, Any]:
    if not text:
        return {}

    candidate = text.strip()
    if candidate.startswith("```"):
        parts = candidate.split("```")
        for block in parts:
            block = block.strip()
            if not block:
                continue
            if block.lower().startswith("json"):
                block = block[4:].strip()
            try:
                parsed = json.loads(block)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                continue

    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass

    first = candidate.find("{")
    last = candidate.rfind("}")
    if first >= 0 and last > first:
        snippet = candidate[first : last + 1]
        try:
            parsed = json.loads(snippet)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _sample_schema_for_understanding(df: pd.DataFrame, max_cols: int = 24, max_values: int = 4) -> str:
    lines: list[str] = []
    for col in list(df.columns)[:max_cols]:
        series = to_1d_series(df, str(col))
        non_null = series.dropna().astype(str)
        unique_values = list(dict.fromkeys(non_null.tolist()))[:max_values]
        sample = ", ".join(unique_values)
        lines.append(f"- {col} ({series.dtype}) sample=[{sample}]")
    return "\n".join(lines)


def _build_understanding_prompt(df: pd.DataFrame, question: str) -> str:
    schema = _sample_schema_for_understanding(df)
    return f"""
You are a multilingual query-understanding engine for analytics over uploaded tabular data.

Your job:
1. Understand the user's question.
2. Ground it to the dataset schema.
3. Return ONLY valid JSON.
4. Do not invent columns that are not present in the schema.

Supported intents:
- count
- lookup
- schema
- raw_rows
- aggregation
- group_aggregation
- comparison
- trend
- relationship
- chart
- quality
- ranking
- unknown

Rules:
- Prefer exact schema column names when possible.
- If the user wording is vague, map to the closest schema terms.
- If multiple interpretations are possible, return alternatives ranked implicitly by confidence.
- If the request seems workbook-wide, set workbookScope accordingly.
- If clarification is needed, set clarificationNeeded=true and provide a short clarificationQuestion.
- Detect likely filter operators such as =, !=, contains, startswith, endswith, between, is_null, not_null.
- Detect likely roles such as filter, group, metric, time, identifier, target.

Return JSON with exactly these keys:
{{
    "canonicalQuestion": "string",
    "alternativeInterpretations": ["string"],
    "language": "string",
    "confidence": 0.0,
    "reasoning": "short explanation",
    "intentFamily": "count|lookup|schema|raw_rows|aggregation|group_aggregation|comparison|trend|relationship|chart|quality|ranking|unknown",
    "outputMode": "scalar|rows|table|chart|table_and_chart|clarify",
    "workbookScope": "single_sheet|all_sheets|unknown",
    "columnCandidates": [
        {{
            "column": "string",
            "role": "filter|group|metric|time|identifier|target",
            "confidence": 0.0
        }}
    ],
    "filters": [
        {{
            "column": "string",
            "operator": "=|!=|>|>=|<|<=|contains|startswith|endswith|between|in|not_in|is_null|not_null",
            "value": "string|null",
            "confidence": 0.0
        }}
    ],
    "timeRange": {{
        "column": "string",
        "from": "string",
        "to": "string",
        "confidence": 0.0
    }},
    "sort": [
        {{
            "column": "string",
        "distinct": false,
            "direction": "asc|desc",
        "qualityChecks": ["duplicates|missing|nulls|outliers|invalid_format"],
            "confidence": 0.0
        }}
    ],
    "limit": 0,
    "chartPreference": "auto|bar|line|area|pie|scatter|hist|box|violin|heatmap|none",
    "clarificationNeeded": false,
    "clarificationQuestion": "string"
}}

Schema:
{schema}

User question:
{question}
""".strip()


def infer_question_understanding(df: pd.DataFrame, question: str, endpoint: str) -> dict[str, Any]:
    enabled = _env_bool("APP_LLM_QUERY_UNDERSTANDING", True)
    provider = os.getenv("LOCAL_LLM_PROVIDER", "none").strip().lower()
    if not enabled or provider in {"", "none", "off", "disabled"}:
        return {
            "enabled": False,
            "canonicalQuestion": question,
            "alternativeInterpretations": [],
            "alternatives": [],
            "confidence": 0.0,
            "reasoning": "LLM understanding disabled or unavailable.",
            "intentFamily": "unknown",
            "outputMode": "table",
            "workbookScope": "unknown",
            "columnCandidates": [],
            "filters": [],
            "timeRange": {},
            "sort": [],
            "limit": None,
            "chartPreference": "none",
            "clarificationNeeded": False,
            "clarificationQuestion": "",
        }

    understanding_model = os.getenv("LOCAL_LLM_UNDERSTAND_MODEL", "").strip() or os.getenv("LOCAL_LLM_MODEL", "gamma13b").strip()
    understanding_temp = _env_float("LOCAL_LLM_UNDERSTAND_TEMPERATURE", 0.2)
    understanding_max_tokens = _env_int("LOCAL_LLM_UNDERSTAND_MAX_TOKENS", 1400)
    understanding_timeout = _env_float("LOCAL_LLM_UNDERSTAND_TIMEOUT_SEC", 75.0)

    system_prompt = (
        "You are a multilingual query-understanding engine for analytics over tabular data. "
        "Interpret user intent robustly across abbreviations and imperfect phrasing. "
        "Output strictly valid JSON only."
    )
    prompt = _build_understanding_prompt(df, question)
    raw = call_local_llm(
        prompt,
        system_prompt=system_prompt,
        model_override=understanding_model,
        temperature=understanding_temp,
        max_tokens=understanding_max_tokens,
        timeout_sec=understanding_timeout,
    )

    parsed = _extract_json_object(raw)
    canonical = (
        str(parsed.get("canonicalQuestion") or "").strip()
        or str(parsed.get("canonical_question") or "").strip()
        or question
    )
    alternatives_raw = parsed.get("alternativeInterpretations")
    if alternatives_raw is None:
        alternatives_raw = parsed.get("alternative_interpretations")
    alternatives: list[str] = []
    if isinstance(alternatives_raw, list):
        for item in alternatives_raw:
            val = str(item or "").strip()
            if val:
                alternatives.append(val)

    confidence_raw = parsed.get("confidence", 0.0)
    try:
        confidence = max(0.0, min(1.0, float(confidence_raw)))
    except (TypeError, ValueError):
        confidence = 0.0

    reasoning = str(parsed.get("reasoning") or "").strip()
    language = str(parsed.get("language") or "").strip()
    intent_family = str(parsed.get("intentFamily") or "unknown").strip() or "unknown"
    output_mode = str(parsed.get("outputMode") or "table").strip() or "table"
    workbook_scope = str(parsed.get("workbookScope") or "unknown").strip() or "unknown"
    column_candidates = parsed.get("columnCandidates") if isinstance(parsed.get("columnCandidates"), list) else []
    filters = parsed.get("filters") if isinstance(parsed.get("filters"), list) else []
    if not filters and isinstance(parsed.get("filterCandidates"), list):
        filters = parsed.get("filterCandidates")
    time_range = parsed.get("timeRange") if isinstance(parsed.get("timeRange"), dict) else {}
    sort = parsed.get("sort") if isinstance(parsed.get("sort"), list) else []
    chart_preference = str(parsed.get("chartPreference") or "none").strip() or "none"
    quality_checks = parsed.get("qualityChecks") if isinstance(parsed.get("qualityChecks"), list) else []
    distinct = bool(parsed.get("distinct", False))
    clarification_needed = bool(parsed.get("clarificationNeeded", False))
    clarification_question = str(parsed.get("clarificationQuestion") or "").strip()

    log_llm_trace(
        endpoint,
        {
            "stage": "question_understanding",
            "question": question,
            "model": understanding_model,
            "confidence": confidence,
            "canonicalQuestion": canonical,
            "intentFamily": intent_family,
            "outputMode": output_mode,
            "workbookScope": workbook_scope,
            "columnCandidates": column_candidates,
            "filters": filters,
            "rawResponse": shorten_text(raw or "", LLM_TRACE_RESPONSE_MAX),
        },
    )

    return {
        "enabled": True,
        "canonicalQuestion": canonical,
        "alternativeInterpretations": alternatives,
        "alternatives": alternatives,
        "confidence": confidence,
        "language": language,
        "reasoning": reasoning,
        "intentFamily": intent_family,
        "outputMode": output_mode,
        "workbookScope": workbook_scope,
        "columnCandidates": column_candidates,
        "filters": filters,
        "filterCandidates": filters,
        "timeRange": time_range,
        "sort": sort,
        "limit": parsed.get("limit"),
        "distinct": distinct,
        "chartPreference": chart_preference,
        "qualityChecks": quality_checks,
        "clarificationNeeded": clarification_needed,
        "clarificationQuestion": clarification_question,
        "rawParsed": parsed,
    }


def resolve_execution_question(
    df: pd.DataFrame,
    original_question: str,
    endpoint: str,
) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    understanding = infer_question_understanding(df, original_question, endpoint)

    candidates: list[str] = []
    for candidate in [
        understanding.get("canonicalQuestion"),
        *(understanding.get("alternativeInterpretations") or understanding.get("alternatives") or []),
        original_question,
    ]:
        value = str(candidate or "").strip()
        if value and value not in candidates:
            candidates.append(value)

    if not candidates:
        candidates = [original_question]

    chosen_question = original_question
    chosen_scope = "out_of_scope"
    chosen_score = -1.0
    for candidate in candidates:
        relation = core_intents.fused_question_schema_relation(df, candidate, understanding=understanding)
        scope = core_intents.classify_question_scope_hybrid(df, candidate, understanding=understanding)
        score = float(relation.get("score") or 0.0)
        bonus = 0.0
        if candidate == understanding.get("canonicalQuestion"):
            bonus += 0.08
        if scope == "in_scope":
            bonus += 0.15
        elif scope == "clarify":
            bonus += 0.05
        final_score = score + bonus
        if final_score > chosen_score:
            chosen_score = final_score
            chosen_question = candidate
            chosen_scope = scope

    plan = core_intents.build_query_plan(df, chosen_question, llm_understanding=understanding)
    if str(plan.get("scope") or "") != "in_scope":
        chosen_scope = str(plan.get("scope") or chosen_scope)

    understanding["candidates"] = candidates
    understanding["chosenQuestion"] = chosen_question
    understanding["chosenScope"] = chosen_scope
    understanding["chosenScore"] = round(chosen_score, 3)
    return chosen_question, chosen_scope, understanding, plan


def should_render_chart_with_original_intent(
    original_question: str,
    execution_question: str,
    chart_type: str,
    result_df: pd.DataFrame,
) -> bool:
    if core_intents.should_render_chart(execution_question, chart_type, result_df):
        return True
    if str(chart_type or "").lower() == "auto" and core_intents.asks_chart_request(original_question):
        return True
    return False


def chart_type_from_plan(plan: Optional[dict[str, Any]], requested_chart_type: str) -> str:
    plan_data = plan if isinstance(plan, dict) else {}
    plan_pref = str(plan_data.get("chart_pref") or "").strip().lower()
    if plan_pref and plan_pref not in {"", "auto", "none"}:
        return plan_pref
    return requested_chart_type


def should_render_chart_from_plan_or_intent(
    plan: Optional[dict[str, Any]],
    original_question: str,
    execution_question: str,
    chart_type: str,
    result_df: pd.DataFrame,
) -> bool:
    if core_intents.should_render_chart_from_plan(plan, result_df):
        return True
    return should_render_chart_with_original_intent(original_question, execution_question, chart_type, result_df)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/preview-file")
def preview_single_file(
    filePath: str,
    limit: Optional[int] = Query(default=20, ge=1, le=200000),
    sheetName: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    normalized = core_normalize_input_path(filePath)
    if not Path(normalized).exists():
        raise HTTPException(status_code=404, detail=f"File not found: {normalized}")

    selected_sheet: Optional[str] = None
    available_sheets: list[str] = []
    if _is_excel_path(normalized):
        workbook = pd.read_excel(normalized, sheet_name=None)
        if isinstance(workbook, dict) and workbook:
            available_sheets = [str(name) for name in workbook.keys()]
            if sheetName and sheetName in workbook:
                selected_sheet = sheetName
            else:
                selected_sheet = available_sheets[0]
            df = normalize_uploaded_dataframe(workbook[selected_sheet])
        else:
            df = pd.DataFrame()
    else:
        df = load_dataframe_from_path(normalized)

    if limit is not None:
        df = df.head(limit)
    records = coerce_json_safe(df.to_dict(orient="records"))
    profile = profile_dataframe(df)

    return {
        "filePath": normalized,
        "sheetName": selected_sheet,
        "sheetNames": available_sheets,
        "rows": records,
        "columns": [str(c) for c in df.columns],
        "columnProfile": profile,
    }


@app.post("/nl-query")
def run_java_nl_query(req: JavaNLQueryRequest) -> dict[str, Any]:
    original_question = req.question.strip()
    question = original_question
    if not question:
        raise HTTPException(status_code=400, detail="question must not be empty")

    df, data_context = build_dataframe_from_java_request(req, question)
    question, scope, understanding_meta, plan = resolve_execution_question(df, question, endpoint="/nl-query")

    if (
        str(plan.get("sheet_mode") or "") == "all_sheets"
        and data_context.get("mode") == "single_source"
        and data_context.get("selectedSources")
    ):
        df, data_context = build_dataframe_from_java_request(req, question, force_all_sheets=True)
        question, scope, understanding_meta, plan = resolve_execution_question(df, question, endpoint="/nl-query")

    trace = core_intents.build_reasoning_trace_v2(df, question, llm_understanding=understanding_meta)
    trace["originalQuestion"] = original_question
    trace["executionQuestion"] = question
    trace["questionUnderstanding"] = understanding_meta
    trace["queryPlan"] = plan
    trace["dataContext"] = data_context
    context_note = summarize_data_context(data_context)

    if scope != "in_scope":
        key = unresolved_question_key(original_question)
        count = UNRESOLVED_QUESTION_COUNTS.get(key, 0) + 1
        UNRESOLVED_QUESTION_COUNTS[key] = count
        summary_text = (
            str(plan.get("clarification_question") or "").strip()
            if bool(plan.get("clarification_needed"))
            else core_intents.build_scope_guidance(
                df,
                original_question,
                escalation=count > 1,
                out_of_scope=scope == "out_of_scope",
            )
        )
        charts = [{"type": "none", "xField": None, "yField": None}]
        trace["routing"] = "scope_guard"
        trace["decisionPath"] = "scope_guard"
        trace["scope"] = scope
        trace["clarificationCount"] = count
        trace["resultRows"] = 0
        trace["chartReturned"] = False
        log_query_trace("/nl-query", trace)
        response = {
            "rows": [],
            "columns": [],
            "chart": charts[0],
            "charts": charts,
            "summary": summary_text,
            "generated_code": "clarification_needed",
            "attempts": 0,
            "dataContext": data_context,
        }
        if QUERY_TRACE_INCLUDE_IN_RESPONSE:
            response["debugTrace"] = trace
        return response

    UNRESOLVED_QUESTION_COUNTS.pop(unresolved_question_key(original_question), None)

    planner_result = core_intents.execute_query_plan(df, question, plan=plan, trace=trace)
    if planner_result is not None:
        result_df, summary_text = planner_result
        result_preview, _ = core_intents.apply_requested_row_limit(result_df, question)
        rows = coerce_json_safe(result_preview.to_dict(orient="records"))
        columns = [str(c) for c in result_preview.columns]
        resolved_chart_type = chart_type_from_plan(plan, req.chartType)
        charts = (
            core_charts.render_charts(result_preview, resolved_chart_type, CHART_DIR)
            if should_render_chart_from_plan_or_intent(plan, original_question, question, resolved_chart_type, result_preview)
            else [{"type": "none", "xField": None, "yField": None}]
        )
        trace["routing"] = "planner_rule_based"
        trace["resultRows"] = len(rows)
        trace["chartReturned"] = charts[0].get("type") != "none"
        log_query_trace("/nl-query", trace)
        response = {
            "rows": rows,
            "columns": columns,
            "chart": charts[0],
            "charts": charts,
            "summary": f"{summary_text} {context_note}".strip(),
            "generated_code": "rule_based",
            "attempts": 0,
            "dataContext": data_context,
        }
        if QUERY_TRACE_INCLUDE_IN_RESPONSE:
            response["debugTrace"] = trace
        return response

    if core_intents.should_use_rule_based(question) or str(plan.get("intent_family") or "unknown") != "unknown":
        result_df, summary_text = core_intents.default_fallback_result(df, question, trace=trace)
        result_preview, _ = core_intents.apply_requested_row_limit(result_df, question)
        rows = coerce_json_safe(result_preview.to_dict(orient="records"))
        columns = [str(c) for c in result_preview.columns]
        resolved_chart_type = chart_type_from_plan(plan, req.chartType)
        charts = (
            core_charts.render_charts(result_preview, resolved_chart_type, CHART_DIR)
            if should_render_chart_from_plan_or_intent(plan, original_question, question, resolved_chart_type, result_preview)
            else [{"type": "none", "xField": None, "yField": None}]
        )
        trace["routing"] = "rule_based"
        trace["resultRows"] = len(rows)
        trace["chartReturned"] = charts[0].get("type") != "none"
        log_query_trace("/nl-query", trace)
        response = {
            "rows": rows,
            "columns": columns,
            "chart": charts[0],
            "charts": charts,
            "summary": f"{summary_text} {context_note}".strip(),
            "generated_code": "rule_based",
            "attempts": 0,
            "dataContext": data_context,
        }
        if QUERY_TRACE_INCLUDE_IN_RESPONSE:
            response["debugTrace"] = trace
        return response

    prompt = build_query_prompt(df, question, data_context=data_context)
    raw_llm_response = call_local_llm(prompt)
    generated = extract_code(raw_llm_response)
    log_llm_trace(
        "/nl-query",
        {
            "stage": "initial_generation",
            "question": question,
            "prompt": shorten_text(prompt, LLM_TRACE_PROMPT_MAX),
            "rawResponse": shorten_text(raw_llm_response or "", LLM_TRACE_RESPONSE_MAX),
            "generatedCode": generated,
        },
    )
    if not generated:
        result_df, summary_text = core_intents.default_fallback_result(df, question, trace=trace)
        result_preview, _ = core_intents.apply_requested_row_limit(result_df, question)
        rows = coerce_json_safe(result_preview.to_dict(orient="records"))
        columns = [str(c) for c in result_preview.columns]
        resolved_chart_type = chart_type_from_plan(plan, req.chartType)
        charts = (
            core_charts.render_charts(result_preview, resolved_chart_type, CHART_DIR)
            if should_render_chart_from_plan_or_intent(plan, original_question, question, resolved_chart_type, result_preview)
            else [{"type": "none", "xField": None, "yField": None}]
        )
        trace["routing"] = "llm_empty_rule_fallback"
        trace["llmReason"] = "empty_model_response"
        trace["resultRows"] = len(rows)
        trace["chartReturned"] = charts[0].get("type") != "none"
        log_query_trace("/nl-query", trace)
        response = {
            "rows": rows,
            "columns": columns,
            "chart": charts[0],
            "charts": charts,
            "summary": f"{summary_text} {context_note}".strip(),
            "generated_code": "rule_based_fallback",
            "attempts": 0,
            "dataContext": data_context,
        }
        if QUERY_TRACE_INCLUDE_IN_RESPONSE:
            response["debugTrace"] = trace
        return response

    attempts = 1
    status = "success"

    try:
        result_df = execute_query_code(df, generated)
    except Exception as first_error:
        log_llm_trace(
            "/nl-query",
            {
                "stage": "first_execution_failed",
                "question": question,
                "generatedCode": generated,
                "error": str(first_error),
            },
        )
        retry_prompt = (
            prompt
            + "\n\n"
            + f"Previous code:\n{generated}\n"
            + f"Error:\n{first_error}\n"
            + "Please return corrected code only."
        )
        raw_retry_response = call_local_llm(retry_prompt)
        retry_code = extract_code(raw_retry_response)
        log_llm_trace(
            "/nl-query",
            {
                "stage": "retry_generation",
                "question": question,
                "retryPrompt": shorten_text(retry_prompt, LLM_TRACE_PROMPT_MAX),
                "rawRetryResponse": shorten_text(raw_retry_response or "", LLM_TRACE_RESPONSE_MAX),
                "retryCode": retry_code,
            },
        )
        attempts += 1
        if retry_code:
            generated = retry_code
            try:
                result_df = execute_query_code(df, generated)
            except Exception as second_error:
                attempts += 1
                status = "failed"
                generated = "result = df"
                result_df, fallback_summary = core_intents.default_fallback_result(df, question, trace=trace)
                log_llm_trace(
                    "/nl-query",
                    {
                        "stage": "retry_execution_failed",
                        "question": question,
                        "retryCode": retry_code,
                        "error": str(second_error),
                        "fallbackSummary": fallback_summary,
                    },
                )
        else:
            status = "failed"
            generated = "result = df"
            result_df, fallback_summary = core_intents.default_fallback_result(df, question, trace=trace)
            log_llm_trace(
                "/nl-query",
                {
                    "stage": "retry_code_empty",
                    "question": question,
                    "fallbackSummary": fallback_summary,
                },
            )

    result_preview, _ = core_intents.apply_requested_row_limit(result_df, question)
    rows = coerce_json_safe(result_preview.to_dict(orient="records"))
    columns = [str(c) for c in result_preview.columns]

    resolved_chart_type = chart_type_from_plan(plan, req.chartType)
    charts = (
        core_charts.render_charts(result_preview, resolved_chart_type, CHART_DIR)
        if should_render_chart_from_plan_or_intent(plan, original_question, question, resolved_chart_type, result_preview)
        else [{"type": "none", "xField": None, "yField": None}]
    )
    chart_spec = charts[0]

    summary = f"Returned {len(rows)} rows from {len(df)} input rows ({status})."
    if status == "failed" and 'fallback_summary' in locals():
        summary = f"{summary} {fallback_summary}"
    if context_note:
        summary = f"{summary} {context_note}".strip()

    trace["routing"] = "llm"
    trace["attempts"] = attempts
    trace["status"] = status
    trace["generatedCode"] = generated
    trace["resultRows"] = len(rows)
    trace["chartReturned"] = chart_spec.get("type") != "none"
    log_query_trace("/nl-query", trace)

    response = {
        "rows": rows,
        "columns": columns,
        "chart": chart_spec,
        "charts": charts,
        "summary": summary,
        "generated_code": generated,
        "attempts": attempts,
        "dataContext": data_context,
    }
    if QUERY_TRACE_INCLUDE_IN_RESPONSE:
        response["debugTrace"] = trace
    return response


@app.post("/auth/register", response_model=AuthResponse)
def register(req: RegisterRequest) -> AuthResponse:
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (req.username, hash_password(req.password), utc_now()),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.close()
        raise HTTPException(status_code=409, detail="Username already exists") from exc

    user_id = cur.lastrowid
    conn.close()

    token = secrets.token_urlsafe(32)
    TOKEN_STORE[token] = int(user_id)
    return AuthResponse(token=token)


@app.post("/auth/login", response_model=AuthResponse)
def login(req: LoginRequest) -> AuthResponse:
    conn = get_db()
    row = conn.execute(
        "SELECT id, password_hash FROM users WHERE username = ?",
        (req.username,),
    ).fetchone()
    conn.close()

    if not row or row["password_hash"] != hash_password(req.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = secrets.token_urlsafe(32)
    TOKEN_STORE[token] = int(row["id"])
    return AuthResponse(token=token)


@app.post("/datasets/upload")
async def upload_datasets(
    files: list[UploadFile] = File(...),
    parseAllSheets: bool = Query(default=True),
    user_id: int = Depends(get_current_user_id),
) -> dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    conn = get_db()
    cur = conn.cursor()
    created: list[dict[str, Any]] = []

    for file in files:
        extension = Path(file.filename or "").suffix.lower()
        if extension not in {".csv", ".xlsx", ".xls"}:
            raise HTTPException(status_code=400, detail=f"Unsupported file format: {file.filename}")

        file_id = f"{uuid.uuid4().hex}_{Path(file.filename or 'upload').name}"
        disk_path = UPLOAD_DIR / file_id
        content = await file.read()
        disk_path.write_bytes(content)

        if extension == ".csv":
            df = read_csv_robust(str(disk_path))
            profile = profile_dataframe(df)
            cur.execute(
                """
                INSERT INTO datasets (user_id, name, file_path, sheet_name, source_type, column_profile_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    Path(file.filename or "dataset").stem,
                    str(disk_path),
                    None,
                    "csv",
                    json.dumps(profile),
                    utc_now(),
                ),
            )
            created.append(
                {
                    "datasetId": cur.lastrowid,
                    "name": Path(file.filename or "dataset").stem,
                    "sheetName": None,
                    "columns": profile,
                    "rowCount": int(len(df)),
                    "columnCount": int(len(df.columns)),
                }
            )
        else:
            sheets = pd.read_excel(disk_path, sheet_name=None)
            selected_sheet_names = list(sheets.keys()) if parseAllSheets else [next(iter(sheets.keys()))]
            for sheet_name in selected_sheet_names:
                df = normalize_uploaded_dataframe(sheets[sheet_name])
                profile = profile_dataframe(df)
                cur.execute(
                    """
                    INSERT INTO datasets (user_id, name, file_path, sheet_name, source_type, column_profile_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        Path(file.filename or "dataset").stem,
                        str(disk_path),
                        sheet_name,
                        "excel",
                        json.dumps(profile),
                        utc_now(),
                    ),
                )
                created.append(
                    {
                        "datasetId": cur.lastrowid,
                        "name": Path(file.filename or "dataset").stem,
                        "sheetName": sheet_name,
                        "columns": profile,
                        "rowCount": int(len(df)),
                        "columnCount": int(len(df.columns)),
                    }
                )

    conn.commit()
    conn.close()

    return {"datasets": created, "count": len(created)}


@app.get("/datasets")
def list_datasets(user_id: int = Depends(get_current_user_id)) -> dict[str, Any]:
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, sheet_name, source_type, column_profile_json, created_at FROM datasets WHERE user_id = ? ORDER BY id DESC",
        (user_id,),
    ).fetchall()
    conn.close()

    datasets = []
    for row in rows:
        datasets.append(
            {
                "datasetId": row["id"],
                "name": row["name"],
                "sheetName": row["sheet_name"],
                "sourceType": row["source_type"],
                "columnProfile": json.loads(row["column_profile_json"]),
                "createdAt": row["created_at"],
            }
        )

    return {"datasets": datasets}


@app.get("/datasets/{dataset_id}/preview", response_model=DatasetPreviewResponse)
def preview_dataset(
    dataset_id: int,
    limit: Optional[int] = Query(default=20, ge=1, le=200000),
    user_id: int = Depends(get_current_user_id),
) -> DatasetPreviewResponse:
    row = get_dataset_row(user_id, dataset_id)
    df = load_dataframe(row["file_path"], row["sheet_name"])
    if limit is not None:
        df = df.head(limit)

    records = coerce_json_safe(df.to_dict(orient="records"))
    return DatasetPreviewResponse(
        datasetId=row["id"],
        name=row["name"],
        sourcePath=row["file_path"],
        sheetName=row["sheet_name"],
        rows=records,
        columns=[str(c) for c in df.columns],
        columnProfile=json.loads(row["column_profile_json"]),
    )


@app.post("/relationships")
def create_relationship(
    req: RelationshipRequest,
    user_id: int = Depends(get_current_user_id),
) -> dict[str, Any]:
    if req.joinType not in {"inner", "left", "right", "outer"}:
        raise HTTPException(status_code=400, detail="joinType must be one of inner/left/right/outer")

    left_row = get_dataset_row(user_id, req.leftDatasetId)
    right_row = get_dataset_row(user_id, req.rightDatasetId)

    left_profile = json.loads(left_row["column_profile_json"])
    right_profile = json.loads(right_row["column_profile_json"])

    if req.leftKey not in left_profile:
        raise HTTPException(status_code=400, detail=f"leftKey {req.leftKey} not found in left dataset")
    if req.rightKey not in right_profile:
        raise HTTPException(status_code=400, detail=f"rightKey {req.rightKey} not found in right dataset")

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO relationships (user_id, left_dataset_id, right_dataset_id, left_key, right_key, join_type, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            req.leftDatasetId,
            req.rightDatasetId,
            req.leftKey,
            req.rightKey,
            req.joinType,
            utc_now(),
        ),
    )
    conn.commit()
    relationship_id = cur.lastrowid
    conn.close()

    return {
        "relationshipId": relationship_id,
        "leftDatasetId": req.leftDatasetId,
        "rightDatasetId": req.rightDatasetId,
        "leftKey": req.leftKey,
        "rightKey": req.rightKey,
        "joinType": req.joinType,
    }


@app.get("/relationships")
def list_relationships(user_id: int = Depends(get_current_user_id)) -> dict[str, Any]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM relationships WHERE user_id = ? ORDER BY id DESC",
        (user_id,),
    ).fetchall()
    conn.close()

    return {
        "relationships": [
            {
                "relationshipId": r["id"],
                "leftDatasetId": r["left_dataset_id"],
                "rightDatasetId": r["right_dataset_id"],
                "leftKey": r["left_key"],
                "rightKey": r["right_key"],
                "joinType": r["join_type"],
                "createdAt": r["created_at"],
            }
            for r in rows
        ]
    }


@app.post("/query", response_model=NLQueryResponse)
def run_nl_query(
    req: NLQueryRequest,
    user_id: int = Depends(get_current_user_id),
) -> NLQueryResponse:
    original_question = req.question.strip()
    question = original_question
    if not question:
        raise HTTPException(status_code=400, detail="question must not be empty")

    if not req.datasetId and not req.relationshipId:
        raise HTTPException(status_code=400, detail="Provide datasetId or relationshipId")

    prompt_context: dict[str, Any] = {}
    if req.relationshipId:
        df = build_joined_dataframe(user_id, req.relationshipId)
        prompt_context = {
            "mode": "relationship_merge",
            "selectedSources": [{"mode": "relationship", "filePath": f"relationship:{req.relationshipId}"}],
        }
    else:
        dataset_row = get_dataset_row(user_id, int(req.datasetId))
        df = load_dataframe(dataset_row["file_path"], dataset_row["sheet_name"])
        prompt_context = {
            "mode": "single_source",
            "selectedSources": [
                {
                    "sourceType": dataset_row["source_type"],
                    "filePath": dataset_row["file_path"],
                    "mode": "selected_sheet" if dataset_row["sheet_name"] else "single_file",
                    "selectedSheet": dataset_row["sheet_name"],
                }
            ],
        }

    question, scope, understanding_meta, plan = resolve_execution_question(df, question, endpoint="/query")
    trace = core_intents.build_reasoning_trace_v2(df, question, llm_understanding=understanding_meta)
    trace["originalQuestion"] = original_question
    trace["executionQuestion"] = question
    trace["questionUnderstanding"] = understanding_meta
    trace["queryPlan"] = plan

    if scope != "in_scope":
        key = unresolved_question_key(original_question)
        count = UNRESOLVED_QUESTION_COUNTS.get(key, 0) + 1
        UNRESOLVED_QUESTION_COUNTS[key] = count
        summary_text = (
            str(plan.get("clarification_question") or "").strip()
            if bool(plan.get("clarification_needed"))
            else core_intents.build_scope_guidance(
                df,
                original_question,
                escalation=count > 1,
                out_of_scope=scope == "out_of_scope",
            )
        )
        charts = [{"type": "none", "xField": None, "yField": None}]

        conn = get_db()
        conn.execute(
            """
            INSERT INTO query_history (
                user_id, dataset_id, relationship_id, question, generated_code,
                status, error, result_preview_json, chart_file_name, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                req.datasetId,
                req.relationshipId,
                original_question,
                "clarification_needed",
                "success",
                None,
                json.dumps([]),
                None,
                utc_now(),
            ),
        )
        conn.commit()
        conn.close()

        trace["routing"] = "scope_guard"
        trace["decisionPath"] = "scope_guard"
        trace["scope"] = scope
        trace["clarificationCount"] = count
        trace["resultRows"] = 0
        trace["chartReturned"] = False
        log_query_trace("/query", trace)

        return NLQueryResponse(
            rows=[],
            columns=[],
            chart=charts[0],
            charts=charts,
            summary=summary_text,
            generated_code="clarification_needed",
            attempts=0,
            debugTrace=trace if QUERY_TRACE_INCLUDE_IN_RESPONSE else None,
        )

    UNRESOLVED_QUESTION_COUNTS.pop(unresolved_question_key(original_question), None)

    planner_result = core_intents.execute_query_plan(df, question, plan=plan, trace=trace)
    if planner_result is not None:
        result_df, summary_text = planner_result
        result_preview, _ = core_intents.apply_requested_row_limit(result_df, question)
        rows = coerce_json_safe(result_preview.to_dict(orient="records"))
        columns = [str(c) for c in result_preview.columns]
        resolved_chart_type = chart_type_from_plan(plan, req.chartType)
        charts = (
            core_charts.render_charts(result_preview, resolved_chart_type, CHART_DIR)
            if should_render_chart_from_plan_or_intent(plan, original_question, question, resolved_chart_type, result_preview)
            else [{"type": "none", "xField": None, "yField": None}]
        )
        chart_spec = charts[0]
        chart_file_name = None
        if chart_spec.get("downloadUrl"):
            chart_file_name = str(chart_spec["downloadUrl"]).split("/")[-1]

        conn = get_db()
        conn.execute(
            """
            INSERT INTO query_history (
                user_id, dataset_id, relationship_id, question, generated_code,
                status, error, result_preview_json, chart_file_name, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                req.datasetId,
                req.relationshipId,
                original_question,
                "rule_based",
                "success",
                None,
                json.dumps(rows[:30]),
                chart_file_name,
                utc_now(),
            ),
        )
        conn.commit()
        conn.close()

        trace["routing"] = "planner_rule_based"
        trace["resultRows"] = len(rows)
        trace["chartReturned"] = chart_spec.get("type") != "none"
        log_query_trace("/query", trace)

        return NLQueryResponse(
            rows=rows,
            columns=columns,
            chart=chart_spec,
            charts=charts,
            summary=summary_text,
            generated_code="rule_based",
            attempts=0,
            debugTrace=trace if QUERY_TRACE_INCLUDE_IN_RESPONSE else None,
        )

    if core_intents.should_use_rule_based(question) or str(plan.get("intent_family") or "unknown") != "unknown":
        result_df, summary_text = core_intents.default_fallback_result(df, question, trace=trace)
        result_preview, _ = core_intents.apply_requested_row_limit(result_df, question)
        rows = coerce_json_safe(result_preview.to_dict(orient="records"))
        columns = [str(c) for c in result_preview.columns]
        resolved_chart_type = chart_type_from_plan(plan, req.chartType)
        charts = (
            core_charts.render_charts(result_preview, resolved_chart_type, CHART_DIR)
            if should_render_chart_from_plan_or_intent(plan, original_question, question, resolved_chart_type, result_preview)
            else [{"type": "none", "xField": None, "yField": None}]
        )
        chart_spec = charts[0]
        chart_file_name = None
        if chart_spec.get("downloadUrl"):
            chart_file_name = str(chart_spec["downloadUrl"]).split("/")[-1]

        conn = get_db()
        conn.execute(
            """
            INSERT INTO query_history (
                user_id, dataset_id, relationship_id, question, generated_code,
                status, error, result_preview_json, chart_file_name, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                req.datasetId,
                req.relationshipId,
                original_question,
                "rule_based",
                "success",
                None,
                json.dumps(rows[:30]),
                chart_file_name,
                utc_now(),
            ),
        )
        conn.commit()
        conn.close()

        trace["routing"] = "rule_based"
        trace["resultRows"] = len(rows)
        trace["chartReturned"] = chart_spec.get("type") != "none"
        log_query_trace("/query", trace)

        return NLQueryResponse(
            rows=rows,
            columns=columns,
            chart=chart_spec,
            charts=charts,
            summary=summary_text,
            generated_code="rule_based",
            attempts=0,
            debugTrace=trace if QUERY_TRACE_INCLUDE_IN_RESPONSE else None,
        )

    prompt = build_query_prompt(df, question, data_context=prompt_context)
    raw_llm_response = call_local_llm(prompt)
    generated = extract_code(raw_llm_response)
    log_llm_trace(
        "/query",
        {
            "stage": "initial_generation",
            "question": question,
            "prompt": shorten_text(prompt, LLM_TRACE_PROMPT_MAX),
            "rawResponse": shorten_text(raw_llm_response or "", LLM_TRACE_RESPONSE_MAX),
            "generatedCode": generated,
        },
    )
    if not generated:
        result_df, summary_text = core_intents.default_fallback_result(df, question, trace=trace)
        result_preview, _ = core_intents.apply_requested_row_limit(result_df, question)
        rows = coerce_json_safe(result_preview.to_dict(orient="records"))
        columns = [str(c) for c in result_preview.columns]
        resolved_chart_type = chart_type_from_plan(plan, req.chartType)
        charts = (
            core_charts.render_charts(result_preview, resolved_chart_type, CHART_DIR)
            if should_render_chart_from_plan_or_intent(plan, original_question, question, resolved_chart_type, result_preview)
            else [{"type": "none", "xField": None, "yField": None}]
        )
        chart_spec = charts[0]
        chart_file_name = None
        if chart_spec.get("downloadUrl"):
            chart_file_name = str(chart_spec["downloadUrl"]).split("/")[-1]

        conn = get_db()
        conn.execute(
            """
            INSERT INTO query_history (
                user_id, dataset_id, relationship_id, question, generated_code,
                status, error, result_preview_json, chart_file_name, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                req.datasetId,
                req.relationshipId,
                original_question,
                "rule_based_fallback",
                "success",
                None,
                json.dumps(rows[:30]),
                chart_file_name,
                utc_now(),
            ),
        )
        conn.commit()
        conn.close()

        trace["routing"] = "llm_empty_rule_fallback"
        trace["llmReason"] = "empty_model_response"
        trace["resultRows"] = len(rows)
        trace["chartReturned"] = chart_spec.get("type") != "none"
        log_query_trace("/query", trace)

        return NLQueryResponse(
            rows=rows,
            columns=columns,
            chart=chart_spec,
            charts=charts,
            summary=summary_text,
            generated_code="rule_based_fallback",
            attempts=0,
            debugTrace=trace if QUERY_TRACE_INCLUDE_IN_RESPONSE else None,
        )

    attempts = 1
    status = "success"
    error_text = None

    try:
        result_df = execute_query_code(df, generated)
    except Exception as first_error:
        log_llm_trace(
            "/query",
            {
                "stage": "first_execution_failed",
                "question": question,
                "generatedCode": generated,
                "error": str(first_error),
            },
        )
        retry_prompt = (
            prompt
            + "\n\n"
            + f"Previous code:\n{generated}\n"
            + f"Error:\n{first_error}\n"
            + "Please return corrected code only."
        )
        raw_retry_response = call_local_llm(retry_prompt)
        retry_code = extract_code(raw_retry_response)
        log_llm_trace(
            "/query",
            {
                "stage": "retry_generation",
                "question": question,
                "retryPrompt": shorten_text(retry_prompt, LLM_TRACE_PROMPT_MAX),
                "rawRetryResponse": shorten_text(raw_retry_response or "", LLM_TRACE_RESPONSE_MAX),
                "retryCode": retry_code,
            },
        )
        if retry_code:
            attempts += 1
            generated = retry_code
            try:
                result_df = execute_query_code(df, generated)
            except Exception as second_error:
                attempts += 1
                status = "failed"
                error_text = str(second_error)
                generated = "result = df"
                result_df, fallback_summary = core_intents.default_fallback_result(df, question, trace=trace)
                log_llm_trace(
                    "/query",
                    {
                        "stage": "retry_execution_failed",
                        "question": question,
                        "retryCode": retry_code,
                        "error": str(second_error),
                        "fallbackSummary": fallback_summary,
                    },
                )
        else:
            attempts += 1
            status = "failed"
            error_text = str(first_error)
            generated = "result = df"
            result_df, fallback_summary = core_intents.default_fallback_result(df, question, trace=trace)
            log_llm_trace(
                "/query",
                {
                    "stage": "retry_code_empty",
                    "question": question,
                    "fallbackSummary": fallback_summary,
                },
            )

    result_preview, _ = core_intents.apply_requested_row_limit(result_df, question)
    rows = coerce_json_safe(result_preview.to_dict(orient="records"))
    columns = [str(c) for c in result_preview.columns]

    resolved_chart_type = chart_type_from_plan(plan, req.chartType)
    charts = (
        core_charts.render_charts(result_preview, resolved_chart_type, CHART_DIR)
        if should_render_chart_from_plan_or_intent(plan, original_question, question, resolved_chart_type, result_preview)
        else [{"type": "none", "xField": None, "yField": None}]
    )
    chart_spec = charts[0]
    chart_file_name = None
    if chart_spec.get("downloadUrl"):
        chart_file_name = str(chart_spec["downloadUrl"]).split("/")[-1]

    summary = f"Returned {len(rows)} rows using {'relationship' if req.relationshipId else 'dataset'} context."
    if status == "failed" and 'fallback_summary' in locals():
        summary = f"{summary} {fallback_summary}"

    conn = get_db()
    conn.execute(
        """
        INSERT INTO query_history (
            user_id, dataset_id, relationship_id, question, generated_code,
            status, error, result_preview_json, chart_file_name, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            req.datasetId,
            req.relationshipId,
            original_question,
            generated,
            status,
            error_text,
            json.dumps(rows[:30]),
            chart_file_name,
            utc_now(),
        ),
    )
    conn.commit()
    conn.close()

    trace["routing"] = "llm"
    trace["attempts"] = attempts
    trace["status"] = status
    trace["generatedCode"] = generated
    trace["resultRows"] = len(rows)
    trace["chartReturned"] = chart_spec.get("type") != "none"
    log_query_trace("/query", trace)

    return NLQueryResponse(
        rows=rows,
        columns=columns,
        chart=chart_spec,
        charts=charts,
        summary=summary,
        generated_code=generated,
        attempts=attempts,
        debugTrace=trace if QUERY_TRACE_INCLUDE_IN_RESPONSE else None,
    )


@app.get("/charts/{chart_file_name}")
def download_chart(chart_file_name: str) -> FileResponse:
    chart_path = CHART_DIR / chart_file_name
    if not chart_path.exists() or not chart_path.is_file():
        raise HTTPException(status_code=404, detail="Chart not found")
    return FileResponse(path=chart_path, media_type="image/png", filename=chart_file_name)


@app.get("/history")
def get_history(
    limit: int = Query(default=50, ge=1, le=200),
    user_id: int = Depends(get_current_user_id),
) -> dict[str, Any]:
    conn = get_db()
    rows = conn.execute(
        "SELECT id, dataset_id, relationship_id, question, status, error, chart_file_name, created_at FROM query_history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()

    return {
        "history": [
            {
                "queryId": r["id"],
                "datasetId": r["dataset_id"],
                "relationshipId": r["relationship_id"],
                "question": r["question"],
                "status": r["status"],
                "error": r["error"],
                "chartDownloadUrl": f"/charts/{r['chart_file_name']}" if r["chart_file_name"] else None,
                "createdAt": r["created_at"],
            }
            for r in rows
        ]
    }


@app.post("/dashboards")
def create_dashboard(
    req: DashboardRequest,
    user_id: int = Depends(get_current_user_id),
) -> dict[str, Any]:
    now = utc_now()
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dashboards (user_id, name, config_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, req.name.strip(), json.dumps(req.config), now, now),
    )
    conn.commit()
    dashboard_id = cur.lastrowid
    conn.close()

    return {
        "dashboardId": dashboard_id,
        "name": req.name,
        "config": req.config,
        "createdAt": now,
        "updatedAt": now,
    }


@app.get("/dashboards")
def list_dashboards(user_id: int = Depends(get_current_user_id)) -> dict[str, Any]:
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, config_json, created_at, updated_at FROM dashboards WHERE user_id = ? ORDER BY id DESC",
        (user_id,),
    ).fetchall()
    conn.close()

    return {
        "dashboards": [
            {
                "dashboardId": row["id"],
                "name": row["name"],
                "config": json.loads(row["config_json"]),
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]
    }


@app.get("/dashboards/{dashboard_id}")
def get_dashboard(dashboard_id: int, user_id: int = Depends(get_current_user_id)) -> dict[str, Any]:
    conn = get_db()
    row = conn.execute(
        "SELECT id, name, config_json, created_at, updated_at FROM dashboards WHERE id = ? AND user_id = ?",
        (dashboard_id, user_id),
    ).fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    return {
        "dashboardId": row["id"],
        "name": row["name"],
        "config": json.loads(row["config_json"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }