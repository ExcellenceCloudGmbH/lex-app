---
date: 2026-07-16
clusters: [12j]
tests_added: "3 (12.46–12.48)"
suite_tally: "12j 3 pass / 0 fail; regression: serializers+history+audit_logging+crud_api = 277 pass / 1 skip / 3 xfail / 0 fail"
---

**Batch 12j landed — env-gated restore of the pre-rc212 naive-datetime
convention.** Customer ticket 2026-07-16 (output_date values shifted 1h,
equality filters broken, revert shifts the other way): root cause is
v2.0.0rc212 (f622c9c, #635), which flipped TIME_ZONE Berlin→UTC on
USE_TZ=False targets to satisfy django_celery_beat's naive==UTC assumption —
but TIME_ZONE is also the PostgreSQL connection timezone, so the change
silently reinterpreted every stored naive timestamp. With celery-beat being
retired (supervisor + global scheduler), the constraint disappears:
`LEX_TIME_ZONE=Europe/Berlin` restores the original convention per instance,
default unchanged (UTC). Truthfulness pieces in the same change:
`LexAwareDateTimeField` renders naive values with the real DST-aware offset
(a static 'Z' would mislabel Berlin wall-clock), wired into LexSerializer and
the calculation-log serializer; `parse_as_of_datetime` normalizes anchors to
the instance's convention. No data migration shipped — rows written during an
instance's UTC era are a bounded set handled operationally at flip time
(documented in the incident report). Frontend needs no change (ISO offsets
parse identically to 'Z'). See [batch 12j](../../clusters/12-serializers/batches.md).
