# src/ots_containers/commands/instance/app.py

"""Instance management app and commands for OTS containers."""

import logging
import subprocess
import sys
from typing import Annotated

import cyclopts

from ots_containers import assets, db, quadlet, systemd
from ots_containers.config import Config

from ..common import DryRun, Follow, JsonOutput, Lines, Quiet, Yes
from ._helpers import (
    build_secret_args,
    deploy_lock,
    for_each_instance,
    format_command,
    format_journalctl_hint,
    resolve_identifiers,
)
from .annotations import (
    Delay,
    Identifiers,
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
    systemd.require_systemctl()
    instances = resolve_identifiers(identifiers, instance_type, running_only=False)

    if not instances:
        print("No configured instances found")
        return

    cfg = Config()

    if json_output:
        import json

        output = []
        for inst_type, ids in instances.items():
            for id_ in ids:
                unit = systemd.unit_name(inst_type.value, id_)
                service = f"{unit}.service"
                result = subprocess.run(
                    ["systemctl", "is-active", service],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                status = result.stdout.strip()

                # Get deployment info
                if inst_type == InstanceType.WEB:
                    deployments = db.get_deployments(cfg.db_path, limit=1, port=int(id_))
                else:
                    # Worker/scheduler: query by notes containing instance ID
                    deployments = db.get_deployments(
                        cfg.db_path,
                        limit=1,
                        notes_like=f"%{inst_type.value}_id={id_}%",
                    )
                if deployments:
                    dep = deployments[0]
                    output.append(
                        {
                            "type": inst_type.value,
                            "id": id_,
                            "service": service,
                            "container": unit,
                            "status": status,
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
                            "status": status,
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
        f"{'STATUS':<12} {'IMAGE:TAG':<38} {'DEPLOYED':<20} {'ACTION':<10}"
    )
    print(header)
    print("-" * 160)

    for inst_type, ids in instances.items():
        for id_ in ids:
            unit = systemd.unit_name(inst_type.value, id_)
            service = f"{unit}.service"

            # Get systemd status
            result = subprocess.run(
                ["systemctl", "is-active", service],
                capture_output=True,
                text=True,
                timeout=10,
            )
            status = result.stdout.strip()

            # Get last deployment from database
            if inst_type == InstanceType.WEB:
                deployments = db.get_deployments(cfg.db_path, limit=1, port=int(id_))
            else:
                # Worker/scheduler: query by notes containing instance ID
                deployments = db.get_deployments(
                    cfg.db_path,
                    limit=1,
                    notes_like=f"%{inst_type.value}_id={id_}%",
                )
            if deployments:
                dep = deployments[0]
                image_tag = f"{dep.image}:{dep.tag}"
                # Format timestamp - strip microseconds and 'T'
                deployed = dep.timestamp.split(".")[0].replace("T", " ")
                action = dep.action
            else:
                image_tag = "unknown"
                deployed = "n/a"
                action = "n/a"

            row = (
                f"{inst_type.value:<10} {id_:<10} {service:<28} {unit:<24} "
                f"{status:<12} {image_tag:<38} {deployed:<20} {action:<10}"
            )
            print(row)


@app.command(name="list")
def list_cmd(
    identifiers: Identifiers = (),
    instance_type: TypeSelector = None,
    web: WebFlag = False,
    worker: WorkerFlag = False,
    scheduler: SchedulerFlag = False,
    json_output: JsonOutput = False,
):
    """List instances with status, image, and deployment info.

    Auto-discovers all instances if no identifiers specified.

    Examples:
        ots instances list                       # List all instances
        ots instances list --web                 # List web instances only
        ots instances list --web 7043 7044       # List specific web instances
        ots instances list --worker              # List worker instances
        ots instances list --json                # JSON output
    """
    itype = resolve_instance_type(instance_type, web, worker, scheduler)
    _list_instances_impl(identifiers, itype, json_output)


@app.default
def list_instances(
    identifiers: Identifiers = (),
    instance_type: TypeSelector = None,
    web: WebFlag = False,
    worker: WorkerFlag = False,
    scheduler: SchedulerFlag = False,
    json_output: JsonOutput = False,
):
    """List instances with status, image, and deployment info.

    Auto-discovers all instances if no identifiers specified.

    Examples:
        ots instances                            # List all instances
        ots instances --web                      # List web instances only
        ots instances --web 7043 7044            # List specific web instances
        ots instances --worker                   # List worker instances
        ots instances --scheduler                # List scheduler instances
        ots instances --json                     # JSON output
    """
    itype = resolve_instance_type(instance_type, web, worker, scheduler)
    _list_instances_impl(identifiers, itype, json_output)


@app.command
def run(
    port: Annotated[
        int,
        cyclopts.Parameter(
            name=["--port", "-p"],
            help="Container port to run on",
        ),
    ],
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
    tag: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--tag", "-t"],
            help="Image tag to run (default: from TAG env or 'current' alias)",
        ),
    ] = None,
    remote: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--remote", "-r"],
            help="Pull from registry instead of using local image",
        ),
    ] = False,
):
    """Run a container directly with podman (no systemd).

    By default uses local images (from 'ots image build').
    If .env exists in current directory, it will be used.
    Use --remote to pull from registry instead.
    Use --production to include system env file, secrets, and volumes.

    Examples:
        ots instance run -p 7143 --tag plop-2   # local build (default)
        ots instance run -p 7143 -d             # detached
        ots instance run -p 7143 --remote --tag v0.19.0  # from registry
        ots instance run -p 7143 --production   # full production config
    """
    cfg = Config()

    # Resolve image/tag
    # Default: local images (from 'ots image build')
    # --remote: pull from registry (ghcr.io or OTS_REGISTRY)
    if remote:
        if tag:
            image = cfg.image
            resolved_tag = tag
        else:
            image, resolved_tag = cfg.resolve_image_tag()
    else:
        # Local is the default
        image = "onetimesecret"  # localhost/onetimesecret
        resolved_tag = tag or cfg.tag
    full_image = f"{image}:{resolved_tag}"

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

    # Check for local .env file in current directory
    from pathlib import Path

    local_env = Path.cwd() / ".env"
    if local_env.exists():
        cmd.extend(["--env-file", str(local_env)])

    # Production mode: add env file, secrets, and volumes
    if production:
        from ots_containers.environment_file import get_secrets_from_env_file

        env_file = quadlet.DEFAULT_ENV_FILE

        # Environment file
        if env_file.exists():
            cmd.extend(["--env-file", str(env_file)])

            # Secrets
            secret_specs = get_secrets_from_env_file(env_file)
            for spec in secret_specs:
                cmd.extend(
                    [
                        "--secret",
                        f"{spec.secret_name},type=env,target={spec.env_var_name}",
                    ]
                )

        # Config overrides (per-file)
        for f in cfg.existing_config_files:
            cmd.extend(["-v", f"{f}:/app/etc/{f.name}:ro"])
        cmd.extend(["-v", "static_assets:/app/public:ro"])

        # Auth file for private registry
        if cfg.registry:
            cmd.extend(["--authfile", str(cfg.registry_auth_file)])

    # Image
    cmd.append(full_image)

    if not quiet:
        print(format_command(cmd))
        print()

    # Run it
    try:
        if detach:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            print(f"Container started: {result.stdout.strip()[:12]}")
        else:
            # Foreground - let it take over the terminal
            subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Failed to run container: {e}")
        if e.stderr:
            print(e.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\nStopped")


@app.command
def deploy(
    identifiers: Identifiers = (),
    instance_type: TypeSelector = None,
    web: WebFlag = False,
    worker: WorkerFlag = False,
    scheduler: SchedulerFlag = False,
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
):
    """Deploy new instance(s) using quadlet and Podman secrets.

    Writes quadlet config and starts systemd service.
    Requires /etc/default/onetimesecret and Podman secrets to be configured.
    Records deployment to timeline for audit and rollback support.

    Examples:
        ots instances deploy --web 7043 7044        # Deploy web on ports
        ots instances deploy --worker 1 2           # Deploy workers 1, 2
        ots instances deploy --worker billing       # Deploy 'billing' worker
        ots instances deploy --scheduler main       # Deploy scheduler
        ots instances deploy --web 7043 --force     # Skip secrets check (not recommended)
        ots instances deploy --web 7043 --wait-timeout 60  # Wait up to 60s for health
    """
    import datetime
    import json as json_mod

    itype = resolve_instance_type(instance_type, web, worker, scheduler)

    # Deploy requires identifiers AND type
    if not identifiers:
        raise SystemExit(
            "Identifiers required for deploy. Example: ots instances deploy --web 7043"
        )
    if itype is None:
        raise SystemExit("Instance type required for deploy. Use --web, --worker, or --scheduler.")

    cfg = Config()

    # Resolve image/tag (handles CURRENT/ROLLBACK aliases)
    image, tag = cfg.resolve_image_tag()
    if not quiet and not json_output:
        print(f"Image: {image}:{tag}")
        if cfg.has_custom_config:
            mounted = [f.name for f in cfg.existing_config_files]
            print(f"Config overrides: {', '.join(mounted)}")
        else:
            print("Config: using container built-in defaults")

    if dry_run:
        result = {
            "action": "deploy",
            "dry_run": True,
            "instance_type": itype.value,
            "identifiers": list(identifiers),
            "image": image,
            "tag": tag,
        }
        if json_output:
            print(json_mod.dumps(result, indent=2))
        else:
            print(f"[dry-run] Would deploy {itype.value}: {', '.join(identifiers)}")
        return

    deploy_results: list[dict] = []

    with deploy_lock():
        # Write appropriate quadlet template.
        # Raises SystemExit(1) if env file or secrets are missing (unless force=True).
        if itype == InstanceType.WEB:
            assets.update(cfg, create_volume=True)
            logger.info("Writing quadlet files to %s", cfg.web_template_path.parent)
            quadlet.write_web_template(cfg, force=force)
        elif itype == InstanceType.WORKER:
            logger.info("Writing quadlet files to %s", cfg.worker_template_path.parent)
            quadlet.write_worker_template(cfg, force=force)
        elif itype == InstanceType.SCHEDULER:
            logger.info("Writing quadlet files to %s", cfg.scheduler_template_path.parent)
            quadlet.write_scheduler_template(cfg, force=force)

        def do_deploy(inst_type: InstanceType, id_: str) -> None:
            unit = systemd.unit_name(inst_type.value, id_)
            port = int(id_) if inst_type == InstanceType.WEB else 0
            base_notes = None if inst_type == InstanceType.WEB else f"{inst_type.value}_id={id_}"
            try:
                systemd.start(unit)
                # Optionally wait for the unit to become active
                if wait_timeout > 0:
                    if not quiet and not json_output:
                        print(f"  Waiting up to {wait_timeout}s for {unit} to become active...")
                    systemd.wait_for_healthy(unit, timeout=wait_timeout)
                # Record successful deployment
                db.record_deployment(
                    cfg.db_path,
                    image=image,
                    tag=tag,
                    action=f"deploy-{inst_type.value}",
                    port=port,
                    success=True,
                    notes=base_notes,
                )
                deploy_results.append(
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
            except systemd.HealthCheckTimeoutError as e:
                fail_notes = (
                    f"health-timeout: {e}"
                    if inst_type == InstanceType.WEB
                    else f"{inst_type.value}_id={id_}; health-timeout: {e}"
                )
                db.record_deployment(
                    cfg.db_path,
                    image=image,
                    tag=tag,
                    action=f"deploy-{inst_type.value}",
                    port=port,
                    success=False,
                    notes=fail_notes,
                )
                deploy_results.append(
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
                print(f"  ERROR: {e}", file=sys.stderr)
                raise SystemExit(1) from None
            except Exception as e:
                fail_notes = (
                    str(e)
                    if inst_type == InstanceType.WEB
                    else f"{inst_type.value}_id={id_}; error={e}"
                )
                db.record_deployment(
                    cfg.db_path,
                    image=image,
                    tag=tag,
                    action=f"deploy-{inst_type.value}",
                    port=port,
                    success=False,
                    notes=fail_notes,
                )
                deploy_results.append(
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

        instances = {itype: list(identifiers)}
        for_each_instance(instances, delay, do_deploy, "Deploying", show_logs_hint=not json_output)

    if json_output:
        print(
            json_mod.dumps(
                {
                    "action": "deploy",
                    "success": all(r["success"] for r in deploy_results),
                    "instances": deploy_results,
                },
                indent=2,
            )
        )


@app.command
def redeploy(
    identifiers: Identifiers = (),
    instance_type: TypeSelector = None,
    web: WebFlag = False,
    worker: WorkerFlag = False,
    scheduler: SchedulerFlag = False,
    delay: Delay = 5,
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
        ots instances redeploy --web 7043 7044      # Redeploy specific web
        ots instances redeploy --scheduler main     # Redeploy specific scheduler
        ots instances redeploy --force              # Force teardown+recreate
        ots instances redeploy --wait-timeout 60    # Wait up to 60s for health
    """
    import datetime
    import json as json_mod

    itype = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=True)

    if not instances:
        if json_output:
            print(json_mod.dumps({"action": "redeploy", "success": True, "instances": []}))
        else:
            print("No running instances found")
        return

    cfg = Config()

    # Resolve image/tag (handles CURRENT/ROLLBACK aliases)
    image, tag = cfg.resolve_image_tag()
    if not quiet and not json_output:
        print(f"Image: {image}:{tag}")
        if cfg.has_custom_config:
            mounted = [f.name for f in cfg.existing_config_files]
            print(f"Config overrides: {', '.join(mounted)}")
        else:
            print("Config: using container built-in defaults")

    if dry_run:
        verb = "force redeploy" if force else "redeploy"
        dry_items = [{"instance_type": t.value, "identifiers": ids} for t, ids in instances.items()]
        if json_output:
            print(
                json_mod.dumps(
                    {
                        "action": "redeploy",
                        "dry_run": True,
                        "image": image,
                        "tag": tag,
                        "instances": dry_items,
                    },
                    indent=2,
                )
            )
        else:
            for inst_type, ids in instances.items():
                print(f"[dry-run] Would {verb} {inst_type.value}: {', '.join(ids)}")
        return

    redeploy_results: list[dict] = []

    with deploy_lock():
        # Write quadlet templates for each type being redeployed.
        # Raises SystemExit(1) if env file or secrets are missing.
        # Redeploy always enforces secrets check (no --force override for secrets here).
        if InstanceType.WEB in instances:
            assets.update(cfg, create_volume=force)
            logger.info("Writing quadlet files to %s", cfg.web_template_path.parent)
            quadlet.write_web_template(cfg)
        if InstanceType.WORKER in instances:
            logger.info("Writing quadlet files to %s", cfg.worker_template_path.parent)
            quadlet.write_worker_template(cfg)
        if InstanceType.SCHEDULER in instances:
            logger.info("Writing quadlet files to %s", cfg.scheduler_template_path.parent)
            quadlet.write_scheduler_template(cfg)

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
                    print(f"Stopping {unit}")
                systemd.stop(unit)

            try:
                if force or not systemd.container_exists(unit):
                    if not json_output:
                        print(f"Starting {unit}")
                    systemd.start(unit)
                else:
                    if not json_output:
                        print(f"Recreating {unit}")
                    systemd.recreate(unit)

                # Optionally wait for the unit to become active
                if wait_timeout > 0:
                    if not quiet and not json_output:
                        print(f"  Waiting up to {wait_timeout}s for {unit} to become active...")
                    systemd.wait_for_healthy(unit, timeout=wait_timeout)

                db.record_deployment(
                    cfg.db_path,
                    image=image,
                    tag=tag,
                    action=f"redeploy-{inst_type.value}",
                    port=port,
                    success=True,
                    notes=base_notes,
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
            except systemd.HealthCheckTimeoutError as e:
                fail_notes = (
                    f"health-timeout: {e}"
                    if inst_type == InstanceType.WEB
                    else f"{inst_type.value}_id={id_}; health-timeout: {e}"
                )
                db.record_deployment(
                    cfg.db_path,
                    image=image,
                    tag=tag,
                    action=f"redeploy-{inst_type.value}",
                    port=port,
                    success=False,
                    notes=fail_notes,
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
                print(f"  ERROR: {e}", file=sys.stderr)
                raise SystemExit(1) from None
            except Exception as e:
                fail_notes = (
                    str(e)
                    if inst_type == InstanceType.WEB
                    else f"{inst_type.value}_id={id_}; error={e}"
                )
                db.record_deployment(
                    cfg.db_path,
                    image=image,
                    tag=tag,
                    action=f"redeploy-{inst_type.value}",
                    port=port,
                    success=False,
                    notes=fail_notes,
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

    if json_output:
        print(
            json_mod.dumps(
                {
                    "action": "redeploy",
                    "success": all(r["success"] for r in redeploy_results),
                    "instances": redeploy_results,
                },
                indent=2,
            )
        )


@app.command
def undeploy(
    identifiers: Identifiers = (),
    instance_type: TypeSelector = None,
    web: WebFlag = False,
    worker: WorkerFlag = False,
    scheduler: SchedulerFlag = False,
    delay: Delay = 5,
    dry_run: DryRun = False,
    yes: Yes = False,
    json_output: JsonOutput = False,
):
    """Stop systemd service for instance(s).

    Stops systemd service. Records action to timeline for audit.

    Examples:
        ots instances undeploy                      # Undeploy all running
        ots instances undeploy --web                # Undeploy web instances
        ots instances undeploy --web 7043 7044      # Undeploy specific web
        ots instances undeploy --scheduler main     # Undeploy specific scheduler
        ots instances undeploy -y                   # Skip confirmation
        ots instances undeploy --json               # JSON output
    """
    import datetime
    import json as json_mod

    itype = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=True)

    if not instances:
        if json_output:
            print(json_mod.dumps({"action": "undeploy", "success": True, "instances": []}))
        else:
            print("No running instances found")
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
                print(f"[dry-run] Would undeploy {inst_type.value}: {', '.join(ids)}")
        return

    cfg = Config()
    image, tag = cfg.resolve_image_tag()
    undeploy_results: list[dict] = []

    def do_undeploy(inst_type: InstanceType, id_: str) -> None:
        unit = systemd.unit_name(inst_type.value, id_)
        try:
            systemd.stop(unit)
            # Prevent auto-start on reboot — disable is idempotent (no-op if not enabled)
            systemd.disable(unit)
            # Clear failed state so unit doesn't appear in discovery
            systemd.reset_failed(unit)
            port = int(id_) if inst_type == InstanceType.WEB else 0
            db.record_deployment(
                cfg.db_path,
                image=image,
                tag=tag,
                action=f"undeploy-{inst_type.value}",
                port=port,
                success=True,
                notes=None if inst_type == InstanceType.WEB else f"{inst_type.value}_id={id_}",
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
                cfg.db_path,
                image=image,
                tag=tag,
                action=f"undeploy-{inst_type.value}",
                port=port,
                success=False,
                notes=fail_notes,
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
    identifiers: Identifiers = (),
    instance_type: TypeSelector = None,
    web: WebFlag = False,
    worker: WorkerFlag = False,
    scheduler: SchedulerFlag = False,
):
    """Start systemd unit(s) for instance(s).

    Does NOT regenerate quadlet - use 'redeploy' for that.

    Examples:
        ots instances start                         # Start all configured
        ots instances start --web                   # Start web instances
        ots instances start --web 7043 7044         # Start specific web
        ots instances start --scheduler main        # Start specific scheduler
    """
    itype = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=False)

    if not instances:
        print("No configured instances found")
        return

    for inst_type, ids in instances.items():
        for id_ in ids:
            unit = systemd.unit_name(inst_type.value, id_)
            systemd.start(unit)
            print(f"Started {unit}")

    hint = format_journalctl_hint(instances)
    if hint:
        print(f"\nView logs: {hint}")


@app.command
def stop(
    identifiers: Identifiers = (),
    instance_type: TypeSelector = None,
    web: WebFlag = False,
    worker: WorkerFlag = False,
    scheduler: SchedulerFlag = False,
):
    """Stop systemd unit(s) for instance(s).

    Does NOT affect quadlet config.
    Only stops running instances; already-stopped instances are skipped.

    Examples:
        ots instances stop                          # Stop all running
        ots instances stop --web                    # Stop web instances
        ots instances stop --web 7043 7044          # Stop specific web
        ots instances stop --scheduler              # Stop scheduler instances
    """
    itype = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=True)

    if not instances:
        print("No running instances found")
        return

    for inst_type, ids in instances.items():
        for id_ in ids:
            unit = systemd.unit_name(inst_type.value, id_)
            systemd.stop(unit)
            print(f"Stopped {unit}")


@app.command
def restart(
    identifiers: Identifiers = (),
    instance_type: TypeSelector = None,
    web: WebFlag = False,
    worker: WorkerFlag = False,
    scheduler: SchedulerFlag = False,
    delay: Delay = 30,
):
    """Restart systemd unit(s) for instance(s).

    Does NOT regenerate quadlet - use 'redeploy' for that.
    Only restarts running instances; stopped instances are skipped.
    Waits between instances to allow startup before Caddy health checks.

    Examples:
        ots instances restart                       # Restart all running
        ots instances restart --web                 # Restart web instances
        ots instances restart --web 7043 7044       # Restart specific web
        ots instances restart --scheduler main      # Restart specific scheduler
        ots instances restart --delay 10            # Longer wait between restarts
    """
    itype = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=True)

    if not instances:
        print("No running instances found")
        return

    def do_restart(inst_type: InstanceType, id_: str) -> None:
        unit = systemd.unit_name(inst_type.value, id_)
        systemd.restart(unit)

    for_each_instance(instances, delay, do_restart, "Restarting", show_logs_hint=True)


@app.command
def enable(
    identifiers: Identifiers = (),
    instance_type: TypeSelector = None,
    web: WebFlag = False,
    worker: WorkerFlag = False,
    scheduler: SchedulerFlag = False,
):
    """Enable instance(s) to start at boot.

    Does not start the instance - use 'start' for that.

    Examples:
        ots instances enable                        # Enable all configured
        ots instances enable --web                  # Enable web instances
        ots instances enable --web 7043 7044        # Enable specific web
        ots instances enable --scheduler main       # Enable specific scheduler
    """
    systemd.require_systemctl()
    itype = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=False)

    if not instances:
        print("No configured instances found")
        return

    for inst_type, ids in instances.items():
        for id_ in ids:
            unit = systemd.unit_name(inst_type.value, id_)
            try:
                subprocess.run(
                    ["sudo", "systemctl", "enable", unit],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                print(f"Enabled {unit}")
            except subprocess.CalledProcessError as e:
                print(f"Failed to enable {unit}: {e.stderr}")


@app.command
def disable(
    identifiers: Identifiers = (),
    instance_type: TypeSelector = None,
    web: WebFlag = False,
    worker: WorkerFlag = False,
    scheduler: SchedulerFlag = False,
    yes: Yes = False,
):
    """Disable instance(s) from starting at boot.

    Does not stop the instance - use 'stop' for that.

    Examples:
        ots instances disable                       # Disable all configured
        ots instances disable --web                 # Disable web instances
        ots instances disable --web 7043 7044 -y    # Disable specific web
        ots instances disable --scheduler main -y   # Disable specific scheduler
    """
    systemd.require_systemctl()
    itype = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=False)

    if not instances:
        print("No configured instances found")
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
                subprocess.run(
                    ["sudo", "systemctl", "disable", unit],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                print(f"Disabled {unit}")
            except subprocess.CalledProcessError as e:
                print(f"Failed to disable {unit}: {e.stderr}")


@app.command
def status(
    identifiers: Identifiers = (),
    instance_type: TypeSelector = None,
    web: WebFlag = False,
    worker: WorkerFlag = False,
    scheduler: SchedulerFlag = False,
    json_output: JsonOutput = False,
):
    """Show systemd status for instance(s).

    Examples:
        ots instances status                        # Status of all configured
        ots instances status --web                  # Status of web instances
        ots instances status --web 7043 7044        # Status of specific web
        ots instances status --scheduler            # Status of scheduler instances
        ots instances status --json                 # JSON output
    """
    import json as json_mod

    itype = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=False)

    if not instances:
        if json_output:
            print(json_mod.dumps({"instances": []}))
        else:
            print("No configured instances found")
        return

    if json_output:
        results = []
        for inst_type, ids in instances.items():
            for id_ in ids:
                unit = systemd.unit_name(inst_type.value, id_)
                result = subprocess.run(
                    ["systemctl", "is-active", unit],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                active_state = result.stdout.strip()
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
                systemd.status(unit)
                print()


@app.command
def logs(
    identifiers: Identifiers = (),
    instance_type: TypeSelector = None,
    web: WebFlag = False,
    worker: WorkerFlag = False,
    scheduler: SchedulerFlag = False,
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
    itype = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=False)

    if not instances:
        print("No instances found")
        return

    # Build list of units
    units = []
    for inst_type, ids in instances.items():
        for id_ in ids:
            units.append(systemd.unit_name(inst_type.value, id_))

    cmd = ["sudo", "journalctl", "--no-pager", f"-n{lines}"]
    if follow:
        cmd.append("-f")
    for unit in units:
        cmd.extend(["-u", unit])
    subprocess.run(cmd)


@app.command(name="show-env")
def show_env():
    """Show infrastructure environment variables.

    Displays the contents of /etc/default/onetimesecret (shared by all instances).
    Only shows valid KEY=VALUE pairs, sorted alphabetically.

    Examples:
        ots instances show-env
    """
    from pathlib import Path

    env_file = Path("/etc/default/onetimesecret")
    print(f"=== {env_file} ===")
    if env_file.exists():
        lines = env_file.read_text().splitlines()
        # Parse only valid KEY=VALUE lines (key must be valid shell identifier)
        env_vars = {}
        for line in lines:
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
    else:
        print("  (file not found)")
    print()


@app.command(name="exec")
def exec_shell(
    identifiers: Identifiers = (),
    instance_type: TypeSelector = None,
    web: WebFlag = False,
    worker: WorkerFlag = False,
    scheduler: SchedulerFlag = False,
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

    itype = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=True)

    if not instances:
        print("No running instances found")
        return

    shell = command or os.environ.get("SHELL", "/bin/sh")

    for inst_type, ids in instances.items():
        for id_ in ids:
            # Use Quadlet container naming convention
            unit = systemd.unit_name(inst_type.value, id_)
            container = systemd.unit_to_container_name(unit)
            print(f"=== Entering {unit} ===")
            # Interactive exec requires subprocess.run with no capture
            subprocess.run(["podman", "exec", "-it", container, shell])
            print()


@app.command
def shell(
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
    tag: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--tag", "-t"],
            help="Image tag to use (default: from TAG env or 'current' alias)",
        ),
    ] = None,
    remote: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--remote", "-r"],
            help="Pull from registry instead of using local image",
        ),
    ] = False,
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
    """
    from pathlib import Path

    if persistent and volume:
        print("Error: --persistent and --volume are mutually exclusive")
        raise SystemExit(1)

    cfg = Config()

    # Resolve image/tag (same pattern as run command)
    if remote:
        if tag:
            image = cfg.image
            resolved_tag = tag
        else:
            image, resolved_tag = cfg.resolve_image_tag()
    else:
        # Local is the default
        image = "onetimesecret"  # localhost/onetimesecret
        resolved_tag = tag or cfg.tag
    full_image = f"{image}:{resolved_tag}"

    # Build podman run command
    cmd = ["podman", "run", "--rm"]

    # Interactive unless command provided
    if command is None:
        cmd.append("-it")

    cmd.append("--network=host")

    # Environment file and secrets
    env_file = quadlet.DEFAULT_ENV_FILE
    if env_file.exists():
        cmd.extend(["--env-file", str(env_file)])
        cmd.extend(build_secret_args(env_file))

    # Data volume: bind-mount, persistent named volume, or tmpfs (default)
    if volume:
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
    for f in cfg.existing_config_files:
        resolved = f.resolve()  # symlink resolution for macOS podman VM
        cmd.extend(["-v", f"{resolved}:/app/etc/{f.name}:ro"])

    # Image
    cmd.append(full_image)

    # Command to run
    if command:
        cmd.extend(["/bin/bash", "-c", command])
    else:
        cmd.append("/bin/bash")

    if not quiet:
        print(format_command(cmd))
        print()

    # Run it
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Shell exited with code {e.returncode}")
        raise SystemExit(e.returncode)
    except KeyboardInterrupt:
        print("\nInterrupted")


@app.command(name="config-transform")
def config_transform(
    command: Annotated[
        str,
        cyclopts.Parameter(
            name=["--command", "-c"],
            help="Migration command to run (e.g., 'bin/ots migrate 20250727_01')",
        ),
    ],
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
    tag: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--tag", "-t"],
            help="Image tag to use (default: from TAG env or 'current' alias)",
        ),
    ] = None,
    remote: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--remote", "-r"],
            help="Pull from registry instead of using local image",
        ),
    ] = False,
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
    """
    import difflib
    import time
    from pathlib import Path

    cfg = Config()

    # Validate: prevent path traversal
    if ".." in file or file.startswith("/"):
        raise SystemExit(f"Invalid file path: {file!r} (no path traversal allowed)")

    # Check config file exists
    config_path = cfg.config_dir / file
    if not config_path.exists():
        raise SystemExit(f"Config file not found: {config_path}")

    # Resolve image/tag (same pattern as shell command)
    if remote:
        if tag:
            image = cfg.image
            resolved_tag = tag
        else:
            image, resolved_tag = cfg.resolve_image_tag()
    else:
        image = "onetimesecret"
        resolved_tag = tag or cfg.tag
    full_image = f"{image}:{resolved_tag}"

    # Create temporary volume with timestamp
    timestamp = int(time.time())
    volume_name = f"ots-config-transform-{timestamp}"

    try:
        # Create the volume
        subprocess.run(
            ["podman", "volume", "create", volume_name],
            check=True,
            capture_output=True,
        )

        # Copy config file to volume using a helper container
        # We use a busybox-style approach: mount both and copy
        # Resolve symlinks for podman VM compatibility (macOS)
        config_path_resolved = config_path.resolve()
        subprocess.run(
            [
                "podman",
                "run",
                "--rm",
                "-v",
                f"{config_path_resolved}:/src/{file}:ro",
                "-v",
                f"{volume_name}:/dest",
                full_image,
                "/bin/cp",
                f"/src/{file}",
                f"/dest/{file}",
            ],
            check=True,
            capture_output=True,
        )

        # Build and run the transformation command
        env_file = quadlet.DEFAULT_ENV_FILE
        cmd = ["podman", "run", "--rm", "--network=host"]

        if env_file.exists():
            cmd.extend(["--env-file", str(env_file)])
            cmd.extend(build_secret_args(env_file))

        cmd.extend(["-v", f"{volume_name}:/app/data"])
        # Resolve symlinks for podman VM compatibility (macOS)
        config_dir_resolved = cfg.config_dir.resolve()
        cmd.extend(["-v", f"{config_dir_resolved}:/app/etc:ro"])
        cmd.append(full_image)
        cmd.extend(["/bin/bash", "-c", command])

        if not quiet:
            print(f"Running: {format_command(cmd)}")
            print()

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            # Show migration command output for operator debugging.
            # No secrets here: env vars are passed via podman secrets,
            # not visible in command stdout/stderr.
            print(f"Migration command failed (exit {result.returncode})")
            if result.stderr:
                print(result.stderr)
            if result.stdout:
                print(result.stdout)
            raise SystemExit(result.returncode)

        # Read the transformed file from volume
        read_result = subprocess.run(
            [
                "podman",
                "run",
                "--rm",
                "-v",
                f"{volume_name}:/data:ro",
                full_image,
                "/bin/cat",
                f"/data/{file}.new",
            ],
            capture_output=True,
            text=True,
        )

        if read_result.returncode != 0:
            print(f"No transformed file produced: /app/data/{file}.new")
            print("Migration command should write transformed config to {file}.new")
            raise SystemExit(1)

        new_content = read_result.stdout
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
            print("No changes detected")
            return

        print("".join(config_diff))

        if not apply:
            print()
            print("Dry run - no changes made. Use --apply to apply changes.")
            return

        # Create backup with timestamp
        backup_time = time.strftime("%Y%m%d-%H%M%S")
        backup_path = Path(f"{config_path}.bak.{backup_time}")

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
        print(f"Backup created: {backup_path}")

        config_path.write_text(new_content)
        print(f"Config updated: {config_path}")

    finally:
        # Cleanup: remove temporary volume
        subprocess.run(
            ["podman", "volume", "rm", "-f", volume_name],
            capture_output=True,
        )
