from canvas_todoist_sync.http_session import DEFAULT_TIMEOUT, RETRY_STATUSES, build_session


def test_session_retries_transient_failures():
    retry = build_session().get_adapter("https://api.todoist.com").max_retries
    assert retry.total == 4
    assert retry.backoff_factor == 2.0
    assert set(RETRY_STATUSES) == {429, 500, 502, 503, 504}
    assert set(retry.status_forcelist) == set(RETRY_STATUSES)
    assert {"GET", "POST"} <= set(retry.allowed_methods)


def test_session_applies_default_timeout(monkeypatch):
    import requests

    seen = {}

    def fake_request(self, method, url, **kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr(requests.Session, "request", fake_request)
    session = build_session()
    session.get("https://api.todoist.com/api/v1/projects")
    assert seen["timeout"] == DEFAULT_TIMEOUT == 30

    session.get("https://api.todoist.com/api/v1/projects", timeout=5)
    assert seen["timeout"] == 5
