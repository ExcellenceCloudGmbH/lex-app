from __future__ import absolute_import

import sys

_current_module = sys.modules[__name__]
if __name__ == "lex.lex_app":
    sys.modules.setdefault("lex_app", _current_module)
elif __name__ == "lex_app":
    sys.modules.setdefault("lex.lex_app", _current_module)

# This will make sure the app is always imported when
# Django starts so that shared_task will use this app.
from .celery import app as celery_app
