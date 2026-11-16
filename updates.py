"""Check whether the installed Git checkout can fast-forward to GitHub main."""

from http.client import HTTPException
import json
from pathlib import Path
import re
import subprocess
from urllib.request import Request, urlopen

REPOSITORY = "MrNobodyX3/EQ-Audio-Ducking"


def github_commits_behind(project):
    """Return the update count, or zero if the checkout cannot be compared."""
    def git(*args):
        return subprocess.run(
            ("git", *args), cwd=project, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, check=True, timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()

    try:
        root, head = git("rev-parse", "--show-toplevel", "HEAD").splitlines()
        if project.resolve() != Path(root).resolve() or not re.fullmatch("[0-9a-f]{40}", head):
            return 0
        if git("symbolic-ref", "--quiet", "--short", "HEAD") != "main":
            return 0
        remote = git("remote", "get-url", "origin").casefold().rstrip("/").removesuffix(".git")
        repo = REPOSITORY.casefold()
        if remote not in (f"https://github.com/{repo}", f"git@github.com:{repo}",
                          f"ssh://git@github.com/{repo}"):
            return 0
        request = Request(
            f"https://api.github.com/repos/{REPOSITORY}/compare/{head}...main?per_page=1",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "EQ-Audio-Ducking"},
        )
        with urlopen(request, timeout=5) as response:
            comparison = json.load(response)
    except (OSError, HTTPException, ValueError, subprocess.SubprocessError):
        return 0

    if isinstance(comparison, dict) and comparison.get("status") == "ahead":
        count = comparison.get("ahead_by")
        if type(count) is int and count > 0:
            return count
    return 0
