import duckdb
from pathlib import Path


def make_test_datalake(tmp_path: Path) -> Path:
    """Create a minimal datalake with parquet files."""
    con = duckdb.connect()

    partition = tmp_path / "dam_effective_date=2026-04-01"
    partition.mkdir(parents=True)

    ops = [
        {"service_name": "s3", "operation_name": "ListBuckets", "is_cli_custom": False, "dam_file_path": "x", "dam_gitref": "abc"},
        {"service_name": "s3", "operation_name": "PutObject", "is_cli_custom": False, "dam_file_path": "x", "dam_gitref": "abc"},
        {"service_name": "s3", "operation_name": "cp", "is_cli_custom": True, "dam_file_path": "cli_customs.json", "dam_gitref": "local"},
        {"service_name": "sts", "operation_name": "AssumeRole", "is_cli_custom": False, "dam_file_path": "y", "dam_gitref": "abc"},
    ]
    import json, tempfile, os
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(ops, f)
        ops_path = f.name
    con.execute(f"COPY (SELECT * FROM read_json('{ops_path}')) TO '{partition}/operations.parquet' (FORMAT PARQUET)")

    waiters = [
        {"service_name": "s3", "waiter_name": "BucketExists", "dam_file_path": "z", "dam_gitref": "abc"},
    ]
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(waiters, f)
        w_path = f.name
    con.execute(f"COPY (SELECT * FROM read_json('{w_path}')) TO '{partition}/waiters.parquet' (FORMAT PARQUET)")

    regions = [
        {"region_name": "us-east-1", "partition": "aws"},
        {"region_name": "us-gov-west-1", "partition": "aws-us-gov"},
    ]
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(regions, f)
        r_path = f.name
    con.execute(f"COPY (SELECT * FROM read_json('{r_path}')) TO '{partition}/regions.parquet' (FORMAT PARQUET)")

    con.close()
    os.unlink(ops_path)
    os.unlink(w_path)
    os.unlink(r_path)
    return tmp_path


def test_load_catalog(tmp_path):
    datalake = make_test_datalake(tmp_path)
    from src.generate import load_catalog
    catalog = load_catalog(datalake)

    assert "s3" in catalog["services"]
    assert "sts" in catalog["services"]
    # Operations should be kebab-case (non-customs transformed by inflector)
    s3_ops = catalog["service_operations"]["s3"]
    assert "list-buckets" in s3_ops
    assert "put-object" in s3_ops
    assert "cp" in s3_ops  # CLI custom — already lowercase, no transform
    # Waiters
    assert "bucket-exists" in catalog["service_waiters"].get("s3", [])
    # Regions
    assert "us-east-1" in catalog["regions"]
    assert "us-gov-west-1" in catalog["regions"]


def test_emit_zsh(tmp_path):
    datalake = make_test_datalake(tmp_path)
    from src.generate import load_catalog, emit_zsh
    catalog = load_catalog(datalake)
    output = emit_zsh(catalog)

    # Must NOT contain #compdef
    assert "#compdef" not in output
    # Must contain the function name
    assert "_aws_move_outtamyway()" in output
    # Must contain services
    assert "s3" in output
    assert "sts" in output
    # Must contain regions
    assert "us-east-1" in output
    assert "us-gov-west-1" in output
    # Must contain profile logic with sed (not grep -oP)
    assert "sed" in output
    assert "grep -oP" not in output
    # Must end with function call
    assert output.strip().endswith('_aws_move_outtamyway "$@"')
    # Must have wait subcommand for services with waiters
    assert "wait" in output


def test_emit_bash(tmp_path):
    datalake = make_test_datalake(tmp_path)
    from src.generate import load_catalog, emit_bash
    catalog = load_catalog(datalake)
    output = emit_bash(catalog)

    # Must contain function definition
    assert "_aws_move_outtamyway()" in output
    # Must contain complete registration
    assert "complete -F _aws_move_outtamyway aws" in output
    # Must contain services
    assert "s3" in output
    assert "sts" in output
    # Must contain regions
    assert "us-east-1" in output
    # Must contain profile logic with sed (not grep -oP)
    assert "sed" in output
    assert "grep -oP" not in output
    # Must use COMPREPLY
    assert "COMPREPLY" in output
    # Must use associative arrays (declare -A)
    assert "declare -gA" in output or "declare -A" in output
    # Must contain wait handling
    assert "wait" in output
