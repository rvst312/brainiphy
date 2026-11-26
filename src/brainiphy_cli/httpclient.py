"""HTTP plumbing for connectors that pull from a REST API.

Connectors import this the same way they already import frontmatter and
keychain. It exists because every API connector rediscovers the same four
problems, and getting any of them wrong is silent rather than loud:

  - **User-Agent.** urllib's default (`Python-urllib/3.x`) is rejected outright
    by the WAFs in front of several SaaS APIs — Cloudflare error 1010, a 403
    that never reaches the vendor and looks nothing like an auth problem.
  - **One network entry point.** A connector that paginates tends to grow a
    second, bare `urlopen` for the "next page" URL, which then silently skips
    whatever retry and error handling the first one had. Everything here goes
    through request().
  - **Transient faults.** A connector runs unattended under launchd. A DNS
    blip or a TLS handshake timeout must be retried, not turned into a failed
    sync and a stale graph.
  - **Missing scope is not an error.** A token is usually allowed to read some
    objects and not others. A 401/403 on one object should skip that object,
    not abort the run — hence NoScope, which callers catch per object.
"""
from __future__ import annotations

import json
from http.client import IncompleteRead
import time
import urllib.error
import urllib.parse
import urllib.request

from brainiphy_cli.keychain import get_secret

# Anything but urllib's default. Some APIs sit behind a WAF that bans the
# default signature before the request reaches the vendor at all.
DEFAULT_USER_AGENT = "brainiphy-connector/0.1"

DEFAULT_TIMEOUT = 45
DEFAULT_RETRIES = 4        # covers a flaky link and a burst rate limit
DEFAULT_BACKOFF = 4        # seconds, multiplied by the attempt number
DEFAULT_MAX_PAGES = 50     # a huge account must not loop forever
DEFAULT_PAGE_LIMIT = 100


class NoScope(Exception):
    """The credential cannot read this resource (HTTP 401/403).

    An expected outcome, not a failure: tokens are commonly issued with a
    subset of scopes. Catch it per resource and carry on.
    """


class IncompleteFetch(RuntimeError):
    """A partial response must never be mistaken for a complete snapshot."""


def _origin(url: str) -> tuple:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None):
        raise ValueError("API URLs must be HTTP(S) URLs without embedded credentials")
    port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, parsed.hostname.lower(), port


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _origin(req.full_url) != _origin(newurl):
            raise ValueError("refusing an authenticated redirect to a different origin")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class HttpClient:
    """A small retrying JSON GET client for one API.

    Deliberately GET-only. A connector reads a source and writes Markdown; if
    one ever needs to POST, that is a different kind of tool and should not
    borrow a class whose whole contract is "safe to re-run".

    Auth: pass `secret_item` (a Keychain item name) and the token is read
    lazily on first use — so `--probe` on a connector whose credential is not
    registered yet fails with keychain's actionable message at the moment it is
    actually needed. Pass `token` directly only in tests.
    """

    def __init__(
        self,
        base_url: str,
        *,
        secret_item: str | None = None,
        token: str | None = None,
        auth_header: str = "Authorization",
        auth_scheme: str = "Bearer",
        extra_headers: dict[str, str] | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: int = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        backoff_seconds: float = DEFAULT_BACKOFF,
        max_pages: int = DEFAULT_MAX_PAGES,
        page_limit: int = DEFAULT_PAGE_LIMIT,
    ) -> None:
        if not (secret_item or token):
            raise ValueError("HttpClient needs either secret_item= or token=")
        self.base_url = base_url.rstrip("/")
        self.origin = _origin(self.base_url)
        self._opener = urllib.request.build_opener(_SameOriginRedirect())
        self.secret_item = secret_item
        self._token = token
        self.auth_header = auth_header
        self.auth_scheme = auth_scheme
        self.extra_headers = dict(extra_headers or {})
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self.max_pages = max_pages
        self.page_limit = page_limit

    # -- auth ---------------------------------------------------------------

    @property
    def token(self) -> str:
        if self._token is None:
            self._token = get_secret(self.secret_item)
        return self._token

    def headers(self) -> dict[str, str]:
        value = f"{self.auth_scheme} {self.token}".strip() if self.auth_scheme else self.token
        return {
            self.auth_header: value,
            "Accept": "application/json",
            "User-Agent": self.user_agent,
            **self.extra_headers,
        }

    # -- requests -----------------------------------------------------------

    def request(self, url: str) -> dict:
        """GET one absolute URL and parse JSON. The single network entry point.

        Raises NoScope on 401/403, retries 429/5xx and transient network faults
        with linear backoff, RuntimeError on anything else or once retries run
        out.
        """
        last_error: Exception | None = None
        if _origin(url) != self.origin:
            raise ValueError("refusing to send API credentials to a different origin")

        for attempt in range(1, self.retries + 1):
            req = urllib.request.Request(url, headers=self.headers())
            try:
                with self._opener.open(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")[:300]
                if exc.code in (401, 403):
                    raise NoScope(f"HTTP {exc.code}: {body}") from exc
                if exc.code == 429 or exc.code >= 500:
                    last_error = RuntimeError(f"HTTP {exc.code}: {body}")
                    time.sleep(self.backoff_seconds * attempt)
                    continue
                raise RuntimeError(f"HTTP {exc.code} on {url}: {body}") from exc
            except (urllib.error.URLError, TimeoutError, OSError, IncompleteRead) as exc:
                # Flaky DNS, TLS handshake timeout, dropped read. Unattended
                # runs make these routine rather than exceptional.
                last_error = exc
                time.sleep(self.backoff_seconds * attempt)
                continue

        raise RuntimeError(f"{self.retries} attempts failed on {url}: {last_error}")

    def get(self, path: str, params: dict | None = None) -> dict:
        """GET one endpoint by path, with query params (None values dropped)."""
        url = self.base_url + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        return self.request(url)

    def get_list(self, path: str, key: str, params: dict | None = None) -> list:
        """Read a non-paginated collection without treating bad data as empty."""
        data = self.get(path, params)
        if not isinstance(data, dict) or not isinstance(data.get(key), list):
            raise IncompleteFetch(f"response is missing the {key!r} list")
        return data[key]

    def get_object(self, path: str, key: str, params: dict | None = None) -> dict:
        data = self.get(path, params)
        if not isinstance(data, dict) or not isinstance(data.get(key), dict):
            raise IncompleteFetch(f"response is missing the {key!r} object")
        return data[key]

    def paginate(
        self,
        path: str,
        params: dict,
        key: str,
        *,
        cursor_field: str = "startAfterId",
        cursor_params: tuple[str, ...] = ("startAfterId", "startAfter"),
        next_url_field: str = "nextPageUrl",
        meta_key: str = "meta",
    ) -> list[dict]:
        """Collect a paged list endpoint into one list.

        Handles the two styles APIs mix even within a single product: a ready
        made next-page URL in the metadata, or a cursor to feed back into the
        next request. Both go through request(), so retries and NoScope apply
        identically to the first page and the hundredth — which is exactly the
        bug this method exists to make impossible.

        A missing list or an unfinished walk at max_pages raises IncompleteFetch;
        callers must keep their previous snapshot in either case.
        """
        params = dict(params)
        params.setdefault("limit", self.page_limit)
        items: list[dict] = []
        next_url: str | None = None

        for _ in range(self.max_pages):
            data = self.request(next_url) if next_url else self.get(path, params)
            if not isinstance(data, dict) or not isinstance(data.get(key), list):
                raise IncompleteFetch(f"response is missing the {key!r} list")
            batch = data[key]
            items.extend(batch)

            meta = data.get(meta_key) or {}
            next_url = meta.get(next_url_field)
            if next_url:
                next_url = urllib.parse.urljoin(self.base_url + path, next_url)
                continue

            cursor = meta.get(cursor_field)
            # A cursor is explicit evidence that the snapshot is unfinished.
            if cursor:
                for name in cursor_params:
                    if name in meta:
                        params[name] = meta[name]
                params[cursor_field] = cursor
                continue
            break
        else:
            raise IncompleteFetch(f"pagination reached the {self.max_pages} page limit; previous records preserved")

        return items

    def hit_page_cap(self, items: list) -> bool:
        """Legacy size heuristic. paginate() now raises on an unfinished walk;
        retained for connectors that used this helper with their own paging."""
        return len(items) >= self.max_pages * self.page_limit
