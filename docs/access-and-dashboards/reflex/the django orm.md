---
title: The Django ORM
---

A Reflex dashboard reads and writes the project's models with the Django ORM — the same models, the same database, the same lifecycle hooks as the rest of Lex App. `lex reflex` sets Django up before it builds the app, so there is nothing to configure. There is one thing to know.

## Why a plain query fails

Reflex runs every event handler on its event loop, and Django refuses to run its synchronous ORM there: a query made on the loop would stall every other user's events while it waited for the database. So this handler fails the first time it runs:

```python
class FundTable(rx.State):
    total: int = 0

    @rx.event
    async def load(self):
        self.total = Fund.objects.count()   # ← SynchronousOnlyOperation
```

with `SynchronousOnlyOperation: You cannot call this from an async context - use a thread or sync_to_async.`

A Streamlit dashboard never meets this: a Streamlit script runs on a thread of its own. It is the one difference in how the two reach the database, and there are two ways across it.

## Queries: Django's async ORM

For reading and simple writes, use Django's own [asynchronous ORM](https://docs.djangoproject.com/en/stable/topics/async/#queries-the-orm) — the `a`-prefixed methods and `async for`:

```python
import reflex as rx

from Input.Fund import Fund


class FundTable(rx.State):
    total: int = 0
    names: list[str] = []

    @rx.event
    async def load(self):
        self.total = await Fund.objects.acount()
        self.names = [fund.name async for fund in Fund.objects.order_by("name")]
```

| Synchronous | Asynchronous |
|---|---|
| `Fund.objects.count()` | `await Fund.objects.acount()` |
| `Fund.objects.get(pk=pk)` | `await Fund.objects.aget(pk=pk)` |
| `Fund.objects.filter(...).first()` | `await Fund.objects.filter(...).afirst()` |
| `for fund in queryset:` | `async for fund in queryset:` |
| `Fund.objects.create(...)` | `await Fund.objects.acreate(...)` |
| `fund.save()` | `await fund.asave()` |

## Anything synchronous: `run_orm`

Everything else — a model method, `save()` and the [[model-your-data/lifecycle hooks|lifecycle hooks]] it runs, a calculation, a transaction, a helper you already wrote for Streamlit — goes through `run_orm`. It runs the function on Django's own executor, the thread the async ORM uses, and hands back what it returns:

```python
import reflex as rx
from django.db.models import Sum

from Input.Fund import Fund
from lex.lex_app.reflex import run_orm


def fund_totals() -> list[dict]:
    return list(Fund.objects.values("name").annotate(total=Sum("positions__value")))


class FundTable(rx.State):
    totals: list[dict] = []

    @rx.event
    async def load(self):
        self.totals = await run_orm(fund_totals)
```

`run_orm(fn, *args, **kwargs)` passes the arguments on, so `await run_orm(fund.revalue, as_of)` works too. An exception raised inside `fn` reaches the handler unchanged.

The decorator form, `@orm`, turns a synchronous function into one you `await`:

```python
import reflex as rx
from django.db.models import Sum

from Input.Fund import Fund
from lex.lex_app.reflex import orm


@orm
def fund_totals() -> list[dict]:
    return list(Fund.objects.values("name").annotate(total=Sum("positions__value")))


class FundTable(rx.State):
    totals: list[dict] = []

    @rx.event
    async def load(self):
        self.totals = await fund_totals()
```

Two rules for the function you hand over:

- **Keep a transaction inside one call.** `transaction.atomic()` belongs inside `fn`: one unit of work, one `run_orm`.
- **Return the result; do not touch the state inside `fn`.** `fn` runs on another thread and the state belongs to the event — assign what `fn` returns in the handler, as the examples do.

## One thread, so keep it short

`run_orm` and Django's async ORM both run on **one** thread per Reflex worker, one call at a time. That is what makes them safe for Django, and it means a call that takes a minute keeps every other user of that worker waiting for a minute. Keep each call to the queries a page needs. A long calculation belongs on a [[calculations/celery and async calculations|Celery worker]], with the dashboard showing its progress, not in a handler.

## Connections are looked after

A Django request retires a database connection that outlived `CONN_MAX_AGE`, or that the database dropped, at its start and its end. Nothing does that in a long-running Reflex worker on its own, so a single dropped connection would fail every later query until the process restarted. Lex App does it for you around every event and every `run_orm` call — never inside an open transaction — so a dashboard survives a database restart the way the rest of the app does.

## `DJANGO_ALLOW_ASYNC_UNSAFE`

Django's escape hatch: with `DJANGO_ALLOW_ASYNC_UNSAFE=true`, synchronous queries are allowed on the event loop. Every query then blocks every other user's events while it waits, which is the stall Django's refusal exists to prevent. Keep it for a quick experiment; a dashboard that needs it is one `run_orm` away from not needing it. When it is set, Lex App retires the event loop's own stale connections after each event too.

## Related

- [[access-and-dashboards/reflex/dashboards on your models|Dashboards on Your Models]] — `current_record`, which reads a record through the ORM for you
- [[access-and-dashboards/reflex/sessions and authentication|Sessions & Authentication]] — queries run as the Reflex process, not as the user; what to check before showing data
- [[model-your-data/index|Model Your Data]] — the models themselves
