"""Guardrails — post-generation safety layer for the Cenomi concierge."""

from guardrails.hallucination_guard import (
    ValidationResult,
    Violation,
    validate_response,
)

__all__ = ["ValidationResult", "Violation", "validate_response"]
