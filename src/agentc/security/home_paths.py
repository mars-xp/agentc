"""Detect host home-directory literals in project text."""

from __future__ import annotations

import re

_POSIX_MARKERS = ("/Users/", "/home/")
_WIN_USERS = re.compile(r"(?i)[a-z]:\\Users\\")
_WIN_USERS_FWD = re.compile(r"(?i)[a-z]:/Users/")


def contains_home_directory_literal(text: str) -> bool:
    for marker in _POSIX_MARKERS:
        if marker in text:
            return True
    return _WIN_USERS.search(text) is not None or _WIN_USERS_FWD.search(text) is not None
