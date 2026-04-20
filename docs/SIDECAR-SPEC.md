# OTS Sidecar Spec

## Background

We're thinking about ways of adding a setup mode to onetime secret that would allow for a UI
 setup wizard for initial configuration. We would only need this for full authentication
installs which means we have RabbitMQ running. We could run a sidecar that was capable of
specific actions.

Sidecar runs on host (systemd service, not containerized), subscribes to a control queue, and
has the privileges the containers lack: restart containers, write to /etc/default/, read
systemd status.

Feedback channel comes free: sidecar publishes results back to RabbitMQ. Control plane knows
whether restart succeeded, how long it took, what the new container's health check says.
Graceful restart becomes possible: sidecar sends SIGUSR2 to Puma for phased restart, waits for
 health, escalates to container restart only if needed.

We could use the same privileged sidecar to configure the system after an initial setup from
the cloud-init script on a fresh Debian 13 instance. We could then tunnel to the db instance
rabbitMQ server with port forwarding and communicate with the web apps and sidecar using local
 commands.

If we are running from our local infrastructure / manual contol plane, we could use the SSH
key since the pubkey would already be in the envirinment


## Purpose

A privileged host-side daemon that performs operations containers cannot:
- Restart containers
- Write config to `/etc/onetimesecret/`
- Signal Puma for graceful restarts
- Report systemd/container status

## Deployment

Systemd service on the host (Debian 13), not containerized. Managed via rots.

```
rots sidecar install   # writes systemd unit to /etc/systemd/system/, enables
rots sidecar start     # systemctl start onetime-sidecar
rots sidecar stop      # systemctl stop onetime-sidecar
rots sidecar status    # systemctl status onetime-sidecar
rots sidecar logs      # journalctl -u onetime-sidecar
```

Console mode for debugging:

```
sudo rots sidecar run  # foreground, interactive, ctrl-c to stop
```

## Entry Points

### Unix Socket

Path: `/run/onetime-sidecar.sock`

Trust model: reachability is authorization. If you can connect, you're trusted. Access controlled by:
- Socket file permissions (root only by default)
- SSH tunnel forwarding (SSH key is the credential)

Use cases:
- Local CLI on the host
- Remote CLI via SSH tunnel
- Setup wizard when accessed via localhost

### RabbitMQ Queue

Command queue: `ots.sidecar.commands`
Reply pattern: per-request reply queue via `reply_to` property

Connection: sidecar reads RabbitMQ connection details from `/etc/default/onetimesecret` (same source as `rots env`).

Trust model: RabbitMQ credentials and permissions. The app user can publish to the command queue. The sidecar user consumes.

Use cases:
- Setup wizard via public URL
- App-initiated graceful restarts
- Automated recovery flows

## Command Vocabulary

Discrete operations, not shell execution. Unknown commands are rejected.

| Command | Args | Description |
|---------|------|-------------|
| `restart.web` | `{port: int}` | Restart onetime-web@{port} |
| `restart.worker` | `{id: string}` | Restart onetime-worker@{id} |
| `restart.scheduler` | `{id: string}` | Restart onetime-scheduler@{id} |
| `stop.web` | `{port: int}` | Stop onetime-web@{port} |
| `stop.worker` | `{id: string}` | Stop onetime-worker@{id} |
| `stop.scheduler` | `{id: string}` | Stop onetime-scheduler@{id} |
| `start.web` | `{port: int}` | Start onetime-web@{port} |
| `start.worker` | `{id: string}` | Start onetime-worker@{id} |
| `start.scheduler` | `{id: string}` | Start onetime-scheduler@{id} |
| `phased_restart.web` | `{port: int}` | SIGUSR2 to Puma, escalate if needed |
| `phased_restart.worker` | `{id: string}` | SIGUSR2 to worker process |
| `config.stage` | `{key: string, value: string}` | Stage config change (allowlisted keys) |
| `config.apply` | `{}` | Validate and apply staged changes |
| `config.discard` | `{}` | Discard staged changes |
| `config.get` | `{key: string}` | Read from env file |
| `health` | `{port: int}` | HTTP health check result |
| `status` | `{unit: string}` | Systemd unit status |
| `instances.restart_all` | `{type?: string}` | Rolling restart of all instances |
| `postgres.bootstrap_app` | `{app: string, owner_role: string, peer_ip: string, peer_id: string}` | Roles: `db`. Create role + database, generate password, deliver to `peer_id` via `secrets.deliver`. Returns `PostgresBootstrapAppData` `{role, database, password_delivered_to, changed}`. Idempotent: if `owner_role` already exists, short-circuits with `changed=False` (password cannot be re-derived). `changed: bool` surfaces whether state was mutated. |
| `postgres.add_hba` | `{name: string, content: string}` | Roles: `db`. Write a `pg_hba.d/` drop-in (basename allowlist, `.conf` suffix enforced, no symlinks) and call `pg_reload_conf()`. Returns `PostgresAddHbaData` `{reloaded, changed}`. Idempotent: byte-for-byte comparison against existing file. `changed: bool`. |
| `postgres.rotate_password` | `{role: string, peer_id: string}` | Roles: `db`. Generate a fresh password for an existing role and deliver it to `peer_id`. Returns `PostgresRotatePasswordData` `{delivered_to, changed}`. Non-idempotent by design — every call mints a new password. `changed: bool` is always `True` on success; kept for uniform result shape. |
| `valkey.create_acl_user` | `{name: string, rules: list[string], peer_id: string}` | Roles: `db`. Create or update an ACL user, rotate token only when rules differ from stored form, persist via `ACL SAVE`, deliver token to `peer_id`. Returns `ValkeyCreateAclUserData` `{delivered_to, changed}`. Idempotent on rules; token rotates only on rule change. `changed: bool`. |
| `valkey.reload_acl` | `{}` | Roles: `db`. Snapshot `ACL LIST`, run `ACL LOAD`, snapshot again; compares sorted hashes to detect observable difference. Returns `ValkeyReloadAclData` `{ok, changed}`. `ACL LOAD` always runs; `changed: bool` flips only when the in-memory state actually shifted. |
| `secrets.deliver` | `{name: string, value: string, env_file?: string}` | Roles: `db`, `web`. Write-if-different of a named secret into the OTS env file. Hardened allowlists — see callout below. Returns `SecretsDeliverData` `{written, path, changed}`. Idempotent: identical value is a no-op (preserves mtime). `changed: bool`. |
| `backup.install` | `{profile: string, target: string, schedule: string}` | Roles: `db`. Install `ots-backup-<profile>.service` + `.timer` + rclone fragment; `systemctl daemon-reload` and `enable --now` the timer. Profile names allowlisted (`db-daily`, `valkey-hourly`). Returns `BackupInstallData` `{unit, timer, changed}`. Idempotent: write-if-different + enable is a no-op when nothing differed. `changed: bool`. |
| `backup.uninstall` | `{profile: string}` | Roles: `db`. `systemctl disable --now` the timer, unlink the three generated files, `daemon-reload` if anything was removed. Returns `BackupUninstallData` `{ok, changed}`. Idempotent: absent profile returns `changed=False`. `changed: bool`. |

### `secrets.deliver` Allowlist

The `secrets.deliver` handler is reachable from any peer that can publish on the sidecar's queue. Its surface is deliberately narrow:

- `env_file`: allowlisted to `/etc/default/onetimesecret`. No other target accepted; default applied when omitted.
- `name`: allowlisted to `PG_PASSWORD` and `VALKEY_PASSWORD`. Expanding this set requires a threat-model review.
- Symlink defence: the target is `lstat`ed first; a symlink at the path is rejected before any write.
- Atomic write: tempfile created in the same directory, `fsync`, `chmod`, then `os.rename` into place.
- Mode `0640`: owner + group readable, not world-readable.
- The secret value is never logged — only the `name` and target `path`.

### Staged Config Pattern

Direct writes to `/etc/default/onetimesecret` are risky—bad config breaks all containers with no rollback. Instead:

1. `config.stage` writes to `/etc/default/onetimesecret.staged`
2. Multiple `config.stage` calls accumulate changes
3. `config.apply` validates the staged file, backs up current config, atomically moves staged to live, restarts one instance to verify, then rolling restart of remaining instances
4. `config.discard` removes the staged file

If validation or the test instance fails, the backup is restored and an error is returned.

### Config Key Allowlist

Only these keys can be written via `config.stage`:

```
REDIS_URL
SECRET_KEY
DOMAIN
SSL_ENABLED
SMTP_HOST
SMTP_PORT
SMTP_USER
SMTP_PASSWORD
STRIPE_SECRET_KEY
STRIPE_PUBLISHABLE_KEY
```

Requests for unlisted keys return an error.

## Two-phase provisioning

New in issue #55. Cloud-init (`lots`) brings services to life; the sidecar (`rots`) handles application-level provisioning. The split is deliberate: cloud-init is good at installing packages, binding sockets, enabling auth mechanisms, and dropping a bootstrap principal from `LoadCredentialEncrypted`. It is a poor fit for generating passwords, creating roles, delivering secrets to peers, or installing backup jobs — operations that need coordination across hosts and idempotent receipts.

### Phase 1 — cloud-init (`lots`)

Scope: install `postgresql` / `valkey`, bind the local socket, configure peer auth (postgres) or drop a bootstrap ACL user + token (valkey). No application roles, no application databases, no secrets generated in `runcmd`. Tracked externally in `tools-monorepo#37` (Phase 1 of provisioning, lots side — the upstream dependency for this phase).

### Phase 2 — sidecar (`rots`)

Scope: everything above the "service is up" line. Generate application passwords, create roles / databases / ACL users / schemas, deliver secrets to peers via `secrets.deliver`, install and manage backup timers, manage `pg_hba.d/` drop-ins. Triggered by an operator command (`rots env bootstrap --env <env>`, not documented here — this spec is the sidecar contract, not the operator CLI).

### Idempotency contract

Every Phase 2 handler returns a `CommandResult` whose `data` payload includes `changed: bool`. A re-run of the same call against the same state is a no-op (`changed=False`). The one documented exception is `postgres.rotate_password`: it is non-idempotent by design (every call mints a fresh password) and its `changed` field is always `True` on success — the key is kept for uniform payload shape.

### Role gating

Role membership is declared at handler registration:

```python
@register_handler(Command.POSTGRES_BOOTSTRAP_APP, roles={"db"})
```

- Web sidecars register `secrets.deliver` plus the existing container-lifecycle commands (`restart.*`, `config.*`, `status`, etc.).
- DB sidecars register everything: postgres, valkey, backup, secrets, plus container-lifecycle.
- Wrong-role invocation is rejected by the dispatcher before reaching the handler; the caller receives a `CommandResult.fail` with an explicit role-mismatch error.

### Cross-host delivery

Handlers that generate secrets (`postgres.bootstrap_app`, `postgres.rotate_password`, `valkey.create_acl_user`) deliver the generated value to the web peer's sidecar by publishing `secrets.deliver` at `peer_id`. The operator workstation never sees the secret — it transits from db-sidecar to web-sidecar over the RabbitMQ control plane and is written into `/etc/default/onetimesecret` atomically. Delivery failure triggers a rollback (`DROP ROLE`, `ACL DELUSER`, or `ALTER ROLE ... PASSWORD NULL` depending on the handler).

The routing piece — ensuring a publish at `peer_id` reaches that specific web host's sidecar and no other — depends on `tools-monorepo#12` (per-host queue routing). That dependency is external to this repo and gates the operator command's end-to-end flow, not the handlers themselves (handlers are correct in isolation; the transport just has to deliver).

## Message Format

JSON over both socket and RabbitMQ.

### Request

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "command": "restart.web",
  "args": {"port": 7043},
  "timestamp": "2024-01-15T10:30:00Z"
}
```

### Success Response

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "success": true,
  "result": {"state": "active", "uptime": "Up 5 seconds"},
  "duration_ms": 3200,
  "timestamp": "2024-01-15T10:30:03Z"
}
```

### Error Response

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "success": false,
  "error": "config.set: key DANGEROUS_FLAG not in allowlist",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

## Instance Tracking

Sidecar uses the existing rots SQLite database (`/var/lib/onetimesecret/deployments.db`) to know which container instances are running. This enables:

- `instances.restart_all` for rolling restarts across all instances
- `config.apply` to coordinate test-then-propagate across instances
- Status queries that reflect the full deployment state

## Phased Restart Behavior

When `phased_restart.web` is invoked:

1. Locate Puma master PID via `podman exec`
2. Send SIGUSR2 to trigger phased restart
3. Poll `/health` endpoint until healthy or timeout (30s)
4. If healthy: return success
5. If timeout: perform full container restart, return result with `escalated: true`

When `phased_restart.worker` is invoked:

1. Locate worker master PID via `podman exec`
2. Send SIGUSR1 to trigger graceful worker restart
3. Poll process status until new workers are ready or timeout (30s)
4. If ready: return success
5. If timeout: perform full container restart, return result with `escalated: true`

## Setup Mode

### Activation

Environment variable `ENABLE_SETUP_MODE=true` on container start.

### Token Generation

On startup, the app generates a setup token using Redis WATCH for coordination:

```ruby
redis.watch('ots:setup:token') do
  existing = redis.get('ots:setup:token')
  if existing
    existing
  else
    token = SecureRandom.urlsafe_base64(32)
    redis.multi { redis.set('ots:setup:token', token, ex: 3600) }
    logger.info "Setup wizard: https://#{host}/setup/#{token}"
    token
  end
end
```

First container wins. Others read the existing token. Token expires in 1 hour.

### Wizard Flow

1. Customer visits `https://example.com/setup/{token}`
2. Wizard presents configuration steps (domain, SMTP, Stripe, etc.)
3. Each step sends `config.stage` commands to sidecar
4. Final step stages `ENABLE_SETUP_MODE=false`, then sends `config.apply`
5. Sidecar validates, applies config, performs rolling restart
6. Token is invalidated in Redis

### Socket Path for Technical Users

```bash
ssh -L /tmp/sidecar.sock:/run/onetime-sidecar.sock ots-web-01
# then locally (ots-ctl is an alias for rots sidecar over the socket):
ots-ctl config stage REDIS_URL redis://...
ots-ctl config apply
ots-ctl restart web 7043
```

## Systemd Unit

```ini
[Unit]
Description=OTS Sidecar
After=network.target

[Service]
Type=simple
ExecStartPre=/usr/bin/mkdir -p /etc/onetimesecret /var/lib/onetimesecret
ExecStart=/usr/local/bin/rots sidecar run
Restart=always
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/etc/onetimesecret /var/lib/onetimesecret /run

[Install]
WantedBy=multi-user.target
```

## File Layout

```
src/rots/sidecar/
├── __init__.py
├── app.py          # CLI commands: install, start, status, run
├── daemon.py       # Main loop: socket + rabbitmq listeners
├── commands.py     # Command enum, dispatch, handlers
├── socket.py       # Unix socket server
├── rabbitmq.py     # Queue consumer
└── allowlist.py    # Config key allowlist
```

## Dependencies

- Python 3.11+
- rots (existing systemd/podman modules)
- pika (RabbitMQ client)
