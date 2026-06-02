"""CI self-test cluster — intentionally and always fails.

This cluster exists ONLY to exercise the Prerelease Gate's RED path
(``.github/workflows/prerelease_gate.yml``): when it is included in a
gate run, the gate goes red and the ``cleanup-on-red`` job deletes the
prerelease and its tag. That is the whole point — it lets you prove the
auto-cleanup works without having to break a real test.

It is **excluded from the default release cluster selector**, so normal
releases never run it. The runner only executes a cluster folder when its
key is passed via ``run_showcase_suite.py --only``, and the prerelease
gate only appends ``gate_selftest`` when you opt in by putting
``[gate-selftest]`` in the prerelease title or notes. The filename has no
numeric ``test_<N>x_`` prefix on purpose, so copilot_pr_gate's new-test
detection never picks it up either.

If you ever see this failure in a real release gate, someone included
``[gate-selftest]`` (or selected the cluster) by mistake — it is not a
product regression.
"""

from django.test import SimpleTestCase


class TestGateSelfTest(SimpleTestCase):
    def test_gate_selftest_always_fails(self):
        self.fail(
            "Intentional failure: the gate_selftest cluster is a CI "
            "self-test for the Prerelease Gate red path. Seeing this means "
            "the failing cluster was deliberately included — the gate is "
            "expected to go red and auto-delete the prerelease + tag."
        )
