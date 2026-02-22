# src/ots_containers/podman.py

"""Pythonic wrapper for podman CLI commands."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ots_shared.ssh.executor import Executor, Result


class Podman:
    """Wrapper for podman CLI commands.

    Two usage modes:

    1. **No executor (local-only)** -- commands run via ``subprocess.run``
       directly on the local host.  This is the mode used by the module-level
       ``podman`` singleton (see bottom of file) and by the ``image`` command
       family, which always operates on the machine running the CLI.

    2. **With executor (local or remote)** -- commands are dispatched through
       an ``Executor`` instance.  Pass ``executor=`` at construction time.
       Instance and asset commands use this path so the same code works for
       both local management and SSH-remote deployment.

    If you need remote execution, *always* pass an executor.  Instantiating
    ``Podman()`` without one will silently run every command locally via
    ``subprocess.run`` -- that is intentional for the image-management
    commands but would be a bug in any deployment-path code.

    Examples::

        # Local-only (image management)
        podman = Podman()
        podman.pull("ghcr.io/onetimesecret/onetimesecret:latest", check=True)

        # Executor-aware (deployment)
        from ots_shared.ssh import LocalExecutor
        p = Podman(executor=LocalExecutor())
        p.ps(capture_output=True, text=True)
    """

    def __init__(
        self,
        executable: str = "podman",
        _subcommand: list[str] | None = None,
        executor: Executor | None = None,
    ):
        self.executable = executable
        self._subcommand = _subcommand or []
        self._executor = executor

    def __call__(self, *args: str, **kwargs) -> subprocess.CompletedProcess | Result:
        """Run a podman command with the given arguments.

        Returns:
            subprocess.CompletedProcess when no executor is set (local-only
            mode, used by the module-level ``podman`` singleton and image
            commands).

            ots_shared.ssh.Result when an executor is present (deployment
            path, supports both local and SSH-remote hosts).
        """
        cmd = [self.executable, *self._subcommand]
        for key, value in kwargs.items():
            if key in ("capture_output", "text", "check", "timeout"):
                continue
            flag = f"--{key.replace('_', '-')}"
            if isinstance(value, bool):
                if value:
                    cmd.append(flag)
            elif isinstance(value, list | tuple):
                for v in value:
                    cmd.extend([flag, str(v)])
            else:
                cmd.extend([flag, str(value)])
        cmd.extend(args)

        if self._executor is None:
            return subprocess.run(
                cmd,
                capture_output=kwargs.get("capture_output", False),
                text=kwargs.get("text", False),
                check=kwargs.get("check", False),
            )

        return self._executor.run(
            cmd,
            timeout=kwargs.get("timeout"),
            check=kwargs.get("check", False),
        )

    def __getattr__(self, name: str):
        """Dynamically create methods for any podman subcommand.

        Converts underscores to hyphens for subcommand names.
        Supports nested subcommands like podman.volume.create().
        """
        subcommand = name.replace("_", "-")
        return Podman(
            executable=self.executable,
            _subcommand=[*self._subcommand, subcommand],
            executor=self._executor,
        )


# Module-level singleton for local-only commands (image pull/push/build/tag).
# Intentionally has no executor -- these operations always target the local
# podman store.  Deployment-path code should instantiate Podman(executor=...)
# instead; using this singleton there would silently skip SSH remoting.
podman = Podman()
