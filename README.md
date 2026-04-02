# _aws_move_outtamyway

A zero-latency shell completer for the AWS CLI. Supports zsh and bash. No Python on tab. No network calls. No waiting.

The stock AWS CLI completer (`aws_completer`) spawns a Python process on every tab press, loads the entire CLI framework, and sometimes makes network calls to enumerate your resources. On a machine with 50+ SSO profiles and 400+ services, that means seconds of lag — or disabling completion entirely.

This project replaces it with a static completion function (zsh or bash) generated from a DuckDB catalog of the AWS API contract. The tab key returns in milliseconds because there is nothing to compute. The vocabulary is pre-baked.

## What it completes

- **428 services** — every service the AWS CLI exposes, including aliases (`s3api`, `configservice`, `ddb`)
- **18,115 operations** — every API operation plus CLI-only commands (`s3 cp`, `s3 sync`, `configure set`)
- **341 waiters** — `aws ec2 wait instance-running` and friends
- **46 regions** — all 7 AWS partitions (commercial, China, GovCloud, and the four classified ones)
- **`--profile`** — parsed from `~/.aws/config` at tab time (the only file read the completer does)
- **`--region`**, **`--output`** — static completions from the catalog
- **`_files`** — local path completion in argument position (the stock completer never does this)

## Install

Clone the repo and run `make install`, which copies the completion files for both zsh and bash:

```
git clone https://github.com/fizz/_aws_move_outtamyway.git
cd _aws_move_outtamyway
make install
```

Then tell your shell to use it. For **zsh**, add to `.zshrc` after `compinit`:

```zsh
autoload -Uz ~/.zsh/completions/_aws_move_outtamyway
compdef _aws_move_outtamyway aws
```

For **bash**, add to `.bashrc`:

```bash
source ~/.bash_completion.d/_aws_move_outtamyway.bash
```

Open a new shell. `aws s3 [tab]` should be instant.

## How it works

Three layers, fully decoupled:

```
botocore (GitHub)  →  parquet datalake  →  DuckDB + inflector  →  static zsh function
     ingest              catalog              generate              tab time
```

**Ingest** clones `boto/botocore` from GitHub, walks the service model JSON files, and writes partitioned parquet. It also reads a maintained registry (`cli_customs.json`) for the handful of CLI-only commands that don't exist in botocore — things like `s3 cp` and `configure set`.

**Catalog** is DuckDB reading parquet directly. The [inflector](https://github.com/thiruselvaa/duckdb-extensions-inflector) community extension handles the PascalCase-to-kebab-case transformation that botocore operations need (`DescribeInstances` → `describe-instances`). The original PascalCase is preserved in the parquet — transformation happens at query time.

**Generate** queries the catalog and emits completion files for both zsh and bash. Each contains two associative arrays (services→operations, services→waiters), the completion function, and static global options. Both generated files are committed to the repo, so you can install without running the pipeline yourself.

## Regenerating

When AWS adds services or operations (they do, constantly), regenerate from the latest botocore:

```bash
make ingest    # clone botocore, write parquet (~30 seconds)
make generate  # query catalog, emit zsh (~3 seconds)
make install   # copy to fpath
```

The ingest writes date-partitioned parquet, so you accumulate a history of the API contract over time. DuckDB makes this queryable:

```sql
-- what did AWS add this week?
SELECT a.service_name, a.operation_name FROM operations a
LEFT JOIN operations b ON a.service_name = b.service_name
  AND a.operation_name = b.operation_name
  AND b.dam_effective_date = '2026-03-25'
WHERE a.dam_effective_date = '2026-04-01' AND b.operation_name IS NULL;
```

## Requirements

- zsh or bash 4+ (for the completer itself — zero other runtime dependencies)
- [uv](https://docs.astral.sh/uv/) (only if you want to regenerate from botocore)
- DuckDB (installed automatically as a Python dependency when regenerating)

## The name

The stock completer's lag was so bad I had `unset '_comps[aws]'` in my zshrc for years, with the comment: *"get rid of the insane lag autocompleting local paths in aws commands."* This project finally replaces that line with something better.

## License

MIT
