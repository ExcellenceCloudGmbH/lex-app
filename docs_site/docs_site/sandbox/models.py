"""The docs' sample models.

Plain classes, not Django models: lex.ui reads only ``_meta.app_label`` and the class name (a
grant's resource, ``docs.Fund``) and ``_meta.model_name`` (an API id). ``fields`` and
``search_field`` are the sandbox's own: what a row holds, and what a search matches.
"""

from types import SimpleNamespace


class Fund:
    _meta = SimpleNamespace(app_label="docs", model_name="fund")
    fields = ("id", "name", "vintage", "status", "size", "currency")
    search_field = "name"


class Investor:
    _meta = SimpleNamespace(app_label="docs", model_name="investor")
    fields = ("id", "name", "country", "kind")
    search_field = "name"


class Commitment:
    _meta = SimpleNamespace(app_label="docs", model_name="commitment")
    fields = ("id", "fund", "investor", "amount", "signed_on")
    search_field = ""


MODELS: dict[str, type] = {model.__name__: model for model in (Fund, Investor, Commitment)}
