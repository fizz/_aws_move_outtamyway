"""Layer 1: Ingest the AWS API contract of one AWS CLI v2 release.

AWS CLI v2 carries its own copy of botocore in awscli/botocore, with the service models,
waiters, endpoints and CLI name exceptions that its commands come from. Ingest reads
that copy at a release tag, so each catalog partition, and each completer, matches one
AWS CLI version exactly.
"""

from __future__ import annotations

import ast
import gzip
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


def write_parquet(
    operations: list[dict],
    waiters: list[dict],
    regions: list[dict],
    output_dir: Path,
    aws_cli_version: str,
    xform_exceptions: list[dict] | None = None,
) -> Path:
    """Write extracted data to the partition aws_cli_version=<version>."""
    import duckdb

    partition_dir = output_dir / f"aws_cli_version={aws_cli_version}"
    partition_dir.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()

    def _write(rows: list[dict], filename: str) -> None:
        if not rows:
            return
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(rows, f)
            tmp_name = f.name
        try:
            con.read_json(tmp_name).write_parquet(str(partition_dir / filename))
        finally:
            Path(tmp_name).unlink(missing_ok=True)

    _write(operations, "operations.parquet")
    _write(waiters, "waiters.parquet")
    _write(regions, "regions.parquet")
    _write(xform_exceptions or [], "xform_exceptions.parquet")

    con.close()
    return partition_dir


AWS_CLI_URL = "https://github.com/aws/aws-cli.git"
# The first AWS CLI v2 release with its own copy of botocore. Earlier v2 releases
# installed a pre-release botocore package instead.
FIRST_VERSION = (2, 3, 0)


def parse_version(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def aws_cli_versions(url: str = AWS_CLI_URL) -> list[str]:
    """The AWS CLI v2 release tags from FIRST_VERSION on, oldest first."""
    out = subprocess.run(["git", "ls-remote", "--tags", url], capture_output=True, text=True, check=True).stdout
    tags = {line.rsplit("refs/tags/", 1)[1] for line in out.splitlines() if "refs/tags/" in line}
    versions = [t for t in tags if re.fullmatch(r"2\.\d+\.\d+", t) and parse_version(t) >= FIRST_VERSION]
    return sorted(versions, key=parse_version)


def export_aws_cli(version: str, dest: Path, clone: Path | None = None) -> tuple[Path, str, str]:
    """Put awscli/botocore of one AWS CLI release under dest.

    With clone, a full local clone of aws/aws-cli, it uses git archive. Without, it makes
    a shallow, sparse clone of the tag. Returns the botocore root to read (dest/awscli),
    the commit SHA of the tag, and the date of that commit (YYYY-MM-DD).
    """
    env = os.environ.copy()
    env.pop("GIT_ASKPASS", None)
    env.pop("VSCODE_GIT_ASKPASS_MAIN", None)
    env["GIT_TERMINAL_PROMPT"] = "0"

    def git(*args: str, cwd: Path) -> str:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True, env=env).stdout.strip()

    if clone is None:
        clone = dest / "aws-cli"
        subprocess.run(
            ["git", "clone", "--depth=1", "--filter=blob:none", "--sparse", f"--branch={version}", AWS_CLI_URL, str(clone)],
            check=True, capture_output=True, env=env,
        )
        git("sparse-checkout", "set", "awscli/botocore", cwd=clone)
        root = clone / "awscli"
    else:
        archive = subprocess.run(
            ["git", "-C", str(clone), "archive", f"{version}^{{commit}}", "awscli/botocore"],
            capture_output=True, check=True, env=env,
        ).stdout
        subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True)
        root = dest / "awscli"
    sha = git("rev-parse", f"{version}^{{commit}}", cwd=clone)
    date = git("log", "-1", "--format=%cs", sha, cwd=clone)
    return root, sha, date


def ingest(version: str, output: Path, clone: Path | None = None, quiet: bool = False) -> Path:
    """Ingest one AWS CLI release into output/aws_cli_version=<version>/."""
    say = (lambda *a: None) if quiet else print
    with open(Path(__file__).parent.parent / "cli_customs.json") as f:
        customs = json.load(f)

    with tempfile.TemporaryDirectory() as tmp:
        say(f"Reading AWS CLI {version}...")
        root, sha, date = export_aws_cli(version, Path(tmp), clone)
        say(f"  ref: {sha}, released {date}")

        operations = merge_cli_customs(extract_operations(root, gitref=sha), customs)
        waiters = extract_waiters(root, gitref=sha)
        regions = extract_regions(root)
        xform_exceptions = extract_xform_exceptions(root, gitref=sha)
        for row in operations + waiters + regions + xform_exceptions:
            row["dam_effective_date"] = date
        say(f"  {len(operations)} operations ({len([r for r in operations if r['is_cli_custom']])} CLI customs), "
            f"{len(waiters)} waiters, {len(regions)} regions, {len(xform_exceptions)} name exceptions")

        partition = write_parquet(
            operations=operations,
            waiters=waiters,
            regions=regions,
            output_dir=output,
            aws_cli_version=version,
            xform_exceptions=xform_exceptions,
        )
        say(f"  written to {partition}/")
        return partition


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Ingest the API contract of an AWS CLI v2 release into the datalake")
    parser.add_argument("--output", type=Path, default=Path("datalake/aws_api_catalog"))
    parser.add_argument("--aws-cli-version", help="AWS CLI v2 release, for example 2.39.3. Default: the newest.")
    parser.add_argument("--aws-cli-clone", type=Path, help="A full local clone of aws/aws-cli to read the release from.")
    args = parser.parse_args()
    ingest(args.aws_cli_version or aws_cli_versions()[-1], args.output, args.aws_cli_clone)


def extract_operations(botocore_path: Path, gitref: str = "unknown") -> list[dict]:
    """Extract service names and operation names from botocore data directory.

    Returns list of dicts with keys:
        service_name, operation_name, is_cli_custom, dam_file_path, dam_gitref
    """
    data_dir = botocore_path / "data" if (botocore_path / "data").is_dir() else botocore_path / "botocore" / "data"
    rows = []
    for svc_dir in sorted(data_dir.iterdir()):
        if not svc_dir.is_dir() or svc_dir.name.startswith("_"):
            continue
        # Find latest version directory
        versions = sorted(d for d in svc_dir.iterdir() if d.is_dir())
        if not versions:
            continue
        latest = versions[-1]
        model_path = latest / "service-2.json.gz"
        if not model_path.exists():
            model_path = latest / "service-2.json"
        if not model_path.exists():
            continue
        if model_path.suffix == ".gz":
            with gzip.open(model_path, "rt") as f:
                model = json.load(f)
        else:
            with open(model_path) as f:
                model = json.load(f)
        rel_path = str(model_path.relative_to(botocore_path))
        for op_name in model.get("operations", {}):
            rows.append({
                "service_name": svc_dir.name,
                "operation_name": op_name,
                "is_cli_custom": False,
                "dam_file_path": rel_path,
                "dam_gitref": gitref,
            })
    return rows


def merge_cli_customs(operations: list[dict], customs: dict) -> list[dict]:
    """Merge CLI-only custom subcommands and aliases into the operations list.

    Handles three cases:
    1. _aliases (e.g. s3api -> s3): duplicates botocore ops under the alias name
       and removes them from the original service (so s3 keeps only CLI customs).
    2. Services with subcommands (e.g. s3, configure): adds them as is_cli_custom.
    3. Pure CLI services with no subcommands (e.g. login): adds a placeholder entry
       so the service appears in the service list.

    Args:
        operations: list of dicts from extract_operations
        customs: dict mapping service name -> list of custom subcommand strings,
                 plus optional _aliases key mapping CLI names to botocore dirs

    Returns:
        Combined list with aliases resolved and customs merged.
    """
    aliases = customs.get("_aliases", {})
    # Collect the set of botocore service names that are aliased away
    aliased_sources = set(aliases.values())

    result = []

    # Pass 1: copy botocore operations, skipping those claimed by aliases
    for row in operations:
        if row["service_name"] in aliased_sources:
            continue
        result.append(dict(row))

    # Pass 2: for each alias, duplicate botocore ops under the alias name
    for alias_name, botocore_name in aliases.items():
        for row in operations:
            if row["service_name"] == botocore_name:
                aliased = dict(row)
                aliased["service_name"] = alias_name
                result.append(aliased)

    # Pass 3: add CLI custom subcommands
    for service_name, subcommands in customs.items():
        if service_name == "_aliases":
            continue
        if subcommands:
            for subcommand in subcommands:
                result.append({
                    "service_name": service_name,
                    "operation_name": subcommand,
                    "is_cli_custom": True,
                    "dam_file_path": None,
                    "dam_gitref": None,
                })
        else:
            # Pure CLI command with no subcommands — add placeholder so the
            # service appears in the service list
            result.append({
                "service_name": service_name,
                "operation_name": service_name,
                "is_cli_custom": True,
                "dam_file_path": None,
                "dam_gitref": None,
            })

    return result


def extract_waiters(botocore_path: Path, gitref: str = "unknown") -> list[dict]:
    """Extract waiter names from waiters-2.json files in the botocore data directory.

    Returns list of dicts with keys:
        service_name, waiter_name, dam_file_path, dam_gitref
    """
    data_dir = botocore_path / "data" if (botocore_path / "data").is_dir() else botocore_path / "botocore" / "data"
    rows = []
    for svc_dir in sorted(data_dir.iterdir()):
        if not svc_dir.is_dir() or svc_dir.name.startswith("_"):
            continue
        versions = sorted(d for d in svc_dir.iterdir() if d.is_dir())
        if not versions:
            continue
        latest = versions[-1]
        waiters_path = latest / "waiters-2.json"
        if not waiters_path.exists():
            continue
        with open(waiters_path) as f:
            waiters_model = json.load(f)
        rel_path = str(waiters_path.relative_to(botocore_path))
        for waiter_name in waiters_model.get("waiters", {}):
            rows.append({
                "service_name": svc_dir.name,
                "waiter_name": waiter_name,
                "dam_file_path": rel_path,
                "dam_gitref": gitref,
            })
    return rows


def extract_xform_exceptions(botocore_path: Path, gitref: str = "unknown") -> list[dict]:
    """Extract the operation names that botocore's xform_name() does not derive by rule.

    The AWS CLI names a command with xform_name(operation, "-"). Its rule gets a few
    names wrong (CreateCachediSCSIVolume, ExecutePartiQLStatement, the WhatsApp
    operations), so botocore keeps their CLI names in the _xform_cache dict in
    botocore/__init__.py. This reads that dict from the source, at the same ref as the
    service models, without importing botocore.

    botocore_path is a clone of boto/botocore or a directory that contains the botocore
    package. Returns list of dicts with keys: operation_name, cli_name, dam_gitref
    """
    init = botocore_path / "botocore" / "__init__.py"
    if not init.exists():
        init = botocore_path / "__init__.py"
    for node in ast.parse(init.read_text()).body:
        target = node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None
        if isinstance(target, ast.Name) and target.id == "_xform_cache":
            cache = ast.literal_eval(node.value)
            return [
                {"operation_name": name, "cli_name": cli_name, "dam_gitref": gitref}
                for (name, sep), cli_name in sorted(cache.items())
                if sep == "-"
            ]
    raise ValueError(f"no _xform_cache in {init}")


def extract_regions(botocore_path: Path) -> list[dict]:
    """Extract region names from botocore's endpoints.json.

    Looks for endpoints.json at botocore_path/data/endpoints.json or
    botocore_path/botocore/data/endpoints.json.

    Returns list of dicts with keys: region_name, partition
    """
    data_dir = botocore_path / "data" if (botocore_path / "data").is_dir() else botocore_path / "botocore" / "data"
    ep_path = data_dir / "endpoints.json"
    with open(ep_path) as f:
        endpoints = json.load(f)
    rows = []
    for partition in endpoints.get("partitions", []):
        partition_name = partition.get("partition", "")
        for region_name in partition.get("regions", {}):
            rows.append({
                "region_name": region_name,
                "partition": partition_name,
            })
    return rows


if __name__ == "__main__":
    main()
