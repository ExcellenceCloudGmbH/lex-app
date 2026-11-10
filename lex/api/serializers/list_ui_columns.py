"""
Per-column list-UI options a project declares on its serializer.

WHY THIS EXISTS (LEX-1556, customer feedback). A customer asked for the grid's
ID column to be narrower, and the only honest answer was "resize it and save a
view" — which does not survive a read-only System View, has to be redone per
model, and says nothing about the many people who never read the identifier at
all. The frontend already treats three layers as the owners of how a table
looks::

    look-and-feel  -> the individual user   (their Settings panel, localStorage)
    table defaults -> the DEVELOPER         (this module, via the serializer)
    data shape     -> a saved view          (shared, explicitly saved)

The middle layer had exactly one setting (``hide_actions_column``) and no way
to say anything about a COLUMN. This adds that, as additive metadata on the
existing ``list_ui`` payload, so a project can write the answer down once::

    class FundSerializer(LexSerializer):
        class Meta:
            model = Fund
            fields = "__all__"
            lex_column_options = {
                "id": {"max_width": 90},          # cap the automatic width
                "internal_ref": {"hidden": True},  # never show it
                "name": {"width": 320, "pinned": "left"},
            }

NOTHING HERE MAY RAISE. It is read while answering ``model_info``, which the
whole frontend blocks on: a project that mistypes an option gets that option
ignored, not a grid that will not load. That is the single most important
property of this module, and why every value is validated rather than passed
through.
"""

#: Options a project may declare per column, and the type each must be.
#:
#: Deliberately small. Each one answers a question a project has actually been
#: asked -- how wide, how narrow, pinned where, shown at all -- and nothing
#: here lets a serializer reach into the grid's behaviour (sorting, filtering,
#: editability), which is decided by permissions and field types, not by
#: display preferences.
_INT_OPTIONS = ("width", "min_width", "max_width")
_BOOL_OPTIONS = ("hidden",)
_PINNED_VALUES = ("left", "right")


def _clean_column_options(raw) -> dict:
    """
    Validate one column's options, dropping anything unusable.

    A dropped option is silently absent rather than reported, because the
    consumer is a UI that must render regardless. The project sees the effect
    (their option did nothing) in the place they are already looking -- the
    grid -- which is a faster loop than an error buried in a JSON payload.
    """
    if not isinstance(raw, dict):
        return {}

    cleaned = {}

    for key in _INT_OPTIONS:
        value = raw.get(key)
        # ``bool`` is an ``int`` subclass in Python, and ``{"width": True}`` is
        # a typo, not a width of 1 pixel.
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if value <= 0:
            continue
        cleaned[key] = value

    for key in _BOOL_OPTIONS:
        if key in raw:
            cleaned[key] = bool(raw[key])

    pinned = raw.get("pinned")
    if isinstance(pinned, str) and pinned in _PINNED_VALUES:
        cleaned["pinned"] = pinned

    # A column cannot be narrower than it is wide. Rather than guess which the
    # project meant, drop the pair and leave the column to size itself -- a
    # grid that silently ignored one of them would be harder to debug than one
    # that ignored both.
    min_width = cleaned.get("min_width")
    max_width = cleaned.get("max_width")
    if min_width is not None and max_width is not None and min_width > max_width:
        cleaned.pop("min_width")
        cleaned.pop("max_width")

    return cleaned


def build_column_options(meta) -> dict:
    """
    The validated ``{field_name: options}`` map declared on a serializer Meta.

    Returns ``{}`` for a serializer that declares nothing, which is almost all
    of them -- the payload only grows for projects that asked for it.
    """
    declared = getattr(meta, "lex_column_options", None)
    if not isinstance(declared, dict):
        return {}

    options = {}
    for field_name, raw in declared.items():
        if not isinstance(field_name, str) or not field_name:
            continue
        cleaned = _clean_column_options(raw)
        # An entry that survived validation with nothing in it says nothing;
        # shipping it would make the frontend branch on an empty object.
        if cleaned:
            options[field_name] = cleaned

    return options
