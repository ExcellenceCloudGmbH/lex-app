"""Cluster 1ay: an instance answers on every hostname it is reached on, and
frames its dashboards from the one that shares the viewer's registrable domain.

Intent
------
An instance used to have exactly one hostname per role: ``DOMAIN_HOSTED`` for
the app, ``STREAMLIT_URL`` and ``REFLEX_URL`` for its dashboards. Customer-owned
domains ended that. The Instance Controller attaches ``app.customer.com`` to an
instance that is still reachable at ``<sub>.lit.<platform domain>``, routes both
to the same pods, and -- once the customer promotes it -- makes the customer's
the canonical one. From the pods' point of view there is now a *set* of
hostnames, and two separate things have to follow.

**Every one of them has to be accepted.** Django answers a request whose
``Host`` is not in ``ALLOWED_HOSTS`` with "400 Bad Request", and
``AllowedHostsOriginValidator`` refuses the WebSocket on the same grounds. A
hostname the ingress routes and the process rejects is not an attached domain;
it is a domain that resolves and fails. The same is true one layer up: the
Streamlit proxy builds its OIDC ``redirect_uri`` from a host, and building it
from the *canonical* host while the user is on another one sets the session
cookie on a host the user is not on -- a sign-in that loops forever.

**The right one has to be framed.** A dashboard is shown in an ``<iframe>``.
When its origin is on a different registrable domain from the page embedding
it, the frame is a third-party context: Safari blocks its cookies outright and
Chrome is removing them, so the dashboard cannot hold the session it just
established. Framing ``<sub>-streamlit.lit.<platform domain>`` inside
``app.customer.com`` is exactly that, and the attached
``streamlit.app.customer.com`` is exactly the way out of it.

Both halves fail silently and look like something else -- a 400 nobody
attributes to DNS, a dashboard that works in Chrome and not in Safari -- which
is why they are pinned here rather than left to integration testing.

The whole mechanism degrades to the single canonical host when the environment
carries no host list, which is every instance without a custom domain and every
local run. That fallback is itself a scenario below: it is what keeps this
change invisible to the installations that do not use the feature.

Cluster 1ay -- scenarios 1.410-1.426. Type: U / I.
Covers: lex/hosts.py (public_hosts, canonical_url, registrable_domain,
        same_site_host, same_site_url, host_of, CurrentHostMiddleware),
        lex/lex_app/settings.py (ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS),
        lex/proxy.py (PUBLIC_HOSTS, _requested_host, _external_base_url,
        _callback_url, _frontend_origins),
        lex/lex_app/streamlit/Streamlit.py, lex/lex_app/reflex/Reflex.py,
        lex/lex_app/streamlit/embed.py (_resolve_base_url),
        lex/lex_app/reflex/config.py (public_origins).
Run: python -m lex pytest lex/test_project/tests/init/test_1ay_custom_domain_hosts.py -v
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from unittest.mock import patch

import pytest
from django.test import SimpleTestCase
from starlette.requests import Request

from lex import hosts as lex_hosts

pytestmark = pytest.mark.init


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
#: One instance, as the Instance Controller leaves it once a customer has
#: attached `customer.com` and its two dashboard subdomains: the platform
#: hostnames stay, the customer's are added, and the canonical URLs still name
#: the platform ones because nothing has been promoted yet.
ATTACHED_ENV = {
    "DOMAIN_HOSTED": "inst.lit.excellence-cloud.de",
    "STREAMLIT_URL": "https://inst-streamlit.lit.excellence-cloud.de",
    "REFLEX_URL": "https://inst-reflex.lit.excellence-cloud.de",
    "LEX_APP_HOSTS": "inst.lit.excellence-cloud.de,app.customer.com",
    "LEX_STREAMLIT_HOSTS": "inst-streamlit.lit.excellence-cloud.de,streamlit.app.customer.com",
    "LEX_REFLEX_HOSTS": "inst-reflex.lit.excellence-cloud.de,reflex.app.customer.com",
}

#: The same instance before any domain was attached -- the three URL variables
#: alone, which is what every instance has had since long before this feature.
PLAIN_ENV = {
    "DOMAIN_HOSTED": "inst.lit.excellence-cloud.de",
    "STREAMLIT_URL": "https://inst-streamlit.lit.excellence-cloud.de",
    "REFLEX_URL": "https://inst-reflex.lit.excellence-cloud.de",
    "LEX_APP_HOSTS": None,
    "LEX_STREAMLIT_HOSTS": None,
    "LEX_REFLEX_HOSTS": None,
}


def _env(*dicts):
    """Merge the given env dicts; a ``None`` value removes the variable."""
    merged: dict = {}
    for item in dicts:
        merged.update(item)
    return merged


class _Env:
    """``with _Env(ATTACHED_ENV):`` -- set some variables, remove the ``None`` ones."""

    def __init__(self, env: dict) -> None:
        self.overrides = {k: v for k, v in env.items() if v is not None}
        self.removals = [k for k, v in env.items() if v is None]
        self._patch = None
        self._saved: dict = {}

    def __enter__(self):
        self._patch = patch.dict(os.environ, self.overrides, clear=False)
        self._patch.__enter__()
        for key in self.removals:
            self._saved[key] = os.environ.pop(key, None)
        return self

    def __exit__(self, *exc):
        for key, value in self._saved.items():
            if value is not None:
                os.environ[key] = value
        return self._patch.__exit__(*exc)


def _request(host: str, scheme: str = "https", port: int | None = None) -> Request:
    """A Starlette request as the ingress delivers it: a real ``Host`` header."""
    netloc = f"{host}:{port}" if port else host
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [(b"host", netloc.encode())],
        "scheme": scheme,
        "server": (host, port or (443 if scheme == "https" else 80)),
        "root_path": "",
        "client": ("10.0.0.1", 0),
    })


def _reimport_proxy_with(env: dict):
    """Re-import ``lex.proxy`` under ``env``, then restore the live module.

    ``PUBLIC_HOSTS`` and ``FRONTEND_ORIGINS`` are resolved once at import, so
    exercising them means re-importing -- the pattern cluster 1ad established
    for the proxy's other import-time guards.
    """
    saved = sys.modules.get("lex.proxy")
    with _Env(env):
        sys.modules.pop("lex.proxy", None)
        try:
            return importlib.import_module("lex.proxy")
        finally:
            if saved is not None:
                sys.modules["lex.proxy"] = saved


def _django_host_settings(env: dict) -> dict:
    """``ALLOWED_HOSTS`` and ``CSRF_TRUSTED_ORIGINS`` as a fresh process sees them.

    A child process, because both are evaluated once while
    ``lex_app.settings`` is imported, and this suite is already running with
    that module imported under a different environment. Reloading it in place
    would re-run Sentry setup, credential loading and the rest of a settings
    module on the live process -- so the honest way to read what a *deployed*
    pod would get is to start one.
    """
    child_env = dict(os.environ)
    for key, value in env.items():
        if value is None:
            child_env.pop(key, None)
        else:
            child_env[key] = value
    child_env["DJANGO_SETTINGS_MODULE"] = "lex_app.settings"

    # `lex_app.settings` is the settings module every deployed pod uses: the
    # `lex` package aliases itself under that bare name (lex/lex_app/__init__),
    # which needs the directory holding it on the path.
    import lex as lex_pkg

    script = (
        "import sys\n"
        f"sys.path.insert(0, {os.path.dirname(os.path.abspath(lex_pkg.__file__))!r})\n"
        "import json\n"
        "from django.conf import settings\n"
        "print('<<<' + json.dumps({\n"
        "    'allowed_hosts': list(settings.ALLOWED_HOSTS),\n"
        "    'csrf_trusted_origins': list(settings.CSRF_TRUSTED_ORIGINS),\n"
        "    'middleware': list(settings.MIDDLEWARE),\n"
        "    'cors_allowed_origins': list(settings.CORS_ALLOWED_ORIGINS),\n"
        "    'cors_allow_all_origins': bool(getattr(settings, 'CORS_ORIGIN_ALLOW_ALL', False)),\n"
        "}) + '>>>')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=child_env, capture_output=True, text=True, timeout=180,
    )
    assert "<<<" in result.stdout, (
        f"the settings module did not import in a child process:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    payload = result.stdout.split("<<<", 1)[1].split(">>>", 1)[0]
    return json.loads(payload)


# ---------------------------------------------------------------------------
# the host list itself
# ---------------------------------------------------------------------------
class TestCluster01ay_PublicHostList(SimpleTestCase):
    """Cluster 1ay: which hostnames an instance believes it answers on."""

    # -- 1.410 ---------------------------------------------------------
    def test_1_410_an_instance_without_custom_domains_has_exactly_its_canonical_host(self) -> None:
        """
        Scenario 1.410: with no host list configured, each role reports its canonical host alone.
        Given: DOMAIN_HOSTED, STREAMLIT_URL and REFLEX_URL, and no LEX_*_HOSTS
        When: the hostnames of each role are resolved
        Then: each is a single-entry list holding that role's own hostname.

        This is the fallback the whole feature rests on. The LEX_*_HOSTS
        variables are written by the lex-instance terraform module *only* when a
        customer-owned domain is attached, so every other instance -- and every
        instance deployed by an older module -- has none of them. If the
        fallback were anything but "the canonical host", this change would
        silently alter ALLOWED_HOSTS, the proxy's callback URL and both
        dashboards' iframes on installations that do not use custom domains at
        all.
        """
        with _Env(PLAIN_ENV):
            self.assertEqual(
                lex_hosts.public_hosts("app"), ["inst.lit.excellence-cloud.de"],
                msg="without a host list the app answers on DOMAIN_HOSTED and nothing else",
            )
            self.assertEqual(
                lex_hosts.public_hosts("streamlit"),
                ["inst-streamlit.lit.excellence-cloud.de"],
                msg="STREAMLIT_URL is the whole truth when no dashboard domain is attached",
            )
            self.assertEqual(
                lex_hosts.public_hosts("reflex"), ["inst-reflex.lit.excellence-cloud.de"],
                msg="REFLEX_URL is the whole truth when no dashboard domain is attached",
            )

    # -- 1.411 ---------------------------------------------------------
    def test_1_411_the_configured_list_is_read_canonical_first_and_deduplicated(self) -> None:
        """
        Scenario 1.411: the host list is parsed leniently and keeps the canonical host first.
        Given: LEX_APP_HOSTS written with spacing, a full URL, a duplicate and a trailing comma
        When: the app's hostnames are resolved
        Then: bare lower-case hostnames come back, in order, with the canonical one first.

        Order is not cosmetic: the first entry is what `same_site_url` and the
        proxy fall back to when nothing matches, so a list that lost its
        ordering would make an instance fall back to a customer's hostname
        rather than its own. The leniency is there because the value is written
        by terraform and read by three separate processes; a stray space must
        not take a domain offline.
        """
        with _Env(_env(ATTACHED_ENV, {
            "LEX_APP_HOSTS":
                " inst.lit.excellence-cloud.de , https://app.customer.com/ , APP.customer.com ,",
        })):
            self.assertEqual(
                lex_hosts.public_hosts("app"),
                ["inst.lit.excellence-cloud.de", "app.customer.com"],
                msg="whitespace, scheme, case and duplicates must all normalise away",
            )

    # -- 1.412 ---------------------------------------------------------
    def test_1_412_the_registrable_domain_is_what_cookies_are_scoped_to(self) -> None:
        """
        Scenario 1.412: a hostname reduces to the domain it is registered under.
        Given: a bare domain, a deep subdomain, and a name under a two-part suffix
        When: the registrable domain of each is taken
        Then: `customer.com`, `customer.com` and `customer.co.uk` come back.

        This is the comparison that decides first- versus third-party, so it has
        to agree with what a browser does with cookies. `co.uk` is the case
        worth pinning: treating it as the registrable domain would make every
        `.co.uk` customer look same-site with every other one, which is both
        wrong and the kind of wrong that only shows up in one market.
        """
        self.assertEqual(lex_hosts.registrable_domain("customer.com"), "customer.com")
        self.assertEqual(
            lex_hosts.registrable_domain("streamlit.app.customer.com"), "customer.com",
            msg="a dashboard subdomain is the same site as the app it hangs under",
        )
        self.assertEqual(
            lex_hosts.registrable_domain("streamlit.app.customer.co.uk"), "customer.co.uk",
            msg="a two-part public suffix must not be mistaken for a registrable domain",
        )
        self.assertNotEqual(
            lex_hosts.registrable_domain("app.customer.com"),
            lex_hosts.registrable_domain("inst.lit.excellence-cloud.de"),
            msg="a customer's domain and the platform's are different sites, which is the point",
        )


# ---------------------------------------------------------------------------
# picking the hostname to frame
# ---------------------------------------------------------------------------
class TestCluster01ay_SameSiteDashboard(SimpleTestCase):
    """Cluster 1ay: a dashboard is framed from the viewer's own registrable domain."""

    # -- 1.413 ---------------------------------------------------------
    def test_1_413_a_viewer_on_the_customer_domain_gets_the_customer_dashboard(self) -> None:
        """
        Scenario 1.413: the dashboard host follows the host the shell is served on.
        Given: an instance reachable on both its platform hostname and app.customer.com
        When: the dashboard URL is resolved for a viewer on each of them
        Then: each viewer gets the dashboard hostname on their own registrable domain.

        Without this the shell on `app.customer.com` frames
        `inst-streamlit.lit.excellence-cloud.de`, which is a third-party iframe:
        Safari drops its cookies, the proxy never sees a session, and the
        dashboard 401-loops. The symptom is "Streamlit is broken in Safari",
        which is why the fix belongs here and not in the dashboard.
        """
        with _Env(ATTACHED_ENV):
            self.assertEqual(
                lex_hosts.same_site_url("streamlit", "app.customer.com"),
                "https://streamlit.app.customer.com",
                msg="a viewer on the customer's domain must stay on it",
            )
            self.assertEqual(
                lex_hosts.same_site_url("reflex", "app.customer.com"),
                "https://reflex.app.customer.com",
                msg="Reflex follows the same rule as Streamlit; they are framed identically",
            )
            self.assertEqual(
                lex_hosts.same_site_url("streamlit", "inst.lit.excellence-cloud.de"),
                "https://inst-streamlit.lit.excellence-cloud.de",
                msg="a viewer on the platform hostname must keep the platform dashboard",
            )

    # -- 1.414 ---------------------------------------------------------
    def test_1_414_an_unmatched_viewer_falls_back_to_the_canonical_url_verbatim(self) -> None:
        """
        Scenario 1.414: with no same-site dashboard, the canonical URL is used unchanged.
        Given: a viewer on a host that matches no attached dashboard hostname, and
               separately a local run whose dashboard is http://localhost:8501
        When: the dashboard URL is resolved
        Then: the canonical URL comes back complete with its scheme and port.

        A third-party iframe still works in most browsers, so falling back beats
        showing nothing. The port matters for the other half of this scenario:
        `lex streamlit` serves http://localhost:8501, and a resolver that
        assumed https or dropped the port would frame a dashboard that cannot
        load at all -- turning a browser-specific problem into a local one.
        """
        with _Env(ATTACHED_ENV):
            self.assertEqual(
                lex_hosts.same_site_url("streamlit", "unrelated.example.org"),
                "https://inst-streamlit.lit.excellence-cloud.de",
                msg="an unknown viewer host falls back to the canonical dashboard",
            )

        with _Env({
            "DOMAIN_HOSTED": "localhost",
            "STREAMLIT_URL": "http://localhost:8501",
            "REFLEX_URL": "http://localhost:8502",
            "LEX_APP_HOSTS": None,
            "LEX_STREAMLIT_HOSTS": None,
            "LEX_REFLEX_HOSTS": None,
        }):
            self.assertEqual(
                lex_hosts.same_site_url("streamlit", "localhost"), "http://localhost:8501",
                msg="a local run keeps its scheme and port",
            )
            self.assertEqual(
                lex_hosts.same_site_url("reflex", "localhost"), "http://localhost:8502",
                msg="a local Reflex run keeps its scheme and port",
            )

    # -- 1.415 ---------------------------------------------------------
    def test_1_415_the_middleware_makes_the_requests_host_the_default_viewer(self) -> None:
        """
        Scenario 1.415: CurrentHostMiddleware supplies the viewer host, and clears it after.
        Given: a request arriving on app.customer.com
        When: a dashboard URL is resolved inside the request with no host argument
        Then: it is the customer's dashboard -- and outside the request, nothing is remembered.

        An HTMLReport renders through `get_html(self, user)`, a signature every
        project's own reports implement, so it cannot grow a `request`
        parameter. The host travels out-of-band instead. The second half is the
        part worth pinning: the server reuses threads and tasks, so a host left
        behind would have the *next* request frame the previous viewer's
        hostname -- one tenant's domain served inside another's page.
        """
        seen: dict = {}

        def view(request):
            seen["url"] = lex_hosts.same_site_url("streamlit")
            seen["host"] = lex_hosts.current_host()
            return "rendered"

        middleware = lex_hosts.CurrentHostMiddleware(view)

        class _FakeRequest:
            META = {"HTTP_HOST": "app.customer.com"}

        with _Env(ATTACHED_ENV):
            self.assertEqual(middleware(_FakeRequest()), "rendered")
            self.assertEqual(
                seen["url"], "https://streamlit.app.customer.com",
                msg="inside the request the viewer host must be the one it arrived on",
            )
            self.assertEqual(seen["host"], "app.customer.com")
            self.assertEqual(
                lex_hosts.current_host(), "",
                msg="the host must not outlive the request that set it",
            )

    # -- 1.416 ---------------------------------------------------------
    def test_1_416_the_streamlit_report_frames_the_same_site_dashboard(self) -> None:
        """
        Scenario 1.416: the Streamlit HTMLReport's iframe points at the viewer's own domain.
        Given: a request on app.customer.com, on an instance with the dashboard attached there
        When: the Streamlit report renders
        Then: its iframe src is the customer's dashboard, still carrying ?embed=true.

        The report is the only thing that frames Streamlit inside lex-app's own
        pages, so this is the surface the same-site rule has to reach to be
        worth anything. `embed=true` is asserted alongside because it is what
        strips Streamlit's own chrome; losing it while fixing the host would
        trade one visible bug for another.
        """
        from lex.lex_app.streamlit.Streamlit import Streamlit

        with _Env(ATTACHED_ENV):
            lex_hosts.set_current_host("app.customer.com")
            try:
                html = Streamlit().get_html(None)
            finally:
                lex_hosts.set_current_host("")

        self.assertIn(
            'src="https://streamlit.app.customer.com/?embed=true"', html,
            msg=f"the report must frame the same-site dashboard; got {html}",
        )

    # -- 1.426 ---------------------------------------------------------
    def test_1_426_lex_view_frames_the_app_on_the_dashboards_own_domain(self) -> None:
        """
        Scenario 1.426: the frame a dashboard draws around an app page is same-site too.
        Given: a dashboard whose canonical hostname is on the customer's domain
        When: `lex_view()` resolves where its frames point
        Then: the application hostname on that same domain, not the platform one --
              and an explicit REACT_APP_URL still wins.

        This is the reverse direction of the iframe in 1.416, and it has the
        same constraint: a frame across registrable domains is a third-party
        context, so an app page framed inside a dashboard on
        `streamlit.app.customer.com` has to come from `app.customer.com`.
        Deriving it also gives a deployed dashboard a working frame with
        nothing set, where the old fallback was `http://localhost:8000` --
        which no deployment can load.
        """
        import lex.lex_app.streamlit.embed as embed

        promoted = {
            "DOMAIN_HOSTED": "inst.lit.excellence-cloud.de",
            "STREAMLIT_URL": "https://streamlit.app.customer.com",
            "LEX_APP_HOSTS": "inst.lit.excellence-cloud.de,app.customer.com",
            "LEX_STREAMLIT_HOSTS": "streamlit.app.customer.com",
            "REACT_APP_URL": None,
            "LEX_FRONTEND_URL": None,
        }
        with _Env(promoted):
            self.assertEqual(
                embed._resolve_base_url(), "https://app.customer.com",
                msg="the frame must stay on the domain the dashboard is served from",
            )

        with _Env(_env(PLAIN_ENV, {"REACT_APP_URL": None, "LEX_FRONTEND_URL": None})):
            self.assertEqual(
                embed._resolve_base_url(), "https://inst.lit.excellence-cloud.de",
                msg="an instance without a custom domain frames its own platform hostname",
            )

        with _Env(_env(promoted, {"REACT_APP_URL": "https://elsewhere.example.com/"})):
            self.assertEqual(
                embed._resolve_base_url(), "https://elsewhere.example.com",
                msg="an explicit override still wins, for a shell on neither host",
            )

        with _Env({
            "DOMAIN_HOSTED": "localhost",
            "STREAMLIT_URL": "http://localhost:8501",
            "LEX_APP_HOSTS": None,
            "LEX_STREAMLIT_HOSTS": None,
            "REACT_APP_URL": None,
            "LEX_FRONTEND_URL": None,
        }):
            self.assertEqual(
                embed._resolve_base_url(), "http://localhost:8000",
                msg="a local run keeps the development shell, not https://localhost",
            )

    # -- 1.417 ---------------------------------------------------------
    def test_1_417_the_reflex_report_frames_the_same_site_dashboard(self) -> None:
        """
        Scenario 1.417: the Reflex HTMLReport's iframe points at the viewer's own domain.
        Given: a request on app.customer.com, on an instance with Reflex attached there
        When: the Reflex report renders
        Then: its iframe src is the customer's dashboard, still carrying ?lex_embed=1.

        Reflex signs users in itself rather than through a proxy, so a
        third-party frame costs it the same thing it costs Streamlit: the
        session cookie its own sign-in just set. `lex_embed=1` is asserted with
        the host because it is how the dashboard knows lex-app is already
        showing the signed-in user and sign-out.
        """
        from lex.lex_app.reflex.Reflex import Reflex

        with _Env(ATTACHED_ENV):
            lex_hosts.set_current_host("app.customer.com")
            try:
                html = Reflex().get_html(None)
            finally:
                lex_hosts.set_current_host("")

        self.assertIn(
            'src="https://reflex.app.customer.com/?lex_embed=1"', html,
            msg=f"the report must frame the same-site dashboard; got {html}",
        )


# ---------------------------------------------------------------------------
# accepting the hostname: Django
# ---------------------------------------------------------------------------
class TestCluster01ay_DjangoAcceptsEveryAttachedHost(SimpleTestCase):
    """Cluster 1ay: ALLOWED_HOSTS and CSRF cover every hostname the ingress routes."""

    # -- 1.418 ---------------------------------------------------------
    def test_1_418_every_attached_app_host_is_allowed_and_csrf_trusted(self) -> None:
        """
        Scenario 1.418: a deployed pod accepts its platform hostname and the customer's.
        Given: LEX_APP_HOSTS naming both, as the terraform module writes it
        When: a fresh process imports the settings module
        Then: both are in ALLOWED_HOSTS and both have an https entry in CSRF_TRUSTED_ORIGINS.

        Without the first, Django answers the customer's own domain with "400
        Bad Request" and the Channels origin validator refuses its WebSocket --
        the domain resolves, the ingress routes it, and the instance rejects it,
        with nothing in any log that names DNS. Without the second, every form
        POST and every unsafe API call from that domain fails CSRF: Django
        compares the Origin header against this list *exactly* unless the entry
        begins with `*.`, so the existing wildcard does not cover the host
        itself.
        """
        settings = _django_host_settings(ATTACHED_ENV)

        for host in ("inst.lit.excellence-cloud.de", "app.customer.com"):
            self.assertIn(
                host, settings["allowed_hosts"],
                msg=f"{host} is routed to this pod and must be accepted; "
                    f"got {settings['allowed_hosts']}",
            )
            self.assertIn(
                f"https://{host}", settings["csrf_trusted_origins"],
                msg=f"a POST from https://{host} must pass CSRF; "
                    f"got {settings['csrf_trusted_origins']}",
            )

        self.assertIn(
            "lex.hosts.CurrentHostMiddleware", settings["middleware"],
            msg="the middleware that records the request's host must be installed",
        )

    # -- 1.419 ---------------------------------------------------------
    def test_1_419_an_instance_without_custom_domains_keeps_the_hosts_it_always_had(self) -> None:
        """
        Scenario 1.419: with no host list, ALLOWED_HOSTS is the pre-existing set.
        Given: DOMAIN_HOSTED and no LEX_APP_HOSTS, as every instance without a custom domain has
        When: a fresh process imports the settings module
        Then: ALLOWED_HOSTS is exactly DOMAIN_HOSTED, 127.0.0.1, localhost and the pod IP.

        The guarantee that this change is invisible where the feature is not
        used. ALLOWED_HOSTS is a security boundary, so "nothing new is accepted"
        has to be asserted, not assumed -- a resolver that fell back to, say,
        every host it could find would widen that boundary on every instance at
        once.
        """
        settings = _django_host_settings(_env(PLAIN_ENV, {"POD_IP": "10.4.1.7"}))

        self.assertEqual(
            settings["allowed_hosts"],
            ["inst.lit.excellence-cloud.de", "127.0.0.1", "localhost", "10.4.1.7"],
            msg="no custom domain must mean no additional accepted hostname",
        )

    # -- 1.425 ---------------------------------------------------------
    def test_1_425_a_hardened_instance_still_accepts_its_own_attached_domain(self) -> None:
        """
        Scenario 1.425: LEX_CORS_ALLOWED_ORIGINS does not exclude the instance's own hostnames.
        Given: an instance hardened with an explicit CORS allow-list, and a
               customer-owned domain attached afterwards
        When: a fresh process imports the settings module
        Then: the listed origins are honoured AND every hostname of the instance
              is among them.

        An operator setting that variable is naming the third parties that may
        call the instance, not re-declaring the instance itself -- and once a
        domain is attached, "itself" is more than one hostname. Leaving them out
        would make a hardened instance refuse its own new domain twice over:
        for CORS, and as an OIDC next/fail target, since
        OidcRedirectGuardMiddleware reads these same lists.
        """
        settings = _django_host_settings(_env(ATTACHED_ENV, {
            "LEX_CORS_ALLOWED_ORIGINS": "https://partner.example.com",
        }))

        self.assertEqual(
            settings["cors_allowed_origins"],
            [
                "https://partner.example.com",
                "https://inst.lit.excellence-cloud.de",
                "https://app.customer.com",
            ],
            msg="the operator's list is kept, with the instance's own hostnames added",
        )
        self.assertFalse(
            settings["cors_allow_all_origins"],
            msg="an explicit allow-list must still close the permissive default",
        )


# ---------------------------------------------------------------------------
# accepting the hostname: the Streamlit proxy
# ---------------------------------------------------------------------------
class TestCluster01ay_StreamlitProxyFollowsItsHost(SimpleTestCase):
    """Cluster 1ay: the proxy signs users in on the hostname they are actually on."""

    # -- 1.420 ---------------------------------------------------------
    def test_1_420_the_oidc_callback_comes_back_to_the_host_the_user_is_on(self) -> None:
        """
        Scenario 1.420: the callback URL is built from the request's own hostname.
        Given: a dashboard served on both its platform hostname and streamlit.app.customer.com
        When: a user arrives on either and the login round trip is prepared
        Then: the callback URL names the hostname that user actually used.

        The proxy's session cookie is host-only -- it carries no Domain
        attribute, deliberately. So completing the sign-in on the canonical
        hostname while the user is on another one sets the cookie on a host the
        browser then leaves: it returns to the attached hostname with no
        session, is redirected to sign in, and loops. Nothing is logged, because
        every individual step succeeded.
        """
        module = _reimport_proxy_with(_env(ATTACHED_ENV, {"SESSION_SECRET": "fixed"}))

        self.assertEqual(
            module._external_base_url(_request("streamlit.app.customer.com")),
            "https://streamlit.app.customer.com",
            msg="a user on the attached hostname must be sent back to it",
        )
        self.assertEqual(
            module._callback_url(_request("streamlit.app.customer.com")),
            "https://streamlit.app.customer.com/auth/callback",
            msg="the OIDC redirect_uri follows the same hostname",
        )
        self.assertEqual(
            module._external_base_url(_request("inst-streamlit.lit.excellence-cloud.de")),
            "https://inst-streamlit.lit.excellence-cloud.de",
            msg="the platform hostname keeps working exactly as before",
        )

        # Only the host comes from the request: the scheme and port come from
        # the canonical URL, which every hostname of a role shares. Reading the
        # request's scheme instead would put `http://` into the redirect_uri
        # wherever ProxyHeadersMiddleware could not be imported -- a refusal
        # from Keycloak with nothing here to explain it -- and dropping the
        # port would break the local run below.
        local = _reimport_proxy_with({
            "DOMAIN_HOSTED": "localhost",
            "STREAMLIT_URL": "http://localhost:8501",
            "LEX_APP_HOSTS": None,
            "LEX_STREAMLIT_HOSTS": None,
            "SESSION_SECRET": "fixed",
        })
        self.assertEqual(
            local._external_base_url(_request("localhost", scheme="http", port=8501)),
            "http://localhost:8501",
            msg="`lex streamlit` serves a port, and the callback has to carry it",
        )

    # -- 1.421 ---------------------------------------------------------
    def test_1_421_a_host_header_we_do_not_serve_cannot_redirect_the_sign_in(self) -> None:
        """
        Scenario 1.421: an unrecognised Host falls back to the canonical URL.
        Given: a request carrying `Host: evil.example.net`, which this instance does not serve
        When: the callback URL is built
        Then: it names the canonical dashboard, never the header's value.

        This is why the hostname list is an allow-list rather than a read of the
        header. The value feeds the OIDC `redirect_uri` and the post-logout
        URL, so trusting it as it arrives would let anyone able to set a Host
        header -- a misconfigured intermediary is enough -- have Keycloak
        deliver an authorization code to a host of their choosing.
        """
        module = _reimport_proxy_with(_env(ATTACHED_ENV, {"SESSION_SECRET": "fixed"}))

        self.assertEqual(
            module._external_base_url(_request("evil.example.net")),
            "https://inst-streamlit.lit.excellence-cloud.de",
            msg="an unknown Host must never reach the callback URL",
        )
        self.assertEqual(
            module._requested_host(_request("evil.example.net")), "",
            msg="an unknown Host is not a hostname this proxy is served on",
        )

    # -- 1.422 ---------------------------------------------------------
    def test_1_422_a_shell_on_any_attached_host_may_renew_the_dashboards_token(self) -> None:
        """
        Scenario 1.422: every attached app hostname is a trusted adoption origin.
        Given: the shell served on both the platform hostname and app.customer.com
        When: the proxy resolves which origins may hand it a renewed credential
        Then: both are trusted, the attached dashboard hostnames are too, and `*` is not.

        Token renewal is a credentialed cross-origin POST from the shell to the
        dashboard. Deriving the allow-list from DOMAIN_HOSTED alone -- which is
        what it used to be -- refuses it from the customer's domain, so a
        dashboard opened there works until the first access token expires and
        then stops, with a 403 in the console and nothing server-side that
        identifies the cause.
        """
        module = _reimport_proxy_with(_env(ATTACHED_ENV, {
            "SESSION_SECRET": "fixed",
            "REACT_APP_URL": None,
            "LEX_FRONTEND_URL": None,
        }))

        for origin in (
            "https://inst.lit.excellence-cloud.de",
            "https://app.customer.com",
            "https://inst-streamlit.lit.excellence-cloud.de",
            "https://streamlit.app.customer.com",
        ):
            self.assertIn(
                origin, module.FRONTEND_ORIGINS,
                msg=f"{origin} is a hostname of this instance and must be trusted; "
                    f"got {sorted(module.FRONTEND_ORIGINS)}",
            )
        self.assertNotIn(
            "*", module.FRONTEND_ORIGINS,
            msg="a credentialed endpoint must never allow a wildcard origin",
        )


# ---------------------------------------------------------------------------
# accepting the hostname: Reflex
# ---------------------------------------------------------------------------
class TestCluster01ay_ReflexAcceptsEveryAttachedHost(SimpleTestCase):
    """Cluster 1ay: the Reflex backend accepts a websocket from every attached hostname."""

    # -- 1.423 ---------------------------------------------------------
    def test_1_423_every_attached_reflex_host_is_an_allowed_websocket_origin(self) -> None:
        """
        Scenario 1.423: the Reflex CORS origins list every hostname the dashboards serve.
        Given: Reflex served on its platform hostname and on reflex.app.customer.com
        When: the origins for the backend's websocket are resolved
        Then: both appear as https origins, canonical first.

        Reflex's frontend addresses the backend at `window.location`, so a page
        loaded from the customer's hostname opens its websocket there -- and the
        backend compares that Origin against this list. A list holding only the
        canonical hostname refuses it, and the dashboard renders an empty page
        whose only trace is a console error.
        """
        from lex.lex_app.reflex.config import public_origins

        with _Env(ATTACHED_ENV):
            self.assertEqual(
                public_origins(),
                [
                    "https://inst-reflex.lit.excellence-cloud.de",
                    "https://reflex.app.customer.com",
                ],
                msg="every hostname the dashboards answer on must be an allowed origin",
            )

    # -- 1.424 ---------------------------------------------------------
    def test_1_424_a_local_reflex_run_imposes_no_origin_allowlist(self) -> None:
        """
        Scenario 1.424: with no deployed hostname, Reflex keeps its own default.
        Given: REFLEX_URL pointing at localhost, as `lex reflex` leaves it
        When: the origins are resolved
        Then: the list is empty, so the config sets no `cors_allowed_origins` at all.

        Narrowing the allow-list to `https://localhost:8502` in development
        would refuse the dashboard's own websocket -- the page is served over
        http -- and break every local run to fix a deployed-only problem. An
        empty list means "say nothing", which leaves Reflex's permissive default
        in place exactly where it is appropriate.
        """
        from lex.lex_app.reflex.config import public_origins

        with _Env({
            "REFLEX_URL": "http://localhost:8502",
            "LEX_REFLEX_HOSTS": None,
        }):
            self.assertEqual(
                public_origins(), [],
                msg="a local run must not be given a deployed allow-list",
            )
