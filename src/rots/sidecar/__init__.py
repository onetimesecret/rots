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
    rots.doctor          - Health checks

Security: rots.* commands use an allowlist. Blocked: sidecar (recursive),
host.push/pull, cloudinit, env.push.

Usage
-----
Install and run via rots CLI::

    rots sidecar install   # Write systemd unit
    rots sidecar start     # Start daemon
    rots sidecar send '{"command": "rots.proxy.reload"}'

Send from control plane via RabbitMQ::

    {"command": "rots.instance.redeploy", "params": {"args": [7043, 7044]}}
"""
