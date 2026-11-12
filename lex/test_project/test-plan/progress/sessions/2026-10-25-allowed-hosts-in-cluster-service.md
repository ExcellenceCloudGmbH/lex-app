---
date: 2026-10-25
clusters: [1]
tests_added: "5 new scenarios (1.410-1.414)"
suite_tally: "init +5 (batch 1ay, 5 pass / 0 fail); no other cluster touched"
---

# Batch 1ay — `ALLOWED_HOSTS` accepts the instance's own backend Service name

See [1ay](../../clusters/01-init/batches.md). Found from production rather than from reading: on a
customer instance every service-to-service file write failed with `OSError` while the browser-facing
surface kept working, and the backend was logging `Invalid HTTP_HOST header:
'lex-backend-armira-self-servi-prod-1492:7000'`. The API key was registered and valid and the route
was correct — Django was refusing the request in `CommonMiddleware`, before any view, permission
class or model code ran.

The gap was structural rather than local to one instance: `ALLOWED_HOSTS` held `DOMAIN_HOSTED`, the
loopback pair and `POD_IP`, and none of those is how one pod addresses another. `POD_IP` looks like
it should cover it and does not — it is the reading pod's own address, so it lets a caller reach
*that* pod by IP and says nothing about reaching the backend by name, which a caller in another pod
has no way to learn. Any lex instance whose pods talk to each other hits this.

The namespace is read from the mounted serviceaccount file rather than an environment variable,
because `POD_NAMESPACE` is not set in the platform's pods. That choice is deliberate beyond
convenience: `settings.py` already refuses to start when `DOMAIN_HOSTED` is missing, so a second
required variable would have turned a missing value into a crash-loop instead of a fix.

The downstream workaround that found this — presenting `DOMAIN_HOSTED` as an explicit `Host:` from
the calling side — stays correct and compatible once this lands; it simply stops being necessary.
