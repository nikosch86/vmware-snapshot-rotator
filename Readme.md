# snapshot rotator for ESXi / vSphere

A small tool that keeps a fixed number of VM snapshots per VM: it creates a new
snapshot and deletes the oldest ones beyond the configured limit.

> **USE WITH CAUTION.** This tool deletes VM snapshots. Always try `--dry-run`
> first, and double-check your `--keep` value.

## Requirements

- Python 3.10+
- [pyVmomi](https://github.com/vmware/pyvmomi) 8.0+ (installed automatically)

## Installation

```sh
pip install .
```

This installs the `snapshot-rotator` command. For a development checkout with
the test and lint tooling:

```sh
pip install -e ".[dev]"
```

## Usage

```
usage: snapshot-rotator [-h] -s HOST [-o PORT] -u USER [-p PASSWORD] [-t TAG]
                        [-m DESCRIPTION] [-k KEEP] [--prune-only] [-n]
                        [--insecure] [--verbose]

options:
  -h, --help            show this help message and exit
  -s, --host HOST       Remote host to connect to
  -o, --port PORT       Port to connect on (default: 443)
  -u, --user USER       User name to use when connecting to host
  -p, --password PASSWORD
                        Password to connect with (INSECURE: visible in the
                        process list; prefer the VI_PASSWORD environment variable)
  -t, --tag TAG         Comment to append to the name of new snapshots
  -m, --description DESCRIPTION
                        Description to use for new snapshots
  -k, --keep KEEP       How many snapshots to keep (default: 3)
  --prune-only          Only prune old snapshots, do not create snapshots
  -n, --dry-run         Dry run
  --insecure            Disable TLS certificate verification (self-signed labs only)
  --verbose, -v
```

### Examples

Keep the 5 most recent snapshots of every VM, taking a new one now. The
password is read from the environment so it never appears in the process list:

```sh
export VI_PASSWORD='…'
snapshot-rotator -s vcenter.example.com -u svc-snapshots -k 5
```

Preview what would happen without changing anything:

```sh
snapshot-rotator -s vcenter.example.com -u svc-snapshots -k 5 --dry-run -v
```

Only prune (do not create new snapshots), down to 3 per VM:

```sh
snapshot-rotator -s vcenter.example.com -u svc-snapshots -k 3 --prune-only
```

## How rotation works

- New snapshots are named for the current date (`YYYY-MM-DD`); if that name is
  already taken on a VM, a second-precision ISO timestamp is used instead.
- After (optionally) creating a snapshot, the oldest snapshots beyond `--keep`
  are deleted, selected by their creation time. Deletions are deferred until all
  snapshots have been taken, so the snapshot pass completes quickly.
- `--prune-only` skips creation and simply trims each VM back to `--keep`.

## Security

- TLS certificate verification is **on by default**. Use `--insecure` only
  against hosts with self-signed certificates (e.g. a homelab).
- Prefer the `VI_PASSWORD` environment variable over `--password`; the latter is
  visible to anyone who can list processes on the machine. If neither is given,
  the tool prompts interactively.

## Development

```sh
pip install -e ".[dev]"
pytest          # run the test suite
ruff check .    # lint
pyright snapshot_rotator.py   # type-check
```
