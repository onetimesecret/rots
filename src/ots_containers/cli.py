# src/ots_containers/cli.py

"""
Manage OTS Podman containers via Quadlets.

Usage:

    ots-containers init
    ots-containers image pull --tag v0.23.0 --current
    ots-containers instance deploy 7043
    ots-containers instance redeploy 7044
    ots-containers image rollback
    ots-containers instance redeploy
    ots-containers assets sync

    # Or run directly without installing:
    $ cd src/
    $ pip install -e .
    $ python -m ots_containers.cli instance deploy 7043
"""

import logging
from typing import Annotated

import cyclopts

from . import __version__
from .commands import assets as assets_cmd
from .commands import cloudinit, env, image, init, instance, proxy, service
from .commands import db as db_cmd
from .podman import podman

logger = logging.getLogger(__name__)

app = cyclopts.App(
    name="ots-containers",
    help="Service orchestration for OTS: Podman Quadlets and systemd services",
    version=__version__,
)

# Register topic sub-apps
app.command(init.app)
app.command(instance.app)
app.command(image.app)
app.command(assets_cmd.app)
app.command(proxy.app)
app.command(service.app)
app.command(cloudinit.app)
app.command(env.app)
app.command(db_cmd.app)


def _configure_logging(verbose: bool) -> None:
    """Configure root logger based on verbosity flag.

    When --verbose is set, DEBUG-level messages from all ots_containers modules
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
):
    """Global options processed before any subcommand."""
    _configure_logging(verbose)
    app(tokens)


@app.default
def _default():
    """Show help when no command is specified."""
    app.help_print([])


# Root-level command for quick access
@app.command
def ps():
    """Show running OTS containers (podman view)."""
    podman.ps(
        filter="name=onetime",
        format="table {{.ID}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}\t{{.Names}}",
    )


@app.command
def version():
    """Show version and build info."""
    import subprocess
    from pathlib import Path

    print(f"ots-containers {__version__}")

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

    Returns exit code 0 when all checks pass, 1 when any fail.

    Examples:
        ots doctor
    """
    import os
    import shutil
    import subprocess

    from .config import Config
    from .environment_file import EnvFile, secret_exists
    from .quadlet import DEFAULT_ENV_FILE

    cfg = Config()
    checks: list[tuple[str, bool, str]] = []  # (label, ok, detail)

    def _check(label: str, ok: bool, detail: str = "") -> None:
        checks.append((label, ok, detail))

    # 1. systemctl available
    _check("systemctl available", bool(shutil.which("systemctl")), "install systemd")

    # 2. podman available
    _check("podman available", bool(shutil.which("podman")), "install podman")

    # 3. /etc/onetimesecret/ directory exists
    config_dir_ok = cfg.config_dir.exists()
    _check(
        "/etc/onetimesecret/ exists",
        config_dir_ok,
        f"sudo mkdir -p {cfg.config_dir}",
    )

    # 4. /var/lib/onetimesecret/ directory exists and is writable
    var_dir_ok = cfg.var_dir.exists() and os.access(cfg.var_dir, os.W_OK)
    _check(
        "/var/lib/onetimesecret/ writable",
        var_dir_ok,
        f"sudo mkdir -p {cfg.var_dir} && sudo chown $USER {cfg.var_dir}",
    )

    # 5. Env file exists
    env_file_ok = DEFAULT_ENV_FILE.exists()
    _check(
        f"{DEFAULT_ENV_FILE} exists",
        env_file_ok,
        "run: sudo ots init",
    )

    # 6. Env file has SECRET_VARIABLE_NAMES and they are processed
    secrets_ok = False
    secrets_detail = "run: sudo ots env process"
    if env_file_ok:
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
    _check("podman secrets configured", secrets_ok, secrets_detail)

    # 7. Web quadlet template exists
    web_quadlet_ok = cfg.web_template_path.exists()
    _check(
        f"{cfg.web_template_path} exists",
        web_quadlet_ok,
        "run: sudo ots instances deploy --web <port>",
    )

    # 8. At least one web instance running (best-effort)
    web_running = False
    web_running_detail = "run: sudo ots instances start --web"
    if shutil.which("systemctl"):
        try:
            result = subprocess.run(
                ["systemctl", "list-units", "onetime-web@*", "--plain", "--no-legend", "--all"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            web_running = "onetime-web@" in result.stdout
            if not web_running:
                web_running_detail = "no onetime-web@* units found"
        except (subprocess.SubprocessError, OSError, TimeoutError):
            web_running_detail = (
                "systemctl query failed; run: systemctl status onetime-web@*.service"
            )
    _check("web instance(s) running", web_running, web_running_detail)

    # 9. Caddy (proxy) running (best-effort)
    caddy_ok = False
    caddy_detail = "run: sudo systemctl start caddy"
    if shutil.which("systemctl"):
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "caddy"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            caddy_ok = result.stdout.strip() == "active"
        except (subprocess.SubprocessError, OSError, TimeoutError):
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
