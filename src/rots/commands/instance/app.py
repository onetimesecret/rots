# src/rots/commands/instance/app.py

"""Instance management app and commands for OTS containers."""

import dataclasses
import difflib
import logging
import sys
from typing import Annotated

import cyclopts

from rots import assets, context, db, quadlet, systemd
from rots.config import Config, join_image_tag, parse_image_reference
from rots.podman import Podman

from ..common import (
    EXIT_FAILURE,
    EXIT_PARTIAL,
    DryRun,
    Follow,
    ImageRef,
    JsonOutput,
    Lines,
    Quiet,
    TagFlag,
    Yes,
)
from ._helpers import (
    apply_quiet,
    build_secret_args,
    deploy_lock,
    flush_output,
    for_each_instance,
    format_command,
    format_journalctl_hint,
    resolve_identifiers,
    run_hook,
)
from .annotations import (
    Delay,
    InstanceType,
    SchedulerFlag,
    TypeSelector,
    WebFlag,
    WorkerFlag,
    resolve_instance_type,
)

logger = logging.getLogger(__name__)

app = cyclopts.App(
    name=["instance", "instances"],
    help="Manage OTS container instances (quadlet, systemd)",
)


def _list_instances_impl(
    identifiers: tuple[str, ...],
    instance_type: InstanceType | None,
    json_output: bool,
):
    """Shared implementation for listing instances."""
    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    instances = resolve_identifiers(identifiers, instance_type, running_only=False, executor=ex)

    if not instances:
        logger.info("No configured instances found")
        logger.info("Deploy one first: ots instances deploy --help")
        return

    # Fetch container health once for all instances
    health_map = systemd.get_container_health_map(executor=ex)

    if json_output:
        import json

        output = []
        for inst_type, ids in instances.items():
            for id_ in ids:
                unit = systemd.unit_name(inst_type.value, id_)
                service = f"{unit}.service"
                active_status = systemd.is_active(service, executor=ex)

                # Look up container health
                health_info = health_map.get((inst_type.value, id_), {})
                health = health_info.get("health", "")
                uptime = health_info.get("uptime", "")

                # Get deployment info
                db_path = cfg.get_db_path(ex)
                if inst_type == InstanceType.WEB:
                    deployments = db.get_deployments(db_path, limit=1, port=int(id_), executor=ex)
                else:
                    # Worker/scheduler: query by notes containing instance ID
                    deployments = db.get_deployments(
                        db_path,
                        limit=1,
                        notes_like=f"%{inst_type.value}_id={id_}%",
                        executor=ex,
                    )
                if deployments:
                    dep = deployments[0]
                    output.append(
                        {
                            "type": inst_type.value,
                            "id": id_,
                            "service": service,
                            "container": unit,
                            "status": active_status,
                            "health": health,
                            "uptime": uptime,
                            "image": dep.image,
                            "tag": dep.tag,
                            "deployed": dep.timestamp,
                            "action": dep.action,
                        }
                    )
                else:
                    output.append(
                        {
                            "type": inst_type.value,
                            "id": id_,
                            "service": service,
                            "container": unit,
                            "status": active_status,
                            "health": health,
                            "uptime": uptime,
                            "image": None,
                            "tag": None,
                            "deployed": None,
                            "action": None,
                        }
                    )
        print(json.dumps(output, indent=2))
        return

    # Header
    header = (
        f"{'TYPE':<10} {'ID':<10} {'SERVICE':<28} {'CONTAINER':<24} "
        f"{'STATUS':<22} {'IMAGE:TAG':<38} {'DEPLOYED':<20} {'ACTION':<10}"
    )
    print(header)
    print("-" * 170)

    for inst_type, ids in instances.items():
        for id_ in ids:
            unit = systemd.unit_name(inst_type.value, id_)
            service = f"{unit}.service"

            # Get systemd status
            active_status = systemd.is_active(service, executor=ex)

            # Combine systemd status with container health
            health_info = health_map.get((inst_type.value, id_), {})
            health = health_info.get("health", "")
            if health:
                display_status = f"{active_status} ({health})"
            else:
                display_status = active_status

            # Get last deployment from database
            db_path = cfg.get_db_path(ex)
            if inst_type == InstanceType.WEB:
                deployments = db.get_deployments(db_path, limit=1, port=int(id_), executor=ex)
            else:
                # Worker/scheduler: query by notes containing instance ID
                deployments = db.get_deployments(
                    db_path,
                    limit=1,
                    notes_like=f"%{inst_type.value}_id={id_}%",
                    executor=ex,
                )
            if deployments:
                dep = deployments[0]
                image_tag = join_image_tag(dep.image, dep.tag)
                # Format timestamp - strip microseconds and 'T'
                deployed = dep.timestamp.split(".")[0].replace("T", " ")
                action = dep.action
            else:
                image_tag = "unknown"
                deployed = "n/a"
                action = "n/a"

            row = (
                f"{inst_type.value:<10} {id_:<10} {service:<28} {unit:<24} "
                f"{display_status:<22} {image_tag:<38} {deployed:<20} {action:<10}"
            )
            print(row)


@app.command
def ps(
    instance_type: TypeSelector = None,
    web: WebFlag = None,
    worker: WorkerFlag = None,
    scheduler: SchedulerFlag = None,
):
    """Show running OTS containers (podman view).

    Displays the podman-native view of containers, including health status.
    Filters to the selected instance type when --web/--worker/--scheduler is given.

    Examples:
        ots instances ps                    # All OTS containers
        ots instances ps --web              # Web containers only
        ots instances ps --scheduler        # Scheduler containers only
    """
    itype, _ids = resolve_instance_type(instance_type, web, worker, scheduler)
    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    p = Podman(executor=ex)

    if itype is not None:
        name_filter = f"name=onetime-{itype.value}@"
    else:
        name_filter = "name=onetime-"

    p.ps(
        filter=name_filter,
        format="table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Ports}}\t{{.Image}}",
    )


@app.default
@app.command(name="list")
def list_instances(
    instance_type: TypeSelector = None,
    web: WebFlag = None,
    worker: WorkerFlag = None,
    scheduler: SchedulerFlag = None,
    json_output: JsonOutput = False,
):
    """List instances with status, image, and deployment info.

    Auto-discovers all instances if no identifiers specified.

    Examples:
        ots instances                            # List all instances (default)
        ots instances list                       # List all instances (explicit)
        ots instances --web                      # List web instances only
        ots instances list --web 7043,7044       # List specific web instances
        ots instances --worker                   # List worker instances
        ots instances --scheduler                # List scheduler instances
        ots instances --json                     # JSON output
    """
    itype, identifiers = resolve_instance_type(instance_type, web, worker, scheduler)
    _list_instances_impl(identifiers, itype, json_output)


@app.command
def run(
    reference: ImageRef = None,
    port: Annotated[
        int | None,
        cyclopts.Parameter(
            name=["--port", "-p"],
            help="Container port to run on",
        ),
    ] = None,
    detach: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--detach", "-d"],
            help="Run container in background",
        ),
    ] = False,
    rm: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--rm"],
            help="Remove container when it exits",
        ),
    ] = True,
    production: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--production", "-P"],
            help="Include env file, secrets, and volumes (like deploy)",
        ),
    ] = False,
    name: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--name", "-n"],
            help="Container name (default: onetimesecret-{port})",
        ),
    ] = None,
    quiet: Quiet = False,
    tag: TagFlag = None,
):
    """Run a container directly with podman (no systemd).

    If .env exists in current directory, it will be used.
    Use --production to include system env file, secrets, and volumes.

    Examples:
        ots instance run -p 7143 --tag plop-2   # specific tag
        ots instance run -p 7143 -d             # detached
        ots instance run -p 7143 --production   # full production config
        ots instance run ghcr.io/org/image:v1.0 -p 7143  # explicit image ref
    """
    if port is None:
        raise SystemExit("--port / -p is required. Example: ots instance run -p 7143")

    cfg = Config()

    # Apply image reference overrides (positional ref > --tag flag > env/config)
    ref_image, ref_tag = parse_image_reference(reference) if reference else (None, None)
    override_tag = ref_tag or tag
    if ref_image or override_tag:
        cfg = dataclasses.replace(
            cfg,
            image=ref_image or cfg.image,
            tag=override_tag or cfg.tag,
            _image_explicit=bool(ref_image) or cfg._image_explicit,
        )

    ex = cfg.get_executor(host=context.host_var.get(None))

    # Resolve image/tag (handles @current/@rollback aliases)
    full_image = cfg.resolved_image_with_tag(executor=ex)

    # Container name
    container_name = name or f"onetimesecret-{port}"

    # Build podman run command
    cmd = ["podman", "run"]

    if detach:
        cmd.append("-d")
    if rm:
        cmd.append("--rm")

    cmd.extend(["--name", container_name])
    cmd.extend(["-p", f"{port}:{port}"])
    cmd.extend(["-e", f"PORT={port}"])

    # Check for .env file in current directory (local only)
    from pathlib import Path

    from ots_shared.ssh import LocalExecutor as _LECheck

    if isinstance(ex, _LECheck):
        local_env = Path.cwd() / ".env"
        if local_env.exists():
            cmd.extend(["--env-file", str(local_env)])

    # Production mode: add env file, secrets, and volumes
    if production:
        from ots_shared.ssh import LocalExecutor

        from rots.environment_file import get_secrets_from_env_file

        env_file = quadlet.DEFAULT_ENV_FILE

        # Environment file
        if not isinstance(ex, LocalExecutor):
            env_exists = ex.run(["test", "-f", str(env_file)]).ok
        else:
            env_exists = env_file.exists()
        if env_exists:
            cmd.extend(["--env-file", str(env_file)])

            # Secrets
            secret_specs = get_secrets_from_env_file(env_file, executor=ex)
            for spec in secret_specs:
                cmd.extend(
                    [
                        "--secret",
                        f"{spec.secret_name},type=env,target={spec.env_var_name}",
                    ]
                )

        # Config overrides (per-file)
        config_files = cfg.get_existing_config_files(executor=ex)
        for f in config_files:
            cmd.extend(["-v", f"{f}:/app/etc/{f.name}:ro"])
        cmd.extend(["-v", "static_assets:/app/public:ro"])

    # Auth file for private registry (outside production block — needed for pull)
    cmd.extend(cfg.podman_auth_args(executor=ex))

    # Image
    cmd.append(full_image)

    apply_quiet(quiet)
    logger.info(format_command(cmd))
    logger.info("")

    # Run it
    try:
        if detach:
            result = ex.run(cmd, check=True)
            print(f"Container started: {result.stdout.strip()[:12]}")
        else:
            # Foreground - stream output to terminal in real time
            flush_output()
            rc = ex.run_stream(cmd)
            if rc != 0:
                raise SystemExit(rc)
    except KeyboardInterrupt:
        logger.info("Stopped")


@app.command
def deploy(
    reference: ImageRef = None,
    instance_type: TypeSelector = None,
    web: WebFlag = None,
    worker: WorkerFlag = None,
    scheduler: SchedulerFlag = None,
    tag: TagFlag = None,
    delay: Delay = 5,
    dry_run: DryRun = False,
    quiet: Quiet = False,
    json_output: JsonOutput = False,
    force: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--force", "-f"],
            help=(
                "Allow deployment even when env file or Podman secrets are missing. "
                "The application will likely fail at runtime without required secrets."
            ),
        ),
    ] = False,
    wait_timeout: Annotated[
        int,
        cyclopts.Parameter(
            name=["--wait-timeout", "-w"],
            help=(
                "Seconds to wait for the unit to become active after start. "
                "0 disables the health wait (default: 0). "
                "Records success=False in deployment history if unit fails to become active."
            ),
        ),
    ] = 0,
    wait: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--wait"],
            help=(
                "Block until the HTTP health endpoint returns 200 (web instances only). "
                "Polls http://localhost:{port}/health for up to 60s (or --wait-timeout). "
                "Records success=False in deployment history if health check times out."
            ),
        ),
    ] = False,
    pre_hook: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--pre-hook"],
            help=(
                "Shell command to run before deployment. "
                "Aborts deploy if the command exits non-zero. "
                "Example: --pre-hook './scripts/scan.sh'"
            ),
        ),
    ] = None,
    post_hook: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--post-hook"],
            help=(
                "Shell command to run after successful deployment. "
                "Example: --post-hook './scripts/notify.sh'"
            ),
        ),
    ] = None,
):
    """Deploy new instance(s) using quadlet and Podman secrets.

    Writes quadlet config and starts systemd service.
    Requires /etc/default/onetimesecret and Podman secrets to be configured.
    Records deployment to timeline for audit and rollback support.

    Examples:
        ots instances deploy --web 7043,7044        # Deploy web on ports
        ots instances deploy --worker 1,2           # Deploy workers 1, 2
        ots instances deploy --worker billing       # Deploy 'billing' worker
        ots instances deploy --scheduler main       # Deploy scheduler
        ots instances deploy --web 7043 --force     # Skip secrets check (not recommended)
        ots instances deploy --web 7043 --wait-timeout 60  # Wait up to 60s for systemd active
        ots instances deploy --web 7043 --wait      # Wait up to 60s for HTTP health check
        ots instances deploy --web 7043 --pre-hook './scan.sh'   # Validate before deploy
        ots instances deploy --web 7043 --post-hook './notify.sh'  # Notify after deploy
        ots instances deploy ghcr.io/org/image:v1.0 --web 7043  # Explicit image reference
        ots instances deploy --tag v0.24.0 --web 7043  # Specific tag only
    """
    import datetime
    import json as json_mod

    itype, identifiers = resolve_instance_type(instance_type, web, worker, scheduler)

    # Disambiguate positional args: a bare value like "7043" is an identifier,
    # not an image reference.  Image references contain "/" or ":" or "@".
    if reference and not any(c in reference for c in "/:@"):
        identifiers = (reference, *identifiers)
        reference = None

    # Deploy requires identifiers AND type
    if not identifiers:
        raise SystemExit(
            "Identifiers required for deploy. Example: ots instances deploy --web 7043"
        )
    if itype is None:
        raise SystemExit("Instance type required for deploy. Use --web, --worker, or --scheduler.")

    cfg = Config()

    # Apply image reference overrides (positional ref > --tag flag > env/config)
    ref_image, ref_tag = parse_image_reference(reference) if reference else (None, None)
    override_tag = ref_tag or tag
    if ref_image or override_tag:
        cfg = dataclasses.replace(
            cfg,
            image=ref_image or cfg.image,
            tag=override_tag or cfg.tag,
            _image_explicit=bool(ref_image) or cfg._image_explicit,
        )

    ex = cfg.get_executor(host=context.host_var.get(None))

    # Resolve image/tag (handles @current/@rollback aliases)
    image, resolved_tag = cfg.resolve_image_tag(executor=ex)
    apply_quiet(quiet)
    if not json_output:
        logger.info(f"Image: {join_image_tag(image, resolved_tag)}")
        if cfg.registry:
            logger.info(f"Registry: {cfg.registry}")
        config_files = cfg.get_existing_config_files(executor=ex)
        if config_files:
            mounted = [f.name for f in config_files]
            logger.info(f"Config overrides: {', '.join(mounted)}")
        else:
            logger.info("Config: using container built-in defaults")

    if dry_run:
        # Render the quadlet template and diff vs existing file (if any).
        # force=True here: dry-run is a preview; secrets may not be fully
        # configured yet and we don't want that to block the diff output.
        if itype == InstanceType.WEB:
            template_path = cfg.web_template_path
            new_content = quadlet.render_web_template(cfg, force=True, executor=ex)
        elif itype == InstanceType.WORKER:
            template_path = cfg.worker_template_path
            new_content = quadlet.render_worker_template(cfg, force=True, executor=ex)
        else:
            template_path = cfg.scheduler_template_path
            new_content = quadlet.render_scheduler_template(cfg, force=True, executor=ex)

        from ots_shared.ssh import LocalExecutor as _LE

        if ex is not None and not isinstance(ex, _LE):
            _r = ex.run(["cat", str(template_path)])
            old_content = _r.stdout if _r.ok else ""
        else:
            old_content = template_path.read_text() if template_path.exists() else ""
        diff_lines = list(
            difflib.unified_diff(
                old_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{template_path.name}",
                tofile=f"b/{template_path.name}",
            )
        )

        result = {
            "action": "deploy",
            "dry_run": True,
            "instance_type": itype.value,
            "identifiers": list(identifiers),
            "image": image,
            "tag": resolved_tag,
            "quadlet_path": str(template_path),
            "quadlet_changed": bool(diff_lines),
        }
        if json_output:
            result["quadlet_diff"] = "".join(diff_lines)
            print(json_mod.dumps(result, indent=2))
        else:
            logger.info(f"[dry-run] Would deploy {itype.value}: {', '.join(identifiers)}")
            logger.info(f"Quadlet: {template_path}")
            if diff_lines:
                logger.info("--- quadlet diff ---")
                print("".join(diff_lines), end="")
            elif old_content:
                logger.info("(quadlet unchanged)")
            else:
                logger.info("--- new quadlet ---")
                print(new_content, end="")
        return

    # Execute pre-deploy hook (aborts if it exits non-zero)
    if pre_hook and not dry_run:
        run_hook(pre_hook, "pre-hook", quiet=quiet or json_output, executor=ex)

    deploy_results: list[dict] = []

    with deploy_lock(executor=ex):
        # Write appropriate quadlet template.
        # Raises SystemExit(1) if env file or secrets are missing (unless force=True).
        if itype == InstanceType.WEB:
            assets.update(cfg, create_volume=True, executor=ex)
            logger.info(f"Writing quadlet files to {cfg.web_template_path.parent}")
            quadlet.write_web_template(cfg, force=force, executor=ex)
        elif itype == InstanceType.WORKER:
            logger.info(f"Writing quadlet files to {cfg.worker_template_path.parent}")
            quadlet.write_worker_template(cfg, force=force, executor=ex)
        elif itype == InstanceType.SCHEDULER:
            logger.info(f"Writing quadlet files to {cfg.scheduler_template_path.parent}")
            quadlet.write_scheduler_template(cfg, force=force, executor=ex)

        def do_deploy(inst_type: InstanceType, id_: str) -> None:
            unit = systemd.unit_name(inst_type.value, id_)
            port = int(id_) if inst_type == InstanceType.WEB else 0
            base_notes = None if inst_type == InstanceType.WEB else f"{inst_type.value}_id={id_}"
            try:
                systemd.start(unit, executor=ex)
                # Optionally wait for the unit to become active (systemd state)
                if wait_timeout > 0:
                    if not json_output:
                        logger.info(
                            f"  Waiting up to {wait_timeout}s for {unit} to become active..."
                        )
                    systemd.wait_for_healthy(unit, timeout=wait_timeout, executor=ex)
                # Optionally wait for HTTP health check (web instances only)
                if wait and inst_type == InstanceType.WEB:
                    if not json_output:
                        timeout_s = wait_timeout or 60
                        url = f"http://localhost:{port}/health"
                        logger.info(f"  Waiting up to {timeout_s}s for {url} ...")
                    systemd.wait_for_http_healthy(port, timeout=wait_timeout or 60, executor=ex)
                # Record successful deployment
                db.record_deployment(
                    cfg.get_db_path(ex),
                    image=image,
                    tag=resolved_tag,
                    action=f"deploy-{inst_type.value}",
                    port=port,
                    success=True,
                    notes=base_notes,
                    executor=ex,
                )
                deploy_results.append(
                    {
                        "unit": unit,
                        "instance_type": inst_type.value,
                        "identifier": id_,
                        "success": True,
                        "image": image,
                        "tag": resolved_tag,
                        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                    }
                )
            except (systemd.HealthCheckTimeoutError, systemd.HttpHealthCheckTimeoutError) as e:
                fail_notes = (
                    f"health-timeout: {e}"
                    if inst_type == InstanceType.WEB
                    else f"{inst_type.value}_id={id_}; health-timeout: {e}"
                )
                db.record_deployment(
                    cfg.get_db_path(ex),
                    image=image,
                    tag=resolved_tag,
                    action=f"deploy-{inst_type.value}",
                    port=port,
                    success=False,
                    notes=fail_notes,
                    executor=ex,
                )
                deploy_results.append(
                    {
                        "unit": unit,
                        "instance_type": inst_type.value,
                        "identifier": id_,
                        "success": False,
                        "error": str(e),
                        "image": image,
                        "tag": resolved_tag,
                        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                    }
                )
                # Log the error but do not abort — allow remaining instances to proceed.
                # The caller will use EXIT_PARTIAL if some succeeded and some failed.
                logger.error(f"  {e}")
            except Exception as e:
                fail_notes = (
                    str(e)
                    if inst_type == InstanceType.WEB
                    else f"{inst_type.value}_id={id_}; error={e}"
                )
                db.record_deployment(
                    cfg.get_db_path(ex),
                    image=image,
                    tag=resolved_tag,
                    action=f"deploy-{inst_type.value}",
                    port=port,
                    success=False,
                    notes=fail_notes,
                    executor=ex,
                )
                deploy_results.append(
                    {
                        "unit": unit,
                        "instance_type": inst_type.value,
                        "identifier": id_,
                        "success": False,
                        "error": str(e),
                        "image": image,
                        "tag": resolved_tag,
                        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                    }
                )
                raise

        instances = {itype: list(identifiers)}
        for_each_instance(instances, delay, do_deploy, "Deploying", show_logs_hint=not json_output)

    # Execute post-deploy hook (runs only when all instances deployed successfully)
    if post_hook and all(r["success"] for r in deploy_results):
        run_hook(post_hook, "post-hook", quiet=quiet or json_output, executor=ex)

    all_ok = all(r["success"] for r in deploy_results)
    any_ok = any(r["success"] for r in deploy_results)

    if json_output:
        print(
            json_mod.dumps(
                {
                    "action": "deploy",
                    "success": all_ok,
                    "instances": deploy_results,
                },
                indent=2,
            )
        )

    if deploy_results and not all_ok:
        raise SystemExit(EXIT_PARTIAL if any_ok else EXIT_FAILURE)


@app.command
def redeploy(
    reference: ImageRef = None,
    instance_type: TypeSelector = None,
    web: WebFlag = None,
    worker: WorkerFlag = None,
    scheduler: SchedulerFlag = None,
    tag: TagFlag = None,
    delay: Delay = 30,
    force: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--force", "-f"],
            help="Teardown and recreate (stops, redeploys)",
        ),
    ] = False,
    dry_run: DryRun = False,
    quiet: Quiet = False,
    json_output: JsonOutput = False,
    wait_timeout: Annotated[
        int,
        cyclopts.Parameter(
            name=["--wait-timeout", "-w"],
            help=(
                "Seconds to wait for the unit to become active after restart. "
                "0 disables the health wait (default: 0). "
                "Records success=False in deployment history if unit fails to become active."
            ),
        ),
    ] = 0,
    wait: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--wait"],
            help=(
                "Block until the HTTP health endpoint returns 200 (web instances only). "
                "Polls http://localhost:{port}/health for up to 60s (or --wait-timeout). "
                "Records success=False in deployment history if health check times out."
            ),
        ),
    ] = False,
    pre_hook: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--pre-hook"],
            help=(
                "Shell command to run before redeployment. "
                "Aborts redeploy if the command exits non-zero. "
                "Example: --pre-hook './scripts/scan.sh'"
            ),
        ),
    ] = None,
    post_hook: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--post-hook"],
            help=(
                "Shell command to run after successful redeployment. "
                "Example: --post-hook './scripts/notify.sh'"
            ),
        ),
    ] = None,
):
    """Regenerate quadlet and restart containers.

    Use after editing config.yaml or /etc/default/onetimesecret.
    Use --force to fully teardown and recreate.
    Records deployment to timeline for audit and rollback support.

    Note: Always recreates containers (stop+start) to ensure quadlet changes
    (volume mounts, image, etc.) are applied.

    Examples:
        ots instances redeploy                      # Redeploy all running
        ots instances redeploy --web                # Redeploy web instances
        ots instances redeploy --web 7043,7044      # Redeploy specific web
        ots instances redeploy --scheduler main     # Redeploy specific scheduler
        ots instances redeploy --force              # Force teardown+recreate
        ots instances redeploy --wait-timeout 60    # Wait up to 60s for systemd active
        ots instances redeploy --web 7043 --wait    # Wait up to 60s for HTTP health check
        ots instances redeploy --pre-hook './scan.sh'    # Validate before redeploy
        ots instances redeploy --post-hook './notify.sh'  # Notify after redeploy
        ots instances redeploy ghcr.io/org/image:v1.0    # Explicit image reference
        ots instances redeploy --tag v0.24.0             # Specific tag only
    """
    import datetime
    import json as json_mod

    itype, identifiers = resolve_instance_type(instance_type, web, worker, scheduler)

    # Disambiguate positional args (same as deploy)
    if reference and not any(c in reference for c in "/:@"):
        identifiers = (reference, *identifiers)
        reference = None

    cfg = Config()

    # Apply image reference overrides (positional ref > --tag flag > env/config)
    ref_image, ref_tag = parse_image_reference(reference) if reference else (None, None)
    override_tag = ref_tag or tag
    if ref_image or override_tag:
        cfg = dataclasses.replace(
            cfg,
            image=ref_image or cfg.image,
            tag=override_tag or cfg.tag,
            _image_explicit=bool(ref_image) or cfg._image_explicit,
        )

    ex = cfg.get_executor(host=context.host_var.get(None))
    instances = resolve_identifiers(identifiers, itype, running_only=True, executor=ex)

    apply_quiet(quiet)

    if not instances:
        if json_output:
            print(json_mod.dumps({"action": "redeploy", "success": True, "instances": []}))
        else:
            logger.info("No running instances found")
            logger.info("Start existing instances with: ots instances start")
            logger.info("Or deploy new ones with:       ots instances deploy --help")
        return

    # Resolve image/tag (handles CURRENT/ROLLBACK aliases)
    image, tag = cfg.resolve_image_tag(executor=ex)
    if not json_output:
        logger.info(f"Image: {join_image_tag(image, tag)}")
        if cfg.registry:
            logger.info(f"Registry: {cfg.registry}")
        config_files = cfg.get_existing_config_files(executor=ex)
        if config_files:
            mounted = [f.name for f in config_files]
            logger.info(f"Config overrides: {', '.join(mounted)}")
        else:
            logger.info("Config: using container built-in defaults")

    if dry_run:
        verb = "force redeploy" if force else "redeploy"
        dry_items = [{"instance_type": t.value, "identifiers": ids} for t, ids in instances.items()]

        # Collect quadlet diffs for each type being redeployed.
        # force=True here: dry-run is a preview; don't block on missing secrets.
        quadlet_diffs: dict[str, str] = {}
        for inst_type in instances:
            if inst_type == InstanceType.WEB:
                template_path = cfg.web_template_path
                new_content = quadlet.render_web_template(cfg, force=True, executor=ex)
            elif inst_type == InstanceType.WORKER:
                template_path = cfg.worker_template_path
                new_content = quadlet.render_worker_template(cfg, force=True, executor=ex)
            else:
                template_path = cfg.scheduler_template_path
                new_content = quadlet.render_scheduler_template(cfg, force=True, executor=ex)

            from ots_shared.ssh import LocalExecutor as _LE

            if ex is not None and not isinstance(ex, _LE):
                _r = ex.run(["cat", str(template_path)])
                old_content = _r.stdout if _r.ok else ""
            else:
                old_content = template_path.read_text() if template_path.exists() else ""
            diff_lines = list(
                difflib.unified_diff(
                    old_content.splitlines(keepends=True),
                    new_content.splitlines(keepends=True),
                    fromfile=f"a/{template_path.name}",
                    tofile=f"b/{template_path.name}",
                )
            )
            quadlet_diffs[inst_type.value] = "".join(diff_lines)

        if json_output:
            print(
                json_mod.dumps(
                    {
                        "action": "redeploy",
                        "dry_run": True,
                        "image": image,
                        "tag": tag,
                        "instances": dry_items,
                        "quadlet_diffs": quadlet_diffs,
                    },
                    indent=2,
                )
            )
        else:
            for inst_type, ids in instances.items():
                logger.info(f"[dry-run] Would {verb} {inst_type.value}: {', '.join(ids)}")
            for inst_type_val, diff_text in quadlet_diffs.items():
                if inst_type_val == InstanceType.WEB.value:
                    tpath = cfg.web_template_path
                elif inst_type_val == InstanceType.WORKER.value:
                    tpath = cfg.worker_template_path
                else:
                    tpath = cfg.scheduler_template_path
                logger.info(f"Quadlet ({inst_type_val}): {tpath}")
                if diff_text:
                    logger.info("--- quadlet diff ---")
                    print(diff_text, end="")
                else:
                    logger.info("(quadlet unchanged)")
        return

    # Execute pre-redeploy hook (aborts if it exits non-zero)
    if pre_hook and not dry_run:
        run_hook(pre_hook, "pre-hook", quiet=quiet or json_output, executor=ex)

    redeploy_results: list[dict] = []

    with deploy_lock(executor=ex):
        # Write quadlet templates for each type being redeployed.
        # Raises SystemExit(1) if env file or secrets are missing.
        # Redeploy always enforces secrets check (no --force override for secrets here).
        if InstanceType.WEB in instances:
            assets.update(cfg, create_volume=force, executor=ex)
            logger.info(f"Writing quadlet files to {cfg.web_template_path.parent}")
            quadlet.write_web_template(cfg, executor=ex)
        if InstanceType.WORKER in instances:
            logger.info(f"Writing quadlet files to {cfg.worker_template_path.parent}")
            quadlet.write_worker_template(cfg, executor=ex)
        if InstanceType.SCHEDULER in instances:
            logger.info(f"Writing quadlet files to {cfg.scheduler_template_path.parent}")
            quadlet.write_scheduler_template(cfg, executor=ex)

        def do_redeploy(inst_type: InstanceType, id_: str) -> None:
            unit = systemd.unit_name(inst_type.value, id_)
            port = int(id_) if inst_type == InstanceType.WEB else 0
            base_notes = (
                ("force" if force else None)
                if inst_type == InstanceType.WEB
                else f"{inst_type.value}_id={id_}" + (", force" if force else "")
            )

            if force:
                if not json_output:
                    logger.info(f"Stopping {unit}")
                systemd.stop(unit, executor=ex)

            try:
                if force or not systemd.container_exists(unit, executor=ex):
                    if not json_output:
                        logger.info(f"Starting {unit}")
                    systemd.start(unit, executor=ex)
                else:
                    if not json_output:
                        logger.info(f"Recreating {unit}")
                    systemd.recreate(unit, executor=ex)

                # Optionally wait for the unit to become active (systemd state)
                if wait_timeout > 0:
                    if not json_output:
                        logger.info(
                            f"  Waiting up to {wait_timeout}s for {unit} to become active..."
                        )
                    systemd.wait_for_healthy(unit, timeout=wait_timeout, executor=ex)
                # Optionally wait for HTTP health check (web instances only)
                if wait and inst_type == InstanceType.WEB:
                    if not json_output:
                        timeout_s = wait_timeout or 60
                        url = f"http://localhost:{port}/health"
                        logger.info(f"  Waiting up to {timeout_s}s for {url} ...")
                    systemd.wait_for_http_healthy(port, timeout=wait_timeout or 60, executor=ex)

                db.record_deployment(
                    cfg.get_db_path(ex),
                    image=image,
                    tag=tag,
                    action=f"redeploy-{inst_type.value}",
                    port=port,
                    success=True,
                    notes=base_notes,
                    executor=ex,
                )
                redeploy_results.append(
                    {
                        "unit": unit,
                        "instance_type": inst_type.value,
                        "identifier": id_,
                        "success": True,
                        "image": image,
                        "tag": tag,
                        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                    }
                )
            except (systemd.HealthCheckTimeoutError, systemd.HttpHealthCheckTimeoutError) as e:
                fail_notes = (
                    f"health-timeout: {e}"
                    if inst_type == InstanceType.WEB
                    else f"{inst_type.value}_id={id_}; health-timeout: {e}"
                )
                db.record_deployment(
                    cfg.get_db_path(ex),
                    image=image,
                    tag=tag,
                    action=f"redeploy-{inst_type.value}",
                    port=port,
                    success=False,
                    notes=fail_notes,
                    executor=ex,
                )
                redeploy_results.append(
                    {
                        "unit": unit,
                        "instance_type": inst_type.value,
                        "identifier": id_,
                        "success": False,
                        "error": str(e),
                        "image": image,
                        "tag": tag,
                        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                    }
                )
                # Log the error but do not abort — allow remaining instances to proceed.
                # The caller will use EXIT_PARTIAL if some succeeded and some failed.
                logger.error(f"  {e}")
            except Exception as e:
                fail_notes = (
                    str(e)
                    if inst_type == InstanceType.WEB
                    else f"{inst_type.value}_id={id_}; error={e}"
                )
                db.record_deployment(
                    cfg.get_db_path(ex),
                    image=image,
                    tag=tag,
                    action=f"redeploy-{inst_type.value}",
                    port=port,
                    success=False,
                    notes=fail_notes,
                    executor=ex,
                )
                redeploy_results.append(
                    {
                        "unit": unit,
                        "instance_type": inst_type.value,
                        "identifier": id_,
                        "success": False,
                        "error": str(e),
                        "image": image,
                        "tag": tag,
                        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                    }
                )
                raise

        verb = "Force redeploying" if force else "Redeploying"
        for_each_instance(instances, delay, do_redeploy, verb, show_logs_hint=not json_output)

    # Execute post-redeploy hook (runs only when all instances redeployed successfully)
    if post_hook and all(r["success"] for r in redeploy_results):
        run_hook(post_hook, "post-hook", quiet=quiet or json_output, executor=ex)

    all_ok = all(r["success"] for r in redeploy_results)
    any_ok = any(r["success"] for r in redeploy_results)

    if json_output:
        print(
            json_mod.dumps(
                {
                    "action": "redeploy",
                    "success": all_ok,
                    "instances": redeploy_results,
                },
                indent=2,
            )
        )

    if redeploy_results and not all_ok:
        raise SystemExit(EXIT_PARTIAL if any_ok else EXIT_FAILURE)


@app.command
def undeploy(
    instance_type: TypeSelector = None,
    web: WebFlag = None,
    worker: WorkerFlag = None,
    scheduler: SchedulerFlag = None,
    delay: Delay = 5,
    dry_run: DryRun = False,
    yes: Yes = False,
    json_output: JsonOutput = False,
):
    """Stop systemd service for instance(s).

    Stops systemd service. Records action to timeline for audit.

    Note: Podman volumes (static_assets) are NOT removed by undeploy.
    To reclaim disk space after all instances are stopped, run:
        ots instances cleanup

    Examples:
        ots instances undeploy                      # Undeploy all running
        ots instances undeploy --web                # Undeploy web instances
        ots instances undeploy --web 7043,7044      # Undeploy specific web
        ots instances undeploy --scheduler main     # Undeploy specific scheduler
        ots instances undeploy -y                   # Skip confirmation
        ots instances undeploy --json               # JSON output
    """
    import datetime
    import json as json_mod

    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    itype, identifiers = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=True, executor=ex)

    if not instances:
        if json_output:
            print(json_mod.dumps({"action": "undeploy", "success": True, "instances": []}))
        else:
            logger.info("No running instances found")
            logger.info("List all configured instances with: ots instances list")
        return

    # --json implies --yes (non-interactive)
    if not yes and not dry_run and not json_output:
        items = []
        for inst_type, ids in instances.items():
            items.append(f"{inst_type.value}: {', '.join(ids)}")
        print(f"This will stop instances: {'; '.join(items)}")
        response = input("Continue? [y/N] ")
        if response.lower() not in ("y", "yes"):
            print("Aborted")
            return

    if dry_run:
        dry_items = [{"instance_type": t.value, "identifiers": ids} for t, ids in instances.items()]
        if json_output:
            print(
                json_mod.dumps(
                    {
                        "action": "undeploy",
                        "dry_run": True,
                        "instances": dry_items,
                    },
                    indent=2,
                )
            )
        else:
            for inst_type, ids in instances.items():
                logger.info(f"[dry-run] Would undeploy {inst_type.value}: {', '.join(ids)}")
        return

    image, tag = cfg.resolve_image_tag(executor=ex)
    undeploy_results: list[dict] = []

    def do_undeploy(inst_type: InstanceType, id_: str) -> None:
        unit = systemd.unit_name(inst_type.value, id_)
        try:
            systemd.stop(unit, executor=ex)
            # Prevent auto-start on reboot — disable is idempotent (no-op if not enabled)
            systemd.disable(unit, executor=ex)
            # Clear failed state so unit doesn't appear in discovery
            systemd.reset_failed(unit, executor=ex)
            port = int(id_) if inst_type == InstanceType.WEB else 0
            db.record_deployment(
                cfg.get_db_path(ex),
                image=image,
                tag=tag,
                action=f"undeploy-{inst_type.value}",
                port=port,
                success=True,
                notes=None if inst_type == InstanceType.WEB else f"{inst_type.value}_id={id_}",
                executor=ex,
            )
            undeploy_results.append(
                {
                    "unit": unit,
                    "instance_type": inst_type.value,
                    "identifier": id_,
                    "success": True,
                    "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                }
            )
        except Exception as e:
            port = int(id_) if inst_type == InstanceType.WEB else 0
            fail_notes = (
                str(e)
                if inst_type == InstanceType.WEB
                else f"{inst_type.value}_id={id_}; error={e}"
            )
            db.record_deployment(
                cfg.get_db_path(ex),
                image=image,
                tag=tag,
                action=f"undeploy-{inst_type.value}",
                port=port,
                success=False,
                notes=fail_notes,
                executor=ex,
            )
            undeploy_results.append(
                {
                    "unit": unit,
                    "instance_type": inst_type.value,
                    "identifier": id_,
                    "success": False,
                    "error": str(e),
                    "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                }
            )
            raise

    for_each_instance(instances, delay, do_undeploy, "Undeploying")

    if json_output:
        print(
            json_mod.dumps(
                {
                    "action": "undeploy",
                    "success": all(r["success"] for r in undeploy_results),
                    "instances": undeploy_results,
                },
                indent=2,
            )
        )


@app.command
def start(
    instance_type: TypeSelector = None,
    web: WebFlag = None,
    worker: WorkerFlag = None,
    scheduler: SchedulerFlag = None,
):
    """Start systemd unit(s) for instance(s).

    Does NOT regenerate quadlet - use 'redeploy' for that.

    Examples:
        ots instances start                         # Start all configured
        ots instances start --web                   # Start web instances
        ots instances start --web 7043,7044         # Start specific web
        ots instances start --scheduler main        # Start specific scheduler
    """
    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    itype, identifiers = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=False, executor=ex)

    if not instances:
        logger.info("No configured instances found")
        logger.info("Deploy one first: ots instances deploy --help")
        return

    for inst_type, ids in instances.items():
        for id_ in ids:
            unit = systemd.unit_name(inst_type.value, id_)
            systemd.start(unit, executor=ex)
            logger.info(f"Started {unit}")

    hint = format_journalctl_hint(instances)
    if hint:
        logger.info(f"\nView logs: {hint}")


@app.command
def stop(
    instance_type: TypeSelector = None,
    web: WebFlag = None,
    worker: WorkerFlag = None,
    scheduler: SchedulerFlag = None,
):
    """Stop systemd unit(s) for instance(s).

    Does NOT affect quadlet config.
    Only stops running instances; already-stopped instances are skipped.

    Examples:
        ots instances stop                          # Stop all running
        ots instances stop --web                    # Stop web instances
        ots instances stop --web 7043,7044          # Stop specific web
        ots instances stop --scheduler              # Stop scheduler instances
    """
    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    itype, identifiers = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=True, executor=ex)

    if not instances:
        logger.info("No running instances found")
        logger.info("List all configured instances with: ots instances list")
        return

    for inst_type, ids in instances.items():
        for id_ in ids:
            unit = systemd.unit_name(inst_type.value, id_)
            systemd.stop(unit, executor=ex)
            logger.info(f"Stopped {unit}")


@app.command
def restart(
    instance_type: TypeSelector = None,
    web: WebFlag = None,
    worker: WorkerFlag = None,
    scheduler: SchedulerFlag = None,
    delay: Delay = 30,
):
    """Restart systemd unit(s) for instance(s).

    Does NOT regenerate quadlet - use 'redeploy' for that.
    Only restarts running instances; stopped instances are skipped.
    Waits between instances to allow startup before Caddy health checks.

    Examples:
        ots instances restart                       # Restart all running
        ots instances restart --web                 # Restart web instances
        ots instances restart --web 7043,7044       # Restart specific web
        ots instances restart --scheduler main      # Restart specific scheduler
        ots instances restart --delay 10            # Longer wait between restarts
    """
    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    itype, identifiers = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=True, executor=ex)

    if not instances:
        logger.info("No running instances found")
        logger.info("Start existing instances with: ots instances start")
        return

    def do_restart(inst_type: InstanceType, id_: str) -> None:
        unit = systemd.unit_name(inst_type.value, id_)
        systemd.restart(unit, executor=ex)

    for_each_instance(instances, delay, do_restart, "Restarting", show_logs_hint=True)


@app.command
def enable(
    instance_type: TypeSelector = None,
    web: WebFlag = None,
    worker: WorkerFlag = None,
    scheduler: SchedulerFlag = None,
):
    """Enable instance(s) to start at boot.

    Does not start the instance - use 'start' for that.

    Examples:
        ots instances enable                        # Enable all configured
        ots instances enable --web                  # Enable web instances
        ots instances enable --web 7043,7044        # Enable specific web
        ots instances enable --scheduler main       # Enable specific scheduler
    """
    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    itype, identifiers = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=False, executor=ex)

    if not instances:
        logger.info("No configured instances found")
        logger.info("Deploy one first: ots instances deploy --help")
        return

    for inst_type, ids in instances.items():
        for id_ in ids:
            unit = systemd.unit_name(inst_type.value, id_)
            try:
                systemd.enable(unit, executor=ex)
                logger.info(f"Enabled {unit}")
            except systemd.SystemctlError as e:
                logger.error(f"Failed to enable {unit}: {e.journal}")


@app.command
def disable(
    instance_type: TypeSelector = None,
    web: WebFlag = None,
    worker: WorkerFlag = None,
    scheduler: SchedulerFlag = None,
    yes: Yes = False,
):
    """Disable instance(s) from starting at boot.

    Does not stop the instance - use 'stop' for that.

    Examples:
        ots instances disable                       # Disable all configured
        ots instances disable --web                 # Disable web instances
        ots instances disable --web 7043,7044 -y    # Disable specific web
        ots instances disable --scheduler main -y   # Disable specific scheduler
    """
    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    itype, identifiers = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=False, executor=ex)

    if not instances:
        logger.info("No configured instances found")
        logger.info("List all configured instances with: ots instances list")
        return

    if not yes:
        items = []
        for inst_type, ids in instances.items():
            items.append(f"{inst_type.value}: {', '.join(ids)}")
        print(f"This will disable boot startup for: {'; '.join(items)}")
        response = input("Continue? [y/N] ")
        if response.lower() not in ("y", "yes"):
            print("Aborted")
            return

    for inst_type, ids in instances.items():
        for id_ in ids:
            unit = systemd.unit_name(inst_type.value, id_)
            try:
                systemd.disable(unit, executor=ex)
                logger.info(f"Disabled {unit}")
            except systemd.SystemctlError as e:
                logger.error(f"Failed to disable {unit}: {e.journal}")


@app.command
def status(
    instance_type: TypeSelector = None,
    web: WebFlag = None,
    worker: WorkerFlag = None,
    scheduler: SchedulerFlag = None,
    json_output: JsonOutput = False,
):
    """Show systemd status for instance(s).

    Examples:
        ots instances status                        # Status of all configured
        ots instances status --web                  # Status of web instances
        ots instances status --web 7043,7044        # Status of specific web
        ots instances status --scheduler            # Status of scheduler instances
        ots instances status --json                 # JSON output
    """
    import json as json_mod

    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    itype, identifiers = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=False, executor=ex)

    if not instances:
        if json_output:
            print(json_mod.dumps({"instances": []}))
        else:
            logger.info("No configured instances found")
            logger.info("Deploy one first: ots instances deploy --help")
        return

    if json_output:
        results = []
        for inst_type, ids in instances.items():
            for id_ in ids:
                unit = systemd.unit_name(inst_type.value, id_)
                active_state = systemd.is_active(unit, executor=ex)
                results.append(
                    {
                        "unit": unit,
                        "instance_type": inst_type.value,
                        "identifier": id_,
                        "active_state": active_state,
                        "active": active_state == "active",
                    }
                )
        print(json_mod.dumps({"instances": results}, indent=2))
    else:
        for inst_type, ids in instances.items():
            for id_ in ids:
                unit = systemd.unit_name(inst_type.value, id_)
                systemd.status(unit, executor=ex)
                print()


@app.command
def logs(
    instance_type: TypeSelector = None,
    web: WebFlag = None,
    worker: WorkerFlag = None,
    scheduler: SchedulerFlag = None,
    lines: Lines = 50,
    follow: Follow = False,
):
    """Show logs for instance(s).

    Examples:
        ots instances logs                          # Logs from all instances
        ots instances logs --web                    # Logs from web instances
        ots instances logs --web 7043 -f            # Follow specific web logs
        ots instances logs --scheduler main -f      # Follow scheduler logs
        ots instances logs -n 100                   # Last 100 lines
    """
    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    itype, identifiers = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=False, executor=ex)

    if not instances:
        logger.info("No instances found")
        return

    # Build list of units
    units = []
    for inst_type, ids in instances.items():
        for id_ in ids:
            units.append(systemd.unit_name(inst_type.value, id_))

    cmd = ["journalctl", "--no-pager", f"-n{lines}"]
    if follow:
        cmd.append("-f")
    for unit in units:
        cmd.extend(["-u", unit])

    # Route through executor for remote support.
    # Follow mode uses run_stream() for real-time output;
    # non-follow uses run() since output is bounded.
    from rots.systemd import _get_executor

    resolved_ex = _get_executor(ex)
    if follow:
        rc = resolved_ex.run_stream(cmd, sudo=True, timeout=300)
        if rc != 0:
            print(f"journalctl exited with code {rc}", file=sys.stderr)
    else:
        result = resolved_ex.run(cmd, sudo=True, timeout=30)
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)


@app.command(name="show-env")
def show_env():
    """Show infrastructure environment variables.

    Displays the contents of /etc/default/onetimesecret (shared by all instances).
    Only shows valid KEY=VALUE pairs, sorted alphabetically.

    When ``--host`` is set, reads the file from the remote host via the executor.

    Examples:
        ots instances show-env
    """
    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))

    env_path = "/etc/default/onetimesecret"
    print(f"=== {env_path} ===")

    from rots.systemd import _get_executor, _is_local

    resolved_ex = _get_executor(ex)

    if _is_local(resolved_ex):
        # Local: read file directly for efficiency
        from pathlib import Path

        env_file = Path(env_path)
        if not env_file.exists():
            logger.info("  (file not found)")
            print()
            return
        content = env_file.read_text()
    else:
        # Remote: read via executor
        result = resolved_ex.run(["cat", env_path], timeout=10)
        if not result.ok:
            logger.info("  (file not found)")
            print()
            return
        content = result.stdout

    # Parse only valid KEY=VALUE lines (key must be valid shell identifier)
    env_vars = {}
    for line in content.splitlines():
        line = line.strip()
        # Skip empty lines, comments, and shell commands
        if not line or line.startswith("#"):
            continue
        # Must contain = and start with a valid identifier char
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            # Valid env var: letter/underscore start, alnum/underscore chars
            if key and (key[0].isalpha() or key.startswith("_")):
                if all(c.isalnum() or c == "_" for c in key):
                    env_vars[key] = value
    for key in sorted(env_vars.keys()):
        print(f"{key}={env_vars[key]}")
    print()


@app.command(name="exec")
def exec_shell(
    instance_type: TypeSelector = None,
    web: WebFlag = None,
    worker: WorkerFlag = None,
    scheduler: SchedulerFlag = None,
    command: Annotated[
        str,
        cyclopts.Parameter(name=["--command", "-c"], help="Command to run (default: $SHELL)"),
    ] = "",
):
    """Run interactive shell in container(s).

    Uses $SHELL environment variable or /bin/sh as fallback.

    Examples:
        ots instances exec                          # Shell in all running
        ots instances exec --web 7043               # Shell in specific web
        ots instances exec --scheduler main         # Shell in scheduler
        ots instances exec -c "/bin/bash"           # Use specific shell
    """
    import os

    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    itype, identifiers = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=True, executor=ex)

    if not instances:
        logger.info("No running instances found")
        logger.info("Start existing instances with: ots instances start")
        return

    shell = command or os.environ.get("SHELL", "/bin/sh")

    for inst_type, ids in instances.items():
        for id_ in ids:
            # Use Quadlet container naming convention
            unit = systemd.unit_name(inst_type.value, id_)
            container = systemd.unit_to_container_name(unit)
            logger.info(f"=== Entering {unit} ===")
            flush_output()
            ex.run_interactive(["podman", "exec", "-it", container, shell])
            print()


@app.command
def shell(
    reference: ImageRef = None,
    persistent: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--persistent", "-p"],
            help="Named volume for persistent data (survives exit)",
        ),
    ] = None,
    volume: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--volume", "-v"],
            help="Host path to bind-mount at /app/data (rw, user-mapped)",
        ),
    ] = None,
    env: Annotated[
        tuple[str, ...],
        cyclopts.Parameter(
            name=["--env", "-e"],
            help="Set container env var (KEY=VALUE, repeatable)",
        ),
    ] = (),
    command: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--command", "-c"],
            help="Command to run (default: interactive bash)",
        ),
    ] = None,
    quiet: Quiet = False,
    tag: TagFlag = None,
):
    """Run ephemeral shell for migrations and maintenance.

    By default uses tmpfs at /app/data (data destroyed on exit).
    Use --persistent to create a named volume that survives exit.
    Use --volume to bind-mount a host directory at /app/data.
    Config is mounted read-only at /app/etc.

    Examples:
        ots instance shell                              # tmpfs, interactive bash
        ots instance shell --persistent upgrade-v024    # named volume survives exit
        ots instance shell -c "bin/ots migrate"         # run command and exit
        ots instance shell --tag v0.24.0                # specific image tag
        ots instance shell -v ./data                    # bind-mount ./data at /app/data
        ots instance shell -v ./data -e REDIS_URL=redis://10.0.0.5:6379/0  # with env
        ots instance shell -e FOO=bar -e BAZ=qux        # multiple env vars, tmpfs default
        ots instance shell ghcr.io/org/image:v0.24.0     # explicit image ref
    """
    from pathlib import Path

    apply_quiet(quiet)

    if persistent and volume:
        logger.error("--persistent and --volume are mutually exclusive")
        raise SystemExit(1)

    cfg = Config()

    # Apply image reference overrides (positional ref > --tag flag > env/config)
    ref_image, ref_tag = parse_image_reference(reference) if reference else (None, None)
    override_tag = ref_tag or tag
    if ref_image or override_tag:
        cfg = dataclasses.replace(
            cfg,
            image=ref_image or cfg.image,
            tag=override_tag or cfg.tag,
            _image_explicit=bool(ref_image) or cfg._image_explicit,
        )

    # Obtain executor early for remote env file checks
    from rots.systemd import _get_executor

    ex = _get_executor(cfg.get_executor(host=context.host_var.get(None)))

    # Check for unresolved sentinel tags before proceeding
    _canonical_image, resolved_tag = cfg.resolve_image_tag(executor=ex)
    if resolved_tag.startswith("@"):
        logger.error(f"tag '{resolved_tag}' is a sentinel (no deploy recorded).")
        logger.error("Use --tag to specify an image tag, e.g.: rots instance shell --tag v0.24.0")
        raise SystemExit(1)

    # Operational image:tag with registry prefix applied when OTS_REGISTRY is set
    full_image = cfg.resolved_image_with_tag(executor=ex)

    # Build podman run command
    cmd = ["podman", "run", "--rm"]

    # Interactive unless command provided
    if command is None:
        cmd.append("-it")

    cmd.append("--network=host")

    # Environment file and secrets
    env_file = quadlet.DEFAULT_ENV_FILE
    from ots_shared.ssh import LocalExecutor

    if not isinstance(ex, LocalExecutor):
        env_exists = ex.run(["test", "-f", str(env_file)]).ok
    else:
        env_exists = env_file.exists()
    if env_exists:
        cmd.extend(["--env-file", str(env_file)])
        cmd.extend(build_secret_args(env_file, executor=ex))

    # Data volume: bind-mount, persistent named volume, or tmpfs (default)
    if volume:
        if not isinstance(ex, LocalExecutor):
            # Remote: create directory on the remote host, use path as-is
            host_path = Path(volume)
            ex.run(["mkdir", "-p", str(host_path)])
        else:
            host_path = Path(volume).resolve()
            host_path.mkdir(parents=True, exist_ok=True)
        cmd.extend(["-v", f"{host_path}:/app/data:rw,U"])
    elif persistent:
        volume_name = f"ots-migration-{persistent}"
        cmd.extend(["-v", f"{volume_name}:/app/data"])
    else:
        cmd.extend(["--tmpfs", "/app/data"])

    # Ad-hoc environment variables
    for entry in env:
        cmd.extend(["-e", entry])

    # Config overrides (per-file, if any exist on host)
    for f in cfg.get_existing_config_files(executor=ex):
        resolved = f.resolve()  # symlink resolution for macOS podman VM
        cmd.extend(["-v", f"{resolved}:/app/etc/{f.name}:ro"])

    # Auth file for private registry
    cmd.extend(cfg.podman_auth_args(executor=ex))

    # Image
    cmd.append(full_image)

    # Command to run
    if command:
        cmd.extend(["/bin/bash", "-c", command])
    else:
        cmd.append("/bin/bash")

    logger.info(format_command(cmd))
    logger.info("")

    try:
        if command is None:
            # Interactive shell — full PTY
            flush_output()
            rc = ex.run_interactive(cmd)
        else:
            # Non-interactive command — stream output
            flush_output()
            rc = ex.run_stream(cmd)
        if rc != 0:
            logger.info(f"Shell exited with code {rc}")
            raise SystemExit(rc)
    except KeyboardInterrupt:
        logger.info("\nInterrupted")


@app.command(name="config-transform")
def config_transform(
    reference: ImageRef = None,
    command: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--command", "-c"],
            help="Migration command to run (e.g., 'bin/ots migrate 20250727_01')",
        ),
    ] = None,
    file: Annotated[
        str,
        cyclopts.Parameter(
            name=["--file", "-f"],
            help="Config file to transform (default: config.yaml)",
        ),
    ] = "config.yaml",
    apply: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--apply"],
            help="Apply changes (default: dry-run showing diff only)",
        ),
    ] = False,
    quiet: Quiet = False,
    tag: TagFlag = None,
):
    """Transform config files with backup/apply workflow.

    Runs a migration command in a container to transform config files.
    By default shows a unified diff without making changes (dry-run).
    Use --apply to backup the original and apply the transformation.

    Note: Config files (config.yaml, auth.yaml, logging.yaml) contain
    application settings, not secrets. Secrets are managed separately
    via podman secrets and env files. The diff output is the primary
    interface for reviewing proposed changes before applying them.

    The migration command should:
    - Read from /app/data/{file} (original config copied there)
    - Write to /app/data/{file}.new (transformed output)
    - Exit 0 on success, non-zero on failure

    Examples:
        # Dry run (default) - shows diff
        ots instance config-transform -c "bin/ots migrate 20250727_01"

        # Apply changes (creates backup, replaces original)
        ots instance config-transform -c "bin/ots migrate 20250727_01" --apply

        # Different config file
        ots instance config-transform -c "bin/ots migrate auth_fix" -f auth.yaml --apply

        # Explicit image reference
        ots instance config-transform ghcr.io/org/image:v0.25.0 -c "bin/ots migrate fix"
    """
    import difflib
    import time
    from pathlib import Path

    if command is None:
        raise SystemExit(
            "--command / -c is required. "
            "Example: ots instance config-transform -c 'bin/ots migrate fix'"
        )

    cfg = Config()

    # Apply image reference overrides (positional ref > --tag flag > env/config)
    ref_image, ref_tag = parse_image_reference(reference) if reference else (None, None)
    override_tag = ref_tag or tag
    if ref_image or override_tag:
        cfg = dataclasses.replace(
            cfg,
            image=ref_image or cfg.image,
            tag=override_tag or cfg.tag,
            _image_explicit=bool(ref_image) or cfg._image_explicit,
        )

    ex = cfg.get_executor(host=context.host_var.get(None))
    p = Podman(executor=ex)

    from ots_shared.ssh import LocalExecutor

    is_remote = not isinstance(ex, LocalExecutor)

    # Validate: prevent path traversal
    if ".." in file or file.startswith("/"):
        raise SystemExit(f"Invalid file path: {file!r} (no path traversal allowed)")

    # Check config file exists
    config_path = cfg.config_dir / file
    if is_remote:
        if not ex.run(["test", "-f", str(config_path)]).ok:
            raise SystemExit(f"Config file not found: {config_path}")
    elif not config_path.exists():
        raise SystemExit(f"Config file not found: {config_path}")

    # Resolve image/tag (handles @current/@rollback aliases, applies registry prefix)
    full_image = cfg.resolved_image_with_tag(executor=ex)

    # Create temporary volume with timestamp
    timestamp = int(time.time())
    volume_name = f"ots-config-transform-{timestamp}"

    try:
        # Create the volume
        p.volume.create(volume_name, check=True, capture_output=True)

        # Copy config file to volume using a helper container
        # Resolve symlinks for podman VM compatibility (macOS, local only)
        config_path_str = str(config_path.resolve()) if not is_remote else str(config_path)
        p.run(
            "--rm",
            "-v",
            f"{config_path_str}:/src/{file}:ro",
            "-v",
            f"{volume_name}:/dest",
            full_image,
            "/bin/cp",
            f"/src/{file}",
            f"/dest/{file}",
            check=True,
            capture_output=True,
        )

        # Build and run the transformation command
        env_file = quadlet.DEFAULT_ENV_FILE
        cmd = ["podman", "run", "--rm", "--network=host"]

        if is_remote:
            env_exists = ex.run(["test", "-f", str(env_file)]).ok
        else:
            env_exists = env_file.exists()
        if env_exists:
            cmd.extend(["--env-file", str(env_file)])
            cmd.extend(build_secret_args(env_file, executor=ex))

        cmd.extend(["-v", f"{volume_name}:/app/data"])
        # Resolve symlinks for podman VM compatibility (macOS, local only)
        config_dir_str = str(cfg.config_dir.resolve()) if not is_remote else str(cfg.config_dir)
        cmd.extend(["-v", f"{config_dir_str}:/app/etc:ro"])
        cmd.extend(cfg.podman_auth_args(executor=ex))
        cmd.append(full_image)
        cmd.extend(["/bin/bash", "-c", command])

        apply_quiet(quiet)
        logger.info(f"Running: {format_command(cmd)}")
        logger.info("")

        flush_output()
        result = ex.run(cmd)

        if not result.ok:
            # Show migration command output for operator debugging.
            # No secrets here: env vars are passed via podman secrets,
            # not visible in command stdout/stderr.
            logger.error(f"Migration command failed (exit {result.returncode})")
            if result.stderr:
                print(result.stderr)
            if result.stdout:
                print(result.stdout)
            raise SystemExit(result.returncode)

        # Read the transformed file from volume
        read_result = ex.run(
            [
                "podman",
                "run",
                "--rm",
                "-v",
                f"{volume_name}:/data:ro",
                full_image,
                "/bin/cat",
                f"/data/{file}.new",
            ]
        )

        if not read_result.ok:
            logger.error(f"No transformed file produced: /app/data/{file}.new")
            logger.error(f"Migration command should write transformed config to {file}.new")
            raise SystemExit(1)

        new_content = read_result.stdout

        # Read original config content
        if is_remote:
            orig_result = ex.run(["cat", str(config_path)])
            original_content = orig_result.stdout
        else:
            original_content = config_path.read_text()

        # Show unified diff of proposed config changes. This is the primary
        # output of dry-run mode — config files contain app settings, not secrets.
        config_diff = list(
            difflib.unified_diff(
                original_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{file}",
                tofile=f"b/{file}",
            )
        )

        if not config_diff:
            logger.info("No changes detected")
            return

        print("".join(config_diff))

        if not apply:
            print()
            logger.info("Dry run - no changes made. Use --apply to apply changes.")
            return

        # Create backup with timestamp
        backup_time = time.strftime("%Y%m%d-%H%M%S")
        backup_path = Path(f"{config_path}.bak.{backup_time}")

        if is_remote:
            # Remote: use cp for backup, tee for write
            ex.run(["cp", "-p", str(config_path), str(backup_path)], check=True)
            logger.info(f"Backup created: {backup_path}")
            ex.run(["tee", str(config_path)], input=new_content)
            logger.info(f"Config updated: {config_path}")
        else:
            # Handle numbered backups if timestamp backup exists
            if backup_path.exists():
                counter = 1
                while True:
                    numbered_backup = Path(f"{config_path}.bak.{backup_time}.{counter}")
                    if not numbered_backup.exists():
                        backup_path = numbered_backup
                        break
                    counter += 1

            # Create backup and apply
            import shutil

            shutil.copy2(config_path, backup_path)
            logger.info(f"Backup created: {backup_path}")

            config_path.write_text(new_content)
            logger.info(f"Config updated: {config_path}")

    finally:
        # Cleanup: remove temporary volume
        p.volume.rm("-f", volume_name, capture_output=True)


@app.command
def cleanup(
    yes: Yes = False,
    json_output: JsonOutput = False,
):
    """Remove the static_assets Podman volume to reclaim disk space.

    The 'static_assets' volume is created during deploy but is NOT removed
    by 'undeploy'. Run this after all instances are stopped to free disk.

    This is safe to run when no instances are running; if instances are still
    running they will continue to serve cached assets until restarted.

    Examples:
        ots instances cleanup          # Prompt for confirmation
        ots instances cleanup -y       # Skip confirmation
        ots instances cleanup --json   # JSON output
    """
    import json as json_mod

    volume_name = "static_assets"

    if not yes and not json_output:
        print(f"This will remove the Podman volume '{volume_name}'.")
        response = input("Continue? [y/N] ")
        if response.lower() not in ("y", "yes"):
            print("Aborted")
            return

    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    p = Podman(executor=ex)

    try:
        result = p.volume.rm(volume_name)
        if result.returncode == 0:
            outcome = {"success": True, "volume": volume_name, "removed": True}
            if json_output:
                print(json_mod.dumps(outcome, indent=2))
            else:
                logger.info(f"Removed volume: {volume_name}")
        else:
            # Volume may not exist - treat as success (idempotent)
            stderr = result.stderr.strip()
            if "no such volume" in stderr.lower() or "no such container" in stderr.lower():
                outcome = {
                    "success": True,
                    "volume": volume_name,
                    "removed": False,
                    "message": "volume not found",
                }
                if json_output:
                    print(json_mod.dumps(outcome, indent=2))
                else:
                    logger.info(
                        f"Volume '{volume_name}' not found (already removed or never created)"
                    )
            else:
                outcome = {"success": False, "volume": volume_name, "error": stderr}
                if json_output:
                    print(json_mod.dumps(outcome, indent=2))
                else:
                    logger.error(f"Failed to remove volume '{volume_name}': {stderr}")
                raise SystemExit(1)
    except FileNotFoundError:
        msg = "podman not found - is Podman installed?"
        if json_output:
            print(json_mod.dumps({"success": False, "error": msg}))
        else:
            logger.error(f"{msg}")
        raise SystemExit(1)


@app.command
def metrics(
    instance_type: TypeSelector = None,
    web: WebFlag = None,
    worker: WorkerFlag = None,
    scheduler: SchedulerFlag = None,
    json_output: JsonOutput = False,
):
    """Show resource usage metrics for instance(s).

    Shells out to 'podman stats --no-stream' to collect per-container
    CPU, memory, network I/O, and block I/O metrics. Also reports
    systemd active state for each unit.

    Containers must be running for podman stats to return data.

    Examples:
        ots instances metrics                    # Metrics for all instances
        ots instances metrics --web              # Web instances only
        ots instances metrics --web 7043         # Specific instance
        ots instances metrics --json             # JSON output
    """
    import json as json_mod

    itype, identifiers = resolve_instance_type(instance_type, web, worker, scheduler)

    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))

    instances = resolve_identifiers(identifiers, itype, running_only=False, executor=ex)

    if not instances:
        if json_output:
            print(json_mod.dumps({"instances": []}))
        else:
            logger.info("No configured instances found")
            logger.info("Deploy one first: ots instances deploy --help")
        return

    p = Podman(executor=ex)

    results = []

    for inst_type, ids in instances.items():
        for id_ in ids:
            unit = systemd.unit_name(inst_type.value, id_)
            container = systemd.unit_to_container_name(unit)

            # Get systemd active state
            try:
                active_state = systemd.is_active(f"{unit}.service", executor=ex)
            except Exception:
                active_state = "unknown"

            # Get podman stats (non-streaming, single snapshot)
            stats_data = None
            try:
                stats_result = p.stats(container, no_stream=True, format="json", timeout=15)
                if stats_result.returncode == 0 and stats_result.stdout.strip():
                    raw = json_mod.loads(stats_result.stdout)
                    # podman stats --format json returns a list
                    if isinstance(raw, list) and raw:
                        stats_data = raw[0]
            except Exception:
                stats_data = None

            entry: dict = {
                "unit": unit,
                "container": container,
                "instance_type": inst_type.value,
                "identifier": id_,
                "active_state": active_state,
            }

            if stats_data:
                entry["cpu_percent"] = stats_data.get("CPU", "n/a")
                entry["mem_usage"] = stats_data.get("MemUsage", "n/a")
                entry["mem_percent"] = stats_data.get("MemPerc", "n/a")
                entry["net_input"] = stats_data.get("NetInput", "n/a")
                entry["net_output"] = stats_data.get("NetOutput", "n/a")
                entry["block_input"] = stats_data.get("BlockInput", "n/a")
                entry["block_output"] = stats_data.get("BlockOutput", "n/a")
            else:
                entry["cpu_percent"] = "n/a"
                entry["mem_usage"] = "n/a"
                entry["mem_percent"] = "n/a"
                entry["net_input"] = "n/a"
                entry["net_output"] = "n/a"
                entry["block_input"] = "n/a"
                entry["block_output"] = "n/a"

            results.append(entry)

    if json_output:
        print(json_mod.dumps({"instances": results}, indent=2))
        return

    # Table output
    header = (
        f"{'TYPE':<10} {'ID':<10} {'UNIT':<28} {'STATE':<10} "
        f"{'CPU%':<8} {'MEM':<20} {'MEM%':<8} {'NET IN/OUT':<24}"
    )
    print(header)
    print("-" * len(header))

    for entry in results:
        net_io = f"{entry['net_input']}/{entry['net_output']}"
        row = (
            f"{entry['instance_type']:<10} {entry['identifier']:<10} "
            f"{entry['unit']:<28} {entry['active_state']:<10} "
            f"{entry['cpu_percent']:<8} {entry['mem_usage']:<20} "
            f"{entry['mem_percent']:<8} {net_io:<24}"
        )
        print(row)


@app.command
def rollback(
    instance_type: TypeSelector = None,
    web: WebFlag = None,
    worker: WorkerFlag = None,
    scheduler: SchedulerFlag = None,
    delay: Delay = 30,
    dry_run: DryRun = False,
    yes: Yes = False,
    json_output: JsonOutput = False,
):
    """Roll back running instances to the previous deployment.

    Queries the deployment timeline for the previous successful image/tag,
    updates the CURRENT/ROLLBACK aliases in the database, then redeploys
    all targeted running instances with the rolled-back tag.

    Comparable to 'helm rollback' or 'kamal rollback': the alias update
    and instance redeployment happen in one step.

    Use --dry-run to preview what would happen without making changes.

    Examples:
        ots instances rollback                        # Rollback all running
        ots instances rollback --web                  # Rollback web instances
        ots instances rollback --web 7043,7044        # Rollback specific web
        ots instances rollback --dry-run              # Preview only
        ots instances rollback -y                     # Skip confirmation
        ots instances rollback --json                 # JSON output
    """
    import datetime
    import json as json_mod

    itype, identifiers = resolve_instance_type(instance_type, web, worker, scheduler)
    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))

    # Determine rollback target from the deployment timeline
    previous = db.get_previous_tags(cfg.get_db_path(ex), limit=5, executor=ex)
    if len(previous) < 2:
        msg = "No previous deployment found in history - cannot roll back"
        if json_output:
            print(json_mod.dumps({"action": "rollback", "success": False, "error": msg}))
        else:
            logger.error(f"{msg}")
        raise SystemExit(1)

    current_image, current_tag, _ = previous[0]
    rollback_image, rollback_tag, rollback_ts = previous[1]

    if dry_run:
        instances = resolve_identifiers(identifiers, itype, running_only=True, executor=ex)
        dry_items = [{"instance_type": t.value, "identifiers": ids} for t, ids in instances.items()]
        result = {
            "action": "rollback",
            "dry_run": True,
            "from": {"image": current_image, "tag": current_tag},
            "to": {"image": rollback_image, "tag": rollback_tag, "last_deployed": rollback_ts},
            "instances": dry_items,
        }
        if json_output:
            print(json_mod.dumps(result, indent=2))
        else:
            current_ref = join_image_tag(current_image, current_tag)
            rollback_ref = join_image_tag(rollback_image, rollback_tag)
            logger.info(f"[dry-run] Would roll back from {current_ref}")
            logger.info(f"         to {rollback_ref} (last deployed: {rollback_ts})")
            if instances:
                for inst_type_key, ids in instances.items():
                    logger.info(f"[dry-run] Would redeploy {inst_type_key.value}: {', '.join(ids)}")
            else:
                logger.info("[dry-run] No running instances found to redeploy")
        return

    # Confirm unless --yes or --json
    if not yes and not json_output:
        current_ref = join_image_tag(current_image, current_tag)
        rollback_ref = join_image_tag(rollback_image, rollback_tag)
        print(f"Rolling back from {current_ref}")
        print(f"           to    {rollback_ref} (last deployed: {rollback_ts})")
        response = input("Continue? [y/N] ")
        if response.lower() not in ("y", "yes"):
            print("Aborted")
            return

    # Update DB aliases (CURRENT -> ROLLBACK, rollback_tag -> CURRENT)
    rollback_result = db.rollback(cfg.get_db_path(ex), executor=ex)
    if rollback_result is None:
        msg = "Rollback failed - deployment timeline returned no result"
        if json_output:
            print(json_mod.dumps({"action": "rollback", "success": False, "error": msg}))
        else:
            logger.error(f"{msg}")
        raise SystemExit(1)

    new_image, new_tag = rollback_result

    if not json_output:
        logger.info(
            f"Aliases updated: CURRENT={join_image_tag(new_image, new_tag)},"
            f" ROLLBACK={join_image_tag(current_image, current_tag)}"
        )

    # Redeploy running instances with the rolled-back image/tag
    instances = resolve_identifiers(identifiers, itype, running_only=True, executor=ex)

    if not instances:
        msg_extra = "No running instances to redeploy"
        if json_output:
            print(
                json_mod.dumps(
                    {
                        "action": "rollback",
                        "success": True,
                        "image": new_image,
                        "tag": new_tag,
                        "message": msg_extra,
                        "instances": [],
                    },
                    indent=2,
                )
            )
        else:
            logger.info(f"{msg_extra}")
        return

    redeploy_results: list[dict] = []

    with deploy_lock(executor=ex):
        # Write quadlet templates for each instance type being redeployed
        if InstanceType.WEB in instances:
            assets.update(cfg, create_volume=False, executor=ex)
            quadlet.write_web_template(cfg, executor=ex)
        if InstanceType.WORKER in instances:
            quadlet.write_worker_template(cfg, executor=ex)
        if InstanceType.SCHEDULER in instances:
            quadlet.write_scheduler_template(cfg, executor=ex)

        def do_rollback_redeploy(inst_type: InstanceType, id_: str) -> None:
            unit = systemd.unit_name(inst_type.value, id_)
            port = int(id_) if inst_type == InstanceType.WEB else 0
            base_notes = (
                f"rollback from {current_tag}"
                if inst_type == InstanceType.WEB
                else f"{inst_type.value}_id={id_}; rollback from {current_tag}"
            )
            try:
                systemd.recreate(unit, executor=ex)
                db.record_deployment(
                    cfg.get_db_path(ex),
                    image=new_image,
                    tag=new_tag,
                    action=f"rollback-{inst_type.value}",
                    port=port,
                    success=True,
                    notes=base_notes,
                    executor=ex,
                )
                redeploy_results.append(
                    {
                        "unit": unit,
                        "instance_type": inst_type.value,
                        "identifier": id_,
                        "success": True,
                        "image": new_image,
                        "tag": new_tag,
                        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                    }
                )
            except Exception as e:
                fail_notes = (
                    f"rollback from {current_tag}; error={e}"
                    if inst_type == InstanceType.WEB
                    else f"{inst_type.value}_id={id_}; rollback from {current_tag}; error={e}"
                )
                db.record_deployment(
                    cfg.get_db_path(ex),
                    image=new_image,
                    tag=new_tag,
                    action=f"rollback-{inst_type.value}",
                    port=port,
                    success=False,
                    notes=fail_notes,
                    executor=ex,
                )
                redeploy_results.append(
                    {
                        "unit": unit,
                        "instance_type": inst_type.value,
                        "identifier": id_,
                        "success": False,
                        "error": str(e),
                        "image": new_image,
                        "tag": new_tag,
                        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                    }
                )
                raise

        for_each_instance(
            instances, delay, do_rollback_redeploy, "Rolling back", show_logs_hint=not json_output
        )

    if json_output:
        print(
            json_mod.dumps(
                {
                    "action": "rollback",
                    "success": all(r["success"] for r in redeploy_results),
                    "image": new_image,
                    "tag": new_tag,
                    "instances": redeploy_results,
                },
                indent=2,
            )
        )
