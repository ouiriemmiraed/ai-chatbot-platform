# AI Chatbot Platform

Self-hosted RAG chatbot platform. It turns the real content of a website (crawled pages and uploaded documents) into an embeddable AI assistant that answers with sources, refuses out-of-scope questions, and reports everything in an admin dashboard.

## Key features

- **RAG pipeline**: website crawling with smart re-scrape, document upload (PDF, DOCX, TXT), chunking, multilingual embeddings, vector search in ChromaDB with reranking.
- **LLM cascade**: Gemini, then OpenRouter, then a local Ollama model, then a frozen-answer cache, with circuit breakers per provider.
- **Embeddable widget**: one script tag on any website, domain allowlist per chatbot.
- **Admin dashboard**: chatbot configuration, conversations, leads, page management, analytics (conversion funnel, activity heatmap, answer quality, LLM costs), content-gap detection, user management with roles, 2FA, French and English UI.
- **Security**: JWT in HttpOnly cookies, rate limiting, SSRF-safe crawler, audit logs.

## Architecture (7 Docker services)

| Service | Role | Exposed on |
|---|---|---|
| `admin` | React admin dashboard | `http://localhost:3001` |
| `backend` | FastAPI API and widget delivery | `http://localhost:8001` |
| `celery_crawl` | Celery worker, crawl queue (scraping and ingestion) | internal |
| `celery_light` | Celery worker, light queue (classification, gaps, emails) | internal |
| `mongodb` | Main database | `127.0.0.1:27018` |
| `redis` | Cache, Celery broker, rate limiting | `127.0.0.1:6379` |
| `chromadb` | Vector store | internal |

## Project structure

- `backend/`: FastAPI service (routers, services, Celery workers).
- `admin/`: React administration dashboard.
- `widget/`: embeddable chatbot widget (React, bundled as a single IIFE file).
- `scripts/`: maintenance utilities.

## Quick start (Docker, recommended)

```bash
cp .env.example .env
# Fill in at least: SECRET_KEY, MONGO_PASSWORD, REDIS_PASSWORD,
# GEMINI_API_KEY and/or OPENROUTER_API_KEY, REACT_APP_API_URL

docker compose up -d --build
```

Then open:

- Admin dashboard: `http://localhost:3001`
- API health check: `http://localhost:8001/health`

Note: the first backend start takes about a minute because the embedding model is loaded before the API serves requests.

Application code is baked into the images. After a code change, rebuild the affected service instead of restarting it:

```bash
docker compose build backend && docker compose up -d backend
```

## Embedding the widget on a website

```html
<script src="http://YOUR_HOST:8001/widget.js" data-chatbot-id="YOUR_CHATBOT_ID" defer></script>
```

The chatbot id and the allowed domains are configured in the admin dashboard. The bundle can also be built manually:

```bash
cd widget
npm install
npm run build:widget   # outputs build/widget.bundle.js
```

The widget calls the API at `REACT_APP_API_URL`, which is frozen into the bundle at build time. Set it in `.env` before building.

## Manual development run (without Docker)

MongoDB, Redis and ChromaDB must be running and referenced in `.env`.

```bash
# Backend
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload

# Admin dashboard
cd admin
npm install
npm start   # http://localhost:3000
```

## Main environment variables

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | JWT signing key |
| `MONGO_PASSWORD`, `REDIS_PASSWORD` | Database credentials |
| `GEMINI_API_KEY`, `OPENROUTER_API_KEY` | LLM providers (at least one required) |
| `EMBEDDING_MODEL`, `HF_TOKEN` | Embedding model selection |
| `ALLOWED_ORIGINS` | CORS origins of the admin dashboard |
| `REACT_APP_API_URL` | Public API URL baked into the widget bundle |
| `SMTP_USERNAME`, `SMTP_PASSWORD`, `SENDER_EMAIL` | Service emails (password reset, 2FA); logged to console when unset |

See `.env.example` for the full list.
