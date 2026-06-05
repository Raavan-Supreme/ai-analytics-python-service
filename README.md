# Python NL Query Service

AI-powered single-user analytics backend:

- Upload CSV/Excel (single or multiple files, all sheets)
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
# export LOCAL_LLM_MODEL=llama3

uvicorn app.main:app --reload --port 8000
```

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
