"""Tests for how send_showcase_email resolves its configuration.

The bug these exist for: a workflow that passes an unset secret or variable
sets the environment key to the EMPTY STRING, not to nothing. So
`os.environ.get(name, default)` finds the key, returns "", and the default
never applies — which is how every showcase report came to be sent with a
blank sender name while the code appeared to have a sensible fallback.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "send_showcase_email.py"


def _module():
    spec = importlib.util.spec_from_file_location("send_showcase_email", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["send_showcase_email"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name,default", [
    ("SHOWCASE_REPORT_FROM_NAME", "Platform Health"),
    ("SHOWCASE_BRAND", "Excellence Cloud"),
])
def test_an_empty_value_falls_back_to_the_default(name, default, monkeypatch):
    monkeypatch.setenv(name, "")          # exactly what an unset secret yields
    source = SCRIPT.read_text()
    # Pinned on the source rather than by running main(), which would need
    # SendGrid: the distinction being protected is `or` versus a get() default,
    # and that is visible here without a network call.
    assert f'os.environ.get("{name}") or "{default}"' in source, (
        f"{name} must use `or`, not a get() default — an unset secret arrives "
        "as an empty string and a get() default never fires"
    )


def test_no_setting_relies_on_a_non_empty_get_default():
    """A drift guard: the same mistake must not come back elsewhere.

    Only a NON-EMPTY default is a problem. `get(NAME, "")` is fine and is used
    deliberately for required settings — empty then means missing, and the
    missing-config check reports it.
    """
    import re

    # Whitespace-normalised, because the original bug spanned TWO lines:
    #     os.environ.get("SHOWCASE_REPORT_FROM_NAME",
    #                    "Platform Health")
    # A line-oriented or raw-source check would have a hole exactly where the
    # bug actually lived.
    source = " ".join(SCRIPT.read_text().split())
    pattern = re.compile(r'os\.environ\.get\( *"[A-Z_]+" *, *"[^"]+"')
    offenders = [m.group(0) for m in pattern.finditer(source)]
    assert not offenders, (
        "these fall back with a get() default, which an unset secret defeats "
        f"because it arrives as an empty string — use `or` instead: {offenders}"
    )
