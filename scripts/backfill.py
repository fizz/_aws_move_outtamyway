"""Build a completer for every AWS CLI v2 release, oldest first, on the branch history.

    uv run python scripts/backfill.py [--clone .cache/aws-cli.git] [--from 2.3.0] [--to 2.39.3]

For each release it ingests awscli/botocore at the release tag (git archive from one bare
clone of aws/aws-cli), generates the zsh and bash completers, commits them with LICENSE
to the local branch history, and tags that commit with the AWS CLI version. It builds
locally only; pushing the branch and tags, and the GitHub releases, are separate steps.
Versions already in the datalake are not ingested again, and versions already tagged
are skipped, so a second run continues where the first one stopped.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.generate import emit_bash, emit_zsh, load_catalog  # noqa: E402
from src.ingest import AWS_CLI_URL, aws_cli_versions, ingest, parse_version  # noqa: E402

BRANCH = "history"


def git(*args: str, input: bytes | None = None, env: dict | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), *args], input=input, capture_output=True, check=True, env=env
    ).stdout.decode().strip()


def tag_exists(tag: str) -> bool:
    return subprocess.run(["git", "-C", str(REPO), "rev-parse", "-q", "--verify", f"refs/tags/{tag}"], capture_output=True).returncode == 0


def blob(content: bytes) -> str:
    return git("hash-object", "-w", "--stdin", input=content)


def commit_version(version: str, released: str, zsh: str, bash: str, license_text: bytes) -> str:
    """Commit the two completers and LICENSE on BRANCH, and tag the commit."""
    tree = git(
        "mktree",
        input=(
            f"100644 blob {blob(license_text)}\tLICENSE\n"
            f"100644 blob {blob(zsh.encode())}\t_aws_move_outtamyway\n"
            f"100644 blob {blob(bash.encode())}\t_aws_move_outtamyway.bash\n"
        ).encode(),
    )
    parent = subprocess.run(["git", "-C", str(REPO), "rev-parse", "-q", "--verify", f"refs/heads/{BRANCH}"], capture_output=True, text=True).stdout.strip()
    message = f"AWS CLI {version}\n\nThe completer for AWS CLI {version}, released {released}.\n"
    commit = git("commit-tree", tree, *(["-p", parent] if parent else []), "-m", message)
    git("update-ref", f"refs/heads/{BRANCH}", commit, *([parent] if parent else []))
    git("tag", "-a", version, "-m", f"AWS CLI {version}, released {released}", commit)
    return commit


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--clone", type=Path, default=REPO / ".cache" / "aws-cli.git")
    p.add_argument("--datalake", type=Path, default=REPO / "datalake" / "aws_api_catalog")
    p.add_argument("--from", dest="first", default="2.3.0")
    p.add_argument("--to", dest="last")
    a = p.parse_args()

    if not a.clone.exists():
        print(f"Cloning {AWS_CLI_URL} to {a.clone} (bare, about 600 MB)...")
        subprocess.run(["git", "clone", "-q", "--bare", AWS_CLI_URL, str(a.clone)], check=True)
    else:
        subprocess.run(["git", "-C", str(a.clone), "fetch", "-q", "--tags", "origin"], check=True)

    versions = [
        v for v in aws_cli_versions()
        if parse_version(v) >= parse_version(a.first) and (not a.last or parse_version(v) <= parse_version(a.last))
    ]
    license_text = (REPO / "LICENSE").read_bytes()
    started, done = time.monotonic(), 0
    for i, version in enumerate(versions, 1):
        if tag_exists(version):
            continue
        if not (a.datalake / f"aws_cli_version={version}").exists():
            ingest(version, a.datalake, a.clone, quiet=True)
        catalog = load_catalog(a.datalake, version)
        commit_version(version, str(catalog["effective_date"]), emit_zsh(catalog), emit_bash(catalog), license_text)
        done += 1
        ops = sum(len(v) for v in catalog["service_operations"].values())
        rate = (time.monotonic() - started) / done
        print(f"[{i}/{len(versions)}] {version} ({catalog['effective_date']}): "
              f"{len(catalog['services'])} services, {ops} operations, ~{rate * (len(versions) - i) / 60:.0f} min left",
              flush=True)
    print(f"Done: {done} new versions on {BRANCH}.")


if __name__ == "__main__":
    os.environ.setdefault("GIT_TERMINAL_PROMPT", "0")
    main()
