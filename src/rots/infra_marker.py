# src/rots/infra_marker.py

"""`.otsinfra.yaml` discovery + schema for the bootstrap command.

The bootstrap command (``rots env bootstrap``) is the first consumer of a
per-repo YAML marker file that sits beside the long-standing
``.otsinfra.env`` / ``.otsinfra-hosts.txt`` files. This module implements
*only* the subset the bootstrap command needs:

* walk-up discovery from CWD to either ``.git`` or ``$HOME`` (mirrors the
  pattern used by :mod:`rots.deploy.manifest`);
* parsing the ``envs.<env>`` block into a typed :class:`EnvConfig`;
* validation of the required keys listed in the issue #55 spec.

This is deliberately NOT a general-purpose marker-file library -- keeping it
focused makes the failure modes easy to reason about. When a second caller
lands, refactor rather than speculatively generalise.

Example ``.otsinfra.yaml``::

    envs:
      eu-demo:
        db:
          host_id: eu-demo-db
        web:
          host_id: eu-demo-web
          ip: 10.0.0.5
        app:
          name: onetimesecret
          owner_role: onetimesecret
        valkey:
          rules:
            - "+@read"
            - "+@write"
            - "-@admin"
            - "~otsv1:*"
        backup:
          profile: db-daily
          target: b2-ots:/eu-demo/db
          schedule: "*-*-* 03:00:00"
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

INFRA_MARKER_FILENAME = ".otsinfra.yaml"


class InfraMarkerError(Exception):
    """Error loading, parsing, or validating ``.otsinfra.yaml``."""


@dataclass(frozen=True)
class DbTarget:
    """Database sidecar target within an env block."""

    host_id: str


@dataclass(frozen=True)
class WebTarget:
    """Web sidecar target within an env block."""

    host_id: str
    ip: str


@dataclass(frozen=True)
class AppSpec:
    """Application name + owner role."""

    name: str
    owner_role: str


@dataclass(frozen=True)
class ValkeySpec:
    """Valkey ACL rules for the app user."""

    rules: tuple[str, ...]


@dataclass(frozen=True)
class BackupSpec:
    """Optional backup job spec routed to ``backup.install``."""

    profile: str
    target: str
    schedule: str


@dataclass(frozen=True)
class EnvConfig:
    """Parsed + validated ``envs.<env>`` block.

    Attributes:
        env: The environment name (key under ``envs``).
        db: Database sidecar target.
        web: Web sidecar target (with private IP for pg_hba).
        app: Application name + owner role.
        valkey: Valkey ACL rules.
        backup: Optional backup profile; ``None`` when the block is absent.
        source: Path to the marker file the config was loaded from.
    """

    env: str
    db: DbTarget
    web: WebTarget
    app: AppSpec
    valkey: ValkeySpec
    backup: BackupSpec | None = None
    source: Path | None = field(default=None, repr=False)


def find_marker_file(start: Path | None = None) -> Path | None:
    """Walk up from *start* looking for a ``.otsinfra.yaml`` file.

    Stops at the first directory containing ``.git`` or at the user's home
    directory -- whichever is reached first. Returns ``None`` if not found.

    Mirrors the walk-up discovery pattern used for ``.otsinfra.env``,
    ``.otsinfra-hosts.txt``, and ``.ots-deploy.yaml``.
    """
    current = (start or Path.cwd()).resolve()
    home = Path.home().resolve()

    while True:
        candidate = current / INFRA_MARKER_FILENAME
        if candidate.is_file():
            logger.debug("Found infra marker file: %s", candidate)
            return candidate

        # Stop at .git boundary (inclusive: check after the candidate lookup
        # so a marker sitting next to .git is still discoverable).
        if (current / ".git").exists():
            logger.debug("Reached .git boundary at %s, no marker found", current)
            return None

        if current == home:
            logger.debug("Reached home directory, no marker found")
            return None

        parent = current.parent
        if parent == current:
            # Filesystem root.
            return None
        current = parent


def load_env_config(env: str, *, start: Path | None = None) -> EnvConfig:
    """Discover ``.otsinfra.yaml`` and return the parsed ``envs.<env>`` block.

    Args:
        env: Environment name matching a key under ``envs:`` in the file.
        start: Optional start directory for discovery. Defaults to CWD.

    Returns:
        Validated :class:`EnvConfig`.

    Raises:
        InfraMarkerError: when the file is missing, unreadable, is not valid
            YAML, does not define ``envs.<env>``, or fails the required-keys
            check from issue #55.
    """
    path = find_marker_file(start)
    if path is None:
        raise InfraMarkerError(
            f"{INFRA_MARKER_FILENAME} not found. "
            "Create it in the repo root (or any ancestor of the current directory)."
        )
    return load_env_config_from_file(env, path)


def load_env_config_from_file(env: str, path: Path) -> EnvConfig:
    """Load ``.otsinfra.yaml`` from an explicit path.

    Separate entry-point so callers with their own discovery logic (and tests)
    can bypass the walk-up without monkeypatching.
    """
    try:
        import yaml
    except ImportError as exc:
        raise InfraMarkerError(
            "PyYAML is required to read .otsinfra.yaml. Install with: pip install pyyaml"
        ) from exc

    if not path.is_file():
        raise InfraMarkerError(f"{INFRA_MARKER_FILENAME} not found: {path}")

    try:
        text = path.read_text()
    except OSError as exc:
        raise InfraMarkerError(f"Cannot read {path}: {exc}") from exc

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise InfraMarkerError(f"Invalid YAML in {path}: {exc}") from exc

    if raw is None:
        raise InfraMarkerError(f"{path} is empty")
    if not isinstance(raw, dict):
        raise InfraMarkerError(f"{path}: top-level must be a mapping, got {type(raw).__name__}")

    envs_block = raw.get("envs")
    if not isinstance(envs_block, dict):
        raise InfraMarkerError(
            f"{path}: missing or non-mapping 'envs' key (got {type(envs_block).__name__})"
        )

    env_block = envs_block.get(env)
    if env_block is None:
        available = sorted(str(k) for k in envs_block.keys())
        raise InfraMarkerError(f"{path}: no block for env {env!r}. Available envs: {available}")
    if not isinstance(env_block, dict):
        raise InfraMarkerError(
            f"{path}: envs.{env} must be a mapping, got {type(env_block).__name__}"
        )

    return _parse_env_block(env, env_block, source=path)


def _parse_env_block(env: str, block: dict[str, Any], *, source: Path | None) -> EnvConfig:
    """Validate and project ``envs.<env>`` into an :class:`EnvConfig`.

    The required keys are the ones the bootstrap command actually consumes:
    ``db.host_id``, ``web.host_id``, ``web.ip``, ``app.name``,
    ``app.owner_role``, ``valkey.rules``. ``backup`` is optional but fully
    validated when present.
    """
    db_raw = _require_mapping(block, "db", env)
    db = DbTarget(host_id=_require_str(db_raw, "db.host_id", env))

    web_raw = _require_mapping(block, "web", env)
    web_ip = _require_str(web_raw, "web.ip", env)
    if "\n" in web_ip or "\r" in web_ip:
        raise InfraMarkerError(f"envs.{env}.web.ip must not contain newlines")
    try:
        ipaddress.ip_address(web_ip)
    except ValueError as exc:
        raise InfraMarkerError(
            f"envs.{env}.web.ip must be a valid IPv4 or IPv6 address: {exc}"
        ) from exc
    web = WebTarget(
        host_id=_require_str(web_raw, "web.host_id", env),
        ip=web_ip,
    )

    app_raw = _require_mapping(block, "app", env)
    app = AppSpec(
        name=_require_str(app_raw, "app.name", env),
        owner_role=_require_str(app_raw, "app.owner_role", env),
    )

    valkey_raw = _require_mapping(block, "valkey", env)
    rules_raw = valkey_raw.get("rules")
    if not isinstance(rules_raw, list) or not rules_raw:
        raise InfraMarkerError(f"envs.{env}.valkey.rules must be a non-empty list")
    rules: list[str] = []
    for idx, rule in enumerate(rules_raw):
        if not isinstance(rule, str) or not rule:
            raise InfraMarkerError(
                f"envs.{env}.valkey.rules[{idx}] must be a non-empty string, got {rule!r}"
            )
        rules.append(rule)
    valkey = ValkeySpec(rules=tuple(rules))

    backup: BackupSpec | None = None
    if "backup" in block and block["backup"] is not None:
        backup_raw = _require_mapping(block, "backup", env)
        backup = BackupSpec(
            profile=_require_str(backup_raw, "backup.profile", env),
            target=_require_str(backup_raw, "backup.target", env),
            schedule=_require_str(backup_raw, "backup.schedule", env),
        )

    return EnvConfig(
        env=env,
        db=db,
        web=web,
        app=app,
        valkey=valkey,
        backup=backup,
        source=source,
    )


def _require_mapping(block: dict[str, Any], key: str, env: str) -> dict[str, Any]:
    value = block.get(key)
    if value is None:
        raise InfraMarkerError(f"envs.{env}.{key} is required")
    if not isinstance(value, dict):
        raise InfraMarkerError(f"envs.{env}.{key} must be a mapping, got {type(value).__name__}")
    return value


def _require_str(block: dict[str, Any], dotted: str, env: str) -> str:
    # Last segment is the key within the local block.
    key = dotted.rsplit(".", 1)[-1]
    value = block.get(key)
    if value is None:
        raise InfraMarkerError(f"envs.{env}.{dotted} is required")
    if not isinstance(value, str) or not value.strip():
        raise InfraMarkerError(f"envs.{env}.{dotted} must be a non-empty string, got {value!r}")
    return value


__all__ = [
    "INFRA_MARKER_FILENAME",
    "AppSpec",
    "BackupSpec",
    "DbTarget",
    "EnvConfig",
    "InfraMarkerError",
    "ValkeySpec",
    "WebTarget",
    "find_marker_file",
    "load_env_config",
    "load_env_config_from_file",
]
