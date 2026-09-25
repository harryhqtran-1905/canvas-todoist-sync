"""Shared HTTP session with a per-request timeout and retries for transient failures."""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

RETRY_STATUSES = (429, 500, 502, 503, 504)
DEFAULT_TIMEOUT = 30  # seconds; a stalled connection must fail well before the next 30-minute tick


class TimeoutSession(requests.Session):
    """requests.Session that applies a default timeout to every request.

    `requests` has no session-wide timeout, and without one a stalled
    connection hangs forever: the retries never start, and later timer ticks
    are skipped while the stuck process is alive.
    """

    def __init__(self, timeout: float = DEFAULT_TIMEOUT):
        super().__init__()
        self.timeout = timeout

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        return super().request(method, url, **kwargs)


def build_session(
    total_retries: int = 4, backoff_factor: float = 2.0, timeout: float = DEFAULT_TIMEOUT
) -> requests.Session:
    """Session that retries brief network failures and 429/5xx on an always-on host.

    Retries handle a blip that lasts seconds. A longer outage exhausts them,
    the run aborts without writing, and the next 30-minute tick tries again.

    POST is retried too: Todoist de-duplicates writes by X-Request-Id, and a
    retry resends the same headers, so the same id.
    """
    retry = Retry(
        total=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=RETRY_STATUSES,
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = TimeoutSession(timeout=timeout)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
