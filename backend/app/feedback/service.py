"""
Feedback service — re-exports from the canonical service location.

The full implementation lives in app.services.feedback_service.
This module exists for backward compatibility with imports that
referenced app.feedback.service.
"""

from app.services.feedback_service import FeedbackService

__all__ = ["FeedbackService"]
