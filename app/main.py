import ast
import hashlib
import json
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx
import matplotlib
import pandas as pd
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
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
CHART_DIR = DATA_DIR / "charts"
DB_PATH = DATA_DIR / "app.db"

SUPPORTED_CHART_TYPES = ["line", "bar", "scatter", "hist", "box", "area", "pie", "violin", "heatmap"]

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
CHART_DIR.mkdir(parents=True, exist_ok=True)

TOKEN_STORE: dict[str, int] = {}


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
        return pd.read_csv(file_path)
    except Exception:
        return pd.read_csv(file_path, engine="python", on_bad_lines="skip", encoding_errors="ignore")


def load_dataframe(file_path: str, sheet_name: Optional[str]) -> pd.DataFrame:
    if file_path.lower().endswith(".csv"):
        return read_csv_robust(file_path)
    return pd.read_excel(file_path, sheet_name=sheet_name)


def load_dataframe_from_path(file_path: str) -> pd.DataFrame:
    lower_path = file_path.lower()
    if lower_path.endswith(".csv"):
        return read_csv_robust(file_path)
    if lower_path.endswith(".xlsx") or lower_path.endswith(".xls"):
        return pd.read_excel(file_path)
    raise HTTPException(status_code=400, detail=f"Unsupported file type: {Path(file_path).suffix}")


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


def call_local_llm(prompt: str) -> str:
    provider = os.getenv("LOCAL_LLM_PROVIDER", "none")
    base_url = os.getenv("LOCAL_LLM_BASE_URL", "")
    model = os.getenv("LOCAL_LLM_MODEL", "")

    if provider == "lmstudio":
        url = base_url.rstrip("/") + "/v1/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "Write safe pandas code only. Use df as dataframe and assign output to result.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        }
        with httpx.Client(timeout=45.0) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

    if provider == "ollama":
        url = base_url.rstrip("/") + "/api/chat"
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "Write safe pandas code only. Use df as dataframe and assign output to result.",
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }
        with httpx.Client(timeout=45.0) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return data.get("message", {}).get("content", "")

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


def build_query_prompt(df: pd.DataFrame, question: str) -> str:
    schema_lines = []
    for col, dtype in zip(df.columns, df.dtypes):
        schema_lines.append(f"- {col} ({dtype})")

    return (
        "You are given a pandas DataFrame named df.\n"
        + "Columns:\n"
        + "\n".join(schema_lines)
        + "\n"
        + f"Question: {question}\n\n"
        + "Rules:\n"
        + "1) Use the existing DataFrame variable df.\n"
        + "2) Do not import anything.\n"
        + "3) Assign final answer to variable result.\n"
        + "4) Return code only.\n"
        + "5) If user asks for all/full data, return all rows (result = df).\n"
        + "6) If user asks whether data exists, return boolean existence and matching rows.\n"
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


def build_dataframe_from_java_request(req: JavaNLQueryRequest) -> pd.DataFrame:
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
        return load_dataframe_from_path(unique_paths[0])

    frames: dict[str, pd.DataFrame] = {path: load_dataframe_from_path(path) for path in unique_paths}

    current_df: Optional[pd.DataFrame] = None
    covered_paths: set[str] = set()

    for relation in req.relationships:
        left_path = core_normalize_input_path(relation.leftPath)
        right_path = core_normalize_input_path(relation.rightPath)

        if left_path not in frames or right_path not in frames:
            raise HTTPException(status_code=400, detail="Relationship file paths must exist in filePaths/filePath")

        left_df = current_df if current_df is not None and left_path in covered_paths else frames[left_path]
        right_df = current_df if current_df is not None and right_path in covered_paths else frames[right_path]

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

    return current_df if current_df is not None else load_dataframe_from_path(unique_paths[0])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/preview-file")
def preview_single_file(filePath: str, limit: int = Query(default=20, ge=1, le=200)) -> dict[str, Any]:
    normalized = core_normalize_input_path(filePath)
    if not Path(normalized).exists():
        raise HTTPException(status_code=404, detail=f"File not found: {normalized}")

    df = load_dataframe_from_path(normalized).head(limit)
    records = coerce_json_safe(df.to_dict(orient="records"))
    profile = profile_dataframe(df)

    return {
        "filePath": normalized,
        "rows": records,
        "columns": [str(c) for c in df.columns],
        "columnProfile": profile,
    }


@app.post("/nl-query")
def run_java_nl_query(req: JavaNLQueryRequest) -> dict[str, Any]:
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question must not be empty")

    df = build_dataframe_from_java_request(req)

    if core_intents.should_use_rule_based(question):
        result_df, summary_text = core_intents.default_fallback_result(df, question)
        max_rows = len(result_df) if core_intents.wants_full_data(question) else 5000
        result_preview = result_df.head(max_rows)
        rows = coerce_json_safe(result_preview.to_dict(orient="records"))
        columns = [str(c) for c in result_preview.columns]
        charts = (
            core_charts.render_charts(result_preview, req.chartType, CHART_DIR)
            if core_intents.should_render_chart(question, req.chartType, result_preview)
            else [{"type": "none", "xField": None, "yField": None}]
        )
        return {
            "rows": rows,
            "columns": columns,
            "chart": charts[0],
            "charts": charts,
            "summary": summary_text,
            "generated_code": "rule_based",
            "attempts": 0,
        }

    prompt = build_query_prompt(df, question)
    generated = extract_code(call_local_llm(prompt))
    if not generated:
        generated = "result = df"

    attempts = 1
    status = "success"

    try:
        result_df = execute_query_code(df, generated)
    except Exception as first_error:
        retry_prompt = (
            prompt
            + "\n\n"
            + f"Previous code:\n{generated}\n"
            + f"Error:\n{first_error}\n"
            + "Please return corrected code only."
        )
        retry_code = extract_code(call_local_llm(retry_prompt))
        attempts += 1
        if retry_code:
            generated = retry_code
            try:
                result_df = execute_query_code(df, generated)
            except Exception:
                attempts += 1
                status = "failed"
                generated = "result = df"
                result_df, fallback_summary = default_fallback_result(df, question)
        else:
            status = "failed"
            generated = "result = df"
            result_df, fallback_summary = default_fallback_result(df, question)

    max_rows = len(result_df) if core_intents.wants_full_data(question) else 5000
    result_preview = result_df.head(max_rows)
    rows = coerce_json_safe(result_preview.to_dict(orient="records"))
    columns = [str(c) for c in result_preview.columns]

    charts = (
        core_charts.render_charts(result_preview, req.chartType, CHART_DIR)
        if core_intents.should_render_chart(question, req.chartType, result_preview)
        else [{"type": "none", "xField": None, "yField": None}]
    )
    chart_spec = charts[0]

    summary = f"Returned {len(rows)} rows from {len(df)} input rows ({status})."
    if status == "failed" and 'fallback_summary' in locals():
        summary = f"{summary} {fallback_summary}"

    return {
        "rows": rows,
        "columns": columns,
        "chart": chart_spec,
        "charts": charts,
        "summary": summary,
        "generated_code": generated,
        "attempts": attempts,
    }


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
                }
            )
        else:
            sheets = pd.read_excel(disk_path, sheet_name=None)
            selected_sheet_names = list(sheets.keys()) if parseAllSheets else [next(iter(sheets.keys()))]
            for sheet_name in selected_sheet_names:
                df = sheets[sheet_name]
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
    limit: int = Query(default=20, ge=1, le=200),
    user_id: int = Depends(get_current_user_id),
) -> DatasetPreviewResponse:
    row = get_dataset_row(user_id, dataset_id)
    df = load_dataframe(row["file_path"], row["sheet_name"]).head(limit)

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
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question must not be empty")

    if not req.datasetId and not req.relationshipId:
        raise HTTPException(status_code=400, detail="Provide datasetId or relationshipId")

    if req.relationshipId:
        df = build_joined_dataframe(user_id, req.relationshipId)
    else:
        df = load_dataset_df(user_id, int(req.datasetId))

    if core_intents.should_use_rule_based(question):
        result_df, summary_text = core_intents.default_fallback_result(df, question)
        max_rows = len(result_df) if core_intents.wants_full_data(question) else 5000
        result_preview = result_df.head(max_rows)
        rows = coerce_json_safe(result_preview.to_dict(orient="records"))
        columns = [str(c) for c in result_preview.columns]
        charts = (
            core_charts.render_charts(result_preview, req.chartType, CHART_DIR)
            if core_intents.should_render_chart(question, req.chartType, result_preview)
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
                question,
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

        return NLQueryResponse(
            rows=rows,
            columns=columns,
            chart=chart_spec,
            charts=charts,
            summary=summary_text,
            generated_code="rule_based",
            attempts=0,
        )

    prompt = build_query_prompt(df, question)
    generated = extract_code(call_local_llm(prompt))
    if not generated:
        generated = "result = df"

    attempts = 1
    status = "success"
    error_text = None

    try:
        result_df = execute_query_code(df, generated)
    except Exception as first_error:
        retry_prompt = (
            prompt
            + "\n\n"
            + f"Previous code:\n{generated}\n"
            + f"Error:\n{first_error}\n"
            + "Please return corrected code only."
        )
        retry_code = extract_code(call_local_llm(retry_prompt))
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
                result_df, fallback_summary = default_fallback_result(df, question)
        else:
            attempts += 1
            status = "failed"
            error_text = str(first_error)
            generated = "result = df"
            result_df, fallback_summary = default_fallback_result(df, question)

    max_rows = len(result_df) if core_intents.wants_full_data(question) else 5000
    result_preview = result_df.head(max_rows)
    rows = coerce_json_safe(result_preview.to_dict(orient="records"))
    columns = [str(c) for c in result_preview.columns]

    charts = (
        core_charts.render_charts(result_preview, req.chartType, CHART_DIR)
        if core_intents.should_render_chart(question, req.chartType, result_preview)
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
            question,
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

    return NLQueryResponse(
        rows=rows,
        columns=columns,
        chart=chart_spec,
        charts=charts,
        summary=summary,
        generated_code=generated,
        attempts=attempts,
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
