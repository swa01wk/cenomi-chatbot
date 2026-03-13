# Cenomi Mall Concierge

AI-powered single-mall concierge chatbot platform. Answers visitor questions about stores, dining, entertainment, and services — grounded in structured mall intelligence.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Tech Stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Repository Setup](#repository-setup)
- [Backend](#backend)
  - [Backend Structure](#backend-structure)
  - [Backend Environment Variables](#backend-environment-variables)
  - [Backend Setup](#backend-setup)
  - [Backend API Endpoints](#backend-api-endpoints)
  - [LangGraph Pipeline](#langgraph-pipeline)
  - [Services Layer](#services-layer)
  - [Mall Intelligence Data](#mall-intelligence-data)
  - [Backend Testing](#backend-testing)
  - [Backend Linting](#backend-linting)
- [Frontend](#frontend)
  - [Frontend Structure](#frontend-structure)
  - [Frontend Environment Variables](#frontend-environment-variables)
  - [Frontend Setup](#frontend-setup)
  - [Frontend Components](#frontend-components)
  - [Frontend Build](#frontend-build)
  - [Frontend Linting](#frontend-linting)
- [Running the Full Stack](#running-the-full-stack)
- [Pushing to GitHub](#pushing-to-github)
- [Project Status](#project-status)
- [License](#license)

---

## Architecture Overview

```
cenomi-chatbot/
├── backend/          Python FastAPI + LangGraph concierge engine
│   ├── app/          Application source code
│   │   ├── api/      FastAPI route handlers
│   │   ├── config/   Settings and constants
│   │   ├── context/  Mall context loader
│   │   ├── feedback/ Feedback service
│   │   ├── graph/    LangGraph pipeline builder
│   │   ├── models/   Pydantic data models
│   │   ├── nodes/    LangGraph processing nodes
│   │   ├── observability/  Logging
│   │   ├── prompts/  Prompt builder
│   │   ├── retrieval/ Retrieval engine
│   │   └── services/ Business logic services
│   ├── data/         Mall intelligence data (JSON)
│   ├── scripts/      Dev helper scripts
│   └── tests/        Pytest test suite
├── frontend/         React + Vite testing console
│   ├── src/
│   │   ├── api/      API client
│   │   ├── components/ UI components
│   │   ├── hooks/    Custom React hooks
│   │   ├── lib/      Constants and utilities
│   │   ├── mock/     Mock response data
│   │   ├── pages/    Page components
│   │   ├── styles/   CSS / Tailwind
│   │   └── types/    TypeScript type definitions
│   └── public/       Static assets
└── docs/             Architecture and iteration documentation
```

### Core Principles

- **Answer first** — respond immediately, elaborate as needed
- **Strongly grounded** — every answer references verified mall data
- **Context-aware** — maintains conversation state and mall awareness
- **Concierge-like** — proactive, helpful, low-clarification interactions
- **Extensible** — single-mall now, multi-mall ready in structure

### Mall Intelligence Layers

Mall information is transformed into three first-class layers:

1. **Canonical Entities** — normalized tenant records (stores, restaurants, services)
2. **Semantic Intelligence** — derived facts, spatial relationships, curated descriptions
3. **Scenario Playbooks** — pre-built response strategies for common visitor intents

---

## Tech Stack

| Layer    | Technology                                     |
|----------|------------------------------------------------|
| Backend  | Python 3.11+, FastAPI, LangGraph, Pydantic v2  |
| Frontend | React 19, Vite 7, TypeScript 5.9, Tailwind v4  |
| LLM      | OpenAI GPT-4o (configurable)                   |
| State    | LangGraph StateGraph (in-memory sessions)       |
| Icons    | Lucide React                                    |
| Linting  | Ruff (backend), ESLint (frontend)               |
| Testing  | Pytest + pytest-asyncio (backend)               |

---

## Prerequisites

| Tool       | Minimum Version | Installation                                  |
|------------|-----------------|-----------------------------------------------|
| Python     | 3.11+           | https://www.python.org/downloads/             |
| Node.js    | 18+             | https://nodejs.org/ (LTS recommended)          |
| npm        | 9+              | Bundled with Node.js                           |
| Git        | 2.30+           | https://git-scm.com/                           |
| OpenAI Key | —               | https://platform.openai.com/api-keys           |

---

## Repository Setup

```bash
# Clone the repository
git clone https://github.com/<your-username>/cenomi-chatbot.git
cd cenomi-chatbot
```

---

## Backend

### Backend Structure

```
backend/
├── app/
│   ├── main.py                 # FastAPI app entry point and lifespan
│   ├── runtime.py              # Global registry (mall context, sessions, feedback)
│   ├── api/
│   │   ├── chat.py             # POST /api/chat — main chat endpoint
│   │   ├── feedback.py         # Feedback submission and analytics
│   │   ├── health.py           # GET /api/health — health check
│   │   └── session.py          # Session management (reset, get)
│   ├── config/
│   │   ├── constants.py        # Application constants
│   │   └── settings.py         # Pydantic-settings configuration
│   ├── context/
│   │   └── mall_context.py     # Mall context loader and entity lookup
│   ├── feedback/
│   │   └── service.py          # Feedback service implementation
│   ├── graph/
│   │   └── builder.py          # LangGraph pipeline compilation
│   ├── models/
│   │   ├── api.py              # Request/response schemas
│   │   ├── context_pack.py     # Context pack model
│   │   ├── feedback.py         # Feedback data models
│   │   ├── mall.py             # Mall entity models
│   │   ├── playbook.py         # Playbook models
│   │   ├── semantic.py         # Semantic intelligence models
│   │   ├── state.py            # LangGraph state schema
│   │   └── tenant.py           # Tenant config models
│   ├── nodes/
│   │   ├── _tracing.py         # Node tracing utilities
│   │   ├── choose_strategy.py  # Choose response strategy
│   │   ├── compose_context.py  # Build context blocks
│   │   ├── decide_retrieval.py # Decide if retrieval is needed
│   │   ├── emit_debug_payload.py # Finalize debug trace
│   │   ├── fetch_exact_facts.py  # Optional retrieval step
│   │   ├── generate_response.py  # LLM response generation
│   │   ├── interpret_turn.py   # Intent classification
│   │   ├── load_session.py     # Load session and tenant config
│   │   ├── resolve_playbooks.py # Match scenario playbooks
│   │   ├── update_memory.py    # Update conversation state
│   │   └── update_scene_memory.py # Update scene memory
│   ├── observability/
│   │   └── logger.py           # Structured logger
│   ├── prompts/
│   │   └── builder.py          # LLM prompt construction
│   ├── retrieval/
│   │   └── retriever.py        # Canonical/semantic/playbook retrieval
│   └── services/
│       ├── concierge.py        # Orchestrates chat turns via LangGraph
│       ├── context_builder.py  # Builds mall context from data layers
│       ├── enricher.py         # Semantic enrichment of entities
│       ├── feedback_normalizer.py     # Normalizes feedback signals
│       ├── feedback_service.py        # Feedback lifecycle management
│       ├── implicit_feedback_detector.py # Detects implicit feedback
│       ├── knowledge_gap_analyzer.py  # Playbook/knowledge gap analysis
│       ├── normalizer.py       # Canonical data normalization
│       ├── playbook_engine.py  # Playbook matching and ranking
│       ├── session_store.py    # In-memory session storage
│       ├── session_tuning_engine.py   # Feedback-based session tuning
│       ├── tenant_params.py    # Tenant config loading/merging
│       ├── tenant_parameter_tuner.py  # Tenant-level feedback aggregation
│       └── tenant_runtime.py   # Tenant runtime management
├── data/
│   ├── canonical/
│   │   └── cenomi_mall_01.json # Normalized store/restaurant/service records
│   ├── context_packs/
│   │   └── global_context.json # Global context pack
│   ├── examples/
│   │   └── example_turn_state.json # Example state for reference
│   ├── feedback/
│   │   ├── implicit/           # Implicit feedback signals (auto-generated)
│   │   └── normalized/         # Normalized feedback data
│   ├── playbooks/
│   │   └── cenomi_mall_01.json # Scenario playbooks
│   ├── semantic/
│   │   └── cenomi_mall_01.json # Semantic intelligence data
│   └── tenant_config/
│       ├── cenomi_mall_01.json # Mall-specific tenant config
│       └── tenant_defaults.json # Default tenant parameters
├── scripts/
│   └── run_dev.sh              # Dev server startup script
├── tests/
│   ├── test_chat.py            # Chat endpoint tests
│   └── test_health.py          # Health endpoint tests
├── pyproject.toml              # Project metadata and dependencies
├── .env.example                # Environment variable template
└── .env                        # Local env overrides (gitignored)
```

### Backend Environment Variables

All backend environment variables use the **`BACKEND_`** prefix (enforced by `pydantic-settings`).

Copy the example file and fill in your values:

```bash
cd backend
cp .env.example .env
```

| Variable                       | Default                  | Required | Description                              |
|--------------------------------|--------------------------|----------|------------------------------------------|
| `BACKEND_OPENAI_API_KEY`       | `""`                     | **Yes**  | Your OpenAI API key                      |
| `BACKEND_OPENAI_MODEL`         | `gpt-4o`                | No       | OpenAI model name                        |
| `BACKEND_OPENAI_TEMPERATURE`   | `0.3`                    | No       | LLM temperature (0.0–2.0)               |
| `BACKEND_HOST`                 | `0.0.0.0`               | No       | Server bind host                         |
| `BACKEND_PORT`                 | `8000`                   | No       | Server bind port                         |
| `BACKEND_ENV`                  | `development`            | No       | Environment name                         |
| `BACKEND_DEBUG`                | `true`                   | No       | Enable debug mode                        |
| `BACKEND_LOG_LEVEL`            | `debug`                  | No       | Logging level (debug/info/warning/error) |
| `BACKEND_FRONTEND_ORIGIN`      | `http://localhost:5173`  | No       | Allowed CORS origin for the frontend     |
| `BACKEND_MALL_ID`              | `cenomi_mall_01`         | No       | Active mall identifier                   |
| `BACKEND_MALL_NAME`            | `Cenomi Mall`            | No       | Display name for the mall                |
| `BACKEND_VECTOR_STORE_TYPE`    | `chroma`                 | No       | Vector store backend type                |
| `BACKEND_EMBEDDING_MODEL`      | `text-embedding-3-small` | No       | Embedding model name                     |
| `BACKEND_ENABLE_TRACING`       | `false`                  | No       | Enable LangSmith tracing                 |
| `BACKEND_LANGSMITH_API_KEY`    | `""`                     | No       | LangSmith API key (if tracing enabled)   |
| `BACKEND_LANGSMITH_PROJECT`    | `cenomi-concierge`       | No       | LangSmith project name                   |
| `BACKEND_FEEDBACK_STORAGE`     | `local`                  | No       | Feedback storage backend (`local`)       |

**Example `.env` file:**

```env
BACKEND_OPENAI_API_KEY=sk-your-openai-api-key-here
BACKEND_OPENAI_MODEL=gpt-4o
BACKEND_OPENAI_TEMPERATURE=0.3
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
BACKEND_ENV=development
BACKEND_LOG_LEVEL=debug
BACKEND_DEBUG=true
BACKEND_FRONTEND_ORIGIN=http://localhost:5173
BACKEND_MALL_ID=cenomi_mall_01
BACKEND_MALL_NAME=Cenomi Mall
BACKEND_FEEDBACK_STORAGE=local
```

### Backend Setup

```bash
# Navigate to the backend directory
cd backend

# Create and activate a Python virtual environment
python -m venv .venv
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\activate         # Windows

# Install dependencies (including dev tools)
pip install -e ".[dev]"

# Copy the environment template and fill in your API keys
cp .env.example .env
# Edit .env and set BACKEND_OPENAI_API_KEY=sk-...

# Start the development server (auto-reload enabled)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Alternatively, use the dev script
./scripts/run_dev.sh
```

The backend starts at **http://localhost:8000**. On startup, the lifespan handler initializes the mall context, session store, and feedback services.

### Backend API Endpoints

| Method | Endpoint             | Description                          |
|--------|----------------------|--------------------------------------|
| `GET`  | `/api/health`        | Health check — returns server status |
| `POST` | `/api/chat`          | Send a chat message, receive AI response with debug payload |
| `GET`  | `/api/session`       | Get current session state            |
| `POST` | `/api/session/reset` | Reset the current session            |
| `POST` | `/api/feedback`      | Submit explicit feedback (thumbs up/down, reasons, comments) |
| `GET`  | `/api/feedback`      | Retrieve feedback analytics          |

### LangGraph Pipeline

The chat engine is a **LangGraph StateGraph** compiled from 11 sequential nodes. Each node reads from and writes to a shared `TurnState`:

```
load_session
    → interpret_turn
        → update_scene_memory
            → resolve_playbooks
                → choose_strategy
                    → compose_context
                        → decide_retrieval
                            → fetch_exact_facts
                                → generate_response
                                    → update_memory
                                        → emit_debug_payload
```

| Node                  | Responsibility                                              |
|-----------------------|-------------------------------------------------------------|
| `load_session`        | Load session, tenant config, normalize the incoming message |
| `interpret_turn`      | Classify intent (domain, sub-intent, message kind)          |
| `update_scene_memory` | Update the scene memory with new context                    |
| `resolve_playbooks`   | Match the turn against scenario playbooks                   |
| `choose_strategy`     | Select the response strategy and confidence level           |
| `compose_context`     | Build context blocks (topic, entities, signals)             |
| `decide_retrieval`    | Decide whether RAG retrieval is needed                      |
| `fetch_exact_facts`   | Execute retrieval if needed                                 |
| `generate_response`   | Call the LLM to generate the concierge response             |
| `update_memory`       | Persist conversation state and scene updates                |
| `emit_debug_payload`  | Finalize the debug trace for the frontend inspector         |

### Services Layer

| Service                       | Description                                               |
|-------------------------------|-----------------------------------------------------------|
| `concierge`                   | Orchestrates chat turns by invoking the LangGraph pipeline |
| `context_builder`             | Builds mall context from canonical, semantic, and playbook data |
| `session_store`               | In-memory session storage (Redis-ready for production)     |
| `feedback_service`            | Manages the full feedback lifecycle                        |
| `feedback_normalizer`         | Normalizes feedback into actionable signals                |
| `implicit_feedback_detector`  | Detects implicit feedback (e.g., user corrections)         |
| `session_tuning_engine`       | Applies feedback-based tuning per session                  |
| `tenant_params`               | Loads and merges tenant configuration                      |
| `tenant_parameter_tuner`      | Tenant-level feedback aggregation and tuning               |
| `knowledge_gap_analyzer`      | Identifies playbook and knowledge gaps                     |
| `playbook_engine`             | Playbook matching and ranking                              |
| `normalizer`                  | Canonical data normalization                               |
| `enricher`                    | Semantic enrichment of entities                            |

### Mall Intelligence Data

Located in `backend/data/`, all data is stored as JSON:

| Directory        | File(s)                  | Description                                    |
|------------------|--------------------------|------------------------------------------------|
| `canonical/`     | `cenomi_mall_01.json`    | Normalized tenant records (stores, restaurants) |
| `semantic/`      | `cenomi_mall_01.json`    | Derived facts, spatial relationships           |
| `playbooks/`     | `cenomi_mall_01.json`    | Pre-built scenario response strategies          |
| `context_packs/` | `global_context.json`    | Global context pack                            |
| `tenant_config/` | `tenant_defaults.json`, `cenomi_mall_01.json` | Tenant parameters and overrides |
| `examples/`      | `example_turn_state.json`| Reference state object for development         |
| `feedback/`      | `implicit/`, `normalized/` | Auto-generated feedback data                 |

### Backend Testing

```bash
cd backend
source .venv/bin/activate

# Run the full test suite
pytest

# Run with verbose output
pytest -v

# Run a specific test file
pytest tests/test_health.py

# Run with coverage (install pytest-cov first)
pip install pytest-cov
pytest --cov=app --cov-report=term-missing
```

### Backend Linting

```bash
cd backend
source .venv/bin/activate

# Check for lint issues
ruff check .

# Auto-fix issues
ruff check --fix .

# Format code
ruff format .

# Type checking
mypy app/
```

---

## Frontend

### Frontend Structure

```
frontend/
├── src/
│   ├── main.tsx                  # React entry point (createRoot, StrictMode)
│   ├── App.tsx                   # Root component with routing
│   ├── api/
│   │   └── client.ts            # HTTP client for backend API calls
│   ├── components/
│   │   ├── TopBar.tsx            # Header: tenant/mall selectors, debug toggle, export, reset
│   │   ├── ChatInput.tsx         # Message textarea with send button (4000 char limit)
│   │   ├── ChatMessage.tsx       # Single message bubble with sources and feedback
│   │   ├── SuggestedChips.tsx    # Clickable suggestion chips
│   │   ├── FeedbackControls.tsx  # Thumbs up/down with reasons and comments
│   │   ├── DebugPanel.tsx        # Debug sidebar with turn inspector
│   │   ├── RawDrawer.tsx         # Collapsible raw JSON viewer (trace, state, prompt)
│   │   ├── StrategyCard.tsx      # Playbook, confidence, strategy display
│   │   ├── RetrievalCard.tsx     # Retrieval status, reason, targets, results
│   │   ├── ContextBlocksCard.tsx # Topic blocks, entities, semantic signals
│   │   └── SceneSnapshotCard.tsx # Scene summary key-value display
│   ├── hooks/
│   │   └── useChat.ts            # Core chat hook: send, feedback, reset, export, mock mode
│   ├── lib/
│   │   └── constants.ts          # Application constants
│   ├── mock/
│   │   └── responses.ts          # Mock API responses for offline development
│   ├── pages/
│   │   └── ChatPage.tsx          # Main chat page: messages, welcome, typing indicator
│   ├── styles/
│   │   └── index.css             # Tailwind imports and custom CSS variables
│   └── types/
│       ├── api.ts                # API request/response TypeScript types
│       └── chat.ts               # Chat-related TypeScript types
├── public/
│   └── favicon.svg               # App favicon
├── index.html                    # HTML entry point
├── vite.config.ts                # Vite configuration (proxy, plugins)
├── tsconfig.json                 # TypeScript project references
├── tsconfig.app.json             # TypeScript config for app source
├── tsconfig.node.json            # TypeScript config for Vite config
├── eslint.config.js              # ESLint flat config
├── package.json                  # Dependencies and scripts
├── .env.example                  # Environment variable template
└── .gitignore                    # Git ignore rules
```

### Frontend Environment Variables

The frontend uses **Vite** — only variables prefixed with `VITE_` are exposed to client code.

```bash
cd frontend
cp .env.example .env
```

| Variable            | Default                  | Required | Description                     |
|---------------------|--------------------------|----------|---------------------------------|
| `VITE_API_BASE_URL` | `""` (same-origin proxy) | No       | Backend API base URL            |

**Example `.env` file:**

```env
VITE_API_BASE_URL=http://localhost:8000
```

> **Note:** When `VITE_API_BASE_URL` is empty or unset, the Vite dev server proxies all `/api` requests to `http://localhost:8000` automatically. You typically don't need to set this variable during local development.

### Frontend Setup

```bash
# Navigate to the frontend directory
cd frontend

# Install dependencies
npm install

# Copy the environment template (optional for local dev)
cp .env.example .env

# Start the development server
npm run dev
```

The frontend starts at **http://localhost:5173** and automatically proxies `/api` requests to the backend.

### Frontend Components

| Component           | Description                                                          |
|---------------------|----------------------------------------------------------------------|
| `ChatPage`          | Main page: renders the message list, welcome screen, typing indicator, and input area |
| `TopBar`            | Header bar with tenant/mall selectors, session ID display, debug mode toggle, export button, and reset button |
| `ChatInput`         | Text input area with send button and a 4000-character limit          |
| `ChatMessage`       | Individual message bubble displaying the AI response, cited sources, and feedback controls |
| `SuggestedChips`    | Row of clickable suggestion chips for quick prompts                  |
| `FeedbackControls`  | Thumbs up/down buttons with optional negative feedback reasons and a comment field |
| `DebugPanel`        | Collapsible sidebar showing the pipeline inspector per turn          |
| `RawDrawer`         | Expandable raw JSON viewer for trace, state, and prompt data with copy functionality |
| `StrategyCard`      | Displays the selected playbook, confidence score, strategy, and response shape |
| `RetrievalCard`     | Shows retrieval status, reasoning, targets, and result count         |
| `ContextBlocksCard` | Displays topic blocks, entities, semantic signals, and ranking notes |
| `SceneSnapshotCard` | Key-value display of the current scene snapshot                      |

### Frontend Build

```bash
cd frontend

# Type-check and build for production
npm run build

# Preview the production build locally
npm run preview
```

The production build outputs to `frontend/dist/`.

### Frontend Linting

```bash
cd frontend

# Run ESLint
npm run lint
```

---

## Running the Full Stack

Open **two terminals** and run the backend and frontend simultaneously:

**Terminal 1 — Backend:**

```bash
cd cenomi-chatbot/backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Edit .env → set BACKEND_OPENAI_API_KEY
uvicorn app.main:app --reload
```

**Terminal 2 — Frontend:**

```bash
cd cenomi-chatbot/frontend
npm install
npm run dev
```

Then open **http://localhost:5173** in your browser. The frontend proxies API requests to the backend at port 8000.

---

## Pushing to GitHub

### First-time setup (new repository)

```bash
# 1. Navigate to the project root
cd cenomi-chatbot

# 2. Initialize Git (skip if already initialized)
git init

# 3. Verify .gitignore is in place (it should already exist)
cat .gitignore

# 4. Create a new repository on GitHub
#    Go to https://github.com/new and create a repo named "cenomi-chatbot"
#    Do NOT initialize with README, .gitignore, or license (we already have them)

# 5. Add the remote origin
git remote add origin https://github.com/<your-username>/cenomi-chatbot.git

# 6. Stage all files
git add .

# 7. Verify what will be committed (check no .env or secrets are staged)
git status

# 8. Create the initial commit
git commit -m "Initial commit: Cenomi Mall Concierge chatbot platform"

# 9. Push to GitHub
git branch -M main
git push -u origin main
```

### Subsequent pushes

```bash
git add .
git commit -m "Your commit message here"
git push
```

### Security checklist before pushing

- [ ] `.env` files are listed in `.gitignore` and will NOT be committed
- [ ] No API keys or secrets are hardcoded in source files
- [ ] Only `.env.example` files (with placeholder values) are committed
- [ ] `node_modules/`, `.venv/`, `__pycache__/` are all gitignored

### Using SSH instead of HTTPS

```bash
# If you prefer SSH authentication
git remote set-url origin git@github.com:<your-username>/cenomi-chatbot.git
```

---

## Project Status

**Iteration 1 — Scaffolding complete.** Core structure is in place. Components are being implemented incrementally.

- Single mall knowledge layer
- Tenant parameter system
- LangGraph state and node contracts
- Single-mall concierge runtime
- Chatbot testing UI
- Feedback system

---

## License

This project is proprietary. All rights reserved.
