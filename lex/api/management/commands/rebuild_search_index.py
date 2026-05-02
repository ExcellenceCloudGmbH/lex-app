"""
Backfill / repair the global-search ``LexSearchDocument`` index.

Examples::

    # Full rebuild (drops and repopulates every container).
    lex rebuild_search_index

    # Single container (matches the URL slug, e.g. ``period``).
    lex rebuild_search_index --model period

    # Bigger batches for very large tables.
    lex rebuild_search_index --batch 5000

    # Skip a few notoriously slow models for now.
    lex rebuild_search_index --skip foo,bar

    # Cap rows per table (useful for smoke-testing).
    lex rebuild_search_index --max-rows 1000
"""

from __future__ import annotations

import sys
import time
from typing import Iterable

from django.core.management.base import BaseCommand
from django.db import transaction

from lex.api.models import LexSearchDocument
from lex.api.views.global_search_for_models.indexer import build_document
from lex.process_admin.settings import processAdminSite


# Per-model wall-clock cap. If a single model takes longer than this we
# log the slowness and move on so one bad table can't wedge the whole
# rebuild.
DEFAULT_PER_MODEL_TIMEOUT = 120.0


class Command(BaseCommand):
    help = "Rebuild the lex_search_document index from live model data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--model",
            dest="container_id",
            help="Restrict the rebuild to a single container id (URL slug).",
        )
        parser.add_argument(
            "--batch",
            type=int,
            default=1000,
            help="Iterator chunk size when streaming rows (default: 1000).",
        )
        parser.add_argument(
            "--keep",
            action="store_true",
            help=(
                "Don't delete existing index rows before rebuilding "
                "(use when bulk-adding rows for a new model)."
            ),
        )
        parser.add_argument(
            "--skip",
            default="",
            help="Comma-separated container ids to skip.",
        )
        parser.add_argument(
            "--max-rows",
            type=int,
            default=0,
            help="Stop after this many rows per model (0 = no cap).",
        )
        parser.add_argument(
            "--per-model-timeout",
            type=float,
            default=DEFAULT_PER_MODEL_TIMEOUT,
            help=(
                "Skip a model after this many wall-clock seconds "
                f"(default: {DEFAULT_PER_MODEL_TIMEOUT:.0f})."
            ),
        )
        parser.add_argument(
            "--include-history",
            action="store_true",
            help=(
                "Also index ``historical*`` / ``metahistorical*`` audit "
                "containers (default: skip — they're append-only audit "
                "trails, not user-facing)."
            ),
        )

    # ------------------------------------------------------------------

    def _say(self, msg: str) -> None:
        # Print + flush so progress shows up immediately under tail/`time`.
        self.stdout.write(msg)
        try:
            self.stdout.flush()
        except Exception:
            pass
        try:
            sys.stdout.flush()
        except Exception:
            pass

    def _bulk_index(self, container_hits: Iterable[dict]) -> int:
        """Insert one batch of pre-built doc dicts. Returns count written."""
        rows = [
            LexSearchDocument(
                container_id=d["container_id"],
                object_id=d["object_id"],
                content_type=d["content_type"],
                model_label=d["model_label"],
                title=d["title"],
                body=d["body"],
                url=d["url"],
            )
            for d in container_hits
        ]
        if not rows:
            return 0
        # ``ignore_conflicts`` makes this idempotent against the
        # (container_id, object_id) unique constraint when --keep is
        # set or when reconciling.
        LexSearchDocument.objects.bulk_create(
            rows, batch_size=len(rows), ignore_conflicts=True
        )
        return len(rows)

    # ------------------------------------------------------------------

    def handle(
        self,
        *args,
        container_id=None,
        batch=1000,
        keep=False,
        skip="",
        max_rows=0,
        per_model_timeout=DEFAULT_PER_MODEL_TIMEOUT,
        include_history=False,
        **opts,
    ):
        # Force the model collection to materialise.
        if not processAdminSite.initialized:
            _ = processAdminSite.urls

        skip_set = {s.strip() for s in (skip or "").split(",") if s.strip()}
        # Always skip the search index itself + its (auto-generated)
        # historical model so a misconfiguration can't cause an
        # infinite-recursion / "table doesn't exist" loop.
        skip_set.update({"lexsearchdocument", "historicallexsearchdocument"})
        containers = list(processAdminSite.model_collection.all_containers)
        if container_id:
            containers = [c for c in containers if getattr(c, "id", None) == container_id]
            if not containers:
                self.stderr.write(self.style.ERROR(f"Unknown container '{container_id}'"))
                return
        elif not include_history:
            # Audit-trail containers ship with every history-tracked
            # model and dwarf the live data while serving no
            # user-facing search use case. Keep them out by default.
            before = len(containers)
            containers = [
                c for c in containers
                if not (getattr(c, "id", "") or "").startswith(("historical", "metahistorical"))
            ]
            dropped = before - len(containers)
            if dropped:
                self._say(
                    f"Skipping {dropped} historical/metahistorical container(s) "
                    f"(pass --include-history to index them)."
                )

        total_indexed = 0
        total_skipped_models = 0
        rebuild_started = time.monotonic()
        self._say(f"Rebuilding search index across {len(containers)} container(s)…")

        for idx, container in enumerate(containers, start=1):
            cid = getattr(container, "id", None)
            model_class = getattr(container, "model_class", None)
            label = f"[{idx}/{len(containers)}] {cid}"

            if not cid or model_class is None:
                self._say(f"  {label}: skipped (no model_class)")
                total_skipped_models += 1
                continue
            if cid in skip_set:
                self._say(f"  {label}: skipped (in --skip list)")
                total_skipped_models += 1
                continue

            self._say(f"  {label}: starting…")

            if not keep:
                try:
                    deleted, _ = LexSearchDocument.objects.filter(container_id=cid).delete()
                    if deleted:
                        self._say(f"    cleared {deleted} existing rows")
                except Exception as exc:
                    self._say(f"    WARN: could not clear existing rows: {exc}")

            t0 = time.monotonic()
            n_built = 0
            n_written = 0
            n_failed = 0
            buffer: list[dict] = []
            timed_out = False

            try:
                # ``.only()`` would be ideal but we don't know the
                # searchable field set generically — rely on the chunked
                # iterator to keep memory bounded.
                qs = model_class._default_manager.all().iterator(chunk_size=batch)
            except Exception as exc:
                self._say(f"    WARN: skipped ({exc})")
                total_skipped_models += 1
                continue

            for instance in qs:
                if time.monotonic() - t0 > per_model_timeout:
                    timed_out = True
                    break
                try:
                    doc = build_document(instance)
                except Exception as exc:
                    n_failed += 1
                    if n_failed <= 3:
                        self._say(
                            f"    skip {cid}#{getattr(instance, 'pk', '?')}: {exc}"
                        )
                    continue
                if doc is None:
                    continue
                buffer.append(doc)
                n_built += 1

                if len(buffer) >= batch:
                    try:
                        with transaction.atomic():
                            n_written += self._bulk_index(buffer)
                    except Exception as exc:
                        self._say(f"    WARN: bulk insert failed ({exc})")
                    buffer.clear()
                    self._say(f"    progress: {n_built} built, {n_written} written")

                if max_rows and n_built >= max_rows:
                    self._say(f"    hit --max-rows={max_rows}, stopping early")
                    break

            if buffer:
                try:
                    with transaction.atomic():
                        n_written += self._bulk_index(buffer)
                except Exception as exc:
                    self._say(f"    WARN: final bulk insert failed ({exc})")
                buffer.clear()

            elapsed = time.monotonic() - t0
            total_indexed += n_written
            tag = " (timeout)" if timed_out else ""
            extra = f", {n_failed} failed" if n_failed else ""
            self._say(
                f"  {label}: indexed {n_written}/{n_built} rows in "
                f"{elapsed:.1f}s{extra}{tag}"
            )

        wall = time.monotonic() - rebuild_started
        self._say(
            self.style.SUCCESS(
                f"Done — {total_indexed} documents indexed across "
                f"{len(containers) - total_skipped_models} model(s) in {wall:.1f}s."
            )
        )
