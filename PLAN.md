# foji-ai-api — Plan

## Role in the Foji AI Ecosystem

`foji-ai-api` is the **AI chat service** for Foji AI. It handles all real-time conversations between end-users and agents. It connects to the shared PostgreSQL database (read-only on schema), loads agent configuration and file context, routes to one of three AI providers (randomly, Phase 1), and streams responses back to the widget via SSE.

This service **never manages schema migrations** — it reads from the database that FojiApi owns.

---

## Tech Stack

- **Python 3.12** — async-first
- **FastAPI** — ASGI framework with SSE support
- **SQLAlchemy 2.0** — ORM (read + limited writes for chat metadata)
- **boto3** — AWS S3, DynamoDB, Bedrock
- **openai** — OpenAI GPT provider
- **google-generativeai** — Gemini provider
- **uvicorn** — ASGI server

---

## Architecture

```
app/
├── api/
│   └── routes/
│       ├── chat.py          # POST /api/v1/chat (SSE streaming)
│       └── widget.py        # GET /api/v1/widget/agent-info, /files
├── providers/
│   ├── base.py              # AIProvider Protocol
│   ├── openai_provider.py   # OpenAI GPT implementation
│   ├── gemini_provider.py   # Google Gemini implementation
│   ├── bedrock_provider.py  # Amazon Nova via AWS Bedrock
│   └── router.py            # Random provider selection (Phase 1)
├── services/
│   ├── agent_service.py     # Load agent by token, verify
│   ├── file_context.py      # Load extracted_text, chunk management
│   ├── chat_history.py      # DynamoDB read/write for session history
│   └── prompt_builder.py    # Assemble system + user + context + history
├── models/                  # SQLAlchemy ORM (mirrors FojiApi schema)
│   ├── agent.py
│   ├── agent_file.py
│   ├── ai_model.py
│   └── company.py
├── core/
│   ├── config.py            # Pydantic Settings from env
│   ├── database.py          # Async SQLAlchemy engine
│   └── security.py          # Token validation
└── main.py
```

---

## Key Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/v1/chat` | Start/continue chat session (SSE streaming) |
| `GET` | `/api/v1/widget/agent-info` | Agent name, description, industry, language |
| `GET` | `/api/v1/widget/files` | List agent's ready files (for display) |
| `GET` | `/health` | Health check |

### Chat Request
```json
{
  "agent_token": "abc123...",
  "message": "What is amortization?",
  "session_id": "uuid-v4-or-null"
}
```

### Chat Response (SSE)
```
data: {"chunk": "Amortization is..."}
data: {"chunk": " the process of..."}
data: {"done": true, "session_id": "uuid", "provider": "openai"}
```

---

## Provider Layer

### Phase 1 (current): Random Selection

```python
class ProviderRouter:
    _providers = [OpenAIProvider, GeminiProvider, BedrockProvider]

    def select(self, agent: Agent) -> AIProvider:
        return random.choice(self._providers)()
```

### Phase 2 (future): Agent-level preference
- `Agent.preferred_model_id` (nullable) → maps to `AIModel` → instantiates specific provider
- No code change needed in router — just add the nullable field lookup

### AIProvider Protocol
```python
class AIProvider(Protocol):
    async def stream_chat(
        self,
        messages: list[dict],
        system_prompt: str,
        stream: bool = True
    ) -> AsyncIterator[str]: ...
```

### Supported Models (initial seeded by FojiApi)
| Provider | Model ID | Notes |
|----------|----------|-------|
| OpenAI | `gpt-4o-mini` | Cost-efficient, fast |
| Gemini | `gemini-2.0-flash` | Multimodal |
| Bedrock | `amazon.nova-lite-v1:0` | AWS-native |

---

## Chat Flow

1. Receive `agent_token` + `message` + optional `session_id`
2. `AgentService.get_by_token(token)` → load agent + verify active
3. `ChatHistoryService.load(session_id)` → DynamoDB (up to last N messages)
4. `FileContextService.build(agent_id)` → load `extracted_text` from ready files, chunk + truncate
5. `PromptBuilder.build(agent, context, history)` → full prompt payload
6. `ProviderRouter.select(agent)` → random provider
7. Stream chunks back as SSE
8. `ChatHistoryService.save(session_id, user_msg, assistant_msg, provider)`

---

## File Context

Same approach as `tutoria-api`'s `document_context_service`:
- Load `extracted_text` from `AgentFile` where `ProcessingStatus = 'ready'`
- If `len(extracted_text) > 50_000` chars → use `summarized_text` instead
- Chunk with overlap (chunk: 4000 chars, overlap: 200)
- Cap total context at 50k chars across all files

---

## Auth

- **Widget auth**: `X-Agent-Token` header — agent token from `Agent.AgentToken` column
- **Internal auth**: `X-Internal-Api-Key` header — for service-to-service calls from FojiApi

No user login required for chat — end users are anonymous.

---

## Chat History (DynamoDB)

Table: `foji-chats-dev` / `foji-chats-prod`

Schema:
```
PK: session_id (String)
SK: timestamp (Number)
Attributes: role, content, provider, agent_id
TTL: 90 days (auto-expire)
```

On-demand billing. Near-zero cost in dev.

---

## Environment Variables

```
DATABASE_URL                   # PostgreSQL (read-only user recommended)
INTERNAL_API_KEY               # Shared secret with FojiApi
OPENAI_API_KEY
GEMINI_API_KEY
AWS_REGION
AWS_BEDROCK_REGION             # us-east-1 (Nova availability)
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_DYNAMODB_TABLE             # foji-chats-dev or foji-chats-prod
AWS_S3_BUCKET
PORT                           # default 8000
```

---

## Deploy Target

**AWS ECS Fargate** (required — SSE/streaming needs persistent connections that App Runner cannot sustain)
- Dev: 0.25 vCPU / 0.5 GB, 1 task minimum (~$8/mo)
- Prod: auto-scale on CPU

**CI/CD** (GitHub Actions):
- `.github/workflows/deploy-dev.yml` — on push to `main`
- `.github/workflows/deploy-prod.yml` — `workflow_dispatch`

---

## Connections to Other Services

| Service | How |
|---------|-----|
| `FojiApi` | Shares PostgreSQL DB (read); also calls FojiApi via internal API key if needed |
| `foji-widget` | Receives chat requests, streams SSE responses |
| `foji-worker` | Worker processes files; this service reads `extracted_text` from DB |
| AWS DynamoDB | Stores chat session history |
| AWS S3 | Reads raw files if needed (fallback, when extracted_text not ready) |
| AWS Bedrock | Nova model inference |
| OpenAI API | GPT inference |
| Google Gemini API | Gemini inference |
