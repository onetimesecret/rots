# Remote Execution

ots-containers supports executing commands on remote hosts via SSH, allowing a single operator workstation to manage containers across multiple servers.

## How It Works

Every command that interacts with systemd, podman, or the deployment database accepts an optional executor. When a remote host is resolved, commands are dispatched over SSH instead of running locally. The resolution is transparent to command logic -- the same code path handles both cases.

### Resolution Priority

The target host is determined by this chain (first match wins):

1. **`--host` flag** -- explicit CLI argument
2. **`OTS_HOST` environment variable** -- shell-level override
3. **`.otsinfra.env` file** -- walk-up discovery from current directory
4. **None** -- local execution (default)

## .otsinfra.env File

The `.otsinfra.env` file provides per-jurisdiction targeting context. It is discovered by walking up from the current working directory, stopping at the first `.git` boundary or the user's home directory.

### Placement

Place `.otsinfra.env` files in your ops-jurisdictions repository, one per jurisdiction directory:

```
ops-jurisdictions/
  eu/.otsinfra.env     # European servers
  ca/.otsinfra.env     # Canadian servers
  us/.otsinfra.env     # US servers
```

When you `cd` into a jurisdiction directory and run `ots instances ...`, the walk-up resolver finds the appropriate `.otsinfra.env` and connects to the right host automatically.

### Format

Simple `KEY=VALUE` lines. Blank lines and `#` comments are ignored. Values may optionally be quoted (single or double).

```bash
# ops-jurisdictions/eu/.otsinfra.env
OTS_HOST=eu-prod-1.onetimesecret.com
OTS_REPOSITORY=ghcr.io/onetimesecret/onetimesecret
OTS_IMAGE=ghcr.io/onetimesecret/onetimesecret
OTS_TAG=v0.19.0
```

### Keys

| Key | Purpose | Required |
|---|---|---|
| `OTS_HOST` | Target hostname for SSH connection | Yes (for remote) |
| `OTS_REPOSITORY` | Container image repository | No |
| `OTS_IMAGE` | Full image reference | No |
| `OTS_TAG` | Image tag to deploy | No |

Only `OTS_HOST` is used by the executor resolution chain. The other keys are available for tooling and CI workflows.

## SSH Configuration

Remote connections use the standard SSH config (`~/.ssh/config`) for all connection parameters. The host specified in `OTS_HOST` is looked up in SSH config to determine:

- **Hostname** -- the actual address to connect to
- **User** -- remote username
- **Port** -- SSH port (default 22)
- **IdentityFile** -- SSH key to use
- **ProxyCommand** -- jump host or tunnel configuration

### Example SSH Config

```
Host eu-prod-1.onetimesecret.com
    User deploy
    IdentityFile ~/.ssh/ots_deploy_ed25519
    Port 22
```

### Known Hosts

Connections use `RejectPolicy` -- the remote host must already be in `~/.ssh/known_hosts`. This prevents MITM attacks. Add hosts with:

```bash
ssh-keyscan -H eu-prod-1.onetimesecret.com >> ~/.ssh/known_hosts
```

### otsinfra Integration

The `otsinfra` tool (in `hosts/inventory/`) generates both SSH config entries and dnsmasq configuration at setup time. When you provision a new host through otsinfra:

1. SSH config entries are added for each host's FQDN
2. dnsmasq configuration maps FQDNs to IP addresses for local DNS resolution
3. SSH host keys are added to known_hosts

This means the SSH infrastructure is established once during host provisioning, and the runtime executor has no dependency on the inventory system -- it uses standard SSH config and DNS.

## Executor Types

### LocalExecutor

Default when no host is resolved. Runs commands via `subprocess`:

- `run()` -- captures stdout/stderr, returns Result
- `run_stream()` -- inherits terminal stdout/stderr, returns exit code
- `run_interactive()` -- inherits full terminal (stdin/stdout/stderr), returns exit code

### SSHExecutor

Used when a remote host is resolved. Runs commands over a paramiko SSH connection:

- `run()` -- `exec_command()` with output capture, returns Result
- `run_stream()` -- select loop forwarding stdout/stderr to local terminal
- `run_interactive()` -- full PTY allocation with bidirectional I/O and SIGWINCH propagation

### Method Selection by Command

| Command | Method | Why |
|---|---|---|
| `deploy`, `redeploy`, `undeploy` | `run()` | Needs captured output for status tracking |
| `run` (foreground) | `run_stream()` | Real-time output, no stdin needed |
| `exec` (interactive shell) | `run_interactive()` | Full PTY for shell interaction |
| `shell` (interactive) | `run_interactive()` | Full PTY for shell interaction |
| `shell -c "command"` | `run_stream()` | One-shot command, stream output |
| `logs -f` | `run_stream()` | Follow mode, real-time streaming |
| `logs` (bounded) | `run()` | Bounded output, capture is fine |

## Error Handling

SSH connection errors are caught and translated to user-friendly messages:

- **AuthenticationException** -- "Authentication failed. Check your SSH key and that the remote user is correct."
- **NoValidConnectionsError / OSError** -- "SSH to {host} failed: {details}"
- **socket.timeout** -- "Connection timed out. Check that the host is reachable and accepting SSH connections."
- **ImportError** -- "paramiko is required for SSH connections. Install it with: pip install ots-shared[ssh]"

## Connection Caching

SSH connections are cached per hostname for the lifetime of the process. Multiple commands within a single CLI invocation reuse the same SSH transport. Connections are closed automatically at interpreter exit via `atexit`.

## Deployment Database

The sqlite database on each server is the single source of truth. There is no local mirror or sync protocol.

- **Local:** `db.py` uses Python's `sqlite3` module directly.
- **Remote:** `db.py` shells out `sqlite3 -json` on the remote host via the executor. Requires sqlite3 >= 3.33 (standard on any recent Linux).

Every `db.*` function (`record_deployment`, `get_deployments`, `set_alias`, etc.) accepts an optional `executor` parameter. `_is_remote(executor)` branches to the appropriate backend. Command code is unaware of the distinction.
