"""The Reflex app ``lex reflex`` runs -- the counterpart of ``streamlit_app.py``.

Reflex imports this module in every process that compiles or serves the app
(``app_module_import`` in the project's ``rxconfig.py`` points here), and none
of those processes has run the ``lex`` CLI's Django bootstrap. So the order
below is the whole point of the file: Django is set up before anything imports
a model, and only then is the app built -- from the project's models and its
``_reflex_structure.py``.
"""

from lex.tools.django_bootstrap import setup_django

setup_django()

from lex.lex_app.reflex.app import create_app  # noqa: E402  (Django must be ready first)

app = create_app()
