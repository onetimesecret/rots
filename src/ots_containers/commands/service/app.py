# src/ots_containers/commands/service/app.py

"""Service management commands for systemd template services.

Manages systemd template services like valkey-server@ and redis-server@
on Debian 13 systems. Uses package-provided templates rather than custom
unit files.
"""

import logging
import subprocess
from typing import Annotated

import cyclopts

from ..common import DryRun, Follow, JsonOutput, Lines, Yes
from ._helpers import (
    add_secrets_include,
    check_default_service_conflict,
    copy_default_config,
    create_secrets_file,
    ensure_data_dir,
    is_service_active,
    is_service_enabled,
    systemctl,
    update_config_value,
)
from .packages import get_package, list_packages

logger = logging.getLogger(__name__)

app = cyclopts.App(
    name=["service", "services"],
    help="Manage systemd template services (valkey, redis)",
)

# Type aliases for cyclopts annotations
Package = Annotated[str, cyclopts.Parameter(help="Package name (valkey, redis)")]
Instance = Annotated[str, cyclopts.Parameter(help="Instance identifier (usually port)")]
OptInstance = Annotated[str | None, cyclopts.Parameter(help="Instance identifier (optional)")]


@app.default
def list_all(json_output: JsonOutput = False):
    """List all service instances across all packages.

    Auto-discovers running and configured instances for all supported packages.

    Examples:
        ots service list
        ots service list --json
    """
    all_instances = []

    for pkg_name in list_packages():
        pkg = get_package(pkg_name)

        # Find running/enabled units matching the template
        pattern = f"{pkg.template}*"
        result = subprocess.run(
            [
                "systemctl",
                "list-units",
                "--type=service",
                "--all",
                pattern,
                "--no-pager",
                "--plain",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.stdout.strip():
            for line in result.stdout.splitlines():
                if pkg.template in line:
                    parts = line.split()
                    if parts:
                        unit_name = parts[0]
                        if "@" in unit_name and ".service" in unit_name:
                            instance = unit_name.split("@")[1].replace(".service", "")
                            active = is_service_active(unit_name)
                            enabled = is_service_enabled(unit_name)
                            config_exists = pkg.config_file(instance).exists()
                            all_instances.append(
                                {
                                    "package": pkg_name,
                                    "instance": instance,
                                    "unit": unit_name,
                                    "active": active,
                                    "enabled": enabled,
                                    "config_exists": config_exists,
                                }
                            )

    if json_output:
        import json

        print(json.dumps(all_instances, indent=2))
        return

    if not all_instances:
        print("No service instances found.")
        print()
        print("Available packages:")
        for name in list_packages():
            pkg = get_package(name)
            print(f"  {name:10} - {pkg.template}.service")
        print()
        print("Initialize with: ots service init <package> <instance>")
        return

    print("Service instances:")
    print("-" * 70)
    print(f"{'PACKAGE':<10} {'INSTANCE':<10} {'STATUS':<10} {'ENABLED':<10} {'CONFIG':<10}")
    print("-" * 70)

    for inst in all_instances:
        status = "active" if inst["active"] else "inactive"
        enabled = "enabled" if inst["enabled"] else "disabled"
        config = "ok" if inst["config_exists"] else "missing"
        print(
            f"{inst['package']:<10} {inst['instance']:<10} {status:<10} {enabled:<10} {config:<10}"
        )


@app.command
def init(
    package: Package,
    instance: Instance,
    *,
    port: Annotated[
        int | None,
        cyclopts.Parameter(
            name=["--port", "-p"],
            help="Port number",
        ),
    ] = None,
    bind: Annotated[
        str,
        cyclopts.Parameter(
            name=["--bind", "-b"],
            help="Bind address",
        ),
    ] = "127.0.0.1",
    no_secrets: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--no-secrets"],
            help="Skip secrets file creation",
        ),
    ] = False,
    start: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--start", "-s"],
            help="Start service after init",
        ),
    ] = True,
    enable: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--enable", "-e"],
            help="Enable service at boot",
        ),
    ] = True,
    force: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--force", "-f"],
            help="Overwrite existing config and recreate instance (not idempotent by default)",
        ),
    ] = False,
    dry_run: DryRun = False,
):
    """Initialize a new service instance.

    Creates config files, sets up directories, optionally starts service.
    Config is copy-on-write from package default to /etc/<pkg>/instances/.

    Idempotent by default: if the config already exists, prints a notice and
    skips all modifications. Use --force to overwrite the existing config.

    Examples:
        ots service init valkey 6379
        ots service init redis 6380 --port 6380 --bind 0.0.0.0
        ots service init valkey 6379 --dry-run
        ots service init valkey 6379 --force   # Overwrite existing config
    """
    pkg = get_package(package)
    if port is not None:
        port_num = port
    elif instance.isnumeric():
        port_num = int(instance)
    else:
        raise SystemExit(
            f"--port is required when instance name '{instance}' is not a port number.\n"
            f"Example: ots service init {package} {instance} --port 6379"
        )

    print(f"Initializing {pkg.name} instance '{instance}'")
    print(f"  Template: {pkg.template_unit}")
    print(f"  Port: {port_num}")
    print(f"  Bind: {bind}")
    print()

    if dry_run:
        config_exists = pkg.config_file(instance).exists()
        if config_exists and not force:
            print(f"[dry-run] Config already exists: {pkg.config_file(instance)}")
            print("[dry-run] Would skip (use --force to overwrite)")
        else:
            verb = "overwrite" if config_exists else "create"
            print(f"[dry-run] Would {verb}:")
            print(f"  Config: {pkg.config_file(instance)}")
            print(f"  Data:   {pkg.data_dir / instance}")
            if not no_secrets and pkg.secrets:
                print(f"  Secrets: {pkg.secrets_file(instance)}")
        return

    # Check for default service conflict
    check_default_service_conflict(pkg)

    # Step 1: Copy default config
    print(f"Creating config from {pkg.default_config}...")
    try:
        config_path = copy_default_config(pkg, instance)
        print(f"  Created: {config_path}")
    except FileExistsError:
        if not force:
            # Idempotent: skip all modifications when config already exists
            print(f"  Config already exists: {pkg.config_file(instance)}")
            print("  Skipping config modifications (use --force to overwrite)")
            print()
            print(f"Instance '{instance}' already configured.")
            print(f"  Status: ots service status {package} {instance}")
            return
        # --force: delete and recreate from package default
        import os

        os.unlink(pkg.config_file(instance))
        print("  Removed existing config (--force)")
        try:
            config_path = copy_default_config(pkg, instance)
            print(f"  Recreated: {config_path}")
        except FileNotFoundError as e:
            print(f"  ERROR: {e}")
            raise SystemExit(1)
    except FileNotFoundError as e:
        print(f"  ERROR: {e}")
        raise SystemExit(1)

    # Step 2: Update port and bind in config
    print("Updating config values...")
    update_config_value(config_path, pkg.port_config_key, str(port_num), pkg)
    update_config_value(config_path, pkg.bind_config_key, bind, pkg)

    # Step 3: Set data directory
    data_dir = ensure_data_dir(pkg, instance)
    print(f"  Data dir: {data_dir}")
    # Update config to point to instance-specific data dir
    update_config_value(config_path, "dir", str(data_dir), pkg)

    # Step 4: Create secrets file (if applicable)
    if not no_secrets and pkg.secrets:
        print("Creating secrets file...")
        secrets_path = create_secrets_file(pkg, instance)
        if secrets_path:
            print(f"  Created: {secrets_path}")
            add_secrets_include(config_path, secrets_path, pkg)
            print("  Added include directive to config")
    else:
        print("Skipping secrets file (--no-secrets or package has no secrets config)")

    # Step 5: Enable service
    unit = pkg.instance_unit(instance)
    if enable:
        print(f"Enabling {unit}...")
        try:
            systemctl("enable", unit)
            print("  Enabled")
        except subprocess.CalledProcessError as e:
            print(f"  WARNING: Could not enable: {e.stderr}")

    # Step 6: Start service
    if start:
        print(f"Starting {unit}...")
        try:
            systemctl("start", unit)
            print("  Started")
        except subprocess.CalledProcessError as e:
            print(f"  ERROR: Could not start: {e.stderr}")
            raise SystemExit(1)

    print()
    print(f"Instance '{instance}' initialized successfully!")
    print(f"  Config: {config_path}")
    print(f"  Data:   {data_dir}")
    print(f"  Status: ots service status {package} {instance}")


@app.command
def enable(package: Package, instance: Instance):
    """Enable a service instance to start at boot.

    Examples:
        ots service enable valkey 6379
    """
    pkg = get_package(package)
    unit = pkg.instance_unit(instance)

    print(f"Enabling {unit}...")
    try:
        systemctl("enable", unit)
        print("Enabled")
    except subprocess.CalledProcessError as e:
        print(f"ERROR: {e.stderr}")
        raise SystemExit(1)


@app.command
def disable(
    package: Package,
    instance: Instance,
    yes: Yes = False,
):
    """Disable a service instance and stop it.

    Examples:
        ots service disable valkey 6379
        ots service disable valkey 6379 -y
    """
    pkg = get_package(package)
    unit = pkg.instance_unit(instance)

    if not yes:
        print(f"This will disable {unit}")
        response = input("Continue? [y/N] ")
        if response.lower() not in ("y", "yes"):
            print("Aborted")
            return

    print(f"Stopping {unit}...")
    try:
        systemctl("stop", unit, check=False)
    except subprocess.CalledProcessError:
        pass

    print(f"Disabling {unit}...")
    try:
        systemctl("disable", unit)
        print("Disabled")
    except subprocess.CalledProcessError as e:
        print(f"ERROR: {e.stderr}")
        raise SystemExit(1)


@app.command
def status(package: Package, instance: OptInstance = None):
    """Show status of service instance(s).

    Examples:
        ots service status valkey 6379
        ots service status valkey  # Shows all valkey instances
    """
    pkg = get_package(package)

    if instance:
        unit = pkg.instance_unit(instance)
        result = systemctl("status", unit, check=False)
        print(result.stdout)
        if result.stderr:
            print(result.stderr)
    else:
        # Show all instances of this template
        pattern = f"{pkg.template}*"
        result = subprocess.run(
            [
                "systemctl",
                "list-units",
                "--type=service",
                pattern,
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        print(result.stdout)


@app.command
def start(package: Package, instance: Instance):
    """Start a service instance.

    Examples:
        ots service start valkey 6379
    """
    pkg = get_package(package)
    unit = pkg.instance_unit(instance)

    print(f"Starting {unit}...")
    try:
        systemctl("start", unit)
        print("Started")
    except subprocess.CalledProcessError as e:
        print(f"ERROR: {e.stderr}")
        raise SystemExit(1)


@app.command
def stop(package: Package, instance: Instance):
    """Stop a service instance.

    Examples:
        ots service stop valkey 6379
    """
    pkg = get_package(package)
    unit = pkg.instance_unit(instance)

    print(f"Stopping {unit}...")
    try:
        systemctl("stop", unit)
        print("Stopped")
    except subprocess.CalledProcessError as e:
        print(f"ERROR: {e.stderr}")
        raise SystemExit(1)


@app.command
def restart(package: Package, instance: Instance):
    """Restart a service instance.

    Examples:
        ots service restart valkey 6379
    """
    pkg = get_package(package)
    unit = pkg.instance_unit(instance)

    print(f"Restarting {unit}...")
    try:
        systemctl("restart", unit)
        print("Restarted")
    except subprocess.CalledProcessError as e:
        print(f"ERROR: {e.stderr}")
        raise SystemExit(1)


@app.command
def logs(
    package: Package,
    instance: Instance,
    *,
    follow: Follow = False,
    lines: Lines = 50,
):
    """Show logs for a service instance.

    Examples:
        ots service logs valkey 6379
        ots service logs valkey 6379 -f
        ots service logs valkey 6379 -n 100
    """
    pkg = get_package(package)
    unit = pkg.instance_unit(instance)

    cmd = ["journalctl", "-u", unit, "-n", str(lines), "--no-pager"]
    if follow:
        cmd.append("-f")

    subprocess.run(cmd)


@app.command(name="list")
def list_instances(
    package: Package,
    json_output: JsonOutput = False,
):
    """List all instances of a service package.

    Auto-discovers running and enabled instances via systemctl.

    Examples:
        ots service list valkey
        ots service list valkey --json
    """
    pkg = get_package(package)
    instances = []

    # Find running/enabled units matching the template
    pattern = f"{pkg.template}*"
    result = subprocess.run(
        [
            "systemctl",
            "list-units",
            "--type=service",
            "--all",
            pattern,
            "--no-pager",
            "--plain",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    if result.stdout.strip():
        # Parse output to extract instance names
        for line in result.stdout.splitlines():
            if pkg.template in line:
                parts = line.split()
                if parts:
                    unit_name = parts[0]
                    # Extract instance from unit name
                    # e.g., valkey-server@6379.service -> 6379
                    if "@" in unit_name and ".service" in unit_name:
                        instance = unit_name.split("@")[1].replace(".service", "")
                        active = is_service_active(unit_name)
                        enabled = is_service_enabled(unit_name)
                        config_exists = pkg.config_file(instance).exists()
                        instances.append(
                            {
                                "instance": instance,
                                "unit": unit_name,
                                "active": active,
                                "enabled": enabled,
                                "config_exists": config_exists,
                            }
                        )

    if json_output:
        import json

        print(json.dumps(instances, indent=2))
        return

    print(f"Instances of {pkg.name} ({pkg.template}):")
    print("-" * 50)

    if instances:
        for inst in instances:
            active = "active" if inst["active"] else "inactive"
            enabled = "enabled" if inst["enabled"] else "disabled"
            config_status = "config ok" if inst["config_exists"] else "no config"
            print(f"  {inst['instance']:10} {active:10} {enabled:10} {config_status}")

    # Also check for config files that might not have running services
    config_dir = pkg.instances_dir if pkg.use_instances_subdir else pkg.config_dir
    if config_dir.exists():
        print()
        print("Config files in config directory:")
        for conf in config_dir.glob("*.conf"):
            # Extract instance from filename based on pattern
            # For instances subdir: "6380.conf" -> "6380"
            # For direct configs: "valkey-6380.conf" -> "6380"
            if pkg.use_instances_subdir:
                instance = conf.stem
            else:
                # Remove package-specific prefix to get instance
                # e.g., "valkey-6380.conf" -> "6380"
                instance = conf.stem.replace(f"{pkg.name}-", "")

            unit = pkg.instance_unit(instance)
            active = "active" if is_service_active(unit) else "inactive"
            print(f"  {conf.name:30} -> {active}")
