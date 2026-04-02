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
