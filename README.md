# foji-ai-api

AI chat inference service for the Foji platform. Receives chat requests via widget or WhatsApp relay, selects an AI provider, streams responses via SSE, and stores chat history in DynamoDB.

## Tech

- Python 3.12 / FastAPI
- SQLAlchemy (async, read-only against PostgreSQL)
- OpenAI, Google Gemini, AWS Bedrock providers
- SSE streaming via sse-starlette
- AWS App Runner

## Local Development

```bash
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

Runs on `http://localhost:8000`.

## Environment

Config is loaded from AWS SSM Parameter Store by prefix (`AWS_SSM_PREFIX`). For local dev, use a `.env` file.

## Deploy

- **Dev**: Push to `main` triggers deploy via GitHub Actions to App Runner.
- **Prod**: Manual `workflow_dispatch` with confirmation.
