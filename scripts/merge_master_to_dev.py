#!/usr/bin/env python3
"""Fast master -> dev auto-merge for the provider-registry.

Standard flow: model-list sync commits to ``master`` (the config-table source of
truth); this script merges ``master`` into ``dev`` so dev stays current. It is
deliberately tiny and fast (git operations only, no network model fetches).

Exits 0 when dev is already up-to-date (nothing to merge). Designed to run every
6 hours from CI.
"""

import subprocess
import sys


def git(*args: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def main() -> int:
    branch = git("rev-parse", "--abbrev-ref", "HEAD")[1]
    if branch != "dev":
        print(f"[merge-dev] not on dev (on {branch!r}), aborting")
        return 1

    git("fetch", "origin", "master:master")
    code, out = git("merge-base", "--is-ancestor", "master", "HEAD")
    if code == 0:
        print("[merge-dev] dev already contains master; nothing to merge")
        return 0

    code, out = git("merge", "--no-edit", "master")
    print(f"[merge-dev] merge master -> dev:\n{out}")
    if code != 0:
        print("[merge-dev] merge produced conflicts; aborting merge")
        git("merge", "--abort")
        return 1

    code, out = git("push", "origin", "dev")
    print(f"[merge-dev] push dev:\n{out}")
    return 0 if code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
