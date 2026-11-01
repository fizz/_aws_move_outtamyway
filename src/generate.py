"""Layer 3: Generate zsh completion function from DuckDB catalog."""

from __future__ import annotations
from pathlib import Path
import duckdb

# The CLI name of a botocore operation or waiter, as the AWS CLI derives it with
# botocore's xform_name(name, "-"): a plural acronym at the end becomes one word
# (ListWebACLs -> list-web-acls), a capital that starts a word is split off, and a
# capital after a lowercase letter or a digit is split off. tests/test_xform.py checks it
# against xform_name() for every model in botocore.
BOTOCORE_KEBAB = r"""
CREATE OR REPLACE MACRO botocore_kebab(name) AS lower(regexp_replace(regexp_replace(
    CASE WHEN regexp_matches(name, '[A-Z]{2,}s$')
         THEN regexp_replace(name, '[A-Z]{2,}s$', '') || '-' || lower(regexp_extract(name, '[A-Z]{2,}s$'))
         ELSE name END,
    '(.)([A-Z][a-z]+)', '\1-\2', 'g'),
    '([a-z0-9])([A-Z])', '\1-\2', 'g'))
"""


def define_cli_names(con: duckdb.DuckDBPyConnection, exceptions_glob: str | None = None, effective_date=None) -> None:
    """Define botocore_kebab() and the xform_exceptions table on a connection.

    xform_exceptions holds the names botocore spells out by hand (ingest reads them
    from botocore's source); an exception wins over the rule. Without the file the
    table is empty.
    """
    con.execute(BOTOCORE_KEBAB)
    con.execute("CREATE OR REPLACE TABLE xform_exceptions (operation_name VARCHAR, cli_name VARCHAR)")
    if exceptions_glob:
        try:
            con.execute(
                f"""INSERT INTO xform_exceptions
                    SELECT operation_name, cli_name FROM read_parquet('{exceptions_glob}', hive_partitioning=true)
                    WHERE dam_effective_date = '{effective_date}'"""
            )
        except duckdb.IOException:
            pass


def load_catalog(datalake_path: Path) -> dict:
    """Load the latest catalog partition from parquet via DuckDB."""
    con = duckdb.connect()

    ops_glob = str(datalake_path / "dam_effective_date=*" / "operations.parquet")
    waiters_glob = str(datalake_path / "dam_effective_date=*" / "waiters.parquet")
    regions_glob = str(datalake_path / "dam_effective_date=*" / "regions.parquet")
    exceptions_glob = str(datalake_path / "dam_effective_date=*" / "xform_exceptions.parquet")

    # Find latest effective date
    dates = con.execute(
        f"SELECT DISTINCT dam_effective_date FROM read_parquet('{ops_glob}', hive_partitioning=true) ORDER BY dam_effective_date DESC LIMIT 1"
    ).fetchall()
    effective_date = dates[0][0] if dates else "unknown"
    define_cli_names(con, exceptions_glob, effective_date)

    # Services
    services = sorted(
        r[0]
        for r in con.execute(
            f"SELECT DISTINCT service_name FROM read_parquet('{ops_glob}', hive_partitioning=true) WHERE dam_effective_date = '{effective_date}'"
        ).fetchall()
    )

    # Operations per service — kebab-case for botocore ops, as-is for CLI customs
    service_operations = {}
    rows = con.execute(
        f"""SELECT o.service_name,
                   CASE WHEN o.is_cli_custom THEN o.operation_name
                        ELSE coalesce(e.cli_name, botocore_kebab(o.operation_name))
                   END as cli_name
            FROM read_parquet('{ops_glob}', hive_partitioning=true) o
            LEFT JOIN xform_exceptions e ON e.operation_name = o.operation_name
            WHERE o.dam_effective_date = '{effective_date}'
            ORDER BY o.service_name, cli_name"""
    ).fetchall()
    for svc, op in rows:
        service_operations.setdefault(svc, []).append(op)

    # Waiters per service — kebab-case
    service_waiters = {}
    try:
        waiter_rows = con.execute(
            f"""SELECT w.service_name, coalesce(e.cli_name, botocore_kebab(w.waiter_name)) as cli_name
                FROM read_parquet('{waiters_glob}', hive_partitioning=true) w
                LEFT JOIN xform_exceptions e ON e.operation_name = w.waiter_name
                WHERE w.dam_effective_date = '{effective_date}'
                ORDER BY w.service_name, cli_name"""
        ).fetchall()
        for svc, name in waiter_rows:
            service_waiters.setdefault(svc, []).append(name)
    except duckdb.IOException:
        pass

    # Regions
    try:
        region_rows = con.execute(
            f"SELECT DISTINCT region_name FROM read_parquet('{regions_glob}', hive_partitioning=true) ORDER BY region_name"
        ).fetchall()
        regions = [r[0] for r in region_rows]
    except duckdb.IOException:
        regions = []

    # Gitref
    gitref_rows = con.execute(
        f"SELECT DISTINCT dam_gitref FROM read_parquet('{ops_glob}', hive_partitioning=true) WHERE dam_effective_date = '{effective_date}' AND dam_gitref != 'local' LIMIT 1"
    ).fetchall()
    gitref = gitref_rows[0][0] if gitref_rows else "unknown"

    con.close()

    return {
        "services": services,
        "service_operations": service_operations,
        "service_waiters": service_waiters,
        "regions": regions,
        "effective_date": effective_date,
        "gitref": gitref,
    }


def emit_zsh(catalog: dict) -> str:
    """Generate the zsh completion function from catalog data."""
    services = catalog["services"]
    service_ops = catalog["service_operations"]
    service_waiters = catalog["service_waiters"]
    regions = catalog["regions"]
    effective_date = catalog["effective_date"]
    gitref = catalog["gitref"]

    lines = []
    lines.append("# Generated by _aws_gen from damsight catalog")
    lines.append("# Not a compdef file — registered via explicit compdef in .zshrc")
    lines.append(f"# Catalog date: {effective_date} | botocore ref: {gitref[:8]}")
    lines.append(f"# Services: {len(services)} | Operations: {sum(len(v) for v in service_ops.values())}")
    lines.append("")

    # Service -> operations associative array
    lines.append("typeset -gA _aws_momy_ops")
    for svc in services:
        ops = list(service_ops.get(svc, []))
        if svc in service_waiters:
            ops = sorted(set(ops + ["wait"]))
        ops_str = " ".join(ops)
        lines.append(f"_aws_momy_ops[{svc}]='{ops_str}'")
    lines.append("")

    # Service -> waiters associative array
    lines.append("typeset -gA _aws_momy_waiters")
    for svc, waiters in sorted(service_waiters.items()):
        lines.append(f"_aws_momy_waiters[{svc}]='{' '.join(waiters)}'")
    lines.append("")

    # The function
    lines.append("_aws_move_outtamyway() {")
    lines.append('  local curcontext="$curcontext" state line')
    lines.append("  typeset -A opt_args")
    lines.append("")
    lines.append("  _arguments -C \\")
    lines.append("    '1:service:->service' \\")
    lines.append("    '2:subcommand:->subcommand' \\")
    lines.append("    '*::argument:->argument' \\")
    lines.append("    '--region[AWS region]:region:->region' \\")
    lines.append("    '--profile[AWS profile]:profile:->profile' \\")
    lines.append('    "--output[Output format]:format:(json text table yaml yaml-stream)" \\')
    lines.append("    '--query[JMESPath query]:query:' \\")
    lines.append("    '--no-paginate[Disable pagination]' \\")
    lines.append("    '--no-sign-request[Skip signing]' \\")
    lines.append("    '--no-verify-ssl[Skip SSL verification]' \\")
    lines.append("    '--debug[Enable debug logging]' \\")
    lines.append("    '--endpoint-url[Override endpoint URL]:url:_urls' \\")
    lines.append('    "--color[Color output]:(on off auto)" \\')
    lines.append("    '--ca-bundle[CA certificate bundle]:file:_files' \\")
    lines.append("    '--cli-read-timeout[Socket read timeout]:seconds:' \\")
    lines.append("    '--cli-connect-timeout[Socket connect timeout]:seconds:'")
    lines.append("")
    lines.append("  case $state in")
    lines.append("    service)")
    lines.append(f"      local -a services=({' '.join(services)})")
    lines.append("      _describe 'AWS service' services")
    lines.append("      ;;")
    lines.append("    subcommand)")
    lines.append('      local service="${line[1]}"')
    lines.append('      local ops_str="${_aws_momy_ops[$service]}"')
    lines.append("      if [[ -n $ops_str ]]; then")
    lines.append("        local -a ops=(${(s: :)ops_str})")
    lines.append('        _describe "$service subcommand" ops')
    lines.append("      fi")
    lines.append("      ;;")
    lines.append("    argument)")
    lines.append('      local service="${line[1]}" subcommand="${line[2]}"')
    lines.append("      if [[ $subcommand == wait ]]; then")
    lines.append('        local waiters_str="${_aws_momy_waiters[$service]}"')
    lines.append("        if [[ -n $waiters_str ]]; then")
    lines.append("          local -a waiters=(${(s: :)waiters_str})")
    lines.append('          _describe "$service waiter" waiters')
    lines.append("        fi")
    lines.append("        return")
    lines.append("      fi")
    lines.append("      _files")
    lines.append("      ;;")
    lines.append("    region)")
    lines.append(f"      local -a regions=({' '.join(regions)})")
    lines.append("      _describe 'AWS region' regions")
    lines.append("      ;;")
    lines.append("    profile)")
    lines.append("      local -a profiles")
    lines.append("      profiles=(${(f)\"$(command sed -n 's/^\\[profile \\(.*\\)\\]/\\1/p' ~/.aws/config 2>/dev/null)\"})")
    lines.append("      [[ -n $profiles ]] && _describe 'AWS profile' profiles")
    lines.append("      ;;")
    lines.append("  esac")
    lines.append("}")
    lines.append("")
    lines.append('_aws_move_outtamyway "$@"')
    lines.append("")

    return "\n".join(lines)


def emit_bash(catalog: dict) -> str:
    """Generate a bash completion script from catalog data."""
    services = catalog["services"]
    service_ops = catalog["service_operations"]
    service_waiters = catalog["service_waiters"]
    regions = catalog["regions"]
    effective_date = catalog["effective_date"]
    gitref = catalog["gitref"]

    global_opts = (
        "--region --profile --output --query --no-paginate --no-sign-request "
        "--no-verify-ssl --debug --endpoint-url --color --ca-bundle "
        "--cli-read-timeout --cli-connect-timeout"
    )

    lines = []
    lines.append("# Generated by _aws_gen from damsight catalog")
    lines.append("# Bash 4+ completion for the aws CLI")
    lines.append(f"# Catalog date: {effective_date} | botocore ref: {gitref[:8]}")
    lines.append(f"# Services: {len(services)} | Operations: {sum(len(v) for v in service_ops.values())}")
    lines.append("")

    # Associative array: service -> operations
    lines.append("declare -gA _aws_momy_ops")
    for svc in services:
        ops = list(service_ops.get(svc, []))
        if svc in service_waiters:
            ops = sorted(set(ops + ["wait"]))
        lines.append(f"_aws_momy_ops[{svc}]='{' '.join(ops)}'")
    lines.append("")

    # Associative array: service -> waiters
    lines.append("declare -gA _aws_momy_waiters")
    for svc, waiters in sorted(service_waiters.items()):
        lines.append(f"_aws_momy_waiters[{svc}]='{' '.join(waiters)}'")
    lines.append("")

    # Static data
    lines.append(f"_aws_momy_services='{' '.join(services)}'")
    lines.append(f"_aws_momy_regions='{' '.join(regions)}'")
    lines.append(f"_aws_momy_global_opts='{global_opts}'")
    lines.append("")

    # The completion function
    lines.append("_aws_move_outtamyway() {")
    lines.append('    local cur="${COMP_WORDS[COMP_CWORD]}"')
    lines.append('    local prev="${COMP_WORDS[COMP_CWORD-1]}"')
    lines.append("")
    lines.append("    # Find positional args (skip flags and their values)")
    lines.append("    local service=\"\" subcommand=\"\" pos=0")
    lines.append("    local i=1")
    lines.append("    while [[ $i -lt $COMP_CWORD ]]; do")
    lines.append('        local word="${COMP_WORDS[$i]}"')
    lines.append("        case \"$word\" in")
    lines.append("            --region|--profile|--output|--query|--endpoint-url|--color|--ca-bundle|--cli-read-timeout|--cli-connect-timeout)")
    lines.append("                ((i++))  # skip the flag's value")
    lines.append("                ;;")
    lines.append("            --*)")
    lines.append("                ;;  # standalone flag, no value to skip")
    lines.append("            *)")
    lines.append("                if [[ $pos -eq 0 ]]; then")
    lines.append("                    service=\"$word\"")
    lines.append("                elif [[ $pos -eq 1 ]]; then")
    lines.append("                    subcommand=\"$word\"")
    lines.append("                fi")
    lines.append("                ((pos++))")
    lines.append("                ;;")
    lines.append("        esac")
    lines.append("        ((i++))")
    lines.append("    done")
    lines.append("")
    lines.append("    # Complete flag values")
    lines.append("    case \"$prev\" in")
    lines.append("        --region)")
    lines.append("            COMPREPLY=($(compgen -W \"$_aws_momy_regions\" -- \"$cur\"))")
    lines.append("            return")
    lines.append("            ;;")
    lines.append("        --profile)")
    lines.append("            local profiles")
    lines.append("            profiles=\"$(sed -n 's/^\\[profile \\(.*\\)\\]/\\1/p' ~/.aws/config 2>/dev/null)\"")
    lines.append("            COMPREPLY=($(compgen -W \"$profiles\" -- \"$cur\"))")
    lines.append("            return")
    lines.append("            ;;")
    lines.append("        --output)")
    lines.append("            COMPREPLY=($(compgen -W \"json text table yaml yaml-stream\" -- \"$cur\"))")
    lines.append("            return")
    lines.append("            ;;")
    lines.append("        --color)")
    lines.append("            COMPREPLY=($(compgen -W \"on off auto\" -- \"$cur\"))")
    lines.append("            return")
    lines.append("            ;;")
    lines.append("        --endpoint-url|--query|--ca-bundle|--cli-read-timeout|--cli-connect-timeout)")
    lines.append("            return  # no completion for free-form values")
    lines.append("            ;;")
    lines.append("    esac")
    lines.append("")
    lines.append("    # Complete flags")
    lines.append('    if [[ "$cur" == --* ]]; then')
    lines.append("        COMPREPLY=($(compgen -W \"$_aws_momy_global_opts\" -- \"$cur\"))")
    lines.append("        return")
    lines.append("    fi")
    lines.append("")
    lines.append("    # Positional completion")
    lines.append("    if [[ $pos -eq 0 ]]; then")
    lines.append("        # Service name")
    lines.append("        COMPREPLY=($(compgen -W \"$_aws_momy_services\" -- \"$cur\"))")
    lines.append("    elif [[ $pos -eq 1 ]]; then")
    lines.append("        # Subcommand for service")
    lines.append('        local ops="${_aws_momy_ops[$service]}"')
    lines.append("        if [[ -n \"$ops\" ]]; then")
    lines.append("            COMPREPLY=($(compgen -W \"$ops\" -- \"$cur\"))")
    lines.append("        fi")
    lines.append("    elif [[ $pos -eq 2 && \"$subcommand\" == \"wait\" ]]; then")
    lines.append("        # Waiter name")
    lines.append('        local waiters="${_aws_momy_waiters[$service]}"')
    lines.append("        if [[ -n \"$waiters\" ]]; then")
    lines.append("            COMPREPLY=($(compgen -W \"$waiters\" -- \"$cur\"))")
    lines.append("        fi")
    lines.append("    else")
    lines.append("        # Fall through to file completion")
    lines.append("        COMPREPLY=($(compgen -f -- \"$cur\"))")
    lines.append("    fi")
    lines.append("}")
    lines.append("")
    lines.append("complete -F _aws_move_outtamyway aws")
    lines.append("")

    return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Generate shell completers from datalake catalog")
    parser.add_argument("--datalake", type=Path, default=Path("datalake/aws_api_catalog"))
    parser.add_argument("--output", type=Path, default=Path("_aws_move_outtamyway"))
    parser.add_argument("--output-bash", type=Path, default=None)
    args = parser.parse_args()

    print(f"Loading catalog from {args.datalake}...")
    catalog = load_catalog(args.datalake)
    print(f"  {len(catalog['services'])} services, {sum(len(v) for v in catalog['service_operations'].values())} operations")
    print(f"  {sum(len(v) for v in catalog['service_waiters'].values())} waiters, {len(catalog['regions'])} regions")

    print(f"Generating zsh: {args.output}...")
    zsh = emit_zsh(catalog)
    args.output.write_text(zsh)
    print(f"  {len(zsh)} bytes written")

    if args.output_bash:
        print(f"Generating bash: {args.output_bash}...")
        bash = emit_bash(catalog)
        args.output_bash.write_text(bash)
        print(f"  {len(bash)} bytes written")

    print("Done.")


if __name__ == "__main__":
    main()
