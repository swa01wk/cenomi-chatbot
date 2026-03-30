"""
Intent utilities for the Cenomi concierge.

Classification is handled entirely by the LLM in interpret_turn.py.
This package exposes canonical example mappings and validation helpers only.
"""

from intent.intent_parser import (
    EXAMPLE_MAPPINGS,
    MALL_INFO_EXAMPLES,
    validate_intent,
)

__all__ = [
    "EXAMPLE_MAPPINGS",
    "MALL_INFO_EXAMPLES",
    "validate_intent",
]
