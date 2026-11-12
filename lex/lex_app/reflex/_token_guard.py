"""What keeps a Reflex dashboard's Keycloak sign-in alive -- plan "long-lived sessions", section 6.4.

A Reflex dashboard holds its own Keycloak tokens: Reflex Enterprise's OIDC provider keeps them in
the browser's cookies and refreshes them itself (decision D5 -- until reflex-enterprise offers a
token-source hook, Reflex keeps its own refresh-token holder). Out of the box (0.9.7) that holder
signs people out far too often, and :class:`~lex.lex_app.reflex.auth.LexKeycloakAuthState`
overrides it with what is here:

* **Refresh errors are classified** (:func:`classify_refresh`, django-oauth2-codeflow's own
  taxonomy). Only ``invalid_grant`` ends the sign-in; ``invalid_client`` and the other
  configuration errors keep the tokens and are logged at ERROR; 5xx, 429, timeouts and network
  errors keep the tokens and back off (:func:`note_failure`, :func:`retry_in`).
* **One refresh per refresh token, across tabs and replicas.** A Redis lock
  ``lex:rx:refresh:{sha256(rt)}`` lets one tab refresh; it publishes the new token set,
  Fernet-encrypted, for :data:`PUBLISHED_TTL_SECONDS` at ``lex:rx:refreshed:{sha256(old rt)}``,
  and every other tab holding the old refresh token adopts it instead of asking Keycloak. Without
  Redis (or while it does not answer) the same happens in process memory: the tabs one Reflex
  process serves still share one refresh.
* **Cooldown.** A tab that refreshed less than :data:`REFRESH_COOLDOWN_SECONDS` ago does not ask
  Keycloak again: a second refresh that soon cannot cure anything, and it is suppressed -- never
  turned into a sign-out.
* **Back-channel logout reaches the dashboards.** The Django process records
  ``lex:kc_sid_ended:{sid}`` (:func:`mark_sid_ended`, 90 days) when Keycloak ends a session, and a
  dashboard whose tokens belong to that session resets at its next token validation
  (:func:`sid_ended`). Tokens Keycloak issued after the logout are not affected, so a session
  Keycloak still honours is never reset by a stale marker.
* **Cookie lifetime 30 days** (:func:`plugin_cookies_lasting`), and a warning when
  reflex-enterprise is not the version these overrides were written against.

Every Redis key is the frozen name of the implementation plan; values never hold a token in
clear. This module imports nothing from Reflex: the Django process imports it for the
back-channel receiver (``lex.lex_app.reflex.backchannel``).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import secrets
import threading
import time
from collections import OrderedDict
from typing import Any, AsyncIterator, Callable, Mapping

logger = logging.getLogger(__name__)

#: The reflex-enterprise release the overrides in ``auth.py`` were written and tested against.
SUPPORTED_REFLEX_ENTERPRISE = "0.9.7"

#: How long the Reflex sign-in cookies live (plan: 30 days; the plugin's default is 7).
COOKIE_MAX_AGE_SECONDS = 30 * 24 * 3600

#: The plugin's four sign-in cookies, by the module-level name its cookie metaclass reads.
PLUGIN_COOKIES = ("COOKIE_ACCESS_TOKEN", "COOKIE_ID_TOKEN", "COOKIE_REFRESH_TOKEN", "COOKIE_GRANTED_SCOPES")

#: A tab that refreshed this recently does not ask Keycloak again (the refresh is suppressed).
REFRESH_COOLDOWN_SECONDS = 30.0
#: How long a refreshed token set stays published for the other tabs and replicas.
PUBLISHED_TTL_SECONDS = 120
#: How long a refresh lock is held at most (a leader that dies frees it after this).
REFRESH_LOCK_SECONDS = 15
#: How long a tab waits for the tab that holds the lock before it keeps its tokens.
REFRESH_WAIT_SECONDS = 10.0
#: Back-off after a transient failure: doubled per failure, from the first value up to the second.
TRANSIENT_BACKOFF_SECONDS = (5.0, 120.0)
#: Back-off after a configuration failure (``invalid_client`` ...): it will not cure itself soon.
CONFIG_BACKOFF_SECONDS = 300.0
#: How long the "Keycloak ended this session" marker is kept (the realm's longest session is 90 d).
SID_ENDED_TTL_SECONDS = 90 * 24 * 3600
#: How long Redis is left alone after it failed to answer.
REDIS_RETRY_SECONDS = 30.0
#: The most entries the in-memory fallback store keeps (the soonest to expire go first).
LOCAL_STORE_MAX_ENTRIES = 10_000
#: Clock skew tolerated when a token's ``iat``, ``nbf`` and ``exp`` are checked (Keycloak's clock
#: ahead of or behind this host's), the leeway django-oauth2-codeflow uses too.
JWT_LEEWAY_SECONDS = 30
#: An ID token signed with a key the cached key set lacks makes the key set be fetched again --
#: Keycloak rotated its keys -- at most once per this many seconds (a forged ``kid`` must not make
#: every request hit Keycloak).
JWKS_REFETCH_SECONDS = 60.0

#: The categories of :func:`classify_refresh` (django-oauth2-codeflow's names).
OK = "ok"
TERMINAL = "terminal"
CONFIG = "config"
TRANSIENT = "transient"

#: Clocks, a module attribute each so tests can move time.
now = time.time
monotonic = time.monotonic


# --- keys ------------------------------------------------------------------------------
def digest(token: str) -> str:
    """The SHA-256 hex digest that stands for a refresh token in every key (never the token)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_lock_key(refresh_token: str) -> str:
    return f"lex:rx:refresh:{digest(refresh_token)}"


def refreshed_key(refresh_token: str) -> str:
    return f"lex:rx:refreshed:{digest(refresh_token)}"


def sid_ended_key(sid: str) -> str:
    return f"lex:kc_sid_ended:{sid}"


# --- the shared store: Redis, else this process's memory ------------------------------
class LocalStore:
    """The fallback without Redis: this process's memory, with the same operations and TTLs.

    Every write first drops the entries whose time is up, and the store never holds more than
    :data:`LOCAL_STORE_MAX_ENTRIES` (the ones closest to expiry go first): a published token set
    is a few kilobytes, and a long-running process must not keep them all.
    """

    def __init__(self, max_entries: int | None = None) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, tuple[str, float]] = {}
        self.max_entries = max_entries

    def _live(self, key: str) -> str | None:
        entry = self._values.get(key)
        if entry is None:
            return None
        if entry[1] <= monotonic():
            del self._values[key]
            return None
        return entry[0]

    def _make_room(self) -> None:
        """Drop expired entries; then, at the cap, the ones expiring soonest (lock held)."""
        moment = monotonic()
        for key in [key for key, (_, expires) in self._values.items() if expires <= moment]:
            del self._values[key]
        cap = self.max_entries if self.max_entries is not None else LOCAL_STORE_MAX_ENTRIES
        overflow = len(self._values) - cap + 1
        if overflow > 0:
            for key, _ in sorted(self._values.items(), key=lambda item: item[1][1])[:overflow]:
                del self._values[key]

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._live(key)

    def set(self, key: str, value: str, ttl: float) -> None:
        with self._lock:
            self._values.pop(key, None)
            self._make_room()
            self._values[key] = (value, monotonic() + ttl)

    def add(self, key: str, value: str, ttl: float) -> bool:
        with self._lock:
            if self._live(key) is not None:
                return False
            self._make_room()
            self._values[key] = (value, monotonic() + ttl)
            return True

    def extend_if(self, key: str, value: str, ttl: float) -> bool:
        """Push ``key``'s expiry to ``ttl`` from now, if it still holds ``value`` (a lock's owner)."""
        with self._lock:
            if self._live(key) != value:
                return False
            self._values[key] = (value, monotonic() + ttl)
            return True

    def delete_if(self, key: str, value: str) -> None:
        with self._lock:
            if self._live(key) == value:
                del self._values[key]

    def clear(self) -> None:
        with self._lock:
            self._values.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._values)


_RELEASE_IF_OWNER = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
_EXTEND_IF_OWNER = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end"
)


class RedisStore:
    """The store every Reflex replica and the Django process share."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def get(self, key: str) -> str | None:
        value = self.client.get(key)
        return value.decode("utf-8") if isinstance(value, bytes) else value

    def set(self, key: str, value: str, ttl: float) -> None:
        self.client.set(key, value, px=max(1, int(ttl * 1000)))

    def add(self, key: str, value: str, ttl: float) -> bool:
        return bool(self.client.set(key, value, nx=True, px=max(1, int(ttl * 1000))))

    def extend_if(self, key: str, value: str, ttl: float) -> bool:
        return bool(self.client.eval(_EXTEND_IF_OWNER, 1, key, value, max(1, int(ttl * 1000))))

    def delete_if(self, key: str, value: str) -> None:
        self.client.eval(_RELEASE_IF_OWNER, 1, key, value)


_LOCAL = LocalStore()
_redis_lock = threading.Lock()
_redis_client: Any = None
_redis_client_url: str | None = None
_redis_down_until = 0.0
_redis_down_reported = False


def redis_url() -> str | None:
    """The Redis every lex pod of an instance shares: the Celery broker's (``REDIS_HOST`` & co.)."""
    try:
        from django.conf import settings

        return getattr(settings, "CELERY_BROKER_URL", None) or None
    except Exception:  # settings not configured: no Redis to share
        return None


def _redis_store() -> RedisStore | None:
    global _redis_client, _redis_client_url
    url = redis_url()
    if not url or monotonic() < _redis_down_until:
        return None
    with _redis_lock:
        if _redis_client is None or _redis_client_url != url:
            try:
                import redis

                _redis_client = redis.Redis.from_url(url, socket_connect_timeout=0.5, socket_timeout=1.0)
            except Exception as exc:  # a URL redis-py cannot use
                _redis_failed(exc)
                return None
            _redis_client_url = url
        return RedisStore(_redis_client)


def _redis_failed(exc: BaseException) -> None:
    global _redis_down_until, _redis_down_reported
    _redis_down_until = monotonic() + REDIS_RETRY_SECONDS
    message = (
        "Redis did not answer (%s): Reflex sign-ins coordinate their refreshes within this process "
        "only, and back-channel logouts reach dashboards at their next refresh, until it does."
    )
    if not _redis_down_reported:
        _redis_down_reported = True
        logger.warning(message, type(exc).__name__)
    else:
        logger.debug(message, type(exc).__name__)


def _shared(operation: Callable[[Any], Any]) -> Any:
    """Run ``operation(store)`` on Redis; on this process's memory without it, or when it fails."""
    global _redis_down_reported
    store = _redis_store()
    if store is not None:
        try:
            result = operation(store)
        except Exception as exc:  # redis.RedisError, OSError: whatever kept Redis from answering
            _redis_failed(exc)
        else:
            _redis_down_reported = False
            return result
    return operation(_LOCAL)


def shared_backend() -> str:
    """``"redis"`` or ``"local"``: where the next operation goes (for logs and tests)."""
    return "redis" if _redis_store() is not None else "local"


def reset() -> None:
    """Forget the Redis client, its failures, the local store and the back-off (tests, settings changes)."""
    global _redis_client, _redis_client_url, _redis_down_until, _redis_down_reported
    with _redis_lock:
        _redis_client = None
        _redis_client_url = None
        _redis_down_until = 0.0
        _redis_down_reported = False
    _LOCAL.clear()
    with _failures_lock:
        _FAILURES.clear()
    with _refetch_lock:
        _LAST_REFETCH.clear()


# --- classification --------------------------------------------------------------------
def classify_refresh(status: int | None, body: Any) -> tuple[str, str | None, str]:
    """``(category, oauth_error, description)`` of a token-endpoint answer to a refresh.

    django-oauth2-codeflow's classification, the one Django's own sign-ins follow: only
    ``invalid_grant`` is :data:`TERMINAL` (unless it is about the issuer, which is a
    misconfiguration); ``invalid_client``, ``unauthorized_client``, ``unsupported_grant_type``,
    ``invalid_scope`` and ``invalid_request`` are :data:`CONFIG`; 429, 5xx, a page from a proxy
    and anything else are :data:`TRANSIENT`. ``status=None``: no answer at all (transient).
    """
    if status is None:
        return TRANSIENT, None, "no answer"
    from oauth2_authcodeflow.token_endpoint import classify

    return classify(status, body)


# --- back-off after failures (per refresh token, per process) --------------------------
_failures_lock = threading.Lock()
_FAILURES: "OrderedDict[str, tuple[int, float]]" = OrderedDict()
_MAX_TRACKED = 10_000


def note_failure(refresh_token: str, category: str) -> float:
    """Record a failed refresh; returns how many seconds the next one waits."""
    key = digest(refresh_token)
    with _failures_lock:
        count = _FAILURES.get(key, (0, 0.0))[0] + 1
        if category == CONFIG:
            delay = CONFIG_BACKOFF_SECONDS
        else:
            first, cap = TRANSIENT_BACKOFF_SECONDS
            delay = min(cap, first * 2 ** (count - 1))
        _FAILURES[key] = (count, monotonic() + delay)
        _FAILURES.move_to_end(key)
        while len(_FAILURES) > _MAX_TRACKED:
            _FAILURES.popitem(last=False)
    return delay


def note_success(refresh_token: str) -> None:
    with _failures_lock:
        _FAILURES.pop(digest(refresh_token), None)


def retry_in(refresh_token: str) -> float:
    """Seconds before this refresh token may be refreshed again after a failure (0: now)."""
    with _failures_lock:
        entry = _FAILURES.get(digest(refresh_token))
    return 0.0 if entry is None else max(0.0, entry[1] - monotonic())


def cooling_down(refreshed_at: float) -> bool:
    """Whether a tab that last refreshed at ``refreshed_at`` (epoch seconds) is in its cooldown.

    The value is a wall-clock time kept in the tab's state (shared by replicas), so a clock that
    steps back would put it in the future: that is no cooldown, never a refresh suppressed for as
    long as the step.
    """
    if not refreshed_at:
        return False
    elapsed = now() - refreshed_at
    return 0 <= elapsed < REFRESH_COOLDOWN_SECONDS


# --- key-set refetches (an unknown kid: Keycloak rotated its keys) ----------------------
_refetch_lock = threading.Lock()
_LAST_REFETCH: dict[str, float] = {}


def may_refetch_keys(jwks_uri: str) -> bool:
    """Whether the key set at ``jwks_uri`` may be fetched again now (once per JWKS_REFETCH_SECONDS)."""
    with _refetch_lock:
        last = _LAST_REFETCH.get(jwks_uri)
        moment = monotonic()
        if last is not None and moment - last < JWKS_REFETCH_SECONDS:
            return False
        _LAST_REFETCH[jwks_uri] = moment
        return True


# --- the published token set -----------------------------------------------------------
def _cipher() -> Any:
    from oauth2_authcodeflow.crypto import get_cipher

    return get_cipher()


def publish_refreshed(old_refresh_token: str, token_set: Mapping[str, Any]) -> None:
    """Publish the token set a refresh of ``old_refresh_token`` got, encrypted, for the other tabs.

    The sealed payload names the refresh token it was published for (its digest, never the
    token), so a set copied or planted under another key is not adopted (:func:`published`).
    Best effort: the refreshing tab has its tokens either way, and without the published set the
    other tabs refresh themselves.
    """
    payload = {"for": digest(old_refresh_token), "set": dict(token_set)}
    try:
        sealed = _cipher().encrypt(json.dumps(payload, separators=(",", ":")))
    except Exception as exc:  # no key to encrypt with (settings): nothing is published in clear
        logger.warning("A refreshed Reflex token set was not published (%s).", type(exc).__name__)
        return
    _shared(lambda store: store.set(refreshed_key(old_refresh_token), sealed, PUBLISHED_TTL_SECONDS))


def published(refresh_token: str) -> dict | None:
    """The token set another tab or replica got for ``refresh_token``, if it is still published.

    Only a set sealed with this instance's key *and* published for this refresh token counts;
    the caller still checks that it belongs to the same sign-in before adopting it.
    """
    sealed = _shared(lambda store: store.get(refreshed_key(refresh_token)))
    if not sealed:
        return None
    try:
        payload = json.loads(_cipher().decrypt(sealed))
    except Exception:  # sealed with another key, or not ours: not a token set to adopt
        logger.warning("A published Reflex token set could not be read; this tab refreshes itself.")
        return None
    if not isinstance(payload, dict) or payload.get("for") != digest(refresh_token):
        logger.warning("A published Reflex token set was sealed for another refresh token; it is not adopted.")
        return None
    token_set = payload.get("set")
    return token_set if isinstance(token_set, dict) else None


async def _hold(key: str, owner: str) -> None:
    """Keep a refresh lock alive while its holder works: extended every third of its TTL."""
    try:
        while True:
            await asyncio.sleep(REFRESH_LOCK_SECONDS / 3)
            held = await asyncio.to_thread(_shared, lambda store: store.extend_if(key, owner, REFRESH_LOCK_SECONDS))
            if not held:
                return
    except asyncio.CancelledError:
        pass


@contextlib.asynccontextmanager
async def refresh_lock(refresh_token: str) -> AsyncIterator[bool]:
    """Hold ``lex:rx:refresh:{sha256(rt)}`` while refreshing; yields whether this tab leads.

    The lock is extended for as long as the leader works -- a slow Keycloak, a key-set fetch, a
    slow audit hook -- so no second tab or replica takes it and refreshes the same refresh token;
    a leader that dies stops extending it, and it expires after :data:`REFRESH_LOCK_SECONDS`.
    """
    key = refresh_lock_key(refresh_token)
    owner = secrets.token_hex(16)
    leads = await asyncio.to_thread(_shared, lambda store: store.add(key, owner, REFRESH_LOCK_SECONDS))
    keeper = asyncio.ensure_future(_hold(key, owner)) if leads else None
    try:
        yield leads
    finally:
        if keeper is not None:
            keeper.cancel()
        if leads:
            await asyncio.to_thread(_shared, lambda store: store.delete_if(key, owner))


async def wait_for_published(refresh_token: str, timeout: float = REFRESH_WAIT_SECONDS) -> dict | None:
    """Wait for the tab that holds the lock to publish its token set.

    ``None`` when it released the lock without one (its refresh failed) or after ``timeout``.
    """
    key = refresh_lock_key(refresh_token)
    deadline = monotonic() + timeout
    while True:
        token_set = await asyncio.to_thread(published, refresh_token)
        if token_set is not None:
            return token_set
        if monotonic() >= deadline:
            return None
        if await asyncio.to_thread(_shared, lambda store: store.get(key)) is None:
            # Released without publishing -- unless the set landed in between.
            return await asyncio.to_thread(published, refresh_token)
        await asyncio.sleep(0.05)


# --- back-channel logout ---------------------------------------------------------------
def mark_sid_ended(sid: str, at: float | None = None) -> None:
    """Record that Keycloak ended session ``sid`` (at ``at``, epoch seconds; default now)."""
    if not sid:
        return
    ended_at = now() if at is None else float(at)
    _shared(lambda store: store.set(sid_ended_key(sid), repr(ended_at), SID_ENDED_TTL_SECONDS))


def sid_ended(sid: str, issued_at: Any = None) -> bool:
    """Whether Keycloak ended session ``sid`` after a token issued at ``issued_at`` (epoch seconds).

    Tokens issued after the logout prove that Keycloak still honours the session (a refresh it
    accepted), so they are left alone; without ``issued_at`` the marker alone decides.
    """
    if not sid:
        return False
    marker = _shared(lambda store: store.get(sid_ended_key(sid)))
    if marker is None:
        return False
    try:
        ended_at = float(marker)
    except (TypeError, ValueError):
        return True
    if isinstance(issued_at, (int, float)) and not isinstance(issued_at, bool):
        return float(issued_at) <= ended_at
    return True


# --- the plugin's cookies and version --------------------------------------------------
def plugin_cookies_lasting(module: Any, max_age: int = COOKIE_MAX_AGE_SECONDS) -> dict:
    """Give the plugin's sign-in cookies ``max_age`` for the provider class created next.

    The plugin's cookie metaclass copies ``COOKIE_*`` from its module onto each provider class as
    the class is created, so they are replaced in ``module`` before ``LexKeycloakAuthState`` is
    defined. Returns the originals for :func:`restore_plugin_cookies`.
    """
    originals = {name: getattr(module, name) for name in PLUGIN_COOKIES}
    for name, cookie in originals.items():
        setattr(module, name, cookie._replace(max_age=max_age))
    return originals


def restore_plugin_cookies(module: Any, originals: Mapping[str, Any]) -> None:
    """Put the plugin's own cookies back, so only lex's provider lives longer."""
    for name, cookie in originals.items():
        setattr(module, name, cookie)


def cookie_lifetimes(provider_cls: type) -> dict:
    """The ``max_age`` of each of a provider class's four sign-in cookies, by attribute."""
    from inspect import getattr_static

    names = ("_access_token_data", "_id_token", "_refresh_token", "_granted_scopes")
    return {name: getattr(getattr_static(provider_cls, name, None), "max_age", None) for name in names}


def check_cookie_lifetime(provider_cls: type, expected: int = COOKIE_MAX_AGE_SECONDS) -> bool:
    """After the class exists: are its cookies really ``expected`` long? Logs an ERROR when not."""
    lifetimes = cookie_lifetimes(provider_cls)
    if all(value == expected for value in lifetimes.values()):
        return True
    logger.error(
        "The Reflex sign-in cookies of %s do not last %d s (%s): the plugin no longer takes its cookie "
        "settings from where lex patches them. Dashboards keep signing in, but sessions end sooner.",
        provider_cls.__name__, expected, lifetimes,
    )
    return False


def check_plugin_version(installed: str | None = None) -> bool:
    """Warn when reflex-enterprise is not :data:`SUPPORTED_REFLEX_ENTERPRISE`.

    The overrides replace private methods of its OIDC provider; another release may have renamed
    or changed them, and then lex's sign-in hardening silently stops applying.
    """
    if installed is None:
        from importlib.metadata import PackageNotFoundError, version

        try:
            installed = version("reflex-enterprise")
        except PackageNotFoundError:
            installed = "not installed"
    if installed == SUPPORTED_REFLEX_ENTERPRISE:
        return True
    logger.warning(
        "reflex-enterprise %s is installed; lex's Reflex sign-in hardening (lex/lex_app/reflex/auth.py) was "
        "written against %s and overrides its private methods. Check the overrides before relying on them.",
        installed, SUPPORTED_REFLEX_ENTERPRISE,
    )
    return False


__all__ = [
    "CONFIG",
    "COOKIE_MAX_AGE_SECONDS",
    "JWKS_REFETCH_SECONDS",
    "JWT_LEEWAY_SECONDS",
    "LOCAL_STORE_MAX_ENTRIES",
    "OK",
    "PUBLISHED_TTL_SECONDS",
    "REFRESH_COOLDOWN_SECONDS",
    "SID_ENDED_TTL_SECONDS",
    "SUPPORTED_REFLEX_ENTERPRISE",
    "TERMINAL",
    "TRANSIENT",
    "LocalStore",
    "RedisStore",
    "check_cookie_lifetime",
    "check_plugin_version",
    "classify_refresh",
    "cookie_lifetimes",
    "cooling_down",
    "digest",
    "mark_sid_ended",
    "may_refetch_keys",
    "note_failure",
    "note_success",
    "plugin_cookies_lasting",
    "publish_refreshed",
    "published",
    "redis_url",
    "refresh_lock",
    "refresh_lock_key",
    "refreshed_key",
    "reset",
    "restore_plugin_cookies",
    "retry_in",
    "shared_backend",
    "sid_ended",
    "sid_ended_key",
    "wait_for_published",
]
