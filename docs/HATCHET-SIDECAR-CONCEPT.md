# Hatchet + Sidecar Fleet Orchestration

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Infrastructure Environment                                      │
│  ┌──────────┐    ┌──────────────┐    ┌────────────────────────┐ │
│  │ Hatchet  │───▶│  RabbitMQ    │───▶│ WireGuard/SSH tunnel   │ │
│  │ Workflow │    │  (control)   │    │ to web hosts           │ │
│  └──────────┘    └──────────────┘    └────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼ AMQP over WireGuard
┌─────────────────────────────────────────────────────────────────┐
│ Debian 13 Web Instance(s)                                       │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐   │
│  │ Sidecar      │───▶│ rots CLI     │───▶│ Podman/systemd   │   │
│  │ (systemd)    │    │ subprocess   │    │ containers       │   │
│  └──────────────┘    └──────────────┘    └──────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## Message Flow

1. **Hatchet workflow** publishes to `ots.sidecar.commands` queue:
   ```json
   {"command": "rots.instance.redeploy", "args": {"args": [7043]}}
   ```

2. **Sidecar** consumes, executes `rots instance redeploy 7043`, replies to `reply_to` queue:
   ```json
   {"success": true, "result": {...}, "duration_ms": 4500}
   ```

3. **Hatchet** receives response, branches workflow on `success` field

## Existing Sidecar Capabilities

The sidecar already implements this pattern (`src/rots/sidecar/`):

| Component | Implementation |
|-----------|----------------|
| Queue consumer | `rabbitmq.py` - pika, `ots.sidecar.commands`, `reply_to` pattern |
| Command dispatch | `handlers.py` - routes `rots.*` to subprocess |
| Allowlist | `handlers_rots.py` - permits `instance.{start,stop,restart,redeploy}`, `image.*`, `ps`, etc. |
| Blocklist | Blocks `sidecar`, `host.push`, `cloudinit` (recursive/dangerous) |

## Hatchet Workflow Examples

### Rolling Redeploy

```python
@hatchet.workflow
class RollingRedeploy:
    @hatchet.step()
    def redeploy_instances(self, ctx):
        ports = [7043, 7044, 7045]
        for port in ports:
            result = publish_and_wait(
                queue="ots.sidecar.commands",
                message={"command": "rots.instance.redeploy", "args": {"args": [port]}},
                timeout=120
            )
            if not result["success"]:
                raise WorkflowError(f"Port {port} failed: {result['error']}")
            time.sleep(5)  # delay between instances
```

### Config Push + Restart

```python
@hatchet.workflow
class ConfigUpdate:
    @hatchet.step()
    def stage_config(self, ctx):
        publish_and_wait("config.stage", {"key": "SMTP_HOST", "value": "new.smtp.host"})

    @hatchet.step()
    def apply_and_restart(self, ctx):
        publish_and_wait("config.apply", {})  # validates, backs up, rolling restart
```

## Network Path Options

| Method | Path | When to use |
|--------|------|-------------|
| WireGuard | Direct AMQP 5672 | Persistent mesh, lowest latency |
| SSH tunnel | `-L 5672:localhost:5672` | Ad-hoc, uses existing SSH keys |
| VPN | Site-to-site | Existing enterprise infrastructure |

## Critical Path: RabbitMQ

RabbitMQ availability becomes the single point of failure between control plane and hosts.

Mitigations:
- RabbitMQ cluster (3-node minimum for quorum)
- Heartbeat + reconnection logic (sidecar uses `heartbeat=600`, `blocked_connection_timeout=300`)
- Fallback to direct SSH for emergencies

## What Hatchet Adds

| Capability | Without Hatchet | With Hatchet |
|------------|-----------------|--------------|
| Retries | Manual | Automatic with backoff |
| Timeouts | Per-request | Workflow-level with escalation |
| State | Stateless | Durable across restarts |
| Coordination | Sequential | DAG-based parallelism |
| Visibility | Logs | UI + event stream |

## Files to Review

- `src/rots/sidecar/rabbitmq.py` - queue consumer
- `src/rots/sidecar/handlers_rots.py` - rots CLI invocation + allowlist
- `docs/SIDECAR-SPEC.md` - complete protocol spec
