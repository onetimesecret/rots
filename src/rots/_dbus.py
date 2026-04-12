# src/rots/_dbus.py

"""D-Bus backend for systemd operations using pystemd.

Provides direct D-Bus access to systemd's Manager and Unit interfaces,
replacing fragile CLI output parsing. Used automatically for local
operations when pystemd is available, with transparent fallback to the
CLI path when it is not.

Requires: ``pip install rots[dbus]`` (pystemd), libsystemd, systemd 245+.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

logger = logging.getLogger(__name__)

try:
    import pystemd.systemd1  # noqa: F401  # pyright: ignore[reportMissingImports]

    PYSTEMD_AVAILABLE = True
except ImportError:
    PYSTEMD_AVAILABLE = False


class UnitInfo(NamedTuple):
    """Decoded unit information from ListUnitsByPatterns."""

    name: str
    load_state: str
    active_state: str
    sub_state: str


def _decode(value: bytes | str) -> str:
    """Decode pystemd bytes to str."""
    return value.decode() if isinstance(value, bytes) else value


def _normalize(name: str) -> str:
    """Ensure a unit name has a type suffix (default ``.service``).

    pystemd's Manager methods (``StartUnit``, ``StopUnit``, …) and ``Unit()``
    require a fully-qualified name like ``onetime-web@7043.service``.
    ``systemctl`` auto-appends ``.service`` but D-Bus does not.
    """
    if "." not in name.split("@")[-1]:
        return f"{name}.service"
    return name


def _manager():  # noqa: ANN202
    """Import and return pystemd Manager at call time (avoids module-level None)."""
    from pystemd.systemd1 import Manager  # pyright: ignore[reportMissingImports]

    return Manager()


def _unit(name: str):  # noqa: ANN202
    """Import and return pystemd Unit at call time."""
    from pystemd.systemd1 import Unit  # pyright: ignore[reportMissingImports]

    return Unit(_normalize(name).encode(), _autoload=True)


def available() -> bool:
    """Return True if pystemd is importable and the system bus is reachable."""
    if not PYSTEMD_AVAILABLE:
        return False
    try:
        with _manager() as m:
            _decode(m.Manager.Version)
        return True
    except Exception:
        logger.debug("D-Bus system bus probe failed", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Manager operations
# ---------------------------------------------------------------------------


def list_units_by_pattern(pattern: str) -> list[UnitInfo]:
    """List units matching a glob pattern via ListUnitsByPatterns (systemd 230+).

    Returns a list of :class:`UnitInfo` tuples with decoded string fields.
    """
    with _manager() as m:
        raw = m.Manager.ListUnitsByPatterns([], [pattern.encode()])
    return [
        UnitInfo(
            name=_decode(u[0]),
            load_state=_decode(u[2]),
            active_state=_decode(u[3]),
            sub_state=_decode(u[4]),
        )
        for u in raw
    ]


def reload_manager() -> None:
    """Reload systemd manager configuration (``daemon-reload``)."""
    with _manager() as m:
        m.Manager.Reload()


def start_unit(name: str) -> None:
    """Start a unit (``systemctl start``)."""
    fq = _normalize(name)
    with _manager() as m:
        m.Manager.StartUnit(fq.encode(), b"replace")


def stop_unit(name: str) -> None:
    """Stop a unit (``systemctl stop``)."""
    fq = _normalize(name)
    with _manager() as m:
        m.Manager.StopUnit(fq.encode(), b"replace")


def restart_unit(name: str) -> None:
    """Restart a unit (``systemctl restart``)."""
    fq = _normalize(name)
    with _manager() as m:
        m.Manager.RestartUnit(fq.encode(), b"replace")


def enable_unit_files(names: list[str]) -> None:
    """Enable unit files (``systemctl enable``)."""
    with _manager() as m:
        m.Manager.EnableUnitFiles([_normalize(n).encode() for n in names], False, True)


def disable_unit_files(names: list[str]) -> None:
    """Disable unit files (``systemctl disable``)."""
    with _manager() as m:
        m.Manager.DisableUnitFiles([_normalize(n).encode() for n in names], False)


def reset_failed_unit(name: str) -> None:
    """Clear failed state for a unit (``systemctl reset-failed``)."""
    fq = _normalize(name)
    with _manager() as m:
        m.Manager.ResetFailedUnit(fq.encode())


# ---------------------------------------------------------------------------
# Unit property queries
# ---------------------------------------------------------------------------


def get_active_state(name: str) -> str:
    """Return ActiveState property (e.g. ``'active'``, ``'inactive'``, ``'failed'``)."""
    with _unit(name) as u:
        return _decode(u.Unit.ActiveState)


def get_load_state(name: str) -> str:
    """Return LoadState property (e.g. ``'loaded'``, ``'not-found'``)."""
    with _unit(name) as u:
        return _decode(u.Unit.LoadState)


def unit_file_exists(name: str) -> bool:
    """Check whether a unit file exists by querying LoadState != 'not-found'.

    Returns False (rather than raising) when the D-Bus query itself fails,
    so callers can fall through to the CLI path or treat the unit as absent.
    """
    try:
        return get_load_state(name) != "not-found"
    except Exception:
        logger.debug("Failed to query LoadState for %s via D-Bus", name, exc_info=True)
        return False
