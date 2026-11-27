"""The docs' sample models.

Plain classes, not Django models: lex.ui reads only ``_meta.app_label`` and the class name (a
grant's resource, ``docs.Fund``), ``_meta.model_name`` (an API id), and ``_meta.pk`` and
``_meta.verbose_name_plural`` (``self.api.all``'s order and its refusal). ``fields`` and
``search_field`` are the sandbox's own: what a row holds, and what a search matches.
"""

from types import SimpleNamespace

_PK = SimpleNamespace(name="id")


class Fund:
    _meta = SimpleNamespace(app_label="docs", model_name="fund", pk=_PK, verbose_name_plural="funds")
    fields = ("id", "name", "vintage", "status", "size", "currency")
    search_field = "name"


class Investor:
    _meta = SimpleNamespace(app_label="docs", model_name="investor", pk=_PK, verbose_name_plural="investors")
    fields = ("id", "name", "country", "kind")
    search_field = "name"


class Commitment:
    _meta = SimpleNamespace(app_label="docs", model_name="commitment", pk=_PK, verbose_name_plural="commitments")
    fields = ("id", "fund", "investor", "amount", "signed_on")
    search_field = ""


MODELS: dict[str, type] = {model.__name__: model for model in (Fund, Investor, Commitment)}
