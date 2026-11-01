"""CLI names: the botocore_kebab() macro and the exceptions read from botocore's source."""

from pathlib import Path

import duckdb
import pytest

from src.generate import define_cli_names
from src.ingest import extract_operations, extract_waiters, extract_xform_exceptions


def cli_names(names: list[str], exceptions: list[dict]) -> dict[str, str]:
    """The CLI name of each name, the way generate.py derives it."""
    con = duckdb.connect()
    define_cli_names(con)
    if exceptions:
        con.executemany(
            "INSERT INTO xform_exceptions VALUES (?, ?)",
            [(e["operation_name"], e["cli_name"]) for e in exceptions],
        )
    rows = con.execute(
        """SELECT n, coalesce(e.cli_name, botocore_kebab(n))
           FROM (SELECT unnest(?::VARCHAR[]) AS n) t
           LEFT JOIN xform_exceptions e ON e.operation_name = n""",
        [names],
    ).fetchall()
    return dict(rows)


def test_names_the_rule_derives():
    expected = {
        "DescribeInstances": "describe-instances",
        "ListWebACLs": "list-web-acls",
        "GetStatementResultV2": "get-statement-result-v2",
        "FunctionActiveV2": "function-active-v2",
        "ListS3Buckets": "list-s3-buckets",
        "CreateAndAttachS3AccessPoint": "create-and-attach-s3-access-point",
        "GetEC2RecommendationProjectedMetrics": "get-ec2-recommendation-projected-metrics",
        "DescribeICD10CMInferenceJob": "describe-icd10-cm-inference-job",
        "ListHITs": "list-hits",
    }
    assert cli_names(list(expected), []) == expected


def test_an_exception_wins_over_the_rule():
    exceptions = [{"operation_name": "CreateCachediSCSIVolume", "cli_name": "create-cached-iscsi-volume"}]
    assert cli_names(["CreateCachediSCSIVolume"], exceptions) == {
        "CreateCachediSCSIVolume": "create-cached-iscsi-volume"
    }


def test_extract_xform_exceptions_keeps_the_cli_spelling(tmp_path):
    pkg = tmp_path / "botocore"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "import re\n"
        "_xform_cache = {\n"
        "    ('ExecutePartiQLStatement', '_'): 'execute_partiql_statement',\n"
        "    ('ExecutePartiQLStatement', '-'): 'execute-partiql-statement',\n"
        "}\n"
    )
    assert extract_xform_exceptions(tmp_path, gitref="abc") == [
        {"operation_name": "ExecutePartiQLStatement", "cli_name": "execute-partiql-statement", "dam_gitref": "abc"}
    ]


def test_every_botocore_name_matches_xform_name():
    """Every operation and waiter in the installed botocore gets the name the CLI uses."""
    botocore = pytest.importorskip("botocore")
    package = Path(botocore.__file__).parent
    names = sorted(
        {r["operation_name"] for r in extract_operations(package)}
        | {r["waiter_name"] for r in extract_waiters(package)}
    )
    assert len(names) > 10000
    got = cli_names(names, extract_xform_exceptions(package.parent))
    wrong = {n: (got[n], botocore.xform_name(n, "-")) for n in names if got[n] != botocore.xform_name(n, "-")}
    assert wrong == {}
