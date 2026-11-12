"""Scheduled jobs for lex instances.

Schedules are ``ScheduledJob`` rows in the instance's own database
(:mod:`lex.scheduling.models`); the served backend's clock fires them
(:mod:`lex.scheduling.loop`, :mod:`lex.scheduling.dispatch`). Register a function
schedules may run, optionally with a schedule of its own::

    from lex.scheduling import lex_job

    @lex_job("send_daily_digest", schedule="0 7 * * 1-5")
    def send_daily_digest():
        ...

Design: docs/superpowers/plans/2026-11-10-scheduled-jobs.md
"""

from lex.scheduling.registry import lex_job

__all__ = ["lex_job"]
