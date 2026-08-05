#!/usr/bin/env python3
"""Publish a release note to quackback.

Not implemented. Only quackback's widget-token endpoint is visible from this
repository (`lex/lex_app/views.py:44`); whether it can ingest a changelog at
all is unconfirmed. This module exists so the call site in
publish_release_notes.yml is written once, against a stable signature, and a
follow-up spec fills in the body without touching anything upstream.

When implementing: the note passed here is the human-approved release body,
not the drafted one. Read it from the release at publish time.
"""

from __future__ import annotations


def publish(tag: str, body: str, *, token: str) -> None:
    """Publish `body` as the release note for `tag`."""
    raise NotImplementedError(
        "quackback publishing is not implemented — see "
        "docs/superpowers/specs/2026-08-05-release-notes-automation-design.md"
    )
