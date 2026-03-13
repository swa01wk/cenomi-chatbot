"""
Shared constants used across the application.

Naming convention: UPPER_SNAKE_CASE for true constants.
"""

# Session / conversation limits
MAX_CONVERSATION_TURNS = 50
MAX_MESSAGE_LENGTH = 4000

# Retrieval
DEFAULT_TOP_K = 5
DEFAULT_SIMILARITY_THRESHOLD = 0.75

# Mall intelligence layers
LAYER_CANONICAL = "canonical"
LAYER_SEMANTIC = "semantic"
LAYER_PLAYBOOK = "playbook"
LAYER_CONTEXT_PACK = "context_pack"

# Node names (LangGraph)
NODE_ROUTER = "router"
NODE_RETRIEVER = "retriever"
NODE_GENERATOR = "generator"
NODE_GUARDRAIL = "guardrail"

# Tenant configuration
TENANT_CONFIG_DIR = "data/tenant_config"
DEFAULT_TENANT_CONFIG_FILE = "tenant_defaults.json"

# Clarification policy hard limits (safety rails)
CLARIFICATION_MAX_FOLLOWUPS_ABSOLUTE = 5
AMBIGUITY_SCORE_FLOOR = 0.1
AMBIGUITY_SCORE_CEILING = 0.95

# Ranking bias bounds (prevent runaway boosts)
MAX_ENTITY_RANKING_BONUS = 1.0
MIN_ENTITY_RANKING_BONUS = -0.5
