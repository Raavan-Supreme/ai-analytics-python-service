# Python NL Query Service

AI-powered single-user analytics backend:

- Upload CSV/Excel (single or multiple files, all sheets)
- Automatic sheet selection for multi-sheet Excel workbooks based on question-to-schema relevance
- Auto column type detection (numeric/date/text)
- Natural-language query to pandas code via local LLM
- Query validation + sandboxed execution + retry loop
- Table output + auto chart generation (bar/line)
- Chart PNG download endpoint
- Basic data relationships (joins)
- Login, query history, and dashboard storage

## Setup

```bash
cd python-service
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# LM Studio example
# export LOCAL_LLM_PROVIDER=lmstudio
# export LOCAL_LLM_BASE_URL=http://localhost:1234
# export LOCAL_LLM_MODEL=your-model-name

# Ollama example
# export LOCAL_LLM_PROVIDER=ollama
# export LOCAL_LLM_BASE_URL=http://localhost:11434
# export LOCAL_LLM_MODEL=gamma13b

uvicorn app.main:app --reload --port 8000
```

## Free Local GPT Model Integration

This service now loads environment variables from a local `.env` file automatically.

1. Copy `.env.example` to `.env`.
2. Download a free local model.
3. Start your local model runtime.
4. Start the Python service.

### Option A: Ollama (recommended)

If `ollama` is not installed, install it first (Linux example):

```bash
sudo snap install ollama
```

```bash
cd python-service
cp .env.example .env

# Download a free model (pick one that exists in your Ollama registry)
ollama pull gamma13b

# Start runtime (if not already running)
ollama serve

# Start API service
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

If `gamma13b` is not available in your environment, update `LOCAL_LLM_MODEL` in `.env` to another local model that exists in your runtime.

### Option B: LM Studio / OpenAI-compatible local server

Set these values in `.env`:

```bash
LOCAL_LLM_PROVIDER=lmstudio
LOCAL_LLM_BASE_URL=http://localhost:1234
LOCAL_LLM_MODEL=<your-downloaded-model-name>
```

Behavior notes:

- If the local model server is unavailable, the service falls back to rule-based query handling.
- LLM-generated code is still validated and sandboxed before execution.

## Stronger LLM Question Understanding

The service now includes a semantic "query understanding" stage before execution:

- Rewrites ambiguous user questions to a canonical dataset-grounded question.
- Generates alternative interpretations and automatically picks the first in-scope one.
- Works before both rule-based and code-generation paths.

Recommended `.env` settings for stronger understanding:

```bash
# Enable semantic understanding stage
APP_LLM_QUERY_UNDERSTANDING=true

# Use a higher-capacity local model if available
LOCAL_LLM_UNDERSTAND_MODEL=gamma13b

# Understanding generation controls
LOCAL_LLM_UNDERSTAND_TEMPERATURE=0.2
LOCAL_LLM_UNDERSTAND_MAX_TOKENS=1400
LOCAL_LLM_UNDERSTAND_TIMEOUT_SEC=75

# Code generation controls
LOCAL_LLM_TEMPERATURE=0.1
LOCAL_LLM_MAX_TOKENS=2000
LOCAL_LLM_TIMEOUT_SEC=60
```

If you have a larger local model available, set both `LOCAL_LLM_MODEL` and `LOCAL_LLM_UNDERSTAND_MODEL` to that model.

## Verify LLM Is Actually Answering

Run a single command to verify all three layers:

- Ollama runtime is reachable and lists models.
- Direct model chat returns real content.
- App understanding path (`infer_question_understanding`) returns parsed LLM output.

```bash
cd python-service
source .venv/bin/activate
python validation/verify_llm_runtime.py
```

Expected output ends with:

- `PASS` when model answers both direct and app paths.
- `FAIL` when model is installed but cannot run (for example, low RAM).

## Multi-Sheet Workbook Behavior

When an uploaded Excel file contains multiple sheets, the Java query path now handles this automatically:

- If your question names a sheet or maps strongly to a specific sheet schema, that sheet is selected.
- If your question asks for broad workbook-wide results (for example count/all style requests) and sheet confidence is low, sheets are combined with origin columns (`__sheet_name`, `__source_file`).
- Relationship queries also attempt key-aware sheet selection so joins can resolve against the right subsheet.

## API Overview

- `POST /auth/register`
- `POST /auth/login`
- `POST /datasets/upload`
- `GET /datasets`
- `GET /datasets/{dataset_id}/preview`
- `POST /relationships`
- `GET /relationships`
- `POST /query`
- `GET /charts/{chart_file_name}`
- `GET /history`
- `POST /dashboards`
- `GET /dashboards`
- `GET /dashboards/{dashboard_id}`

All non-auth endpoints require `Authorization: Bearer <token>`.
# ai-analytics-python-service
