"""Layer 1: Ingest AWS API contract from botocore + aws-cli repos."""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


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
    """Merge CLI-only custom subcommands into the operations list.

    Args:
        operations: list of dicts from extract_operations
        customs: dict mapping service name -> list of custom subcommand strings

    Returns:
        Combined list; custom entries have is_cli_custom=True.
    """
    result = list(operations)
    for service_name, subcommands in customs.items():
        for subcommand in subcommands:
            result.append({
                "service_name": service_name,
                "operation_name": subcommand,
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
