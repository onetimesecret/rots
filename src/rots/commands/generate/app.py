# src/rots/commands/generate/app.py

"""Generate standalone quadlet/unit files for review and manual installation.

Unlike ``rots instance deploy`` which writes units directly to the system
and starts them, ``rots generate`` writes files to a user-chosen directory
(or stdout) without touching the running system.  This supports the
standard sysadmin workflow: generate -> review -> install -> enable.

Examples:

    # Export all quadlet files to a directory
    rots generate ./my-units/

    # Export specific instance types
    rots generate ./my-units/ --web --worker --scheduler

    # Preview on stdout (single type)
    rots generate --web --stdout

    # Include an env file template for reference
    rots generate ./my-units/ --with-env-template

    # Skip secrets/env validation (for machines without podman)
    rots generate ./my-units/ --force
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Annotated

import cyclopts

from rots import quadlet
from rots.commands.common import EXIT_FAILURE, DryRun
from rots.config import Config
from rots.environment_file import ENV_FILE_TEMPLATE
from rots.quadlet import DEFAULT_ENV_FILE

logger = logging.getLogger(__name__)

app = cyclopts.App(
    name="generate",
    help="Generate standalone quadlet files for review and manual installation",
)


# File names matching what quadlet.py writes to /etc/containers/systemd/
_WEB_FILENAME = "onetime-web@.container"
_WORKER_FILENAME = "onetime-worker@.container"
_SCHEDULER_FILENAME = "onetime-scheduler@.container"
_IMAGE_FILENAME = "onetime.image"
_ENV_TEMPLATE_FILENAME = "onetimesecret.env.example"


def _render_all_selected(
    cfg: Config,
    *,
    web: bool,
    worker: bool,
    scheduler: bool,
    force: bool,
) -> list[tuple[str, str]]:
    """Render selected templates and return (filename, content) pairs."""
    files: list[tuple[str, str]] = []

    if web:
        content = quadlet.render_web_template(cfg, force=force)
        files.append((_WEB_FILENAME, content))

    if worker:
        content = quadlet.render_worker_template(cfg, force=force)
        files.append((_WORKER_FILENAME, content))

    if scheduler:
        content = quadlet.render_scheduler_template(cfg, force=force)
        files.append((_SCHEDULER_FILENAME, content))

    # Include image unit when a private registry is configured
    if cfg.registry and any([web, worker, scheduler]):
        content = quadlet.render_image_template(cfg)
        files.append((_IMAGE_FILENAME, content))

    return files


@app.default
def generate(
    output_dir: Annotated[
        str | None,
        cyclopts.Parameter(
            help=("Directory to write generated files to. Created if it does not exist."),
        ),
    ] = None,
    *,
    web: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--web"],
            help="Generate web container quadlet",
        ),
    ] = False,
    worker: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--worker"],
            help="Generate worker container quadlet",
        ),
    ] = False,
    scheduler: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--scheduler"],
            help="Generate scheduler container quadlet",
        ),
    ] = False,
    with_env_template: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--with-env-template"],
            help="Include an env file template with documented variables",
        ),
    ] = False,
    stdout: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--stdout"],
            help="Write to stdout instead of files (useful for piping)",
        ),
    ] = False,
    force: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--force"],
            help="Skip secrets/env file validation (for offline generation)",
        ),
    ] = False,
    dry_run: DryRun = False,
) -> None:
    """Generate quadlet unit files without touching the running system.

    By default generates all three container types (web, worker, scheduler).
    Use --web, --worker, --scheduler to select specific types.

    Files are written to OUTPUT_DIR, or to stdout with --stdout.
    """
    # Default to all types when none specified
    if not any([web, worker, scheduler]):
        web = worker = scheduler = True

    if stdout and output_dir:
        logger.error("Cannot use --stdout with an output directory")
        raise SystemExit(EXIT_FAILURE)

    if not stdout and not output_dir:
        logger.error(
            "Specify an output directory or use --stdout\n"
            "\n"
            "Examples:\n"
            "  rots generate ./my-units/\n"
            "  rots generate --stdout --web"
        )
        raise SystemExit(EXIT_FAILURE)

    cfg = Config()

    # Render selected templates
    files = _render_all_selected(
        cfg,
        web=web,
        worker=worker,
        scheduler=scheduler,
        force=force,
    )

    if with_env_template:
        files.append((_ENV_TEMPLATE_FILENAME, ENV_FILE_TEMPLATE))

    if not files:
        logger.warning("No files to generate")
        return

    if stdout:
        _write_stdout(files)
    elif output_dir is not None:
        dest = Path(output_dir)
        _write_directory(dest, files, dry_run=dry_run)


def _write_stdout(files: list[tuple[str, str]]) -> None:
    """Write generated files to stdout, separated by headers."""
    for i, (filename, content) in enumerate(files):
        if i > 0:
            sys.stdout.write("\n")
        sys.stdout.write(f"# --- {filename} ---\n")
        sys.stdout.write(content)
        if not content.endswith("\n"):
            sys.stdout.write("\n")


def _write_directory(
    dest: Path,
    files: list[tuple[str, str]],
    *,
    dry_run: bool = False,
) -> None:
    """Write generated files to a directory."""
    if dry_run:
        logger.info(f"Would create directory: {dest}")
        for filename, _content in files:
            logger.info(f"  Would write: {dest / filename}")
        return

    dest.mkdir(parents=True, exist_ok=True)

    for filename, content in files:
        path = dest / filename
        path.write_text(content)
        logger.info(f"Wrote {path}")

    logger.info("")
    logger.info("Next steps:")
    logger.info(f"  1. Review the generated files in {dest}/")
    logger.info("  2. Copy quadlet files to /etc/containers/systemd/")
    logger.info(f"  3. Configure {DEFAULT_ENV_FILE} with your settings")
    logger.info("  4. Run: ots env process  (to create podman secrets)")
    logger.info("  5. Run: systemctl daemon-reload")
    logger.info("  6. Start instances: systemctl start onetime-web@7043")
