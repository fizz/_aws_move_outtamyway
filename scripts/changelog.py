"""Release notes: what changed between two generated zsh completers.

    python scripts/changelog.py OLD_FILE NEW_FILE [--old-version v0.1.0] [--new-version v0.2.0]

Reads the completion arrays, the regions and the catalog header of each file, and prints
Markdown for a GitHub release: a summary table, new and removed services, new and removed
operations and waiters per service, regions, and commands whose name changed. Standard
library only.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

BOTOCORE = "https://github.com/boto/botocore/commit/"


@dataclass
class Completer:
    date: str
    ref: str
    ops: dict[str, set[str]]
    waiters: dict[str, set[str]]
    regions: set[str]


def parse(text: str) -> Completer:
    header = re.search(r"Catalog date: (\S+) \| botocore ref: (\S+)", text)

    def array(name: str) -> dict[str, set[str]]:
        return {s: set(v.split()) for s, v in re.findall(rf"^{name}\[([^\]]+)\]='([^']*)'", text, re.M)}

    regions = re.search(r"local -a regions=\(([^)]*)\)", text)
    ops = array("_aws_momy_ops")
    # "wait" is the subcommand that opens the waiters, not an operation.
    for names in ops.values():
        names.discard("wait")
    return Completer(
        date=header.group(1) if header else "unknown",
        ref=header.group(2) if header else "unknown",
        ops=ops,
        waiters=array("_aws_momy_waiters"),
        regions=set(regions.group(1).split()) if regions else set(),
    )


def squash(name: str) -> str:
    return name.replace("-", "")


def diff(old: dict[str, set[str]], new: dict[str, set[str]], services: set[str]):
    """Added, removed and renamed names per service, for services in both files."""
    added, removed, renamed = {}, {}, {}
    for s in sorted(services):
        a, r = new[s] - old.get(s, set()), old.get(s, set()) - new[s]
        # Same letters, different hyphens: the name was corrected, not replaced.
        by_squash = {squash(n): n for n in a}
        pairs = sorted((n, by_squash[squash(n)]) for n in r if squash(n) in by_squash)
        for o, n in pairs:
            a.discard(n)
            r.discard(o)
        if a:
            added[s] = sorted(a)
        if r:
            removed[s] = sorted(r)
        if pairs:
            renamed[s] = pairs
    return added, removed, renamed


def count(d: dict[str, list]) -> int:
    return sum(len(v) for v in d.values())


def code(names) -> str:
    return " ".join(f"`{n}`" for n in names)


def details(title: str, names: list[str]) -> list[str]:
    return [f"<details><summary>{title}</summary>", "", code(names), "", "</details>"]


def signed(n: int) -> str:
    return f"+{n:,}" if n > 0 else (f"{n:,}" if n < 0 else "0")


def notes(old: Completer, new: Completer, old_version: str, new_version: str) -> str:
    new_services = sorted(set(new.ops) - set(old.ops))
    gone_services = sorted(set(old.ops) - set(new.ops))
    both = set(new.ops) & set(old.ops)
    ops_added, ops_removed, ops_renamed = diff(old.ops, new.ops, both)
    w_both = set(new.waiters) & set(old.waiters)
    w_added, w_removed, w_renamed = diff(old.waiters, new.waiters, w_both)
    for s in sorted(set(new.waiters) - set(old.waiters)):
        w_added[s] = sorted(new.waiters[s])
    for s in sorted(set(old.waiters) - set(new.waiters)):
        w_removed[s] = sorted(old.waiters[s])

    def total(d):
        return sum(len(v) for v in d.values())

    out = [
        f"The completer now matches botocore [`{new.ref}`]({BOTOCORE}{new.ref}) from {new.date}. "
        f"{old_version} matched [`{old.ref}`]({BOTOCORE}{old.ref}) from {old.date}.",
        "",
        f"| | {old_version} | {new_version} | Change |",
        "|---|--:|--:|--:|",
        f"| Services | {len(old.ops):,} | {len(new.ops):,} | {signed(len(new.ops) - len(old.ops))} |",
        f"| Operations | {total(old.ops):,} | {total(new.ops):,} | {signed(total(new.ops) - total(old.ops))} |",
        f"| Waiters | {total(old.waiters):,} | {total(new.waiters):,} | {signed(total(new.waiters) - total(old.waiters))} |",
        f"| Regions | {len(old.regions)} | {len(new.regions)} | {signed(len(new.regions) - len(old.regions))} |",
    ]

    if new_services:
        out += ["", f"## New services ({len(new_services)})", "", "| Service | Operations | Waiters |", "|---|--:|--:|"]
        out += [f"| `{s}` | {len(new.ops[s])} | {len(new.waiters.get(s, ()))} |" for s in new_services]
        out += [""]
        for s in new_services:
            out += details(f"<code>{s}</code>: {len(new.ops[s])} operations", sorted(new.ops[s]))
    if gone_services:
        out += ["", f"## Removed services ({len(gone_services)})", "", code(gone_services)]

    if ops_added:
        out += ["", f"## New operations in {len(ops_added)} services ({count(ops_added):,})", ""]
        for s in sorted(ops_added, key=lambda k: (-len(ops_added[k]), k)):
            out += details(f"<code>{s}</code> +{len(ops_added[s])}", ops_added[s])
    if ops_removed:
        out += ["", f"## Removed operations ({count(ops_removed)})", ""]
        out += [f"- `{s}`: {code(v)}" for s, v in sorted(ops_removed.items())]

    if w_added:
        out += ["", f"## New waiters ({count(w_added)})", "", "| Service | `aws <service> wait …` |", "|---|---|"]
        out += [f"| `{s}` | {code(w_added[s])} |" for s in sorted(w_added, key=lambda k: (-len(w_added[k]), k))]
    if w_removed:
        out += ["", f"## Removed waiters ({count(w_removed)})", ""]
        out += [f"- `{s}`: {code(v)}" for s, v in sorted(w_removed.items())]

    if new.regions != old.regions:
        out += ["", "## Regions", ""]
        if new.regions - old.regions:
            out += [f"Added: {code(sorted(new.regions - old.regions))}"]
        if old.regions - new.regions:
            out += [f"Removed: {code(sorted(old.regions - new.regions))}"]

    renamed = {**{s: v for s, v in ops_renamed.items()}}
    for s, v in w_renamed.items():
        renamed.setdefault(s, []).extend(v)
    if renamed:
        out += ["", f"## Corrected names ({count(renamed)})", "",
                "These now complete with the name the AWS CLI uses.", "", "| Service | Was | Now |", "|---|---|---|"]
        out += [f"| `{s}` | `{o}` | `{n}` |" for s in sorted(renamed) for o, n in renamed[s]]

    return "\n".join(out) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("old", type=Path)
    p.add_argument("new", type=Path)
    p.add_argument("--old-version", default="the previous release")
    p.add_argument("--new-version", default="this release")
    a = p.parse_args()
    sys.stdout.write(notes(parse(a.old.read_text()), parse(a.new.read_text()), a.old_version, a.new_version))


if __name__ == "__main__":
    main()
