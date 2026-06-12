# AI Analytics Suite (Local LLM Excel → Insights)

This is a minimal but runnable monorepo:

- `backend-java`: Spring Boot backend (auth, file upload, query orchestration).
- `python-service`: FastAPI + Pandas NL query engine, calling your local LLM.
- `frontend`: React + Vite UI with attractive glassmorphism-style dashboard.

## Prerequisites

- Java 17+
- Maven
- Node.js 18+
- Python 3.10+
- PostgreSQL running locally with database `ai_analytics` and user/password `ai_analytics`.
- Optional local LLM runtime:
  - LM Studio (OpenAI-compatible server) or
  - Ollama (`ollama pull llama3` then run).

## Run order

1. Start PostgreSQL and ensure DB + user exist.
2. Start the Python NL query service.
3. Start the Java backend.
4. Start the React frontend.

See each subfolder README for exact commands.
