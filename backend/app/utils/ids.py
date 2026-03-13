"""Utility functions for ID generation and session management."""

from uuid import uuid4


def generate_session_id() -> str:
    return f"session-{uuid4().hex[:16]}"


def generate_message_id() -> str:
    return f"msg-{uuid4().hex[:12]}"


def generate_feedback_id() -> str:
    return f"fb-{uuid4().hex[:12]}"
