"""Response composition layer — structured, concierge-style output."""

from response.concierge_composer import (
    ConciergeComposer,
    ResponseBlueprint,
    Section,
    StorePick,
    build_composer_prompt,
)

__all__ = [
    "ConciergeComposer",
    "ResponseBlueprint",
    "Section",
    "StorePick",
    "build_composer_prompt",
]
