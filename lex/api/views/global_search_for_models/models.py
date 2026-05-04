"""
Persistent search index used by :mod:`lex.api.views.global_search_for_models`.

A single denormalized table holds one row per indexed model instance.
The actual ``LexSearchDocument`` Django model lives in :mod:`lex.api.models`
so Django's app loader picks it up via ``lex.api``'s migrations directory;
this module just re-exports it for convenience.
"""

from lex.api.models import LexSearchDocument

__all__ = ["LexSearchDocument"]
