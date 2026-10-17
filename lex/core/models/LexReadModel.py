"""Base class of read models over a database view (see :mod:`lex.core.read_models`)."""
from lex.core.models.LexModel import LexModel


class LexReadModel(LexModel):
    """A model whose rows are a database view: no audit columns of its own, nothing to write, no history.

    Subclasses set ``Meta.managed = False`` and create the view with
    :class:`lex.core.read_models.CreateView` in a migration. A projection of an
    existing table (``Meta.proxy = True``) keeps its base model and sets
    ``lex_read_model = True`` instead.
    """

    lex_read_model = True

    # A view has no columns of lex's own; the rows it shows carry their own.
    created_at = None
    edited_at = None
    created_by = None
    edited_by = None

    class Meta:
        abstract = True
