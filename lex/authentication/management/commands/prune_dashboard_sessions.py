"""``lex prune_dashboard_sessions``: delete dashboard hand-off rows that can never be used again.

Run daily, next to ``lex oidc_cleanup`` and ``lex clearsessions`` (the instance maintenance
CronJob does). It deletes:

* hand-off codes past their 60-second lifetime (used or not);
* revoked bindings;
* bindings whose sign-in has ended (``oidc_cleanup`` deletes ended sign-ins after a week, and the
  bindings with them; this removes them as soon as they are dead);
* embed bindings past the access token they were issued from;
* dashboard bindings whose handle cookie has expired in every browser (last set more than the
  cookie lifetime ago).

Nothing it deletes could still sign anything in, so it is safe at any time and on any schedule.
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from lex.authentication.dashboards.cookies import HANDLE_MAX_AGE


class Command(BaseCommand):
    help = (
        "Delete dashboard hand-off codes and bindings that can no longer be used "
        "(expired codes; revoked, ended, expired or long-unused bindings). Run it daily."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="count what would be deleted, delete nothing")

    def handle(self, *args, **options):
        from lex.authentication.models import DashboardBinding, DashboardHandoffCode

        now = timezone.now()
        codes = DashboardHandoffCode.objects.filter(expires_at__lt=now)
        bindings = DashboardBinding.objects.filter(
            Q(revoked_at__isnull=False)
            | Q(session__ended_at__isnull=False)
            | Q(expires_at__lt=now)
            | Q(cookie_set_at__lt=now - timedelta(seconds=HANDLE_MAX_AGE))
        )
        if options["dry_run"]:
            self.stdout.write(
                f"[dry-run] would delete {codes.count()} hand-off code(s) and {bindings.count()} binding(s)"
            )
            return
        deleted_codes, _ = codes.delete()
        deleted_bindings, _ = bindings.delete()
        self.stdout.write(f"deleted {deleted_codes} hand-off code(s) and {deleted_bindings} binding(s)")
