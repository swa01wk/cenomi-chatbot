# Folder Structure

## Root

```
cenomi-chatbot/
├── backend/                 Python concierge engine
├── frontend/                React testing console
├── docs/                    Architecture and planning docs
├── .env.example             Root-level environment template
├── .gitignore               Git ignore rules
└── README.md                Project overview
```

## Backend

```
backend/
├── app/
│   ├── api/                 FastAPI route handlers
│   │   ├── health.py        Health check endpoint
│   │   ├── chat.py          Chat endpoint (main concierge interface)
│   │   └── feedback.py      Feedback submission endpoint
│   │
│   ├── graph/               LangGraph pipeline definition
│   │   └── builder.py       Graph construction and compilation
│   │
│   ├── nodes/               Individual graph node implementations
│   │   ├── router.py        Intent classification + routing
│   │   ├── retriever.py     Multi-layer context retrieval
│   │   ├── generator.py     Grounded response generation
│   │   └── guardrail.py     Response validation + safety
│   │
│   ├── services/            Business logic orchestration
│   │   └── concierge.py     Chat turn orchestration
│   │
│   ├── models/              Pydantic data models
│   │   ├── api.py           API request/response contracts
│   │   ├── state.py         LangGraph pipeline state
│   │   ├── tenant.py        Canonical entities + tunable params
│   │   ├── mall.py          Mall profile, semantic entries, playbooks
│   │   └── feedback.py      Feedback records
│   │
│   ├── config/              Application configuration
│   │   ├── settings.py      Environment-driven settings
│   │   └── constants.py     Shared constants
│   │
│   ├── prompts/             LLM prompt construction
│   │   └── builder.py       Prompt assembly from templates + context
│   │
│   ├── context/             Mall context loading and assembly
│   │   └── mall_context.py  Context pack builder
│   │
│   ├── retrieval/           Search and retrieval services
│   │   └── retriever.py     Multi-layer mall intelligence search
│   │
│   ├── observability/       Logging, tracing, debugging
│   │   └── logger.py        Structured logging + debug tracer
│   │
│   ├── feedback/            Feedback processing
│   │   └── service.py       Feedback storage and analysis
│   │
│   └── utils/               Shared utilities
│       └── ids.py           ID generation helpers
│
├── tests/                   Test suite
├── scripts/                 Dev and ops scripts
│
├── data/
│   ├── raw/                 Unprocessed source data
│   ├── canonical/           Normalized tenant entities
│   ├── semantic/            Derived semantic intelligence
│   ├── playbooks/           Scenario playbooks
│   ├── context_packs/       Pre-assembled context bundles
│   └── examples/            Sample data for development
│
├── pyproject.toml           Python project configuration
└── .env.example             Backend environment template
```

## Frontend

```
frontend/
├── src/
│   ├── components/          Reusable UI components
│   │   ├── ChatMessage.tsx  Single message bubble
│   │   ├── ChatInput.tsx    Message input area
│   │   ├── DebugPanel.tsx   Pipeline debug viewer
│   │   └── FeedbackWidget.tsx  Rating widget
│   │
│   ├── pages/               Page-level components
│   │   └── ChatPage.tsx     Main chat interface
│   │
│   ├── hooks/               Custom React hooks
│   │   └── useChat.ts       Chat state management
│   │
│   ├── api/                 Backend API client
│   │   └── client.ts        HTTP client for all endpoints
│   │
│   ├── lib/                 Shared utilities and constants
│   │   └── constants.ts     App-wide constants
│   │
│   ├── types/               TypeScript type definitions
│   │   ├── api.ts           API contract types
│   │   └── chat.ts          Chat domain types
│   │
│   ├── mock/                Mock data for offline development
│   │   └── responses.ts     Sample API responses
│   │
│   ├── styles/              Global styles
│   │   └── index.css        Tailwind imports
│   │
│   ├── App.tsx              Root component
│   └── main.tsx             Entry point
│
├── index.html               HTML shell
├── vite.config.ts           Vite + Tailwind configuration
├── package.json             Node dependencies
└── .env.example             Frontend environment template
```

## Conventions

| Concern | Location |
|---------|----------|
| API contracts | `backend/app/models/api.py` + `frontend/src/types/api.ts` |
| Pipeline state | `backend/app/models/state.py` |
| Domain models | `backend/app/models/*.py` |
| Configuration | `backend/app/config/settings.py` via `.env` |
| Mall data | `backend/data/` (structured by intelligence layer) |
| Graph nodes | `backend/app/nodes/` (one file per node) |
| Prompts | `backend/app/prompts/` |
