# ResearchFlow AI

**Autonomous AI Research Automation Platform**

Turn one research question into a fully-sourced, citation-validated,
downloadable research report — on demand or on a schedule, with Discord
notifications when each run completes.

---

## 1. Overview

ResearchFlow AI is an end-to-end local research assistant. You give it a
question; it plans the research, searches the free web, extracts and indexes
the sources into a local vector database, synthesizes a grounded answer with
inline citations that are **validated server-side**, renders Markdown + PDF
reports, and can do all of that automatically on any schedule — delivering
the research result itself (summary, key findings, statistics, validated
citations) to Discord when each report is ready.

Everything runs locally except two things: the **GLM API** (the only paid
dependency) and the free DuckDuckGo-based web search. No other API keys, no
cloud vector databases, no brokers, no SaaS.

## 2. Problem

Manually researching a topic means juggling a dozen browser tabs: running
searches, skimming pages, deciding what is relevant, keeping track of where
each claim came from, and then writing it all up. The result is slow, and
the "where did I read that?" problem makes the final write-up hard to trust
or reproduce. Generic chatbots make the trust problem worse, not better:
they answer fluently but rarely tell you which source supports which claim,
and can silently invent support.

For recurring research needs ("brief me on X every morning"), nothing
mainstream automates the *whole* loop — search → read → cite → write →
deliver — without a stack of paid SaaS tools.

## 3. Solution

ResearchFlow AI automates the loop, with citation integrity as a hard
constraint rather than a nice-to-have:

- **One question in, one report out** — the full pipeline runs end-to-end:
  plan → queries → search → extract → chunk → embed → index → retrieve →
  synthesize → validate citations → render Markdown + PDF.
- **Citations the backend can prove.** Evidence chunks are numbered
  `[E1]…[En]` before the model ever sees them; after generation, every
  citation is re-validated server-side and mapped to real source metadata.
  **LLM-generated URLs and citation metadata are never trusted** when
  backend metadata exists. Answers with unbacked citations are retried,
  then rejected as `ungrounded` — not shipped.
- **Autonomous and recurring.** Any question can become an interval or cron
  job in any IANA timezone. One run = exactly one search pass, one indexing
  pass, one synthesis, one report pair — no duplicated stages, no runaway
  loops.
- **Everything persists locally.** Schedules, execution history, and report
  metadata live in SQLite; the vector store and generated reports live on
  disk. Restart the backend — or the whole Docker stack — and nothing is
  lost.

## 4. Features

- AI-powered research plan generation (GLM)
- Automated web research via free DuckDuckGo-based search (no API key)
  with cross-query deduplication and result caps
- Source content extraction: bounded, SSRF-guarded fetching + text cleaning
- Deterministic chunking (1,000 chars, 150 overlap, word-boundary safe)
- Local embeddings via Sentence Transformers (`all-MiniLM-L6-v2`)
- Persistent local ChromaDB vector store with idempotent upserts
  (re-indexing replaces, never duplicates)
- Semantic retrieval with source metadata attached to every chunk
- Grounded GLM synthesis with inline `[E1]…[En]` citations
- Server-side citation validation with deterministic source mapping
- Prompt-injection defense: retrieved web content is treated strictly as
  data, never as instructions
- Markdown + PDF report generation, fully offline (ReportLab)
- Scheduled recurring research: interval or five-field cron, IANA
  timezones, pause/resume/manual-run, concurrency guards, frequency floor
- Discord notification per execution (one embed carrying the research
  result — summary, key findings, source/evidence statistics, validated
  citations; or a failure embed, never both)
- SQLite persistence: schedules, execution history, report metadata —
  restored automatically on startup
- Execution-history API per schedule (newest first, bounded limits)
- Report catalog API + UI with safe id-based Markdown/PDF downloads
- Fully dockerized: one command brings up the stack, volumes preserve all
  data across restarts

## 5. Architecture

```
User
 ↓
Next.js (frontend, :3000)
 ↓  HTTP (JSON)
FastAPI (backend, :8010)
 ↓
Research Planner (GLM) → Query Generator (GLM + deterministic fallback)
 ↓
Web Search (ddgs — free)
 ↓
Content Extraction (httpx, SSRF-guarded, bounded) → Text Cleaning (BeautifulSoup)
 ↓
Chunking (deterministic) → Embeddings (local Sentence Transformers)
 ↓
ChromaDB (persistent local vector store, data/chroma/)
 ↓
Retrieval (top-k, metadata attached)
 ↓
GLM Synthesis (evidence numbered [E1]…[En] before the model sees it)
 ↓
Citation Validation (server-side, deterministic — LLM URLs never trusted)
 ↓
Report Generator (Markdown + ReportLab PDF → reports/)
 ↓
SQLite (data/researchflow.db — schedules, executions, report metadata)
 ↓
APScheduler (in-process runtime engine, restored from SQLite on startup)
 ↓
Discord Webhook (one embed per execution, backend-only secret)
```

Layering inside the backend is strict:

```
API (FastAPI) → Services → Repositories → SQLAlchemy 2.x → SQLite
```

The API layer never touches the ORM directly, services never leak SQL, and
every persistence failure is mapped to a client-safe error (HTTP 503) with
the real cause chained for server logs only.

## 6. Tech Stack

| Layer         | Technology |
| ------------- | ---------- |
| Frontend      | Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS 4 |
| Backend       | Python 3.14, FastAPI, Uvicorn |
| LLM           | GLM API via Z.ai's Anthropic-compatible endpoint (the **only paid API**) |
| Web search    | `ddgs` (free, no API key) behind a swappable `SearchProvider` |
| Embeddings    | Sentence Transformers `all-MiniLM-L6-v2` (local, free) |
| Vector DB     | ChromaDB (persistent local store, cosine space) |
| Database      | SQLite + SQLAlchemy 2.x (WAL mode, naive-UTC datetimes) |
| Scheduler     | APScheduler 3.x (AsyncIOScheduler, in-process) |
| Reports       | Markdown (deterministic renderer) + ReportLab PDF (fully offline) |
| Notifications | Discord incoming webhook via httpx (no bot, no OAuth, no SDK) |
| Containers    | Docker + Docker Compose |

## 7. Project Structure

```
ResearchFlow-AI/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── research.py        # plan/search/index/retrieve/synthesize/report + report downloads & catalog
│   │   │   ├── schedules.py       # schedule CRUD + pause/resume/run + execution history
│   │   │   └── notifications.py   # notification status (boolean only)
│   │   ├── core/config.py         # env config (GLM, CORS, paths, timezone, webhook, database URL)
│   │   ├── database/
│   │   │   ├── database.py        # engine/session factory, naive-UTC helpers
│   │   │   ├── models.py          # ScheduleRow / ExecutionRow / ReportRow
│   │   │   └── repositories.py    # Schedule/Execution/Report repositories
│   │   ├── schemas/               # Pydantic request/response models
│   │   │   ├── research.py · schedule.py · notification.py · report.py
│   │   ├── services/
│   │   │   ├── glm_service.py     # GLM client (httpx, Anthropic Messages format)
│   │   │   ├── research_planner.py · query_generator.py
│   │   │   ├── search_provider.py · search_service.py
│   │   │   ├── content_extractor.py · text_cleaner.py
│   │   │   ├── chunking_service.py · embedding_service.py · vector_store.py
│   │   │   ├── rag_service.py     # index/retrieve orchestration
│   │   │   ├── citation_service.py · synthesis_service.py
│   │   │   ├── report_service.py · markdown_reporter.py · pdf_reporter.py
│   │   │   ├── research_execution_service.py  # one full pipeline run
│   │   │   ├── scheduler_service.py           # APScheduler + SQLite-backed registry
│   │   │   ├── report_catalog_service.py      # report metadata over ReportRepository
│   │   │   └── discord_service.py             # webhook validation + delivery
│   │   └── main.py                # FastAPI entrypoint (lifespan: DB init + scheduler)
│   ├── tests/                     # pytest — all externals mocked, temp SQLite per test
│   ├── Dockerfile · .dockerignore · pyproject.toml (ruff config)
│   ├── requirements.txt · requirements-dev.txt
│   └── .env (git-ignored) · .env.example
├── frontend/
│   ├── src/app/ · src/components/ # form, sources, RAG, synthesis, scheduler, reports, status
│   ├── Dockerfile · .dockerignore
│   └── .env.local (git-ignored)
├── data/                          # SQLite DB, ChromaDB, HF model cache (git-ignored, Docker volume)
├── reports/                       # generated .md/.pdf reports (git-ignored, Docker volume)
├── docker-compose.yml
└── README.md
```

## 8. Research Workflow

One research run — interactive (`POST /api/research/report`) or scheduled —
always follows the same eight stages, exactly once each:

1. **Plan** — GLM decomposes the question into a research plan.
2. **Queries** — GLM proposes 3–5 diverse search queries; a deterministic
   fallback derives queries from the question if GLM is unavailable, so
   search always works.
3. **Search** — queries fan out to the free ddgs provider; results are
   merged, deduplicated by normalized URL, and capped. Individual query
   failures never fail the request; only total failure does.
4. **Extract & clean** — each source page is fetched (15 s timeout,
   5 MB / 100k-char caps, SSRF guard) and reduced to readable text.
5. **Index** — cleaned text is chunked deterministically, embedded locally,
   and upserted into ChromaDB (`sha256(url + chunk_index)` ids →
   re-indexing replaces instead of duplicating). A failing site is counted,
   never fatal.
6. **Retrieve** — the question is embedded with the same model; top-k
   chunks come back with full source metadata.
7. **Synthesize** — evidence is numbered `[E1]…[En]`, GLM writes a grounded
   answer, and every citation is re-validated server-side (see §11).
8. **Report** — the validated synthesis is rendered once as Markdown and
   once as PDF; metadata goes to SQLite; files land in `reports/`.

## 9. RAG Pipeline

```
Web Sources (metadata from search)
    ↓  Content Extraction (httpx · SSRF guard · size caps · no redirects)
Text Cleaning (BeautifulSoup — script/style/nav noise removed)
    ↓
Deterministic Chunking (1,000 chars · 150 overlap · word-boundary safe)
    ↓
Local Embeddings (all-MiniLM-L6-v2 — loaded once per process)
    ↓
ChromaDB Upsert (cosine space · deterministic chunk ids · idempotent)
    ↓
Semantic Retrieval (top-k 1–20 · source URL/title/domain + distance)
```

Design notes:

- **Idempotent indexing.** Chunk ids derive from the normalized URL and
  chunk index, so indexing the same article twice replaces its chunks —
  the knowledge base cannot balloon with duplicates.
- **Partial failure is data, not an error.** `failed_sources` counts sites
  that could not be fetched/parsed; only infrastructure failures
  (embedding model, ChromaDB) surface as 503.
- **The vector store is append-only from the API's perspective** — the
  only write paths are indexing and upserts; no arbitrary deletion.

## 10. AI Synthesis

`POST /api/research/synthesize` (or the same logic inside any run):

1. Retrieve top-k evidence chunks for the question.
2. Number them `[E1]…[En]` and build the evidence context — **before** the
   model is called.
3. Ask GLM for an answer that cites evidence inline, under a system prompt
   that forbids following instructions found inside the evidence
   (prompt-injection defense — see §22).
4. Parse and validate every citation in the response (next section).
5. Retry once when the answer is malformed or cites nonexistent evidence;
   a second failure returns an explicit, honest status instead of a
  fabricated answer:
   - `success` — grounded, fully-cited answer
   - `insufficient_evidence` — the knowledge base is too thin to answer
   - `ungrounded` — the model could not stay on the evidence

## 11. Citation System

Citation integrity is enforced by the backend, deterministically:

- Evidence IDs `[E1]…[En]` are assigned **server-side** before generation,
  so a citation can only refer to something that was actually retrieved.
- After generation, every `[En]` in the answer is checked against the
  retrieved evidence set. Unknown references, malformed brackets, or an
  answer that cites nothing when evidence exists → retry, then reject.
- The citation → source mapping (URL, title, domain, chunk index) is built
  **from backend metadata only**. The model's own claims about URLs or
  sources are never trusted, echoed, or stored.
- The final answer ships with a source list the backend can prove matches
  every citation in the text.

## 12. Report Generation

`POST /api/research/report` runs the validated synthesis once and renders:

- **Markdown** — deterministic renderer (same input ⇒ same output), with
  the full source list.
- **PDF** — ReportLab, fully offline (no remote fonts, images, or
  resources; hostile/XML-unsafe text is escaped and survives).

Downloads use **validated report ids only** — the backend resolves the id
against SQLite metadata, reconstructs the exact expected filename, and
serves the file from `reports/`. No client-supplied paths, no traversal.

- `GET /api/research/reports/{id}/markdown`
- `GET /api/research/reports/{id}/pdf`
- `GET /api/research/reports?limit=1..100` — catalog, newest first

Every report — interactive or scheduled — gets one metadata row in SQLite
(query, filenames, synthesis status, originating schedule/execution). The
files themselves are never duplicated into the database.

## 13. Scheduler

Any question becomes a recurring job:

- **Interval** (`every N minutes`, 5 min … 1 year) or **cron**
  (five-field expressions, parsed only by APScheduler's `CronTrigger` —
  never executed, never passed to a shell).
- **IANA timezones** (default `Asia/Jakarta`); next runs are computed in
  the schedule's timezone and stored as UTC.
- **Pause / resume / run-now / delete**, all persisted immediately.
- **Concurrency safety:** per-schedule running flag + APScheduler
  `max_instances=1` + a global semaphore + a 5-minute frequency floor +
  a 50-schedule registry cap.
- **Failures never kill the scheduler** — a failed run records a
  client-safe error and the schedule stays live.

Endpoints (`/api/research/schedules`):

| Method   | Path                                       | Effect                                    |
| -------- | ------------------------------------------ | ----------------------------------------- |
| `POST`   | `/api/research/schedules`                  | Create (validated, persisted, registered) |
| `GET`    | `/api/research/schedules`                  | List all                                  |
| `GET`    | `/api/research/schedules/{id}`             | Inspect one                               |
| `POST`   | `/api/research/schedules/{id}/pause`       | Disable (persisted)                       |
| `POST`   | `/api/research/schedules/{id}/resume`      | Re-enable (persisted)                     |
| `POST`   | `/api/research/schedules/{id}/run`         | Run immediately (202; schedule untouched) |
| `GET`    | `/api/research/schedules/{id}/executions`  | Execution history, newest first (1–100)   |
| `DELETE` | `/api/research/schedules/{id}`             | Delete (persisted)                        |

## 14. SQLite Persistence

**SQLite is the source of truth; APScheduler is only the runtime engine.**

- Database: `data/researchflow.db` (git-ignored; path via `DATABASE_URL`).
- Tables: `schedules`, `research_executions`, `reports` (metadata only —
  never file contents, never secrets).
- **Startup:** init/create tables → load schedules → re-validate each
  stored definition (an invalid row is disabled with a recorded error and
  never crashes startup) → rebuild triggers → register **enabled**
  schedules only → start APScheduler.
- **Every action persists:** create, pause, resume, delete, execution
  start/outcome, notification status, report metadata — short
  transactions, rolled back on failure, WAL mode for concurrent reads.
- **Transactions stay short:** GLM calls, web searches, embedding,
  ChromaDB, report rendering, and Discord requests all happen **outside**
  any database transaction.
- **Restart safety:** shutdown deliberately keeps all rows; the next boot
  reconstructs the runtime state from SQLite alone (verified by dedicated
  two-instance restart tests).
- Datetimes are stored as naive UTC and re-attached to UTC on read;
  schedule timezones only affect trigger computation.

## 15. Discord Notifications

- One embed **per execution**, sent **after** the outcome is
  known — success or failure, never both, never in-between states.
- The **success embed carries the research result itself**: the summary,
  key findings (normalized to bullets), statistics (sources found ·
  evidence chunks), and the **validated citations** — evidence IDs with
  source title/domain links taken ONLY from citation records the
  synthesis pipeline already validated against retrieved source
  metadata, never from free-form LLM output. The content is re-derived
  from the same synthesis outcome the report was generated from — no
  re-research, no second pipeline run, one delivery per execution.
- **No attachments**: the Markdown/PDF reports are never posted to
  Discord. The embed names the report files; the full reports stay
  downloadable from the web app (Report Catalog).
- Content is size-safe for Discord: per-field and per-embed budgets with
  explicit `+N lainnya` overflow markers — long findings/citation lists
  are truncated visibly, never silently.
- The webhook URL is a **backend-only secret**: strict validation
  (HTTPS, `discord.com`/`discordapp.com` hosts, `/api/webhooks/` path,
  no ports/userinfo/dot-segments — fail-closed to `not_configured`),
  never exposed to the frontend, never in API responses.
- `allowed_mentions: {"parse": []}` kills payload-injection via mentions;
  field lengths are capped; errors are client-safe.
- Delivery: httpx, 8 s timeout, ≤2 attempts, no retry on 4xx.
- Notification status (`not_configured` / `sent` / `failed`) is stored
  per schedule **and** per execution, deliberately separate from research
  status — a Discord failure never marks research failed.
- `GET /api/research/notifications/status` → booleans only (no URL).

## 16. Docker

```bash
docker compose up --build
```

| Service  | URL                        | Notes                                   |
| -------- | -------------------------- | --------------------------------------- |
| Backend  | http://localhost:8010      | Container listens on 8000; healthcheck `/health` |
| Frontend | http://localhost:3000      | Waits for backend health before starting |
| Swagger  | http://localhost:8010/docs |                                          |

- **Volumes:** `./data` (SQLite + ChromaDB + HF model cache) and
  `./reports` (generated reports) are bind-mounted — container restarts
  and rebuilds lose nothing.
- **Secrets:** `backend/.env` (git-ignored) reaches the backend container
  at runtime via `env_file` only. `.dockerignore` keeps it (and `.venv`,
  tests, caches) out of both build contexts; no secret ever enters an
  image layer or the frontend build.
- **Backend image:** Python 3.14-slim, CPU-only torch wheel index,
  non-root user (uid 1000), stdlib-based healthcheck.
- **Frontend image:** Node 22, `npm ci` → `next build` → `next start`,
  with exactly one build arg: the public `NEXT_PUBLIC_API_BASE_URL`.

## 17. Installation

**Prerequisites:** Python 3.14, Node.js 22, a GLM API key
(Z.ai — the only paid dependency). Docker optional.

```bash
git clone <your-repo-url> researchflow-ai && cd researchflow-ai

# Backend
cd backend
python3.14 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env          # then edit: GLM_API_KEY, GLM_MODEL
uvicorn app.main:app --port 8010

# Frontend (new terminal)
cd frontend
npm install
echo 'NEXT_PUBLIC_API_BASE_URL=http://localhost:8010' > .env.local
npm run dev
```

Or with Docker — one command, no local toolchain:

```bash
cp backend/.env.example backend/.env   # fill in GLM_API_KEY, GLM_MODEL
docker compose up --build
```

## 18. Environment Variables

### Backend — `backend/.env` (copy from `.env.example`; git-ignored)

| Variable | Required | Description |
| -------- | -------- | ----------- |
| `GLM_API_KEY` | yes | Your GLM API key (backend-only secret) |
| `GLM_MODEL` | yes | A GLM model available on your account |
| `GLM_BASE_URL` | no | Default `https://api.z.ai/api/anthropic` (Anthropic Messages format, where the GLM Coding Plan quota lives) |
| `DISCORD_WEBHOOK_URL` | no | Placeholder ⇒ notifications off. Discord incoming-webhook URL — backend secret, strictly validated |
| `DATABASE_URL` | no | Default `sqlite:///<project>/data/researchflow.db` |
| `CORS_ORIGINS` | no | Default `http://localhost:3000` |
| `DEFAULT_SCHEDULE_TIMEZONE` | no | Default `Asia/Jakarta` (IANA timezone for new schedules) |
| `CHROMA_DIR` / `CHROMA_COLLECTION_NAME` | no | Vector store location/name |
| `EMBEDDING_MODEL_NAME` | no | Default `sentence-transformers/all-MiniLM-L6-v2` |

### Frontend — `frontend/.env.local` (git-ignored)

| Variable | Description |
| -------- | ----------- |
| `NEXT_PUBLIC_API_BASE_URL` | Backend base URL, `http://localhost:8010` |

No secret ever uses a `NEXT_PUBLIC_` prefix. The frontend receives no
keys, no webhook URLs, no internals — only the public API base URL.

## 19. Local Development

```bash
# Backend — tests (all externals mocked; temp SQLite per test)
cd backend && source .venv/bin/activate
pytest
ruff check app tests

# Frontend
cd frontend
npm run lint
npm run build

# Run
uvicorn app.main:app --port 8010 --reload   # backend :8010
npm run dev                                  # frontend :3000
```

Backend port is **8010** (8000/8001 are commonly taken); the frontend
expects it via `NEXT_PUBLIC_API_BASE_URL`.

## 20. API Endpoints

Base URL: `http://localhost:8010` — full interactive docs at `/docs`
(Swagger UI generated from the OpenAPI schema).

| Method | Path | Purpose |
| ------ | ---- | ------- |
| `GET`  | `/health` | Liveness (used by Docker healthcheck) |
| `POST` | `/api/research/plan` | Research plan for a question (GLM) |
| `POST` | `/api/research/search` | Query generation + free web search + dedup |
| `POST` | `/api/research/index` | Fetch/extract/chunk/embed/upsert sources |
| `POST` | `/api/research/retrieve` | Semantic top-k retrieval with metadata |
| `POST` | `/api/research/synthesize` | Grounded synthesis + validated citations |
| `POST` | `/api/research/report` | Synthesize once → Markdown + PDF |
| `GET`  | `/api/research/reports` | Report catalog (newest first, `limit` 1–100) |
| `GET`  | `/api/research/reports/{id}/markdown` | Safe download (validated id) |
| `GET`  | `/api/research/reports/{id}/pdf` | Safe download (validated id) |
| `POST` | `/api/research/schedules` | Create scheduled research |
| `GET`  | `/api/research/schedules` | List schedules |
| `GET`  | `/api/research/schedules/{id}` | Inspect one |
| `POST` | `/api/research/schedules/{id}/pause` | Pause (persisted) |
| `POST` | `/api/research/schedules/{id}/resume` | Resume (persisted) |
| `POST` | `/api/research/schedules/{id}/run` | Manual run (202, async) |
| `GET`  | `/api/research/schedules/{id}/executions` | Execution history (newest first) |
| `DELETE` | `/api/research/schedules/{id}` | Delete (persisted) |
| `GET`  | `/api/research/notifications/status` | Discord configured? (boolean only) |

Example — create a daily 08:00 research job:

```json
POST /api/research/schedules
{
  "question": "What are the latest developments in Retrieval-Augmented Generation?",
  "schedule_type": "cron",
  "cron_expression": "0 8 * * *",
  "timezone": "Asia/Jakarta"
}
```

All errors are safe: `{"detail": "…"}` with client-safe messages — never
stack traces, never API keys, never filesystem paths.

## 21. Testing

```bash
cd backend
pip install -r requirements-dev.txt
pytest          # 461 tests
ruff check app tests
```

- **Everything external is mocked**: GLM (no paid calls), web pages and
  the Discord webhook (httpx `MockTransport`), the Sentence Transformers
  model, ChromaDB. The suite costs nothing and needs no network.
- **Every test runs against a temporary SQLite file** (autouse fixture) —
  the production database is never touched.
- **Restart persistence is tested at service level**: two scheduler
  instances over one database — enabled schedules restore as active jobs,
  paused ones stay unregistered, execution history and report metadata
  survive.
- Coverage spans: health, planner, search, extraction (incl. the SSRF
  matrix), cleaning, chunking, embeddings, vector store, RAG, synthesis,
  citations, reports (Markdown + PDF), scheduler, notifications, SQLite,
  restart persistence, and the API layer of every endpoint above.

```bash
cd frontend
npm run lint    # eslint (next/core-web-vitals)
npm run build   # production build
```

## 22. Security

- **Secrets** — `GLM_API_KEY` and `DISCORD_WEBHOOK_URL` exist only in
  `backend/.env`, read via environment variables: never hardcoded, never
  logged, never stored in SQLite, never sent to the browser, never in any
  API response or error message.
- **SSRF** — source URLs are validated before fetching: http/https only,
  standard ports only, no embedded credentials, no redirects (the client
  refuses to follow), localhost/loopback/private/link-local/reserved/
  multicast targets rejected — including non-dotted IP forms
  (`2130706433`, `0x7f.0.0.1`) that plain parsing misses. The Discord
  webhook is validated the same fail-closed way.
- **Prompt injection** — retrieved web content is framed as untrusted
  data; the system prompt forbids following instructions found inside
  evidence, and citations are validated server-side regardless of what
  the model claims.
- **File security** — report downloads resolve validated ids against DB
  metadata and reconstruct exact filenames (`relative_to` containment
  check); path traversal and arbitrary filenames are structurally
  impossible. Generated filenames derive from a slugified question plus
  a safe id.
- **Input validation** — every Pydantic schema enforces min/max lengths,
  enums, numeric bounds (intervals, limits, top-k), URL shape, and
  schedule validity (type/expression/timezone/spacing) before any work
  starts.
- **Error handling** — clients see safe messages and correct status codes
  (422/502/503/504); stack traces, SQL, paths, and causes stay in
  server-side logs (`from None` / chained exceptions where useful).
- **Concurrency** — per-schedule guards + `max_instances=1` + global
  semaphore prevent duplicate/overlapping runs; SQLite runs in WAL mode
  with short transactions held open only around quick local writes.
- **No `eval`/`exec`/shell** — cron expressions and every other input are
  parsed by libraries, never executed.

## 23. Limitations

Stated honestly — this is a local/self-hosted research tool, not a
distributed SaaS:

- **SQLite is local**, single-node, and not intended for distributed or
  multi-writer production use.
- **APScheduler is in-process**: one backend process runs the scheduler.
  Running multiple backend replicas against the same database would run
  duplicate schedules — deploy a single backend worker.
- **No distributed scheduler** (no Celery/Redis/broker) by design.
- **ddgs is scraping-based** and may break when providers change; results
  can be empty or rate-limited at times.
- **Some websites block automated requests** — extraction failures are
  expected and counted (`failed_sources`), never fatal.
- **GLM requires an API key** (the only paid dependency); without it,
  planning/synthesis return 503 while search still works via fallback
  queries.
- **ChromaDB is local** — no cross-machine vector sharing.
- **Docker is intended for local/self-hosted deployment** (single host,
  bind-mounted volumes), not orchestrated multi-node production.
- The SSRF guard is deliberately MVP-grade (DNS rebinding is out of
  scope); the tool fetches public web pages, not internal networks.
- Discord delivery is an incoming webhook — post-only, one channel, no
  bot commands or reads.
- **Indonesian UI and output, English search**: the UI, research plans,
  synthesized answers, reports (MD/PDF), and Discord notifications are in
  Bahasa Indonesia. Search queries are deliberately generated in English
  because most indexed web sources and the embedding model are
  English-optimized — retrieval quality in other languages is lower.
  Client-facing error messages are Indonesian; API enum values
  (`running`, `success`, `manual`, ...) and developer logs stay English.

## 24. Future Improvements

- Source credibility scoring & prioritization (domain reputation,
  recency when dates are available)
- Direct PDF/paper source ingestion (PyMuPDF) alongside HTML pages
- Reranking stage between retrieval and synthesis (e.g. cross-encoder)
- Multi-question research sessions and follow-up questions
- Per-schedule Discord channels / notification filters
- Export formats beyond Markdown + PDF (DOCX, BibTeX)
- Retrieval cache and incremental re-indexing by content hash
- Optional authentication for the frontend/API (currently trust-local)
- Migration path from SQLite to Postgres if multi-writer needs emerge

---

*Built as a portfolio project: one paid LLM API (GLM), everything else
free and local.*
