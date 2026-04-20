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
