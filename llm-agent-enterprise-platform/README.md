# Enterprise Autonomous LLM Agent Platform

A production-grade, full-stack autonomous AI Agent and RAG platform with a resilient 6-Database Engine Architecture, ReAct Agent Execution Loop, Next.js 15 UI, SSE streaming, sandboxed code execution, and telemetry analytics.

---

## 🏛️ Multi-Database Architecture

| Database Engine | Technology | Purpose & Responsibility |
| :--- | :--- | :--- |
| **Relational** | PostgreSQL (Async SQLAlchemy 2.0 + Alembic) | Users, authentication, billing tiers, threads, and message history |
| **Vector Engine** | Qdrant | Semantic memory, embeddings, user document search (Cosine HNSW) |
| **Cache & Broker** | Redis | Session state, sliding context window buffer, Celery task queue |
| **NoSQL Documents** | MongoDB (Motor / Beanie) | Agent execution traces, tool logs, dynamic prompt templates |
| **Object Store** | MinIO (S3-Compatible) | Folder uploads, raw document storage, generated zip exports |
| **Time-Series** | ClickHouse | Token consumption, cost estimation, TTFT, and agent execution latency |

---

## 🚀 Quickstart & Deployment

### 1. Configure Environment
```bash
cp .env.example .env
```

### 2. Launch Entire Infrastructure with Docker Compose
```bash
docker compose up --build -d
```

### 3. Run Database Migrations
```bash
docker compose exec backend alembic upgrade head
```

### 4. Access Interfaces
- **Web Frontend**: [http://localhost:3000](http://localhost:3000) (or via Nginx on [http://localhost](http://localhost))
- **FastAPI OpenAPI Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **MinIO S3 Console**: [http://localhost:9001](http://localhost:9001) (`minio_admin` / `minio_secure_password`)
- **Qdrant Vector Dashboard**: [http://localhost:6333/dashboard](http://localhost:6333/dashboard)
