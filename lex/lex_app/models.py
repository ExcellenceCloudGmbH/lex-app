"""Concrete model exports for the ``lex_app`` Django app.

Concrete models live in dedicated modules; this file simply re-exports
them so Django's app loader picks them up under app_label ``lex_app``.
"""
from lex.lex_app.scheduling import ScheduledCalculation  # noqa: F401

