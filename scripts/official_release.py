"""Read public Claude macOS release metadata without credentials or app changes."""
from __future__ import annotations

import json
import re
import datetime as dt
import urllib.request
from urllib.parse import urlparse

RELEASE_URL = "https://downloads.claude.ai/releases/darwin/universal/RELEASES.json"
MAX_BYTES = 2 * 1024 * 1024


def validate_release(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Official release metadata is not an object")
    current = payload.get("currentRelease")
    releases = payload.get("releases")
    if not isinstance(current, str) or not re.fullmatch(r"\d+\.\d+\.\d+", current):
        raise ValueError("Invalid official currentRelease")
    if not isinstance(releases, list):
        raise ValueError("Official release list is missing")
    matches = [item for item in releases if isinstance(item, dict)
               and item.get("version") == current]
    if len(matches) != 1 or not isinstance(matches[0].get("updateTo"), dict):
        raise ValueError("Official current release entry is missing or ambiguous")
    release = matches[0]["updateTo"]
    url = release.get("url")
    parsed = urlparse(url) if isinstance(url, str) else None
    expected = f"/releases/darwin/universal/{current}/"
    if (release.get("version") != current or not parsed or parsed.scheme != "https"
            or parsed.netloc != "downloads.claude.ai" or parsed.query or parsed.fragment
            or not parsed.path.startswith(expected) or not parsed.path.endswith(".zip")
            or "/../" in parsed.path or "%" in parsed.path
            or not isinstance(release.get("pub_date"), str)):
        raise ValueError("Invalid official macOS release artifact metadata")
    try:
        published = dt.datetime.fromisoformat(release["pub_date"].replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Invalid official release publication date") from error
    if published.tzinfo is None:
        raise ValueError("Official release publication date has no timezone")
    return {
        "version": current, "published_at": release["pub_date"],
        "download_url": url, "source": RELEASE_URL,
    }


def fetch_latest() -> dict:
    request = urllib.request.Request(RELEASE_URL, headers={"User-Agent": "Claude-CN-update-check"})
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.geturl() != RELEASE_URL:
            raise ValueError("Official release feed redirected unexpectedly")
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Official release metadata is too large")
    return validate_release(json.loads(raw))


def compare_latest(installed: str, latest: dict) -> dict:
    if not re.fullmatch(r"\d+\.\d+\.\d+", installed):
        raise ValueError("Invalid installed Claude version")
    key = lambda value: tuple(int(part) for part in value.split("."))
    left, right = key(installed), key(latest["version"])
    return {
        **latest, "installed_version": installed,
        "status": "current" if left == right else "update_available" if left < right
        else "installed_ahead_of_public_feed",
    }
