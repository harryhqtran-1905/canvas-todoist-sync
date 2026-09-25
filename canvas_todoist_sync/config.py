"""Configuration: settings loaded from the environment, plus shared constants."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

DAYS_AHEAD = 30
STALE_TRACKING_DAYS = 60     # drop tracked Canvas tasks this long past due
LEGACY_STATE_DAYS = 105      # keep v1 task names this long after upgrading state

# State file lives at the project root, same location as before the refactor.
DEFAULT_STATE_FILE = Path(__file__).resolve().parent.parent / "sync_state.json"

_REQUIRED_ENV_VARS = ("CANVAS_API_TOKEN", "CANVAS_BASE_URL", "TODOIST_API_TOKEN")


@dataclass(frozen=True)
class Settings:
    canvas_token: str
    canvas_url: str
    todoist_token: str
    project_name: str = "School"
    days_ahead: int = DAYS_AHEAD
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo("America/Los_Angeles"))
    state_file: Path = DEFAULT_STATE_FILE

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        missing = [name for name in _REQUIRED_ENV_VARS if not os.getenv(name)]
        if missing:
            raise SystemExit(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                "Set them in .env or the environment before running."
            )
        return cls(
            canvas_token=os.environ["CANVAS_API_TOKEN"],
            canvas_url=os.environ["CANVAS_BASE_URL"],
            todoist_token=os.environ["TODOIST_API_TOKEN"],
            project_name=os.getenv("TODOIST_PROJECT_NAME", "School"),
            timezone=ZoneInfo(os.getenv("TIMEZONE", "America/Los_Angeles")),
        )
