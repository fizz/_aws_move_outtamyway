"""Layer 1: Ingest AWS API contract from botocore + aws-cli repos."""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def write_parquet(
    operations: list[dict],
    waiters: list[dict],
    regions: list[dict],
    output_dir: Path,
    effective_date: str,
) -> Path:
    """Write extracted data to partitioned parquet files."""
    import duckdb

    partition_dir = output_dir / f"dam_effective_date={effective_date}"
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

    con.close()
    return partition_dir


def clone_repo(url: str, branch: str, dest: Path) -> str:
    """Shallow clone a git repo and return the HEAD commit SHA."""
    # Suppress VS Code's GIT_ASKPASS injection which breaks unauthenticated HTTPS clones
    env = os.environ.copy()
    env.pop("GIT_ASKPASS", None)
    env.pop("VSCODE_GIT_ASKPASS_MAIN", None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    subprocess.run(
        ["git", "clone", "--depth=1", f"--branch={branch}", url, str(dest)],
        check=True,
        capture_output=True,
        env=env,
    )
    result = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Ingest AWS API contract into datalake")
    parser.add_argument("--output", type=Path, default=Path("datalake/aws_api_catalog"))
    args = parser.parse_args()

    effective_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    customs_path = Path(__file__).parent.parent / "cli_customs.json"
    with open(customs_path) as f:
        customs = json.load(f)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Clone botocore
        botocore_dest = tmp_path / "botocore"
        print("Cloning boto/botocore...")
        botocore_ref = clone_repo("https://github.com/boto/botocore.git", "develop", botocore_dest)
        print(f"  ref: {botocore_ref}")

        # Extract
        print("Extracting operations...")
        operations = extract_operations(botocore_dest, gitref=botocore_ref)
        operations = merge_cli_customs(operations, customs)
        print(f"  {len(operations)} operations ({len([r for r in operations if r['is_cli_custom']])} CLI customs)")

        print("Extracting waiters...")
        waiters = extract_waiters(botocore_dest, gitref=botocore_ref)
        print(f"  {len(waiters)} waiters")

        print("Extracting regions...")
        regions = extract_regions(botocore_dest)
        print(f"  {len(regions)} regions")

        # Write
        print(f"Writing parquet to {args.output}/dam_effective_date={effective_date}/")
        write_parquet(
            operations=operations,
            waiters=waiters,
            regions=regions,
            output_dir=args.output,
            effective_date=effective_date,
        )
        print("Done.")


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
