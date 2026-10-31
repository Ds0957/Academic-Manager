"""Small shared contracts; no academic facts are inferred."""
from datetime import datetime, timezone


class CaptureError(Exception):
    """An actionable error suitable for the CLI."""


def now():
    return datetime.now(timezone.utc).isoformat()


ARTIFACTS = ("metadata.json", "transcript.json", "transcript.md", "analysis.md")