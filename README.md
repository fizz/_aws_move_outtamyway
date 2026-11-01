# _aws_move_outtamyway

A zero-latency shell completer for the AWS CLI. Supports zsh and bash. No Python on tab. No network calls. No waiting.

The stock AWS CLI completer (`aws_completer`) spawns a Python process on every tab press, loads the entire CLI framework, and sometimes makes network calls to enumerate your resources. On a machine with 50+ SSO profiles and 400+ services, that means seconds of lag — or disabling completion entirely.

This project replaces it with a static completion function (zsh or bash) generated from a DuckDB catalog of the AWS API contract. The tab key returns in milliseconds because there is nothing to compute. The vocabulary is pre-baked.

Each release is named after the AWS CLI v2 release it was generated from, and completes exactly that CLI's commands. There is a release for every AWS CLI v2 release since 2.3.0 (October 2021).

## What it completes

In AWS CLI 2.39.3:

- **442 services** — every service the AWS CLI exposes, including aliases (`s3api`, `configservice`, `ddb`)
- **19,516 operations** — every API operation plus CLI-only commands (`s3 cp`, `s3 sync`, `configure set`)
- **403 waiters** — `aws ec2 wait instance-running` and friends
- **46 regions** — all 7 AWS partitions (commercial, China, GovCloud, and the four classified ones)
- **`--profile`** — parsed from `~/.aws/config` at tab time (the only file read the completer does)
- **`--region`**, **`--output`** — static completions from the catalog
- **`_files`** — local path completion in argument position (the stock completer never does this)

## In action

`aws s3 [tab]` — porcelain commands only (the botocore operations are under `s3api`):

```
 -- AWS service --
cp  ls  mb  mv  presign  rb  rm  sync  wait  website
```

`aws s3api [tab]` — the real botocore surface:

```
abort-multipart-upload    create-bucket             delete-bucket-cors        get-bucket-acl            list-buckets               put-bucket-acl
complete-multipart-upload create-multipart-upload    delete-bucket-encryption  get-bucket-encryption     list-multipart-uploads     put-bucket-encryption
copy-object               create-session            delete-object             get-object                list-objects-v2            put-object
...
```

`aws ec2 wait [tab]` — 43 ec2 waiters, from botocore's `waiters-2.json`:

```
 -- ec2 waiter --
bundle-task-complete      instance-exists           nat-gateway-available     snapshot-completed        volume-available
conversion-task-cancelled instance-running          nat-gateway-deleted       spot-instance-request-fulfilled  volume-deleted
customer-gateway-available instance-stopped         network-interface-available subnet-available         vpc-available
export-task-completed     instance-terminated       password-data-available   system-status-ok          vpn-connection-available
image-available           internet-gateway-exists   security-group-exists     volume-in-use             vpn-connection-deleted
...
```

`aws --region [tab]` — all 46 regions across 7 AWS partitions:

```
 -- AWS region --
af-south-1      ap-southeast-1  ca-west-1       eu-south-2      me-central-1    us-east-2       us-isob-east-1
ap-east-1       ap-southeast-2  cn-north-1      eu-west-1       me-south-1      us-gov-east-1   us-isof-south-1
ap-northeast-1  ap-southeast-3  cn-northwest-1  eu-west-2       sa-east-1       us-gov-west-1   us-west-1
ap-northeast-2  ap-southeast-4  eu-central-1    eu-west-3       us-east-1       us-iso-east-1   us-west-2
ap-northeast-3  ap-south-1      eu-central-2    il-central-1    us-iso-west-1
ap-south-2      ca-central-1    eu-north-1      mx-central-1
```

`aws --output [tab]`:

```
json  table  text  yaml  yaml-stream
```

`aws --profile [tab]` — every profile from `~/.aws/config`, instantly:

```
 -- AWS profile --
Prod.AdministratorAccess       Staging.PowerUserAccess        dev
Dev.AdministratorAccess        Sandbox.ReadOnlyAccess         prod
GovCloud.AdministratorAccess   Security.AWSAdministratorAccess
...
```

## Install

### Quick install (one file, no clone)

Download the release for the AWS CLI you run. `aws --version` shows it, for example `aws-cli/2.39.3`.

For **zsh**:

```
v=$(aws --version | sed -E 's|^aws-cli/([0-9.]+).*|\1|')
mkdir -p ~/.zsh/completions
curl -fL https://github.com/fizz/_aws_move_outtamyway/releases/download/$v/_aws_move_outtamyway \
  -o ~/.zsh/completions/_aws_move_outtamyway
```

Add to `.zshrc` after `compinit`:

```zsh
autoload -Uz ~/.zsh/completions/_aws_move_outtamyway
compdef _aws_move_outtamyway aws
```

For **bash**:

```
v=$(aws --version | sed -E 's|^aws-cli/([0-9.]+).*|\1|')
mkdir -p ~/.bash_completion.d
curl -fL https://github.com/fizz/_aws_move_outtamyway/releases/download/$v/_aws_move_outtamyway.bash \
  -o ~/.bash_completion.d/_aws_move_outtamyway.bash
```

Add to `.bashrc`:

```bash
source ~/.bash_completion.d/_aws_move_outtamyway.bash
```

Open a new shell. `aws s3 [tab]` should be instant. After you upgrade the AWS CLI, run the download again. A release that does not exist yet (the CLI was released minutes ago) fails with a 404; use the previous version until it is published.

### From source (if you want to regenerate)

```
git clone https://github.com/fizz/_aws_move_outtamyway.git
cd _aws_move_outtamyway
make install
```

This installs both the zsh and bash completions. Add the shell config lines above, then open a new shell.

## How it works

Three layers, fully decoupled:

```
aws-cli release  →  parquet datalake  →  DuckDB  →  static zsh function
     ingest              catalog              generate              tab time
```

**Ingest** reads `awscli/botocore` from one `aws/aws-cli` release tag (the AWS CLI v2's own copy of botocore), walks the service model JSON files, and writes one parquet partition per AWS CLI version. It also reads a maintained registry (`cli_customs.json`) for the handful of CLI-only commands that don't exist in botocore — things like `s3 cp` and `configure set`.

**Catalog** is DuckDB reading parquet directly. A SQL macro turns botocore's PascalCase into the command names the AWS CLI uses (`DescribeInstances` → `describe-instances`, `GetStatementResultV2` → `get-statement-result-v2`, `ListWebACLs` → `list-web-acls`). It applies the same rule as botocore's `xform_name()`, and a table of the names botocore spells out by hand (`ExecutePartiQLStatement` → `execute-partiql-statement`), read from that release's source at ingest, wins over the rule. The original PascalCase is preserved in the parquet — transformation happens at query time. A test checks every operation and waiter in botocore against `xform_name()`.

**Generate** queries the catalog and emits completion files for both zsh and bash. Each contains two associative arrays (services→operations, services→waiters), the completion function, and static global options. Both generated files are committed to the repo, so you can install without running the pipeline yourself.

## Regenerating

When AWS releases a new AWS CLI (several times a week), regenerate from it:

```bash
make ingest    # read the newest aws-cli release, write parquet (~15 seconds)
make generate  # query catalog, emit zsh and bash (~3 seconds)
make install   # copy to fpath
```

`VERSION=2.39.3` on `make ingest` and `make generate` picks a release. `uv run python scripts/backfill.py` builds a completer for every release from 2.3.0 on, one commit and tag each on the branch `history`, so `git log -p history` reads as the history of the AWS CLI's commands.

The catalog has one partition per AWS CLI version, so you accumulate a history of the API contract. DuckDB makes this queryable:

```sql
-- what did AWS CLI 2.39.3 add over 2.39.2?
SELECT a.service_name, a.operation_name
FROM read_parquet('datalake/aws_api_catalog/*/operations.parquet', hive_partitioning=true) a
LEFT JOIN read_parquet('datalake/aws_api_catalog/*/operations.parquet', hive_partitioning=true) b
  ON a.service_name = b.service_name AND a.operation_name = b.operation_name
  AND b.aws_cli_version = '2.39.2'
WHERE a.aws_cli_version = '2.39.3' AND b.operation_name IS NULL;
```

For a release, `make changelog PREV=2.39.2 VERSION=2.39.3` compares the committed completer with the one from the tag `PREV` and prints the release notes: new and removed services, operations, waiters and regions, and commands whose name changed.

Each release is an immutable GitHub release: its tag and files cannot be changed after publication, so the install commands above always download the files that were published.

## Requirements

- zsh or bash 4+ (for the completer itself — zero other runtime dependencies)
- [uv](https://docs.astral.sh/uv/) and git (only if you want to regenerate)
- DuckDB (installed automatically as a Python dependency when regenerating)

## Origin story

This project started with a [TIL about `TZ=[tab]`](https://fizz.today/til/tz-tab-zsh-completer-timezone-names/) — the discovery that zsh's `_time_zone` completer reads `/usr/share/zoneinfo/` to offer every timezone on earth, instantly. That became a [longer post about zsh completers as vocabulary construction kits](https://ferkakta.dev/blog/zsh-completions-vocabulary-construction-kit/) — systems that anticipate what you need before you know it exists.

The stock AWS completer was the opposite of that. It shells out to Python on every tab, makes network calls, and never yields to `_files` for local paths. The lag was so bad I had `unset '_comps[aws]'` in my zshrc for years, with the comment: *"get rid of the insane lag autocompleting local paths in aws commands."*

This project replaces that line with something better.

## License

MIT
