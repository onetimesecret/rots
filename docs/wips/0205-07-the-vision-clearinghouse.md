# 0205-the-vision.md

---

## Environments

Groups of web, worker, db instances running onetime secret.

See: 0205-environment-configuration-skeleton.txt.

## The manual process as it exists today

Configuration Files (FHS-Compliant Layout)

### 1. Application Config Directory: `/etc/onetimesecret/`

YAML configuration files (mounted read-only into containers as `/app/etc:ro`):

| File           | Purpose                                                        |
| -------------- | -------------------------------------------------------------- |
| `config.yaml`  | Application configuration (REQUIRED, validated at deploy time) |
| `auth.yaml`    | Authentication configuration (optional)                        |
| `logging.yaml` | Logging configuration (optional)                               |
| `billing.yaml` | Billing configuration (optional)                               |

### 2. Infrastructure Environment: `/etc/default/onetimesecret`

Shared environment file loaded by all instances. Contains non-secret infrastructure variables:

```bash
REDIS_URL=redis://localhost:6379
DATABASE_URL=postgres://localhost:5432/onetimesecret
RABBITMQ_URL=amqp://localhost:5672
LOG_LEVEL=info
SECRET_VARIABLE_NAMES="HMAC_SECRET SECRET SESSION_SECRET STRIPE_API_KEY STRIPE_WEBHOOK_SIGNING_SECRET SMTP_PASSWORD"
```

The `SECRET_VARIABLE_NAMES` convention tells the system which variables should be sourced from Podman secrets instead of the env file.

### 3. Runtime Data: `/var/lib/onetimesecret/`

SQLite database (`deployments.db`) with 4 tables:

| Table               | Purpose                                                           |
| ------------------- | ----------------------------------------------------------------- |
| `deployments`       | Audit trail: timestamp, image, tag, action, port, success/failure |
| `image_aliases`     | Tag aliases: CURRENT/ROLLBACK → actual image:tag mappings         |
| `service_instances` | Service tracking: package, instance, config paths, ports          |
| `service_actions`   | Service audit: timestamp, package, instance, action, success      |

Falls back to `~/.local/share/ots-containers/deployments.db` on macOS or when `/var/lib` not writable.

### 4. Quadlet Templates: `/etc/containers/systemd/`

Systemd unit templates (auto-generated):

- `onetime-web@.container` - Web instance template
- `onetime-worker@.container` - Worker instance template
- `onetime-scheduler@.container` - Scheduler instance template

Each contains the quadlet spec with `Secret=` lines dynamically generated from `SECRET_VARIABLE_NAMES`.

### 5. Podman Secrets (not files)

In-memory secrets managed via `podman secret`:

| Secret Name                 | Environment Variable            |
| --------------------------- | ------------------------------- |
| `ots_hmac_secret`           | `HMAC_SECRET`                   |
| `ots_secret`                | `SECRET`                        |
| `ots_session_secret`        | `SESSION_SECRET`                |
| `ots_stripe_api_key`        | `STRIPE_API_KEY`                |
| `ots_stripe_webhook_secret` | `STRIPE_WEBHOOK_SIGNING_SECRET` |
| `ots_smtp_password`         | `SMTP_PASSWORD`                 |

**Naming convention:** env var `STRIPE_API_KEY` → secret `ots_stripe_api_key` (lowercase with `ots_` prefix)

### 6. Container Registry Auth (optional for private registry)

Podman auth file resolution order:

1. `$REGISTRY_AUTH_FILE` env var
2. `$XDG_RUNTIME_DIR/containers/auth.json`
3. `~/.config/containers/auth.json` (macOS/non-root)
4. `/etc/containers/auth.json` (root on Linux)

### 7. Proxy Config (optional): `/etc/onetimesecret/`

- `Caddyfile.template` - Template for Caddy reverse proxy
- Output: `/etc/caddy/Caddyfile`

### Validation at Deploy Time

The `Config.validate()` checks that `/etc/onetimesecret/config.yaml` exists before allowing deployment. Other YAML files are optional.

### Prospective commands

The target environment is ambient state, not a per-command argument. Like Onetime Secret's Workspace Mode — where the selected organization and custom domain define the context you're working in rather than being a dropdown choice on each action — the `ots host` commands operate on whichever environment is currently active.

```bash
# Set environment context (persists across commands)
export OTS_ENV=prod-us1
# or: ots host use prod-us1

ots host push config.yaml
# rsync's the file to the active environment
# checksums before/after
# records in SQLite
# prints diff if mismatch

ots host status
# config_deployments + config_drift for the active environment

ots host audit
# full history of config changes to the active environment
```

Without `OTS_ENV` set, commands that need a target environment fail with a clear message. Commands that operate across all environments (like a global status overview) work regardless:

```bash
unset OTS_ENV
ots host status --all
# shows which environments are current vs. stale across the fleet
```

### How these compose

```
Local workstation:
  dnsmasq ──→ resolves environment names
  sshtmux ──→ manages SSH config, persistent sessions
  sshmx ───→ parallel operations across environments
  git repo ──→ source of truth (unchanged)
  ots host ──→ config push, drift detection, audit

Network path:
  workstation ──→ production servers (direct SSH)

On each server (existing):
  ots instances ──→ container lifecycle (deploy/redeploy/start/stop)
  deployments.db ──→ container deployment tracking (/var/lib/onetimesecret/)
```

**Note on state tracking**: The existing `deployments.db` on each server tracks container lifecycle events (`ots instances deploy --web 7043`). The `ots host` commands run from the workstation and will need their own state for config push history and drift detection. Whether that's a workstation-local SQLite database, an extension to the server-side databases, or both, is a design decision to defer until the tools take shape.

A deploy operation becomes:

1. Edit config locally, commit to git
2. `OTS_ENV=prod-us1 ots host push config.yaml` (rsync, verify, record)
3. Or for batch: `ots host push --all config.yaml` (parallel rsync to all environments that are behind)

`ots host push` resolves the target from `OTS_ENV` (via the SSH config managed by sshtmux and dnsmasq), calls rsync, and records everything in SQLite. The environment variable can be set inline per-command, exported for a session, or persisted via `ots host use`.

## Package structure

The `host` command group integrates into the existing cyclopts app using the same pattern as all other sub-apps:

```python
# What you have now
app.command(instance.app)
app.command(image.app)

# What you'd add
try:
    from .host.cli import app as host_app
    app.command(host_app)
except ImportError:
    pass
```

The try/except ImportError pattern catches missing extras (no paramiko on the server, no podman on the workstation). The --help output adapts automatically — `ots host` simply doesn't appear when its dependencies aren't installed.

On cyclopts v4, even cleaner:

```python
# v4 lazy loading — no try/except needed
app.command("ots.host.cli:host_app", name="host")
app.command("ots.container.cli:container_app", name="container")
```

Since v4 defers import to execution time, the missing-package case is handled implicitly. The command appears in help but only fails if someone actually runs it without the deps. Arguably worse UX than try/except (command visible but broken vs. invisible), but addressable with a check.

> **Future consideration**: If the host and container halves ever need fully independent release cycles or conflicting dependency trees, a separate-package approach (each with its own cyclopts.App root and shell entry point) would provide maximum decoupling. The tradeoff is that tab completion, --help integration, and consistent flag handling would need to be maintained by convention rather than by framework.

### Migration steps

1. Rename package: ots-containers → ots (pyproject.toml name change)
2. Move source: src/ots_containers/ → src/ots/container/ (or keep flat and just add src/ots/host/)
3. Update internal imports (one-time, mechanical)
4. Add extras to pyproject.toml: host = ["paramiko"]
5. Add conditional import in cli.py (3 lines)
6. Create src/ots/host/ for new code

## More Detail

### Layer 0: Name resolution (dnsmasq)

On your local workstation, dnsmasq replaces scattered IP management. You maintain one file:

```
# /etc/dnsmasq-hosts (or address= directives in dnsmasq.conf)
203.0.113.10    prod-us1.ots.internal
203.0.113.11    prod-eu1.ots.internal
203.0.113.12    prod-ap1.ots.internal
# ... 10+ entries
```

Every other tool in the stack references `prod-us1.ots.internal` instead of an IP. When you rebuild an instance and the IP changes, you update one line. Your deploy scripts, SSH configs, and rsync targets all resolve through it.

The tradeoff: dnsmasq is a local daemon. If it's not running, resolution fails for your custom names. Mitigation is straightforward since `addn-hosts` is just `/etc/hosts` format, so you can symlink or fall back. On modern Debian you'll need to disable systemd-resolved's stub listener (`DNSStubListener=no` in `/etc/systemd/resolved.conf`) to free port 53. That's a one-time configuration cost.

Where dnsmasq earns its keep over plain SSH config Host aliases: other tools besides SSH also need to resolve these names. Your Python scripts, rsync, curl health checks, browser access to admin panels. SSH config only helps SSH.

### Layer 1: SSH config management (sshclick or sshtmux)

sshclick organizes `~/.ssh/config` with group metadata in comments. sshtmux is a fork that adds tmux session integration. Both parse and manage the same underlying SSH config file, so they don't replace SSH, they make the config manageable at scale.

The relevant feature for your workflow: grouping hosts by environment or by role. When you have 10+ environments each with potentially different SSH keys, ports, or jump hosts, `~/.ssh/config` becomes a document management problem. sshclick/sshtmux turns it into a queryable structure.

sshtmux's tmux integration means `sshtmux connect prod-us1` opens a persistent tmux session. You disconnect, reconnect later, your context is preserved. For long config editing sessions where you're verifying changes, this eliminates the "lost my terminal" failure mode.

### Layer 2: Multiplexed operations (sshmx)

sshmx is bash+fzf+tmux. You select multiple hosts interactively, it opens parallel tmux panes, and you can broadcast commands to all of them. This is where the "push to 5 environments at once" capability lives.

For your config workflow, the pattern becomes: select the environments that need the update via fzf multi-select, broadcast the rsync or verification command. Instead of sequential deploy-to-each, you're deploying in parallel and watching verification output across all panes.

sshmx also stores sessions in JSON with optional GPG-encrypted passwords. This overlaps with sshclick/sshtmux's config management but from a different angle (session-oriented vs config-oriented).

The practical question is whether you need both sshtmux and sshmx or pick one. sshtmux is more structured (Python, pip install, config file parsing). sshmx is more ad-hoc (bash, fzf, interactive selection). For the "same change to multiple environments" workflow, sshmx's multi-select broadcasting is the more direct tool.

### Layer 3: State tracking

The shape of the state tracking depends on where `ots host` commands run — the workstation — versus where `ots instances` commands run — each server. The server-side `deployments.db` already tracks container lifecycle. Config push tracking is a workstation concern: which files were pushed where, when, and whether they match.

The schema below is a starting point, not a commitment. Where these tables live (workstation-local database, server-side, or both) is a decision to make as the tools evolve.

```sql
CREATE TABLE config_deployments (
    id INTEGER PRIMARY KEY,
    timestamp TEXT DEFAULT (datetime('now')),
    environment TEXT NOT NULL,        -- 'prod-us1'
    file_path TEXT NOT NULL,          -- '/etc/onetimesecret/config.yaml'
    git_commit TEXT,                  -- local repo commit hash
    checksum_before TEXT,             -- sha256 of remote file before push
    checksum_after TEXT,              -- sha256 of remote file after push
    checksum_local TEXT,              -- sha256 of local source file
    verified INTEGER DEFAULT 0,       -- 1 if post-deploy diff confirmed match
    operator TEXT                     -- who ran it
);

CREATE TABLE config_drift (
    id INTEGER PRIMARY KEY,
    timestamp TEXT DEFAULT (datetime('now')),
    environment TEXT NOT NULL,
    file_path TEXT NOT NULL,
    expected_checksum TEXT,           -- what we last deployed
    actual_checksum TEXT,             -- what's on the server now
    drift_detected INTEGER DEFAULT 0
);
```

The `config_drift` table addresses the key gap: detecting when someone edited a config directly on a server without propagating the change back to the local repo. A periodic check (cron, or manual before each push) SSH's to each environment, checksums the config files, and compares against the last known state. With 10+ environments, this is where the real reliability comes from.

### Where each tool is dispensable

- **dnsmasq** is the most optional. SSH Host aliases get you 80% of the way. dnsmasq only becomes necessary when non-SSH tools need name resolution, or when you're tired of updating Host entries in SSH config AND `/etc/hosts` AND browser bookmarks when an IP changes.

- **sshtmux and sshmx** overlap, but are both valuable for different work tasks. sshtmux for structured config management with tmux persistence for maintaining session state working with customer environments (lots of initial setup, longtail of occasional updates); sshmx for managing our regional environments with interactive multi-select broadcasting.

- **State tracking** is the least optional. Config push history and drift detection fill a gap that none of the other tools address. The current workflow has no record of what config state each environment is in beyond "whatever's in git history and whatever I remember deploying."

### Gaps

#### Environment inventory as a single source of truth.

Environments appear in dnsmasq hosts, SSH config (via sshtmux), sshmx session definitions, and presumably the SQLite state tables. Four places to add a new environment, four places where a stale entry can cause silent failures. An explicit inventory file that the other tools derive from (generate dnsmasq hosts, generate SSH config blocks, populate environment lists) would collapse this to one.

#### The feedback loop after push.

The document describes push (deploy config) and detect (drift), but not verify-service-health. Checksums confirm the file arrived intact; they don't confirm the application accepted it. A post-push health check (even just "can I curl the /health endpoint and get 200?") closes the loop between "config deployed" and "service operational." Without it, the operator has to manually verify each environment, which is the same gap that config push automation was supposed to eliminate.

#### Rollback as a first-class operation.

config_deployments records checksum_before, which implies rollback is possible, but there's no ots host rollback command and no stored copy of the previous file content. Checksums prove what was there; they don't reconstruct it. Either the git history serves as the rollback source (which requires knowing which commit corresponds to which deployment) or the previous file needs to be preserved on the server or workstation.

## Future considerations

### Access gateway (The Bastion)

If compliance requirements grow (SOC 2, customer audit requests) or team size expands beyond a single operator, an SSH bastion with session recording becomes justified. **The Bastion (OVH)** fits the security model: pure SSH protocol, no agent on production servers, no Docker dependency on targets. It's a hardened relay with full TTY session recording, per-user per-host authorization, and MFA. rsync/scp work through it transparently via ProxyJump, so `ots host push` would gain an audit trail at the SSH layer with no code changes — just an SSH config update.

The cost is one more Debian instance to manage (could share the VPS already running the container registry). Worth evaluating when the number of custom installs or operators makes "who changed what, when" a question that git history alone can't answer.

### Terminology to incorporate

- Pit of success
- "Day 0/1/2": The metaphor emerged from network engineering, where "Day 0" described initial device configuration, "Day 1" covered deployment, and "Day 2" meant ongoing network operations. This usage dates back at least to the early 2010s in Cisco and Juniper documentation.

## Framing the whole thing

The system is converging on desired-state configuration management, but with the operator as the reconciliation loop rather than automation. That's a legitimate design choice for a single-operator setup where judgment matters more than speed. The risk is that it stays in the "works perfectly when I remember all the steps" zone without reaching the "the tool prevents me from forgetting steps" zone. The distance between those two zones is roughly: validation before push, health check after push, and rollback when health check fails. Those three additions would turn the tool from "config distribution with audit trail" into "deployment pipeline with safety rails."

---

What questions should we be asking, what decisions do we need to make, and what other information should we gather or consider, to make progress on this vision for a production operations tool/system? What pre-existing tools/ services / patterns / concepts can we use to help us conceive and clarify and solidify our ideas? Is there a missing piece or component that we haven't considered yet?
