# src/rots/cli.py

"""
Manage OTS Podman containers via Quadlets.

Usage:

    rots init
    rots image pull --tag v0.23.0 --current
    rots instance deploy 7043
    rots instance redeploy 7044
    rots image rollback
    rots instance redeploy
    rots assets sync

    # Or run directly without installing:
    $ pip install -e .
    $ python -m rots.cli instance deploy 7043
"""

import logging
from typing import Annotated

import cyclopts

from . import __version__
from .commands import assets as assets_cmd
from .commands import cloudinit, dns, env, host, image, init, instance, proxy, service
from .commands import db as db_cmd

logger = logging.getLogger(__name__)

app = cyclopts.App(
    name="rots",
    help="Service orchestration for OTS: Podman Quadlets and systemd services",
    version=__version__,
)

# Register topic sub-apps
app.command(init.app)
app.command(instance.app)
app.command(image.app)
app.command(assets_cmd.app)
app.command(proxy.app)
app.command(host.app)
app.command(service.app)
app.command(dns.app)
app.command(cloudinit.app)
app.command(env.app)
app.command(db_cmd.app)


def _configure_logging(verbose: bool) -> None:
    """Configure root logger based on verbosity flag.

    When --verbose is set, DEBUG-level messages from all rots modules
    are shown on stderr. Without it, only WARNING and above are shown.
    """
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )
    # Suppress overly chatty third-party loggers even in verbose mode
    logging.getLogger("urllib3").setLevel(logging.WARNING)


@app.meta.default
def _meta(
    *tokens: Annotated[str, cyclopts.Parameter(show=False, allow_leading_hyphen=True)],
    verbose: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--verbose", "-v"],
            help="Enable debug logging",
        ),
    ] = False,
    host: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--host", "-H"],
            help="Target host for remote execution (overrides OTS_HOST and .otsinfra.env)",
        ),
    ] = None,
):
    """Global options processed before any subcommand."""
    from . import context

    _configure_logging(verbose)
    if host is not None:
        context.host_var.set(host)
    app(tokens)


@app.default
def _default():
    """Show help when no command is specified."""
    app.help_print([])


# Root-level command for quick access
@app.command
def ps():
    """Show running OTS containers (podman view)."""
    from . import context
    from .config import Config
    from .podman import Podman

    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    p = Podman(executor=ex)
    p.ps(
        filter="name=onetime",
        format="table {{.ID}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}\t{{.Names}}",
    )


@app.command
def version():
    """Show version and build info."""
    import subprocess
    from pathlib import Path

    print(f"rots {__version__}")

    # Try to get git info if available
    try:
        pkg_dir = Path(__file__).parent
        result = subprocess.run(
            ["git", "-C", str(pkg_dir), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            commit = result.stdout.strip()
            print(f"git commit: {commit}")
    except (FileNotFoundError, subprocess.SubprocessError):
        pass


@app.command
def doctor():
    """Validate the full stack before deploying.

    Checks systemd, podman, images, secrets, env file, quadlets, caddy,
    and required directories.  Prints a pass/fail line for each check so
    operators can quickly identify what needs to be fixed.

    When ``--host`` is set, runs checks on the remote host via the executor.

    Returns exit code 0 when all checks pass, 1 when any fail.

    Examples:
        ots doctor
        ots --host eu1.example.com doctor
    """
    import shutil

    from . import context
    from .config import Config
    from .environment_file import EnvFile, secret_exists
    from .quadlet import DEFAULT_ENV_FILE

    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))

    from ots_shared.ssh import LocalExecutor

    is_local = isinstance(ex, LocalExecutor)

    checks: list[tuple[str, bool, str]] = []  # (label, ok, detail)

    def _check(label: str, ok: bool, detail: str = "") -> None:
        checks.append((label, ok, detail))

    def _has_command(name: str) -> bool:
        """Check whether *name* is on PATH (local or remote)."""
        if is_local:
            return bool(shutil.which(name))
        result = ex.run(["which", name], timeout=10)
        return result.ok

    def _path_exists(path: str) -> bool:
        """Check whether *path* exists (local or remote)."""
        if is_local:
            from pathlib import Path

            return Path(path).exists()
        result = ex.run(["test", "-e", path], timeout=10)
        return result.ok

    def _path_writable(path: str) -> bool:
        """Check whether *path* is a writable directory (local or remote)."""
        if is_local:
            import os
            from pathlib import Path

            p = Path(path)
            return p.exists() and os.access(p, os.W_OK)
        result = ex.run(["test", "-w", path], timeout=10)
        return result.ok

    # 1. systemctl available
    _check("systemctl available", _has_command("systemctl"), "install systemd")

    # 2. podman available
    _check("podman available", _has_command("podman"), "install podman")

    # 3. /etc/onetimesecret/ directory exists
    config_dir_ok = _path_exists(str(cfg.config_dir))
    _check(
        "/etc/onetimesecret/ exists",
        config_dir_ok,
        f"sudo mkdir -p {cfg.config_dir}",
    )

    # 4. /var/lib/onetimesecret/ directory exists and is writable
    var_dir_ok = _path_writable(str(cfg.var_dir))
    _check(
        "/var/lib/onetimesecret/ writable",
        var_dir_ok,
        f"sudo mkdir -p {cfg.var_dir} && sudo chown $USER {cfg.var_dir}",
    )

    # 5. config.yaml exists in config dir
    config_yaml_ok = _path_exists(str(cfg.config_yaml))
    _check(
        f"{cfg.config_yaml} exists",
        config_yaml_ok,
        f"copy config.yaml to {cfg.config_dir}/",
    )

    # 6. Env file exists
    env_file_ok = _path_exists(str(DEFAULT_ENV_FILE))
    _check(
        f"{DEFAULT_ENV_FILE} exists",
        env_file_ok,
        "run: sudo ots init",
    )

    # 7. Env file has SECRET_VARIABLE_NAMES and they are processed
    #    Local: parse env file and verify each declared secret exists in podman
    #    Remote: check for ots_* secrets via podman secret ls
    secrets_ok = False
    secrets_detail = "run: sudo ots env process"
    if env_file_ok and is_local:
        try:
            parsed = EnvFile.parse(DEFAULT_ENV_FILE)
            if parsed.secret_variable_names:
                # Check all declared secrets exist as podman secrets
                all_exist = all(
                    secret_exists(f"ots_{name.lower()}") for name in parsed.secret_variable_names
                )
                secrets_ok = all_exist
                if not all_exist:
                    secrets_detail = "run: sudo ots env process"
            else:
                secrets_detail = "set SECRET_VARIABLE_NAMES in env file"
        except Exception as exc:
            secrets_detail = f"parse error: {exc}"
    elif env_file_ok and not is_local:
        # Remote: check via podman secret ls
        result = ex.run(["podman", "secret", "ls", "--format", "{{.Name}}"], timeout=10)
        if result.ok and result.stdout.strip():
            secrets_ok = any(line.startswith("ots_") for line in result.stdout.splitlines())
        secrets_detail = "run: sudo ots env process (on remote host)" if not secrets_ok else ""
    _check("podman secrets configured", secrets_ok, secrets_detail)

    # 8. Web quadlet template exists
    web_quadlet_ok = _path_exists(str(cfg.web_template_path))
    _check(
        f"{cfg.web_template_path} exists",
        web_quadlet_ok,
        "run: sudo ots instances deploy --web <port>",
    )

    # 9. At least one web instance running (best-effort)
    web_running = False
    web_running_detail = "run: sudo ots instances start --web"
    if _has_command("systemctl"):
        result = ex.run(
            ["systemctl", "list-units", "onetime-web@*", "--plain", "--no-legend", "--all"],
            timeout=10,
        )
        if result.ok:
            web_running = "onetime-web@" in result.stdout
            if not web_running:
                web_running_detail = "no onetime-web@* units found"
        else:
            web_running_detail = (
                "systemctl query failed; run: systemctl status onetime-web@*.service"
            )
    _check("web instance(s) running", web_running, web_running_detail)

    # 10. Caddy (proxy) running (best-effort)
    caddy_ok = False
    caddy_detail = "run: sudo systemctl start caddy"
    if _has_command("systemctl"):
        result = ex.run(["systemctl", "is-active", "caddy"], timeout=10)
        if result.ok:
            caddy_ok = result.stdout.strip() == "active"
        else:
            caddy_detail = "systemctl query failed; run: systemctl status caddy"
    _check("caddy running", caddy_ok, caddy_detail)

    # --- Report ---
    width = max(len(label) for label, _, _ in checks)
    any_failed = False
    for label, ok, detail in checks:
        symbol = "+" if ok else "-"
        status = "ok" if ok else "FAIL"
        line = f"  [{symbol}] {label:<{width}}  {status}"
        if not ok:
            any_failed = True
            if detail:
                line += f"  ({detail})"
        print(line)

    print()
    if any_failed:
        print("Some checks failed. See details above.")
        raise SystemExit(1)
    else:
        print("All checks passed.")


def main():
    """Entry point that invokes the meta app to handle --verbose before subcommands."""
    app.meta()


if __name__ == "__main__":
    main()
