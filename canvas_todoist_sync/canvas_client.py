"""Thin Canvas LMS API client. HTTP only — no sync decisions."""

from .config import Settings
from .http_session import build_session


class CanvasClient:
    def __init__(self, settings: Settings, session=None):
        self.base_url = settings.canvas_url
        self.headers = {"Authorization": f"Bearer {settings.canvas_token}"}
        self.session = session or build_session()

    def _get_all(self, url, params=None):
        """GET a Canvas collection, following Link: rel="next" pagination."""
        params = dict(params or {})
        params.setdefault("per_page", 100)
        results = []
        while url:
            r = self.session.get(url, headers=self.headers, params=params)
            r.raise_for_status()
            results.extend(r.json())
            url = r.links.get("next", {}).get("url")
            params = None  # the "next" URL already carries the query string
        return results

    def get_active_courses(self):
        courses = self._get_all(
            f"{self.base_url}/api/v1/courses",
            params={"enrollment_state": "active"},
        )
        return [c for c in courses if "name" in c]

    def get_assignments(self, course_id):
        return self._get_all(
            f"{self.base_url}/api/v1/courses/{course_id}/assignments",
            params={"order_by": "due_at"},
        )
