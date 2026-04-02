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


def test_merge_cli_customs(tmp_path):
    botocore_path = make_botocore_fixture(tmp_path)
    from src.ingest import extract_operations, merge_cli_customs
    rows = extract_operations(botocore_path)
    customs = {"s3": ["cp", "ls", "sync"]}
    merged = merge_cli_customs(rows, customs)
    s3_ops = [r["operation_name"] for r in merged if r["service_name"] == "s3"]
    assert "cp" in s3_ops
    assert "ls" in s3_ops
    assert "sync" in s3_ops
    custom_rows = [r for r in merged if r["is_cli_custom"]]
    assert len(custom_rows) == 3


def test_extract_waiters(tmp_path):
    svc_dir = tmp_path / "botocore" / "data" / "ec2" / "2024-01-01"
    svc_dir.mkdir(parents=True)
    model = {"operations": {"DescribeInstances": {"name": "DescribeInstances"}}}
    with gzip.open(svc_dir / "service-2.json.gz", "wt") as f:
        json.dump(model, f)
    waiters = {"waiters": {"InstanceRunning": {"operation": "DescribeInstances"}, "InstanceStopped": {}}}
    with open(svc_dir / "waiters-2.json", "w") as f:
        json.dump(waiters, f)
    from src.ingest import extract_waiters
    rows = extract_waiters(tmp_path / "botocore")
    assert len(rows) == 2
    names = {r["waiter_name"] for r in rows}
    assert names == {"InstanceRunning", "InstanceStopped"}
    assert all(r["service_name"] == "ec2" for r in rows)


def test_extract_regions(tmp_path):
    endpoints = {
        "partitions": [
            {"partition": "aws", "regions": {"us-east-1": {}, "us-west-2": {}}},
            {"partition": "aws-us-gov", "regions": {"us-gov-west-1": {}}},
        ]
    }
    ep_path = tmp_path / "botocore" / "data" / "endpoints.json"
    ep_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ep_path, "w") as f:
        json.dump(endpoints, f)
    from src.ingest import extract_regions
    rows = extract_regions(tmp_path / "botocore")
    assert len(rows) == 3
    assert {"us-east-1", "us-west-2", "us-gov-west-1"} == {r["region_name"] for r in rows}
    gov = [r for r in rows if r["region_name"] == "us-gov-west-1"][0]
    assert gov["partition"] == "aws-us-gov"
