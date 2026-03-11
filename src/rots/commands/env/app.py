# src/rots/commands/env/app.py

"""Environment file management commands.

Process environment files to extract secrets and prepare for container deployment.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import cyclopts

from rots import context
from rots.config import Config
from rots.environment_file import (
    EnvFile,
    extract_secrets,
    process_env_file,
    secret_exists,
)
from rots.quadlet import DEFAULT_ENV_FILE

from ..common import DryRun, JsonOutput

if TYPE_CHECKING:
    from ots_shared.ssh import Executor

logger = logging.getLogger(__name__)

app = cyclopts.App(
    name="env",
    help="Manage environment files and secrets.",
)


def _file_exists(path: Path, executor: Executor) -> bool:
    """Check if a file exists, locally or remotely via executor."""
    from ots_shared.ssh import is_remote

    if is_remote(executor):
        result = executor.run(["test", "-f", str(path)])
        return result.ok
    return path.exists()


@app.command
def process(
    env_file: Annotated[
        Path | None,
        cyclopts.Parameter(
            name=["--file", "-f"],
            help="Path to environment file",
        ),
    ] = None,
    dry_run: DryRun = False,
    skip_secrets: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--skip-secrets"],
            help="Skip creating podman secrets (only transform env file)",
        ),
    ] = False,
):
    """Process environment file: extract secrets and create podman secrets.

    Reads SECRET_VARIABLE_NAMES from the environment file to identify
    which variables should be stored as podman secrets.

    For each secret variable found with a value:
    1. Creates a podman secret (ots_<varname_lowercase>)
    2. Transforms the env file entry: VARNAME=value -> _VARNAME=ots_varname

    This command is idempotent - safe to run multiple times.

    Examples:
        ots env process
        ots env process -f /path/to/envfile -n
    """
    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    path = env_file or DEFAULT_ENV_FILE

    if not _file_exists(path, ex):
        logger.error(f"Environment file not found: {path}")
        raise SystemExit(1)

    parsed = EnvFile.parse(path, executor=ex)

    if not parsed.secret_variable_names:
        logger.error("No SECRET_VARIABLE_NAMES defined in environment file.")
        logger.info("Add a line like: SECRET_VARIABLE_NAMES=VAR1,VAR2,VAR3")
        raise SystemExit(1)

    # Header
    suffix = " (dry-run)" if dry_run else ""
    logger.info(f"Processing: {path}{suffix}")
    logger.info(f"Secrets: {', '.join(parsed.secret_variable_names)}")
    logger.info("")

    secrets, messages = process_env_file(
        parsed,
        create_secrets=not skip_secrets,
        dry_run=dry_run,
        executor=ex,
    )

    # Categorize and display messages
    has_errors = False
    file_was_modified = False
    for msg in messages:
        if "secret created:" in msg:
            secret_name = msg.split(": ", 1)[-1]
            logger.info(f"  [created]  {secret_name}  (stored in podman secret store)")
        elif "secret replaced:" in msg:
            secret_name = msg.split(": ", 1)[-1]
            logger.info(f"  [replaced] {secret_name}  (updated in podman secret store)")
        elif "empty" in msg.lower():
            var = msg.split()[1] if len(msg.split()) > 1 else "unknown"
            logger.error(f"  [error]    {var} is empty")
            has_errors = True
        elif "not found" in msg.lower():
            var = msg.split()[1] if len(msg.split()) > 1 else "unknown"
            logger.error(f"  [error]    {var} not in file")
            has_errors = True
        elif "Updated environment file" in msg:
            file_was_modified = True
        elif "No changes needed" in msg:
            logger.info(f"  {msg}")
        else:
            logger.info(f"  {msg}")

    if has_errors:
        logger.error("Errors found. All secrets must have values.")
        raise SystemExit(1)

    if dry_run:
        logger.info("Dry-run complete. Run without --dry-run to apply changes.")
    elif file_was_modified:
        logger.info(f"Updated: {path}")


@app.default
@app.command
def show(
    env_file: Annotated[
        Path | None,
        cyclopts.Parameter(
            name=["--file", "-f"],
            help="Path to environment file",
        ),
    ] = None,
    json_output: JsonOutput = False,
):
    """Show secrets configuration from environment file.

    Displays SECRET_VARIABLE_NAMES and the status of each secret
    (whether it exists in podman, is processed in env file, etc.).

    Examples:
        ots env show
        ots env show -f /path/to/envfile
        ots env show --json
    """
    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    path = env_file or DEFAULT_ENV_FILE

    if not _file_exists(path, ex):
        logger.error(f"Environment file not found: {path}")
        raise SystemExit(1)

    parsed = EnvFile.parse(path, executor=ex)

    if not parsed.secret_variable_names:
        if json_output:
            import json

            print(
                json.dumps(
                    {
                        "file": str(path),
                        "secrets": [],
                        "warning": "No SECRET_VARIABLE_NAMES defined",
                    }
                )
            )
        else:
            print(f"File: {path}")
            print("Warning: No SECRET_VARIABLE_NAMES defined.")
            print("Add a line like: SECRET_VARIABLE_NAMES=VAR1,VAR2,VAR3")
        return

    secrets, messages = extract_secrets(parsed)

    if json_output:
        import json

        data = {
            "file": str(path),
            "secret_variable_names": parsed.secret_variable_names,
            "secrets": [],
        }
        for spec in secrets:
            processed_key = f"_{spec.env_var_name}"
            if parsed.has(processed_key):
                actual_secret_name = parsed.get(processed_key)
                env_status = "processed"
            elif parsed.has(spec.env_var_name):
                actual_secret_name = spec.secret_name
                value = parsed.get(spec.env_var_name)
                env_status = "has value" if value else "empty"
            else:
                actual_secret_name = spec.secret_name
                env_status = "not in file"

            podman_status = (
                "exists" if secret_exists(actual_secret_name, executor=ex) else "missing"
            )

            data["secrets"].append(
                {
                    "env_var": spec.env_var_name,
                    "secret_name": actual_secret_name,
                    "env_status": env_status,
                    "podman_status": podman_status,
                }
            )
        print(json.dumps(data, indent=2))
        return

    print(f"File: {path}")
    print(f"SECRET_VARIABLE_NAMES: {parsed.get('SECRET_VARIABLE_NAMES')}")
    print()
    print("Secret Status:")
    print("-" * 60)

    for spec in secrets:
        processed_key = f"_{spec.env_var_name}"

        # Check env file status and determine actual secret name
        if parsed.has(processed_key):
            # Use actual value from processed entry (may differ from calculated)
            actual_secret_name = parsed.get(processed_key)
            env_status = "processed"
        elif parsed.has(spec.env_var_name):
            actual_secret_name = spec.secret_name  # Use calculated name
            value = parsed.get(spec.env_var_name)
            env_status = "has value" if value else "empty"
        else:
            actual_secret_name = spec.secret_name  # Use calculated name
            env_status = "not in file"

        # Check podman secret based on actual value (not calculated)
        podman_status = "exists" if secret_exists(actual_secret_name, executor=ex) else "missing"

        print(f"  {spec.env_var_name}:")
        print(f"    podman secret: {actual_secret_name} ({podman_status})")
        print(f"    env file: {env_status}")

    # Show any warnings from extraction
    if messages:
        print()
        for msg in messages:
            print(msg)


@app.command(name="quadlet-lines")
def quadlet_lines(
    env_file: Annotated[
        Path | None,
        cyclopts.Parameter(
            name=["--file", "-f"],
            help="Path to environment file",
        ),
    ] = None,
):
    """Generate Secret= lines for quadlet template.

    Outputs the Secret= directives that should be included in the
    quadlet container template based on SECRET_VARIABLE_NAMES.

    Examples:
        ots env quadlet-lines
        ots env quadlet-lines -f /path/to/envfile
    """
    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    path = env_file or DEFAULT_ENV_FILE

    if not _file_exists(path, ex):
        print(f"Error: Environment file not found: {path}", file=sys.stderr)
        raise SystemExit(1)

    parsed = EnvFile.parse(path, executor=ex)

    if not parsed.secret_variable_names:
        print(
            "Error: No SECRET_VARIABLE_NAMES defined in environment file.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    secrets, messages = extract_secrets(parsed)

    # Check for errors (empty or missing secrets)
    errors = [m for m in messages if "empty" in m.lower() or "not found" in m.lower()]
    if errors:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        raise SystemExit(1)

    print("# Secrets via Podman secret store (not on disk)")
    print("# These are injected as environment variables at container start")
    for spec in secrets:
        print(spec.quadlet_line)


@app.command
def verify(
    env_file: Annotated[
        Path | None,
        cyclopts.Parameter(
            name=["--file", "-f"],
            help="Path to environment file",
        ),
    ] = None,
):
    """Verify all required podman secrets exist.

    Checks that each secret defined in SECRET_VARIABLE_NAMES has
    a corresponding podman secret created. Useful before deployment.

    Examples:
        ots env verify
        ots env verify -f /path/to/envfile
    """
    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    path = env_file or DEFAULT_ENV_FILE

    if not _file_exists(path, ex):
        logger.error(f"Environment file not found: {path}")
        raise SystemExit(1)

    parsed = EnvFile.parse(path, executor=ex)

    if not parsed.secret_variable_names:
        logger.info("No SECRET_VARIABLE_NAMES defined - nothing to verify.")
        return

    logger.info(f"Verifying secrets for: {path}")

    secrets, _ = extract_secrets(parsed)
    all_ok = True

    for spec in secrets:
        exists = secret_exists(spec.secret_name, executor=ex)
        status = "OK" if exists else "MISSING"
        symbol = "+" if exists else "-"
        logger.info(f"  [{symbol}] {spec.secret_name} -> {spec.env_var_name}: {status}")
        if not exists:
            all_ok = False

    if all_ok:
        logger.info("All secrets verified.")
    else:
        logger.error("Missing secrets detected. Run 'ots env process' to create them.")
        raise SystemExit(1)


@app.command
def push(
    source: Annotated[
        Path,
        cyclopts.Parameter(help="Local .env file to push to the remote host"),
    ],
    dest: Annotated[
        Path | None,
        cyclopts.Parameter(
            name=["--dest", "-d"],
            help=f"Remote destination path (default: {DEFAULT_ENV_FILE})",
        ),
    ] = None,
    process_secrets: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--process"],
            help="Run 'ots env process' after pushing the file",
        ),
    ] = False,
    dry_run: DryRun = False,
):
    """Push a local .env file to a remote host.

    Copies the local environment file to the remote host at the standard
    path (/etc/default/onetimesecret). Optionally runs 'ots env process'
    afterward to extract secrets into the podman secret store.

    This bridges the gap between local jurisdiction config files and
    remote host deployment. Typical workflow:

        1. Edit .env in ops-jurisdictions/<jurisdiction>/
        2. Push to host: ots --host eu-web-01 env push .env --process
        3. Deploy: ots --host eu-web-01 instance deploy 7043

    Examples:
        ots --host eu-web-01 env push .env
        ots --host eu-web-01 env push .env --process
        ots --host eu-web-01 env push .env.secrets --dest /etc/default/onetimesecret --process
        ots --host eu-web-01 env push .env -n
    """
    from ots_shared.ssh import is_remote

    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    remote_path = dest or DEFAULT_ENV_FILE

    if not is_remote(ex):
        logger.error("push requires a remote host. Use --host to specify one.")
        logger.info("For local env file processing, use 'ots env process' directly.")
        raise SystemExit(1)

    if not source.exists():
        logger.error(f"Local file not found: {source}")
        raise SystemExit(1)

    content = source.read_text()
    if not content.strip():
        logger.error(f"Local file is empty: {source}")
        raise SystemExit(1)

    if dry_run:
        logger.info(f"Would push: {source} -> {remote_path} (on remote host)")
        logger.info(f"  File size: {len(content.encode('utf-8'))} bytes")
        logger.info(f"  Lines: {len(content.splitlines())}")
        if process_secrets:
            logger.info(f"Would then run: ots env process -f {remote_path}")
        return

    # Push the file to the remote host
    logger.info(f"Pushing {source} -> {remote_path}")
    # Ensure parent directory exists
    ex.run(["mkdir", "-p", str(remote_path.parent)])
    result = ex.run(["tee", str(remote_path)], input=content)
    if not result.ok:
        logger.error(f"Failed to write {remote_path} on remote host")
        if result.stderr:
            logger.error(f"  {result.stderr.strip()}")
        raise SystemExit(1)
    logger.info(f"  Pushed ({len(content.encode('utf-8'))} bytes)")

    # Optionally process secrets
    if process_secrets:
        logger.info(f"Processing secrets from {remote_path}...")
        parsed = EnvFile.parse(remote_path, executor=ex)

        if not parsed.secret_variable_names:
            logger.warning("No SECRET_VARIABLE_NAMES defined in pushed file.")
            logger.info("Secrets processing skipped.")
            return

        logger.info(f"Secrets: {', '.join(parsed.secret_variable_names)}")

        secrets, messages = process_env_file(
            parsed,
            create_secrets=True,
            dry_run=False,
            executor=ex,
        )

        for msg in messages:
            if "secret created:" in msg:
                secret_name = msg.split(": ", 1)[-1]
                logger.info(f"  [created]  {secret_name}")
            elif "secret replaced:" in msg:
                secret_name = msg.split(": ", 1)[-1]
                logger.info(f"  [replaced] {secret_name}")
            elif "Updated environment file" in msg:
                pass  # handled below
            elif "No changes needed" in msg:
                logger.info(f"  {msg}")
            else:
                logger.info(f"  {msg}")

        logger.info(f"Updated: {remote_path}")
