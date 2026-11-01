"""Keep the releases current with the AWS CLI. The release workflow runs this.

    uv run python scripts/release.py [--dry-run]

1. Publishes a GitHub release for every tag on the branch history that has none, oldest
   first, so a run that stopped halfway is finished by the next one.
2. For every AWS CLI v2 release newer than the newest tag on history, oldest first:
   ingests it (from a shallow clone of the tag), generates the completers, commits and
   tags them on history (scripts/backfill.py), pushes the branch and the tag, and
   publishes the release with notes against the previous version (scripts/changelog.py).

Needs the branch history and its tags locally, gh with a token that can write contents,
and a git identity for the commits.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from backfill import BRANCH, commit_version  # noqa: E402
from changelog import notes, parse  # noqa: E402
from src.generate import emit_bash, emit_zsh, load_catalog  # noqa: E402
from src.ingest import aws_cli_versions, ingest, parse_version  # noqa: E402

FILES = ("_aws_move_outtamyway", "_aws_move_outtamyway.bash")


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout


def history_versions() -> list[str]:
    tags = git("tag", "--merged", BRANCH).split()
    return sorted((t for t in tags if re.fullmatch(r"2\.\d+\.\d+", t)), key=parse_version)


def published() -> set[str]:
    out = subprocess.run(
        ["gh", "release", "list", "--limit", "5000", "--json", "tagName", "--jq", ".[].tagName"],
        capture_output=True, text=True, check=True, cwd=REPO,
    ).stdout
    return set(out.split())


def release_notes(version: str, previous: str | None) -> str:
    new = parse(git("show", f"{version}:_aws_move_outtamyway"))
    if previous is None:
        ops = sum(len(v) for v in new.ops.values())
        return f"The completer for AWS CLI {version}, released {new.date}: {len(new.ops)} commands, {ops:,} subcommands.\n"
    return notes(parse(git("show", f"{previous}:_aws_move_outtamyway")), new, previous, version)


def publish(version: str, previous: str | None, latest: bool, dry_run: bool) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for f in FILES:
            (tmp / f).write_text(git("show", f"{version}:{f}"))
        (tmp / "notes.md").write_text(release_notes(version, previous))
        cmd = ["gh", "release", "create", version, "--verify-tag", "--title", version,
               "--notes-file", str(tmp / "notes.md"), f"--latest={'true' if latest else 'false'}",
               *(str(tmp / f) for f in FILES)]
        print(("would run: " if dry_run else "") + " ".join(cmd[:4]), flush=True)
        if not dry_run:
            subprocess.run(cmd, check=True, cwd=REPO)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true", help="Show what would be done; change nothing.")
    p.add_argument("--datalake", type=Path, default=REPO / "datalake" / "aws_api_catalog")
    a = p.parse_args()

    # 1. Tags on history without a release.
    versions = history_versions()
    done = published()
    for i, v in enumerate(versions):
        if v not in done:
            publish(v, versions[i - 1] if i else None, latest=(v == versions[-1]), dry_run=a.dry_run)

    # 2. AWS CLI releases newer than history.
    newest = versions[-1] if versions else None
    new = [v for v in aws_cli_versions() if newest is None or parse_version(v) > parse_version(newest)]
    print(f"newest on {BRANCH}: {newest}; new AWS CLI releases: {new or 'none'}", flush=True)
    license_text = (REPO / "LICENSE").read_bytes()
    for v in new:
        if a.dry_run:
            print(f"would ingest, commit, tag and publish {v}")
            continue
        ingest(v, a.datalake)
        catalog = load_catalog(a.datalake, v)
        commit_version(v, str(catalog["effective_date"]), emit_zsh(catalog), emit_bash(catalog), license_text)
        git("push", "origin", f"refs/heads/{BRANCH}", f"refs/tags/{v}")
        publish(v, newest, latest=True, dry_run=False)
        newest = v


if __name__ == "__main__":
    main()
