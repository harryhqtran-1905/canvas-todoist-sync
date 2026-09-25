import pytest
import requests

from canvas_todoist_sync.config import Settings
from canvas_todoist_sync.models import COMPLETED, DELETED, OPEN
from canvas_todoist_sync.todoist_client import TodoistClient
from fakes import FakeResponse


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, params=None):
        self.calls.append((url, dict(params or {})))
        return self.responses.pop(0)


def _client(*responses):
    settings = Settings(canvas_token="c", canvas_url="https://canvas.test", todoist_token="t")
    return TodoistClient(settings, session=FakeSession(*responses))


@pytest.mark.parametrize(
    "response, expected",
    [
        (FakeResponse({"checked": True, "is_deleted": False}), COMPLETED),
        (FakeResponse({"checked": False, "is_deleted": True}), DELETED),
        (FakeResponse({"checked": False, "is_deleted": False}), OPEN),
        (FakeResponse(status_code=404), DELETED),
    ],
)
def test_get_task_status(response, expected):
    client = _client(response)
    assert client.get_task_status("t1") == expected
    assert client.session.calls[0][0].endswith("/tasks/t1")


def test_get_task_status_raises_on_server_error():
    with pytest.raises(requests.HTTPError):
        _client(FakeResponse(status_code=503)).get_task_status("t1")


def test_get_active_tasks_keyed_by_id_with_content():
    page = {
        "results": [
            {
                "id": "t1",
                "content": "[Math] HW1",
                "priority": 4,
                "due": {"date": "2026-09-27", "datetime": "2026-09-28T06:59:59Z"},
            }
        ],
        "next_cursor": None,
    }
    client = _client(FakeResponse(page))
    tasks = client.get_active_tasks("proj")
    assert set(tasks) == {"t1"}
    assert tasks["t1"].content == "[Math] HW1"
    assert tasks["t1"].due_datetime == "2026-09-28T06:59:59Z"
    assert tasks["t1"].due_date == "2026-09-27"
    assert client.session.calls[0][1]["project_id"] == "proj"
