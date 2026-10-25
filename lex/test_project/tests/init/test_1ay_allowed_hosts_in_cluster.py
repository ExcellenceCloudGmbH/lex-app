"""`ALLOWED_HOSTS` accepts the instance's own backend Service name from inside the cluster.

Intent: a lex instance is several pods sharing one database, and the pods that are not the backend
reach it by its Kubernetes Service name (`lex-backend-<instance>:7000`) so the traffic stays inside
the cluster, off the ingress and off TLS. Django compares the `Host:` header against
`ALLOWED_HOSTS` in `CommonMiddleware`, BEFORE any view, permission class or model code runs, so a
Service name missing from that list is not a permission failure or a routing failure - it is a
`DisallowedHost` with nothing downstream reached. A regression here silently breaks every
service-to-service call an instance makes while leaving the browser-facing surface working, which
is exactly how it was found: on a customer instance on 2026-10-25 every file write failed with
`OSError` and the backend logged `Invalid HTTP_HOST header:
'lex-backend-armira-self-servi-prod-1492:7000'`, with a valid registered API key and a correct
route.

Cluster 1ay — scenarios 1.410–1.414. Type: U.
Covers: lex/lex_app/settings.py (`_k8s_namespace`, `_cluster_service_hosts`, `ALLOWED_HOSTS`).
Run: python -m lex pytest lex/test_project/tests/init/test_1ay_allowed_hosts_in_cluster.py -v
"""

import pytest
from django.test import SimpleTestCase

from lex.lex_app.settings import (
    BACKEND_SERVICE_PREFIX,
    _cluster_service_hosts,
    _k8s_namespace,
)

pytestmark = pytest.mark.init

#: A realistic identifier, in the platform's own shape (it is not a hostname and has no dots).
INSTANCE = "armira-self-servi-prod-1492"


class TestCluster1ay_AllowedHostsAcceptsTheInClusterService(SimpleTestCase):
    """Cluster 1ay: the backend accepts its own Service name, and adds nothing off-cluster."""

    def test_1_410_the_short_service_name_is_accepted(self):
        """
        Scenario 1.410: the Service name a same-namespace caller dials is allowed.
        Given: an instance identifier and a namespace
        When: the in-cluster hosts are derived
        Then: `lex-backend-<instance>` is among them
        """
        hosts = _cluster_service_hosts(INSTANCE, "default")

        self.assertIn(
            f"{BACKEND_SERVICE_PREFIX}{INSTANCE}",
            hosts,
            "the short Service name is exactly what a same-namespace caller puts in Host:, and "
            "without it Django answers DisallowedHost before any view runs",
        )

    def test_1_411_the_three_longer_dns_forms_are_accepted(self):
        """
        Scenario 1.411: a caller using any Kubernetes DNS form is allowed.
        Given: an instance identifier and a namespace
        When: the in-cluster hosts are derived
        Then: the `.<ns>`, `.<ns>.svc` and `.<ns>.svc.cluster.local` forms are all present
        """
        hosts = _cluster_service_hosts(INSTANCE, "default")
        short = f"{BACKEND_SERVICE_PREFIX}{INSTANCE}"

        for form in (
            f"{short}.default",
            f"{short}.default.svc",
            f"{short}.default.svc.cluster.local",
        ):
            self.assertIn(
                form,
                hosts,
                f"{form} is a DNS name the same Service answers to; a caller that resolves it "
                "that way sends it verbatim as Host:, and a missing entry refuses the request",
            )

    def test_1_412_nothing_is_added_without_an_instance_identifier(self):
        """
        Scenario 1.412: a developer's machine gains no entries.
        Given: no instance identifier, which is what a local checkout has
        When: the in-cluster hosts are derived
        Then: the list is empty, whatever the namespace says
        """
        self.assertEqual(
            [],
            _cluster_service_hosts("", "default"),
            "outside a deployment there is no Service to accept, and widening ALLOWED_HOSTS "
            "locally would weaken a security setting for no benefit",
        )

    def test_1_413_an_unreadable_namespace_degrades_to_the_short_name(self):
        """
        Scenario 1.413: a missing namespace file is not an error.
        Given: the namespace could not be read, so it is empty
        When: the in-cluster hosts are derived
        Then: exactly the short Service name is returned, and nothing raises

        The short name is still correct for a same-namespace caller, which is the only caller
        that can use a short name at all. Raising here would take the whole process down at
        import time over a file that is absent only outside a cluster.
        """
        hosts = _cluster_service_hosts(INSTANCE, "")

        self.assertEqual(
            [f"{BACKEND_SERVICE_PREFIX}{INSTANCE}"],
            hosts,
            "with no namespace the longer DNS forms cannot be built, but the short one can and "
            "is the form a same-namespace caller uses",
        )

    def test_1_414_an_absent_namespace_file_reads_as_not_in_a_cluster(self):
        """
        Scenario 1.414: `_k8s_namespace` answers "" rather than raising, off-cluster.
        Given: a path that does not exist, as on a developer's machine
        When: the namespace is read
        Then: it is the empty string and no exception escapes

        Settings are imported before anything can catch an error here, so an OSError reaching the
        caller would stop the process from starting at all.
        """
        self.assertEqual(
            "",
            _k8s_namespace("/nonexistent/serviceaccount/namespace"),
            "an unreadable namespace file means 'not in a cluster', which is a normal state and "
            "must not raise during settings import",
        )
