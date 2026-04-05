# src/rots/sidecar/__init__.py

"""Sidecar daemon for remote OTS instance control.

The sidecar runs on each OTS host, accepting commands via Unix socket
(local CLI) or RabbitMQ (remote control plane). It enables centralized
fleet management without direct SSH access.

Architecture
------------
    Control Plane ─── RabbitMQ ───> Sidecar ───> rots CLI / systemd
    Local CLI ─────── Unix Socket ─────────────>

Interfaces
----------
- Unix socket: /run/onetime-sidecar.sock (root only, JSON messages)
- RabbitMQ: ots.sidecar.commands queue, reply via reply_to

Command Categories
------------------
Built-in handlers (fast path, no subprocess):
    restart.web, stop.web, start.web
    restart.worker, stop.worker, start.worker
    restart.scheduler, stop.scheduler, start.scheduler
    phased_restart.web, phased_restart.worker
    rolling_restart, status, health
    config.stage, config.apply, config.discard, config.get

Generic rots CLI invocation (rots.* prefix):
    rots.env.process     - Process env secrets
    rots.proxy.reload    - Reload Caddy
    rots.instance.*      - Instance lifecycle
    rots.image.*         - Image management
    rots.service.*       - Systemd services
    rots.self.upgrade    - Self-upgrade (supports --source for git URLs)
    rots.doctor          - Health checks

Security: rots.* commands use an allowlist. Blocked: sidecar (recursive),
host.push/pull, cloudinit, env.push.

Usage
-----
Install and run via rots CLI::

    rots sidecar install       # Write systemd unit (auto-detects rots path)
    rots sidecar start         # Start daemon
    rots sidecar status        # Check daemon status

Send commands via Unix socket (local)::

    rots sidecar send health --socket
    rots sidecar send status --socket
    rots sidecar send restart.web identifier=7043 --socket

Send commands via RabbitMQ (remote)::

    rots sidecar send health --rabbitmq
    rots sidecar send rots.self.upgrade \
      args=--source args=git+https://github.com/onetimesecret/rots.git@main \
      --rabbitmq

Remote control via SSH tunnel::

    # Forward RabbitMQ port
    ssh -L 5672:maindb:5672 user@server

    # Send command through tunnel
    RABBITMQ_URL=amqp://user:pass@localhost:5672/vhost rots sidecar send health --rabbitmq

Configuration via .otsinfra.env::

    The sidecar send command reads RABBITMQ_URL from:
    1. Environment variable (RABBITMQ_URL=...)
    2. Walk-up discovery of .otsinfra.env files

    This enables per-jurisdiction targeting. Place .otsinfra.env files in
    your ops directory structure::

        ops-jurisdictions/
          eu/.otsinfra.env     # OTS_HOST=eu-prod RABBITMQ_URL=amqp://...
          ca/.otsinfra.env     # OTS_HOST=ca-prod RABBITMQ_URL=amqp://...

    When you cd into a jurisdiction and run sidecar commands, the walk-up
    resolver finds the appropriate .otsinfra.env automatically.
"""
