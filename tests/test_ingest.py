import json
import gzip
from pathlib import Path


def make_botocore_fixture(tmp_path: Path) -> Path:
    """Create a minimal botocore data directory with services."""
    services = [
        ("s3", ["ListBuckets", "PutObject"]),
        ("sts", ["AssumeRole"]),
        ("config", ["DescribeConfigRules", "PutConfigRule"]),
        ("dynamodb", ["GetItem", "PutItem", "Query"]),
    ]
    for svc, ops in services:
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
    assert services == {"s3", "sts", "config", "dynamodb"}
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
    # Without aliases, s3 keeps both botocore ops and customs
    assert "cp" in s3_ops
    assert "ls" in s3_ops
    assert "sync" in s3_ops
    assert "ListBuckets" in s3_ops
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


def test_write_parquet(tmp_path):
    botocore_path = make_botocore_fixture(tmp_path)
    from src.ingest import extract_operations, extract_waiters, extract_regions, write_parquet
    import json
    operations = extract_operations(botocore_path)
    waiters = extract_waiters(botocore_path)
    # Make a fake endpoints.json
    ep = {"partitions": [{"partition": "aws", "regions": {"us-east-1": {}}}]}
    ep_path = botocore_path / "data" / "endpoints.json"
    with open(ep_path, "w") as f:
        json.dump(ep, f)
    regions = extract_regions(botocore_path)

    output_dir = tmp_path / "output"
    write_parquet(
        operations=operations,
        waiters=waiters,
        regions=regions,
        output_dir=output_dir,
        effective_date="2026-04-01",
    )

    partition_dir = output_dir / "dam_effective_date=2026-04-01"
    assert (partition_dir / "operations.parquet").exists()
    assert (partition_dir / "regions.parquet").exists()

    # Verify we can read it back with DuckDB
    import duckdb
    con = duckdb.connect()
    result = con.execute(f"SELECT COUNT(*) FROM '{partition_dir}/operations.parquet'").fetchone()
    assert result[0] == 8  # 2 s3 + 1 sts + 2 config + 3 dynamodb


def test_aliases_separate_porcelain_from_plumbing(tmp_path):
    """s3api gets botocore ops, s3 keeps only CLI customs (cp, ls, etc.)."""
    botocore_path = make_botocore_fixture(tmp_path)
    from src.ingest import extract_operations, merge_cli_customs
    rows = extract_operations(botocore_path)
    customs = {
        "_aliases": {
            "s3api": "s3",
            "configservice": "config",
            "ddb": "dynamodb",
        },
        "s3": ["cp", "ls", "mb", "mv", "presign", "rb", "rm", "sync", "website"],
        "login": [],
        "logout": [],
        "cli-dev": [],
    }
    merged = merge_cli_customs(rows, customs)

    # s3 should have ONLY CLI customs, no botocore operations
    s3_ops = sorted(r["operation_name"] for r in merged if r["service_name"] == "s3")
    assert s3_ops == ["cp", "ls", "mb", "mv", "presign", "rb", "rm", "sync", "website"]
    assert all(r["is_cli_custom"] for r in merged if r["service_name"] == "s3")

    # s3api should have the botocore operations (not marked as cli_custom)
    s3api_ops = sorted(r["operation_name"] for r in merged if r["service_name"] == "s3api")
    assert s3api_ops == ["ListBuckets", "PutObject"]
    assert not any(r["is_cli_custom"] for r in merged if r["service_name"] == "s3api")

    # configservice gets config's botocore ops
    cs_ops = sorted(r["operation_name"] for r in merged if r["service_name"] == "configservice")
    assert cs_ops == ["DescribeConfigRules", "PutConfigRule"]

    # ddb gets dynamodb's botocore ops
    ddb_ops = sorted(r["operation_name"] for r in merged if r["service_name"] == "ddb")
    assert ddb_ops == ["GetItem", "PutItem", "Query"]

    # Original aliased services (config, dynamodb) should have no entries
    assert not any(r["service_name"] == "config" for r in merged)
    assert not any(r["service_name"] == "dynamodb" for r in merged)

    # Pure CLI services appear in the service list
    services = {r["service_name"] for r in merged}
    assert "login" in services
    assert "logout" in services
    assert "cli-dev" in services

    # sts is unaffected (not aliased)
    sts_ops = [r["operation_name"] for r in merged if r["service_name"] == "sts"]
    assert sts_ops == ["AssumeRole"]
