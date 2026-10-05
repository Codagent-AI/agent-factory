"""Keep test subprocesses off github.com."""

from __future__ import annotations

# Git never reaches github.com from a test: any https://github.com/ remote resolves to a
# missing local path and fails at once, as an unreadable private repository would. A test
# that maps a GitHub URL to a local repository adds a longer insteadOf entry (index 1 or
# later), which wins, or sets its own GIT_CONFIG_* variables.
NO_GITHUB = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "url.file:///nonexistent-github/.insteadOf",
    "GIT_CONFIG_VALUE_0": "https://github.com/",
}
