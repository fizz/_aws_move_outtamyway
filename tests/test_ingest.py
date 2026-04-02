import json
import gzip
from pathlib import Path


def make_botocore_fixture(tmp_path: Path) -> Path:
    """Create a minimal botocore data directory with two services."""
    for svc, ops in [("s3", ["ListBuckets", "PutObject"]), ("sts", ["AssumeRole"])]:
        svc_dir = tmp_path / "botocore" / "data" / svc / "2024-01-01"
        svc_dir.mkdir(parents=True)
        model = {"operations": {op: {"name": op, "input": {"shape": f"{op}Request"}} for op in ops}}
        with gzip.open(svc_dir / "service-2.json.gz", "wt") as f:
            json.dump(model, f)
    return tmp_path / "botocore"


def test_extract_services_and_operations(tmp_path):
    botocore_path = make_botocore_fixture(tmp_path)
    from src.ingest import extract_operations
    rows = extract_operations(botocore_path)
    services = {r["service_name"] for r in rows}
    assert services == {"s3", "sts"}
    s3_ops = [r["operation_name"] for r in rows if r["service_name"] == "s3"]
    assert sorted(s3_ops) == ["ListBuckets", "PutObject"]
    # Verify provenance fields present
    assert all("dam_file_path" in r for r in rows)
