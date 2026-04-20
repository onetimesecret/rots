# src/rots/commands/env/bootstrap.py

"""`rots env bootstrap` -- operator-facing command that drives the two-phase
provisioning handlers registered on the db sidecar.

The command is deliberately thin: read ``.otsinfra.yaml``, issue four RPC
calls to the db sidecar in order, surface per-step receipts. The db sidecar
owns cross-host secret delivery; this command never addresses the web
sidecar directly.

Call sequence::

    1. postgres.bootstrap_app   (or postgres.rotate_password when --rotate)
    2. valkey.create_acl_user
    3. postgres.add_hba         (role must exist first)
    4. backup.install           (skipped when no backup block)

Exit codes follow the project convention:

    0  EXIT_SUCCESS  -- every step ok (or --dry-run printed its plan)
    1  EXIT_FAILURE  -- an RPC failed, timed out, or the remote returned an error
    3  EXIT_PRECOND  -- ``.otsinfra.yaml`` missing or schema-invalid

The RPC client factory :func:`_get_rpc_client` is the test seam -- tests
monkeypatch ``bootstrap._get_rpc_client`` to return an
:class:`~rots.commands.sidecar.handlers._transport.InProcessRpcClient`.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any

import cyclopts

from rots.commands.common import (
    EXIT_FAILURE,
    EXIT_PRECOND,
    EXIT_SUCCESS,
    DryRun,
    JsonOutput,
)
from rots.commands.sidecar.handlers._transport import RabbitMqRpcClient
from rots.infra_marker import EnvConfig, InfraMarkerError, load_env_config
from rots.sidecar.commands import Command, CommandResult

if TYPE_CHECKING:
    from rots.commands.sidecar.handlers._transport import RpcClient

logger = logging.getLogger(__name__)

# Per-step RPC timeout (seconds). Matches handler-internal expectations --
# bootstrap_app + create_acl_user do a sub-publish to secrets.deliver with a
# 10s inner timeout, so we give the outer call enough head-room to complete.
_RPC_TIMEOUT: float = 30.0


def _get_rpc_client() -> RpcClient:
    """Return an :class:`RpcClient` bound to the active broker.

    Test seam: tests monkeypatch this function to return an
    :class:`InProcessRpcClient`. Production returns a fresh
    :class:`RabbitMqRpcClient`; :class:`RabbitMQConfig` is loaded per-publish
    inside the client.
    """
    return RabbitMqRpcClient()


# --- step plumbing --------------------------------------------------------


@dataclass
class _StepReceipt:
    """One step's outcome, serialisable for ``--json`` output."""

    name: str
    host: str
    params: dict[str, Any]
    success: bool = False
    changed: bool = False
    data: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "host": self.host,
            "params": self.params,
            "success": self.success,
            "changed": self.changed,
            "data": self.data,
            "warnings": list(self.warnings),
            "error": self.error,
        }


def _render_hba_line(ip: str, role: str, database: str) -> str:
    """Render the pg_hba drop-in content for the web peer.

    Format is fixed per spec: a single ``hostssl`` line with ``/32`` CIDR
    and ``scram-sha-256`` auth, followed by a trailing newline. Inputs are
    NOT escaped -- the db handler validates role/database identifiers and
    ``peer_ip`` is surfaced verbatim into the authz rule.
    """
    return f"hostssl {database} {role} {ip}/32 scram-sha-256\n"


def _plan_steps(cfg: EnvConfig, *, rotate: bool) -> list[_StepReceipt]:
    """Build the ordered list of planned RPC calls.

    Returned receipts carry only ``name``, ``host``, and ``params``; the
    success / data fields are populated when the step runs. Splitting
    planning from execution makes ``--dry-run`` a pure projection.
    """
    db_host = cfg.db.host_id
    steps: list[_StepReceipt] = []

    if rotate:
        # Rotate path: postgres.rotate_password takes {role, peer_id}
        # (handler param is literally 'role', not 'owner_role').
        steps.append(
            _StepReceipt(
                name=Command.POSTGRES_ROTATE_PASSWORD.value,
                host=db_host,
                params={
                    "role": cfg.app.owner_role,
                    "peer_id": cfg.web.host_id,
                },
            )
        )
    else:
        steps.append(
            _StepReceipt(
                name=Command.POSTGRES_BOOTSTRAP_APP.value,
                host=db_host,
                params={
                    "app": cfg.app.name,
                    "owner_role": cfg.app.owner_role,
                    "peer_ip": cfg.web.ip,
                    "peer_id": cfg.web.host_id,
                },
            )
        )

    steps.append(
        _StepReceipt(
            name=Command.VALKEY_CREATE_ACL_USER.value,
            host=db_host,
            # NOTE: --rotate does NOT force a valkey token rotation. The
            # handler is naturally idempotent on rules -- rotating the token
            # without a rule change is out of scope for this PR.
            params={
                "name": cfg.app.name,
                "rules": list(cfg.valkey.rules),
                "peer_id": cfg.web.host_id,
            },
        )
    )

    steps.append(
        _StepReceipt(
            name=Command.POSTGRES_ADD_HBA.value,
            host=db_host,
            params={
                "name": "10-web",
                "content": _render_hba_line(
                    ip=cfg.web.ip,
                    role=cfg.app.owner_role,
                    database=cfg.app.name,
                ),
            },
        )
    )

    if cfg.backup is not None:
        steps.append(
            _StepReceipt(
                name=Command.BACKUP_INSTALL.value,
                host=db_host,
                params={
                    "profile": cfg.backup.profile,
                    "target": cfg.backup.target,
                    "schedule": cfg.backup.schedule,
                },
            )
        )

    return steps


def _run_step(client: RpcClient, step: _StepReceipt) -> None:
    """Execute a single step, mutating the receipt in place.

    Short-circuits on :class:`TimeoutError` / transport errors: the receipt
    ends up with ``success=False`` and an ``error`` describing which RPC
    failed. The caller decides whether to halt or continue (this command
    halts on first failure).
    """
    try:
        result: CommandResult = client.publish(
            step.host,
            step.name,
            step.params,
            timeout=_RPC_TIMEOUT,
        )
    except TimeoutError as exc:
        step.success = False
        step.error = f"RPC timeout after {_RPC_TIMEOUT:.0f}s: {exc}"
        return
    except Exception as exc:  # noqa: BLE001 -- transport-level surface
        step.success = False
        step.error = f"transport error: {exc.__class__.__name__}: {exc}"
        return

    step.success = bool(result.success)
    step.error = result.error
    step.warnings = list(result.warnings)
    # ``data`` shape is a TypedDict per step (see handlers/_types.py). We
    # surface it verbatim -- callers can introspect it for changed / per-step
    # echo fields.
    if isinstance(result.data, dict):
        step.data = dict(result.data)
        step.changed = bool(result.data.get("changed"))
    else:
        step.data = None
        step.changed = False


# --- output helpers -------------------------------------------------------


def _print_plan_text(cfg: EnvConfig, steps: list[_StepReceipt]) -> None:
    source = str(cfg.source) if cfg.source is not None else "<inline>"
    print(f"Dry-run plan for env={cfg.env} (source={source}):")
    for step in steps:
        print(f"  {step.host} <- {step.name} {step.params!r}")


def _print_plan_json(cfg: EnvConfig, steps: list[_StepReceipt], *, rotate: bool) -> None:
    payload = {
        "env": cfg.env,
        "rotate": rotate,
        "dry_run": True,
        "source": str(cfg.source) if cfg.source is not None else None,
        "steps": [{"name": s.name, "host": s.host, "params": s.params} for s in steps],
        "success": True,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


def _print_results_text(cfg: EnvConfig, steps: list[_StepReceipt]) -> None:
    print(f"Bootstrap {cfg.env}:")
    for step in steps:
        if not step.success:
            status = "FAIL"
        elif step.changed:
            status = "CHANGED"
        else:
            status = "OK (no-op)"
        print(f"  [{status}] {step.host} <- {step.name}")
        if step.data:
            # Hide the raw params on success; show the receipt instead.
            for k, v in step.data.items():
                print(f"      {k}: {v}")
        for w in step.warnings:
            print(f"      warning: {w}")
        if step.error:
            print(f"      error: {step.error}")


def _print_results_json(
    cfg: EnvConfig,
    steps: list[_StepReceipt],
    *,
    rotate: bool,
    overall_success: bool,
) -> None:
    payload = {
        "env": cfg.env,
        "rotate": rotate,
        "dry_run": False,
        "source": str(cfg.source) if cfg.source is not None else None,
        "steps": [s.to_dict() for s in steps],
        "success": overall_success,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


# --- command --------------------------------------------------------------


def bootstrap(
    *,
    env: Annotated[
        str,
        cyclopts.Parameter(
            name=["--env", "-e"],
            help="Environment name matching a block under 'envs:' in .otsinfra.yaml.",
        ),
    ],
    rotate: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--rotate"],
            help=(
                "Rotate the Postgres password instead of creating the role. "
                "Re-runs valkey.create_acl_user (idempotent on rules) and "
                "postgres.add_hba. Does not force a Valkey token rotation."
            ),
            negative=[],
        ),
    ] = False,
    dry_run: DryRun = False,
    json_output: JsonOutput = False,
) -> None:
    """Bootstrap an environment from a fresh state via the db sidecar.

    Reads ``.otsinfra.yaml`` (walked up from the current directory) and
    publishes the bootstrap sequence to the db sidecar for ``--env``:

        1. ``postgres.bootstrap_app`` (or ``postgres.rotate_password`` with
           ``--rotate``) -- creates the application role + database and
           delivers ``PG_PASSWORD`` to the web peer via ``secrets.deliver``.
        2. ``valkey.create_acl_user`` -- creates the ACL user + delivers
           ``VALKEY_PASSWORD`` to the web peer.
        3. ``postgres.add_hba`` -- drops in a ``hostssl`` line authorising
           the web peer's private IP.
        4. ``backup.install`` -- optional; only when the env has a
           ``backup:`` block.

    Idempotent: a second run should return ``changed=False`` everywhere.
    Failures short-circuit: a failed step stops the sequence.

    Examples:
        rots env bootstrap --env eu-demo
        rots env bootstrap --env eu-demo --dry-run
        rots env bootstrap --env eu-demo --rotate --json
    """
    # --- load + validate -------------------------------------------------
    try:
        cfg = load_env_config(env)
    except InfraMarkerError as exc:
        if json_output:
            print(
                json.dumps(
                    {
                        "env": env,
                        "error": str(exc),
                        "exit_code": EXIT_PRECOND,
                    },
                    sort_keys=True,
                )
            )
        else:
            logger.error("%s", exc)
        sys.exit(EXIT_PRECOND)

    steps = _plan_steps(cfg, rotate=rotate)

    # --- dry-run branch --------------------------------------------------
    if dry_run:
        if json_output:
            _print_plan_json(cfg, steps, rotate=rotate)
        else:
            _print_plan_text(cfg, steps)
        sys.exit(EXIT_SUCCESS)

    # --- execute ---------------------------------------------------------
    client = _get_rpc_client()
    overall_success = True
    for step in steps:
        _run_step(client, step)
        if not step.success:
            overall_success = False
            break  # short-circuit; leave later steps with success=False defaults

    # --- render output ---------------------------------------------------
    if json_output:
        _print_results_json(cfg, steps, rotate=rotate, overall_success=overall_success)
    else:
        _print_results_text(cfg, steps)

    sys.exit(EXIT_SUCCESS if overall_success else EXIT_FAILURE)


__all__ = ["bootstrap"]
