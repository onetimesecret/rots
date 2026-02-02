# src/ots_containers/commands/instance/app.py
"""Instance management app and commands for OTS containers."""

import subprocess
from typing import Annotated

import cyclopts

from ots_containers import assets, db, quadlet, systemd
from ots_containers.config import Config

from ..common import DryRun, Follow, JsonOutput, Lines, Quiet, Yes
from ._helpers import (
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

app = cyclopts.App(
    name=["instance", "instances"],
    help="Manage OTS container instances (quadlet, systemd)",
)


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
    instances = resolve_identifiers(identifiers, itype, running_only=False)

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
                )
                status = result.stdout.strip()

                # Get deployment info (only for web instances with port)
                port = int(id_) if inst_type == InstanceType.WEB else 0
                deployments = db.get_deployments(cfg.db_path, limit=1, port=port) if port else []
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
            )
            status = result.stdout.strip()

            # Get last deployment from database (only for web instances)
            port = int(id_) if inst_type == InstanceType.WEB else 0
            deployments = db.get_deployments(cfg.db_path, limit=1, port=port) if port else []
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
                cmd.extend(["--secret", f"{spec.secret_name},type=env,target={spec.env_var_name}"])

        # Volumes
        cmd.extend(["-v", f"{cfg.config_dir}:/app/etc:ro"])
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
    """
    itype = resolve_instance_type(instance_type, web, worker, scheduler)

    # Deploy requires identifiers AND type
    if not identifiers:
        raise SystemExit(
            "Identifiers required for deploy. Example: ots instances deploy --web 7043"
        )
    if itype is None:
        raise SystemExit("Instance type required for deploy. Use --web, --worker, or --scheduler.")

    cfg = Config()
    cfg.validate()

    # Resolve image/tag (handles CURRENT/ROLLBACK aliases)
    image, tag = cfg.resolve_image_tag()
    if not quiet:
        print(f"Image: {image}:{tag}")
        print(f"Reading config from {cfg.config_yaml}")

    if dry_run:
        print(f"[dry-run] Would deploy {itype.value}: {', '.join(identifiers)}")
        return

    # Write appropriate quadlet template
    if itype == InstanceType.WEB:
        assets.update(cfg, create_volume=True)
        print(f"Writing quadlet files to {cfg.web_template_path.parent}")
        quadlet.write_web_template(cfg)
    elif itype == InstanceType.WORKER:
        print(f"Writing quadlet files to {cfg.worker_template_path.parent}")
        quadlet.write_worker_template(cfg)
    elif itype == InstanceType.SCHEDULER:
        print(f"Writing quadlet files to {cfg.scheduler_template_path.parent}")
        quadlet.write_scheduler_template(cfg)

    def do_deploy(inst_type: InstanceType, id_: str) -> None:
        unit = systemd.unit_name(inst_type.value, id_)
        try:
            systemd.start(unit)
            # Record successful deployment
            port = int(id_) if inst_type == InstanceType.WEB else 0
            db.record_deployment(
                cfg.db_path,
                image=image,
                tag=tag,
                action=f"deploy-{inst_type.value}",
                port=port,
                success=True,
                notes=None if inst_type == InstanceType.WEB else f"{inst_type.value}_id={id_}",
            )
        except Exception as e:
            port = int(id_) if inst_type == InstanceType.WEB else 0
            db.record_deployment(
                cfg.db_path,
                image=image,
                tag=tag,
                action=f"deploy-{inst_type.value}",
                port=port,
                success=False,
                notes=str(e),
            )
            raise

    instances = {itype: list(identifiers)}
    for_each_instance(instances, delay, do_deploy, "Deploying", show_logs_hint=True)


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
    """
    itype = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=True)

    if not instances:
        print("No running instances found")
        return

    cfg = Config()
    cfg.validate()

    # Resolve image/tag (handles CURRENT/ROLLBACK aliases)
    image, tag = cfg.resolve_image_tag()
    if not quiet:
        print(f"Image: {image}:{tag}")
        print(f"Reading config from {cfg.config_yaml}")

    if dry_run:
        verb = "force redeploy" if force else "redeploy"
        for inst_type, ids in instances.items():
            print(f"[dry-run] Would {verb} {inst_type.value}: {', '.join(ids)}")
        return

    # Write quadlet templates for each type being redeployed
    if InstanceType.WEB in instances:
        assets.update(cfg, create_volume=force)
        print(f"Writing quadlet files to {cfg.web_template_path.parent}")
        quadlet.write_web_template(cfg)
    if InstanceType.WORKER in instances:
        print(f"Writing quadlet files to {cfg.worker_template_path.parent}")
        quadlet.write_worker_template(cfg)
    if InstanceType.SCHEDULER in instances:
        print(f"Writing quadlet files to {cfg.scheduler_template_path.parent}")
        quadlet.write_scheduler_template(cfg)

    def do_redeploy(inst_type: InstanceType, id_: str) -> None:
        unit = systemd.unit_name(inst_type.value, id_)

        if force:
            print(f"Stopping {unit}")
            systemd.stop(unit)

        try:
            if force or not systemd.container_exists(unit):
                print(f"Starting {unit}")
                systemd.start(unit)
            else:
                print(f"Recreating {unit}")
                systemd.recreate(unit)

            port = int(id_) if inst_type == InstanceType.WEB else 0
            db.record_deployment(
                cfg.db_path,
                image=image,
                tag=tag,
                action=f"redeploy-{inst_type.value}",
                port=port,
                success=True,
                notes=("force" if force else None)
                if inst_type == InstanceType.WEB
                else f"{inst_type.value}_id={id_}" + (", force" if force else ""),
            )
        except Exception as e:
            port = int(id_) if inst_type == InstanceType.WEB else 0
            db.record_deployment(
                cfg.db_path,
                image=image,
                tag=tag,
                action=f"redeploy-{inst_type.value}",
                port=port,
                success=False,
                notes=str(e),
            )
            raise

    verb = "Force redeploying" if force else "Redeploying"
    for_each_instance(instances, delay, do_redeploy, verb, show_logs_hint=True)


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
):
    """Stop systemd service for instance(s).

    Stops systemd service. Records action to timeline for audit.

    Examples:
        ots instances undeploy                      # Undeploy all running
        ots instances undeploy --web                # Undeploy web instances
        ots instances undeploy --web 7043 7044      # Undeploy specific web
        ots instances undeploy --scheduler main     # Undeploy specific scheduler
        ots instances undeploy -y                   # Skip confirmation
    """
    itype = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=True)

    if not instances:
        print("No running instances found")
        return

    if not yes and not dry_run:
        items = []
        for inst_type, ids in instances.items():
            items.append(f"{inst_type.value}: {', '.join(ids)}")
        print(f"This will stop instances: {'; '.join(items)}")
        response = input("Continue? [y/N] ")
        if response.lower() not in ("y", "yes"):
            print("Aborted")
            return

    if dry_run:
        for inst_type, ids in instances.items():
            print(f"[dry-run] Would undeploy {inst_type.value}: {', '.join(ids)}")
        return

    cfg = Config()
    image, tag = cfg.resolve_image_tag()

    def do_undeploy(inst_type: InstanceType, id_: str) -> None:
        unit = systemd.unit_name(inst_type.value, id_)
        try:
            systemd.stop(unit)
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
        except Exception as e:
            port = int(id_) if inst_type == InstanceType.WEB else 0
            db.record_deployment(
                cfg.db_path,
                image=image,
                tag=tag,
                action=f"undeploy-{inst_type.value}",
                port=port,
                success=False,
                notes=str(e),
            )
            raise

    for_each_instance(instances, delay, do_undeploy, "Undeploying")


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
):
    """Restart systemd unit(s) for instance(s).

    Does NOT regenerate quadlet - use 'redeploy' for that.
    Only restarts running instances; stopped instances are skipped.

    Examples:
        ots instances restart                       # Restart all running
        ots instances restart --web                 # Restart web instances
        ots instances restart --web 7043 7044       # Restart specific web
        ots instances restart --scheduler main      # Restart specific scheduler
    """
    itype = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=True)

    if not instances:
        print("No running instances found")
        return

    for inst_type, ids in instances.items():
        for id_ in ids:
            unit = systemd.unit_name(inst_type.value, id_)
            systemd.restart(unit)
            print(f"Restarted {unit}")

    hint = format_journalctl_hint(instances)
    if hint:
        print(f"\nView logs: {hint}")


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
                    ["systemctl", "enable", unit],
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
                    ["systemctl", "disable", unit],
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
):
    """Show systemd status for instance(s).

    Examples:
        ots instances status                        # Status of all configured
        ots instances status --web                  # Status of web instances
        ots instances status --web 7043 7044        # Status of specific web
        ots instances status --scheduler            # Status of scheduler instances
    """
    itype = resolve_instance_type(instance_type, web, worker, scheduler)
    instances = resolve_identifiers(identifiers, itype, running_only=False)

    if not instances:
        print("No configured instances found")
        return

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
