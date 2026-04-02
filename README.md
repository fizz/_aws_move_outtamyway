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

`aws ec2 wait [tab]` — 42 ec2 waiters, from botocore's `waiters-2.json`:

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

For **zsh**:

```
mkdir -p ~/.zsh/completions
curl -L https://github.com/fizz/_aws_move_outtamyway/releases/latest/download/_aws_move_outtamyway \
  -o ~/.zsh/completions/_aws_move_outtamyway
```

Add to `.zshrc` after `compinit`:

```zsh
autoload -Uz ~/.zsh/completions/_aws_move_outtamyway
compdef _aws_move_outtamyway aws
```

For **bash**:

```
mkdir -p ~/.bash_completion.d
curl -L https://github.com/fizz/_aws_move_outtamyway/releases/latest/download/_aws_move_outtamyway.bash \
  -o ~/.bash_completion.d/_aws_move_outtamyway.bash
```

Add to `.bashrc`:

```bash
source ~/.bash_completion.d/_aws_move_outtamyway.bash
```

Open a new shell. `aws s3 [tab]` should be instant.

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
