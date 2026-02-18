# src/ots_containers/commands/image/app.py

"""Image management commands for OTS containers.

Supports pulling from multiple registries:
  - ghcr.io/onetimesecret/onetimesecret (default)
  - docker.io/onetimesecret/onetimesecret
  - registry.digitalocean.com/<registry>/onetimesecret
  - Any OCI-compliant registry

Maintains CURRENT and ROLLBACK aliases in SQLite database for:
  - Deterministic deployments
  - Consecutive rollback support (history-based, not env-var based)
  - Full audit trail
"""

from pathlib import Path
from typing import Annotated

import cyclopts

from ots_containers import db
from ots_containers.config import Config
from ots_containers.podman import podman

from ..common import JsonOutput, Lines, Quiet, Yes

app = cyclopts.App(
    name=["image", "images"],
    help="Manage container images (pull, aliases, rollback).",
)


@app.command
def pull(
    tag: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--tag", "-t"],
            help="Image tag to pull (default: from TAG env var)",
        ),
    ] = None,
    image: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--image", "-i"],
            help="Full image path (default: from IMAGE env var)",
        ),
    ] = None,
    set_as_current: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--current", "-c"],
            help="Set as CURRENT alias after pulling",
        ),
    ] = False,
    private: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--private", "-P"],
            help="Pull from private registry (uses configured OTS_REGISTRY)",
        ),
    ] = False,
    platform: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--platform", "-p"],
            help="Target platform (e.g., linux/amd64, linux/arm64)",
        ),
    ] = None,
    quiet: Quiet = False,
):
    """Pull a container image from registry.

    Examples:
        ots image pull --tag v0.23.0
        ots image pull --tag latest --current
        TAG=dev ots image pull                  # Use TAG env var
        ots image pull --tag v0.23.0 --image docker.io/onetimesecret/onetimesecret
        ots image pull --tag v0.23.0 --private  # Pull from private registry
        ots image pull --tag dev --platform linux/amd64  # Pull amd64 on Apple Silicon
    """
    cfg = Config()

    # Resolve image and tag from env vars if not provided
    resolved_image = image or cfg.image
    resolved_tag = tag or cfg.tag
    if not resolved_tag:
        print("Error: --tag is required (or set TAG env var)")
        raise SystemExit(1)

    # Use private registry if requested
    if private:
        if not cfg.private_image:
            print("Error: --private requires OTS_REGISTRY env var to be set")
            raise SystemExit(1)
        resolved_image = cfg.private_image

    full_image = f"{resolved_image}:{resolved_tag}"

    if not quiet:
        print(f"Pulling {full_image}...")

    try:
        # Use auth file for authenticated registries
        pull_kwargs = {
            "authfile": str(cfg.registry_auth_file),
            "check": True,
            "capture_output": True,
            "text": True,
        }
        if platform:
            pull_kwargs["platform"] = platform

        podman.pull(full_image, **pull_kwargs)
        if not quiet:
            print(f"Successfully pulled {full_image}")
    except Exception as e:
        print(f"Failed to pull {full_image}: {e}")
        db.record_deployment(
            cfg.db_path,
            image=resolved_image,
            tag=resolved_tag,
            action="pull",
            success=False,
            notes=str(e),
        )
        raise SystemExit(1)

    # Record successful pull
    db.record_deployment(
        cfg.db_path,
        image=resolved_image,
        tag=resolved_tag,
        action="pull",
        success=True,
    )

    # Set as current if requested
    if set_as_current:
        # Tag in podman before updating the database
        source_ref = f"{resolved_image}:{resolved_tag}"
        current_alias = db.get_current_image(cfg.db_path)
        try:
            podman.tag(
                source_ref,
                f"{resolved_image}:current",
                check=True,
                capture_output=True,
                text=True,
            )
            if current_alias:
                prev_image, prev_tag = current_alias
                podman.tag(
                    f"{prev_image}:{prev_tag}",
                    f"{prev_image}:rollback",
                    check=True,
                    capture_output=True,
                    text=True,
                )
        except Exception as e:
            print(f"Warning: podman tag failed ({e}), aliases updated in DB only")

        previous = db.set_current(cfg.db_path, resolved_image, resolved_tag)
        if not quiet:
            if previous:
                print(f"Set CURRENT to {resolved_tag} (previous: {previous})")
            else:
                print(f"Set CURRENT to {resolved_tag}")


@app.default
@app.command(name="list")
def ls(
    all_tags: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--all", "-a"],
            help="Show all images, not just onetimesecret",
        ),
    ] = False,
    json_output: JsonOutput = False,
):
    """List local container images.

    Shows images available locally (already pulled).

    Examples:
        ots image list
        ots image list --all
        ots image list --json
    """
    cfg = Config()

    if json_output:
        import json as json_module

        result = podman.images(
            format="json",
            capture_output=True,
            text=True,
        )
        # Filter if not all_tags
        if not all_tags:
            images = json_module.loads(result.stdout)
            images = [
                img
                for img in images
                if any("onetimesecret" in name for name in img.get("Names", []))
            ]
            print(json_module.dumps(images, indent=2))
        else:
            print(result.stdout)
        return

    if all_tags:
        result = podman.images(
            format="table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}\t{{.Created}}",
            capture_output=True,
            text=True,
        )
    else:
        result = podman.images(
            filter="reference=*onetimesecret*",
            format="table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}\t{{.Created}}",
            capture_output=True,
            text=True,
        )

    print("Local images:")
    print(result.stdout)

    # Show current aliases
    aliases = db.get_all_aliases(cfg.db_path)
    if aliases:
        print("\nAliases:")
        for alias in aliases:
            print(f"  {alias.alias}: {alias.image}:{alias.tag} (set {alias.set_at})")


@app.command(name="list-remote")
def list_remote(
    registry: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--registry", "-r"],
            help="Registry URL (or set OTS_REGISTRY env var)",
        ),
    ] = None,
    image: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--image", "-i"],
            help="Image name to list tags for (default: basename of IMAGE env var)",
        ),
    ] = None,
    quiet: Quiet = False,
):
    """List image tags on a remote registry.

    Uses skopeo to query the registry API. Requires skopeo to be installed.

    Examples:
        ots image list-remote
        ots image list-remote --registry ghcr.io/onetimesecret
        OTS_REGISTRY=registry.example.com ots image list-remote
    """
    import json
    import shutil
    import subprocess

    cfg = Config()

    # Check for skopeo
    if not shutil.which("skopeo"):
        print("Error: skopeo not found. Install with: brew install skopeo (macOS)")
        raise SystemExit(1)

    # Resolve registry
    reg = registry or cfg.registry
    if not reg:
        print("Error: Registry URL required. Use --registry or set OTS_REGISTRY env var")
        raise SystemExit(1)

    # Build skopeo command
    resolved_image = image or cfg.image.split("/")[-1]
    image_ref = f"docker://{reg}/{resolved_image}"
    cmd = [
        "skopeo",
        "list-tags",
        "--authfile",
        str(cfg.registry_auth_file),
        image_ref,
    ]

    if not quiet:
        print(f"$ {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        tags = data.get("Tags", [])

        if not quiet:
            print(f"\nTags for {reg}/{image} ({len(tags)} total):")

        # Sort tags (newest-looking first)
        tags_sorted = sorted(tags, reverse=True)
        for tag in tags_sorted:
            print(f"  {tag}")

    except subprocess.CalledProcessError as e:
        print(f"Failed to list tags: {e.stderr}")
        raise SystemExit(1)
    except json.JSONDecodeError as e:
        print(f"Failed to parse response: {e}")
        raise SystemExit(1)


@app.command(name="set-current")
def set_current(
    tag: Annotated[
        str,
        cyclopts.Parameter(help="Tag to set as CURRENT"),
    ],
    image: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--image", "-i"],
            help="Full image path (default: from IMAGE env var)",
        ),
    ] = None,
):
    """Set the CURRENT image alias.

    The previous CURRENT becomes ROLLBACK automatically.
    Tags the image in the local podman store before updating the database,
    so the podman image store always reflects the alias state.

    Examples:
        ots image set-current v0.23.0
        ots image set-current latest --image docker.io/onetimesecret/onetimesecret
    """
    cfg = Config()

    resolved_image = image or cfg.image
    source_ref = f"{resolved_image}:{tag}"

    # Verify the source image exists locally before proceeding
    try:
        podman.image.inspect(source_ref, check=True, capture_output=True, text=True)
    except Exception:
        print(f"Image not found locally: {source_ref}")
        print(f"Pull it first: ots image pull --tag {tag}")
        raise SystemExit(1)

    # Tag in podman before updating the database. If podman tag fails,
    # the database remains unchanged.
    current_alias = db.get_current_image(cfg.db_path)
    try:
        podman.tag(
            source_ref,
            f"{resolved_image}:current",
            check=True,
            capture_output=True,
            text=True,
        )
        if current_alias:
            prev_image, prev_tag = current_alias
            podman.tag(
                f"{prev_image}:{prev_tag}",
                f"{prev_image}:rollback",
                check=True,
                capture_output=True,
                text=True,
            )
    except Exception as e:
        print(f"Failed to tag image in podman: {e}")
        raise SystemExit(1)

    previous = db.set_current(cfg.db_path, resolved_image, tag)

    print(f"CURRENT set to {resolved_image}:{tag}")
    if previous:
        print(f"ROLLBACK set to previous: {previous}")
    else:
        print("(No previous CURRENT to roll back to)")


@app.command
def rollback():
    """Roll back to the previous deployment.

    Uses the deployment timeline to find the previous successful deployment,
    NOT environment variables. This ensures consecutive rollbacks work
    correctly by walking back through history.

    The current CURRENT becomes ROLLBACK, and the previous deployment
    becomes the new CURRENT.

    Examples:
        ots image rollback
    """
    cfg = Config()

    # Show current state
    current = db.get_current_image(cfg.db_path)
    if current:
        print(f"Current: {current[0]}:{current[1]}")
    else:
        print("No CURRENT alias set")
        raise SystemExit(1)

    # Get previous tags from timeline for context
    previous = db.get_previous_tags(cfg.db_path, limit=5)
    if len(previous) < 2:
        print("No previous deployment to roll back to")
        raise SystemExit(1)

    print(f"\nRolling back to: {previous[1][0]}:{previous[1][1]}")
    print(f"  (last deployed: {previous[1][2]})")

    result = db.rollback(cfg.db_path)
    if result:
        image, tag = result
        # Update podman tags to reflect the new alias state
        try:
            podman.tag(
                f"{image}:{tag}",
                f"{image}:current",
                check=True,
                capture_output=True,
                text=True,
            )
            podman.tag(
                f"{current[0]}:{current[1]}",
                f"{current[0]}:rollback",
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception as e:
            print(f"Warning: podman tag failed ({e}), aliases updated in DB only")
        print("\nRollback complete!")
        print(f"  CURRENT: {image}:{tag}")
        print(f"  ROLLBACK: {current[0]}:{current[1]}")
        print("\nTo apply: ots instance redeploy")
    else:
        print("Rollback failed - no previous deployment found")
        raise SystemExit(1)


@app.command
def history(
    limit: Lines = 20,
    port: Annotated[
        int | None,
        cyclopts.Parameter(
            name=["--port", "-p"],
            help="Filter by port",
        ),
    ] = None,
    json_output: JsonOutput = False,
):
    """Show deployment timeline.

    The timeline is an append-only audit trail of all deployment actions.

    Examples:
        ots image history
        ots image history -n 50
        ots image history -p 7043
        ots image history --json
    """
    cfg = Config()

    deployments = db.get_deployments(cfg.db_path, limit=limit, port=port)

    if not deployments:
        print("No deployments recorded yet.")
        return

    if json_output:
        import json

        data = [
            {
                "id": d.id,
                "timestamp": d.timestamp,
                "port": d.port,
                "action": d.action,
                "image": d.image,
                "tag": d.tag,
                "success": d.success,
                "notes": d.notes,
            }
            for d in deployments
        ]
        print(json.dumps(data, indent=2))
        return

    # Show aliases first
    aliases = db.get_all_aliases(cfg.db_path)
    if aliases:
        print("Current aliases:")
        for alias in aliases:
            print(f"  {alias.alias}: {alias.image}:{alias.tag}")
        print()

    # Show deployment history
    if port:
        print(f"Deployment history (port {port}):")
    else:
        print("Deployment history:")
    print("-" * 80)
    print(f"{'ID':>4}  {'Timestamp':<20}  {'Port':>5}  {'Action':<12}  {'Tag':<15}  {'Status'}")
    print("-" * 80)

    for d in deployments:
        port_str = str(d.port) if d.port else "-"
        status = "OK" if d.success else "FAIL"
        # Truncate tag if too long
        tag_display = d.tag[:15] if len(d.tag) <= 15 else d.tag[:12] + "..."
        line = f"{d.id:>4}  {d.timestamp:<20}  {port_str:>5}  {d.action:<12}  {tag_display:<15}"
        print(f"{line}  {status}")

    print("-" * 80)
    print(f"Showing {len(deployments)} of {limit} max entries")


@app.command
def aliases(json_output: JsonOutput = False):
    """Show current image aliases (CURRENT, ROLLBACK).

    Examples:
        ots image aliases
        ots image aliases --json
    """
    cfg = Config()

    aliases_list = db.get_all_aliases(cfg.db_path)

    if json_output:
        import json

        data = [
            {
                "alias": a.alias,
                "image": a.image,
                "tag": a.tag,
                "set_at": a.set_at,
            }
            for a in aliases_list
        ]
        print(json.dumps(data, indent=2))
        return

    if not aliases_list:
        print("No aliases configured.")
        print("\nSet an alias with: ots image set-current <tag>")
        return

    print("Image aliases:")
    print("-" * 60)
    for alias in aliases_list:
        print(f"  {alias.alias}:")
        print(f"    Image: {alias.image}:{alias.tag}")
        print(f"    Set:   {alias.set_at}")
    print("-" * 60)

    # Show what commands would resolve to
    print("\nResolution:")
    current = db.get_current_image(cfg.db_path)
    if current:
        print(f"  TAG=current  -> {current[0]}:{current[1]}")

    rollback_img = db.get_rollback_image(cfg.db_path)
    if rollback_img:
        print(f"  TAG=rollback -> {rollback_img[0]}:{rollback_img[1]}")


# --- Private Registry Commands ---


@app.command
def login(
    registry: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--registry", "-r"],
            help="Registry URL (or set OTS_REGISTRY env var)",
        ),
    ] = None,
    username: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--username", "-u"],
            help="Registry username (or set OTS_REGISTRY_USER env var)",
        ),
    ] = None,
    password: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--password"],
            help="Registry password (env: OTS_REGISTRY_PASSWORD, or --password-stdin)",
        ),
    ] = None,
    password_stdin: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--password-stdin"],
            help="Read password from stdin",
        ),
    ] = False,
):
    """Authenticate with a container registry.

    Uses HTTP basic auth. Credentials can be provided via:
      - Command line arguments
      - Environment variables: OTS_REGISTRY_USER, OTS_REGISTRY_PASSWORD
      - Interactive prompt (if not provided)

    Examples:
        ots image login --registry registry.example.com
        ots image login --registry registry.example.com --username admin --password-stdin
        OTS_REGISTRY=registry.example.com ots image login
    """
    import getpass
    import os
    import sys

    cfg = Config()

    # Resolve registry from arg or config
    reg = registry or cfg.registry
    if not reg:
        print("Error: Registry URL required. Use --registry or set OTS_REGISTRY env var")
        raise SystemExit(1)

    # Resolve credentials from args, env vars, or prompt
    user = username or os.environ.get("OTS_REGISTRY_USER")
    pw = password or os.environ.get("OTS_REGISTRY_PASSWORD")

    if not user:
        user = input(f"Username for {reg}: ")

    if password_stdin:
        pw = sys.stdin.read().strip()
    elif not pw:
        pw = getpass.getpass(f"Password for {user}@{reg}: ")

    if not user or not pw:
        print("Error: Username and password are required")
        raise SystemExit(1)

    print(f"Logging in to {reg}...")

    try:
        podman.login(
            reg,
            username=user,
            password=pw,
            authfile=str(cfg.registry_auth_file),
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"Login successful: {reg}")
    except Exception as e:
        print(f"Login failed: {e}")
        raise SystemExit(1)


@app.command
def push(
    tag: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--tag", "-t"],
            help="Image tag to push (default: TAG env var)",
        ),
    ] = None,
    source_image: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--source", "-s"],
            help="Source image to push (default: IMAGE env var or onetimesecret)",
        ),
    ] = None,
    registry: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--registry", "-r"],
            help="Target registry URL (or set OTS_REGISTRY env var)",
        ),
    ] = None,
    quiet: Quiet = False,
):
    """Push an image to a private registry.

    Tags the source image for the target registry and pushes it.
    Requires prior authentication via 'ots image login'.

    Examples:
        ots image push --tag v0.23.0
        ots image push --tag v0.23.0 --registry registry.example.com
        ots image push --tag latest --source ghcr.io/onetimesecret/onetimesecret
        OTS_REGISTRY=registry.example.com ots image push --tag v0.23.0
    """
    cfg = Config()

    # Resolve registry from arg or config
    reg = registry or cfg.registry
    if not reg:
        print("Error: Registry URL required. Use --registry or set OTS_REGISTRY env var")
        raise SystemExit(1)

    # Resolve tag and source image from args or env vars
    resolved_tag = tag or cfg.tag
    if not resolved_tag:
        print("Error: Tag required. Use --tag or set TAG env var")
        raise SystemExit(1)
    src = source_image or cfg.image
    # Derive target image name from source basename (strip host prefix if present)
    src_basename = src.split("/")[-1]
    source_full = f"{src}:{resolved_tag}"
    target_full = f"{reg}/{src_basename}:{resolved_tag}"

    if not quiet:
        print(f"Tagging {source_full} -> {target_full}")

    # Tag the image for the target registry
    try:
        podman.tag(source_full, target_full, check=True, capture_output=True, text=True)
    except Exception as e:
        print(f"Failed to tag image: {e}")
        raise SystemExit(1)

    if not quiet:
        print(f"Pushing {target_full}...")

    # Push to registry
    try:
        podman.push(
            target_full,
            authfile=str(cfg.registry_auth_file),
            check=True,
            capture_output=True,
            text=True,
        )
        if not quiet:
            print(f"Successfully pushed {target_full}")
    except Exception as e:
        print(f"Failed to push {target_full}: {e}")
        raise SystemExit(1)

    # Record the push action
    db.record_deployment(
        cfg.db_path,
        image=f"{reg}/{src_basename}",
        tag=resolved_tag,
        action="push",
        success=True,
    )


@app.command
def logout(
    registry: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--registry", "-r"],
            help="Registry URL (or set OTS_REGISTRY env var)",
        ),
    ] = None,
):
    """Remove authentication for a container registry.

    Examples:
        ots image logout --registry registry.example.com
        OTS_REGISTRY=registry.example.com ots image logout
    """
    cfg = Config()

    # Resolve registry from arg or config
    reg = registry or cfg.registry
    if not reg:
        print("Error: Registry URL required. Use --registry or set OTS_REGISTRY env var")
        raise SystemExit(1)

    print(f"Logging out from {reg}...")

    try:
        podman.logout(
            reg,
            authfile=str(cfg.registry_auth_file),
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"Logged out from {reg}")
    except Exception as e:
        print(f"Logout failed: {e}")


@app.command
def rm(
    tags: Annotated[
        tuple[str, ...],
        cyclopts.Parameter(help="Image tags to remove"),
    ],
    force: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--force", "-f"],
            help="Force removal even if image is in use",
        ),
    ] = False,
    yes: Yes = False,
):
    """Remove local image(s) by tag.

    Examples:
        ots image rm v0.22.0
        ots image rm v0.21.0 v0.20.0 -y
        ots image rm v0.22.0 --force
    """
    if not tags:
        print("Error: At least one tag is required")
        raise SystemExit(1)

    if not yes:
        print(f"This will remove images: {', '.join(tags)}")
        response = input("Continue? [y/N] ")
        if response.lower() not in ("y", "yes"):
            print("Aborted")
            return

    cfg = Config()

    for tag in tags:
        # Try common image patterns, including configured image
        image_basename = cfg.image.split("/")[-1]
        images_to_try = [
            f"{image_basename}:{tag}",
            f"{cfg.image}:{tag}",
            f"localhost/{image_basename}:{tag}",
        ]
        if cfg.private_image:
            images_to_try.append(f"{cfg.private_image}:{tag}")

        removed = False
        for image in images_to_try:
            try:
                kwargs = {"check": True, "capture_output": True, "text": True}
                if force:
                    kwargs["force"] = True
                podman.rmi(image, **kwargs)
                print(f"Removed {image}")
                removed = True
                break
            except Exception:
                continue

        if not removed:
            print(f"Image not found: {tag}")


@app.command
def prune(
    all_images: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--all", "-a"],
            help="Remove all unused images, not just dangling",
        ),
    ] = False,
    yes: Yes = False,
):
    """Remove unused images.

    By default removes dangling images (untagged). Use --all to remove
    all unused images.

    Examples:
        ots image prune
        ots image prune --all
        ots image prune -a -y
    """
    if not yes:
        if all_images:
            print("This will remove all unused images")
        else:
            print("This will remove dangling (untagged) images")
        response = input("Continue? [y/N] ")
        if response.lower() not in ("y", "yes"):
            print("Aborted")
            return

    try:
        kwargs = {"check": True, "capture_output": True, "text": True}
        if all_images:
            kwargs["all"] = True
        result = podman.image.prune(**kwargs)
        print("Pruned images:")
        print(result.stdout)
    except Exception as e:
        print(f"Prune failed: {e}")
        raise SystemExit(1)


# --- Build Commands ---


def _is_dev_version(version: str) -> bool:
    """Check if version is a development placeholder.

    Returns True for versions that should include git hash:
    - Starts with 0.0.0
    - Ends with -rc0, -dev, -alpha, -beta
    """
    import re

    if re.match(r"^0\.0\.0", version):
        return True
    if re.search(r"-(rc0|dev|alpha|beta)$", version):
        return True
    return False


def _get_git_hash(project_dir: Path, required: bool = True) -> str | None:
    """Get short git commit hash from project directory, with dirty indicator.

    Returns an 8-char hash with '*' suffix if there are uncommitted changes.

    Args:
        project_dir: Path to the git repository
        required: If True, raise SystemExit on failure. If False, return None.

    Returns:
        Git hash (e.g., 'a1b2c3d4' or 'a1b2c3d4*' if dirty),
        or None if not available and required=False
    """
    import subprocess

    try:
        # Get the short commit hash
        result = subprocess.run(
            ["git", "rev-parse", "--short=8", "HEAD"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        commit_hash = result.stdout.strip()

        # Check for dirty working tree (uncommitted or untracked changes)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        dirty = "*" if status.stdout.strip() else ""

        return f"{commit_hash}{dirty}"
    except subprocess.CalledProcessError as e:
        if required:
            raise SystemExit(f"Failed to get git hash: {e.stderr}") from e
        return None


def _read_package_version(project_dir: Path) -> str:
    """Read version from package.json in project directory."""
    import json

    package_json = project_dir / "package.json"
    if not package_json.exists():
        raise SystemExit(f"package.json not found in {project_dir}")

    try:
        with package_json.open() as f:
            data = json.load(f)
        version = data.get("version")
        if not version:
            raise SystemExit("No 'version' field in package.json")
        return version
    except json.JSONDecodeError as e:
        raise SystemExit(f"Invalid package.json: {e}") from e


def _determine_build_tag(project_dir: Path, override_tag: str | None) -> str:
    """Determine the build tag based on version and git state.

    Logic:
    1. If override_tag provided, use it as-is
    2. Read version from package.json
    3. If version is a dev placeholder (0.0.0*, -rc0, -dev, -alpha, -beta):
       - Append git hash: v{version}-{hash}
    4. Otherwise use version directly: v{version}
    """
    if override_tag:
        return override_tag

    version = _read_package_version(project_dir)

    if _is_dev_version(version):
        git_hash = _get_git_hash(project_dir)
        return f"v{version}-{git_hash}"
    else:
        return f"v{version}"


def _validate_project_dir(project_dir: Path, skip_dockerfile_check: bool = False) -> None:
    """Validate that project directory has required build files."""
    if not project_dir.is_dir():
        raise SystemExit(f"Project directory not found: {project_dir}")

    if not skip_dockerfile_check:
        has_containerfile = (project_dir / "Containerfile").exists()
        has_dockerfile = (project_dir / "Dockerfile").exists()

        if not has_containerfile and not has_dockerfile:
            raise SystemExit(f"No Containerfile or Dockerfile found in {project_dir}")

    if not (project_dir / "package.json").exists():
        raise SystemExit(f"No package.json found in {project_dir}")


@app.command
def build(
    project_dir: Annotated[
        Path | None,
        cyclopts.Parameter(
            name=["--project-dir", "-d"],
            help="Path to onetimesecret checkout (default: current directory)",
        ),
    ] = None,
    dockerfile: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--dockerfile", "-f"],
            help="Path to Dockerfile relative to project dir (auto-detects by default)",
        ),
    ] = None,
    target: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--target"],
            help="Build target stage for multi-stage builds (e.g., 'final-s6')",
        ),
    ] = None,
    suffix: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--suffix"],
            help="Image name suffix (e.g., '-lite', '-s6')",
        ),
    ] = None,
    platform: Annotated[
        str,
        cyclopts.Parameter(
            name=["--platform"],
            help="Build platforms, comma-separated",
        ),
    ] = "linux/amd64,linux/arm64",
    push: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--push"],
            help="Push to registry after building",
        ),
    ] = False,
    registry: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--registry", "-r"],
            help="Override registry URL (or OTS_REGISTRY env var)",
        ),
    ] = None,
    tag: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--tag", "-t"],
            help="Override version tag (auto-detected from package.json)",
        ),
    ] = None,
    quiet: Quiet = False,
):
    """Build container image from onetimesecret source.

    Automatically determines version tag from package.json. For development
    versions (0.0.0, -rc0, -dev, -alpha, -beta), appends git hash.

    Examples:
        # Standard build
        ots image build --project-dir ~/src/onetimesecret

        # Build lite variant
        ots image build -d . -f docker/variants/lite.dockerfile --suffix -lite

        # Build s6 multi-process variant
        ots image build -d . --target final-s6 --suffix -s6

        # Build and push to registry
        ots image build -d . --push --registry registry.example.com
    """
    cfg = Config()

    # Resolve project directory
    proj_dir = Path(project_dir) if project_dir else Path.cwd()
    proj_dir = proj_dir.resolve()

    # Validate project structure (skip dockerfile check if custom dockerfile specified)
    _validate_project_dir(proj_dir, skip_dockerfile_check=dockerfile is not None)

    # Resolve dockerfile path
    if dockerfile:
        dockerfile_path = proj_dir / dockerfile
        if not dockerfile_path.exists():
            raise SystemExit(f"Dockerfile not found: {dockerfile_path}")
    else:
        dockerfile_path = None  # Let podman auto-detect

    # Determine build tag
    build_tag = _determine_build_tag(proj_dir, tag)

    # Get git hash and version for build args (used by Dockerfile)
    git_hash = _get_git_hash(proj_dir, required=False)
    pkg_version = _read_package_version(proj_dir)

    # Image name with optional suffix
    image_name = f"onetimesecret{suffix or ''}"
    local_image = f"{image_name}:{build_tag}"

    if not quiet:
        print(f"Building {local_image}")
        print(f"  Project: {proj_dir}")
        if dockerfile:
            print(f"  Dockerfile: {dockerfile}")
        if target:
            print(f"  Target: {target}")
        print(f"  Platform: {platform}")
        print(f"  Version: {pkg_version}")
        print(f"  Commit: {git_hash or 'N/A (no git)'}")

    # Build the image
    try:
        # Pass VERSION and COMMIT_HASH for the Dockerfile to embed in the image
        build_args = [f"VERSION={pkg_version}"]
        if git_hash:
            build_args.append(f"COMMIT_HASH={git_hash}")

        # Build kwargs
        build_kwargs: dict = {
            "platform": platform,
            "tag": local_image,
            "build_arg": build_args,
            "check": True,
            "capture_output": quiet,
            "text": True,
        }

        # Add optional dockerfile path
        if dockerfile_path:
            build_kwargs["file"] = str(dockerfile_path)

        # Add optional target stage
        if target:
            build_kwargs["target"] = target

        podman.buildx.build(str(proj_dir), **build_kwargs)

        if not quiet:
            print(f"Successfully built {local_image}")
    except Exception as e:
        print(f"Build failed: {e}")
        raise SystemExit(1)

    # Record the build action
    db.record_deployment(
        cfg.db_path,
        image=image_name,
        tag=build_tag,
        action="build",
        success=True,
        notes=f"target={target}" if target else None,
    )

    # Push if requested
    if push:
        # Resolve registry from arg or config
        reg = registry or cfg.registry
        if not reg:
            print("Error: --push requires --registry or OTS_REGISTRY env var")
            raise SystemExit(1)

        target_image = f"{reg}/{image_name}:{build_tag}"

        if not quiet:
            print(f"Tagging {local_image} -> {target_image}")

        # Tag for registry
        try:
            podman.tag(
                local_image,
                target_image,
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception as e:
            print(f"Failed to tag image: {e}")
            raise SystemExit(1)

        if not quiet:
            print(f"Pushing {target_image}...")

        # Push to registry
        try:
            podman.push(
                target_image,
                authfile=str(cfg.registry_auth_file),
                check=True,
                capture_output=quiet,
                text=True,
            )
            if not quiet:
                print(f"Successfully pushed {target_image}")
        except Exception as e:
            print(f"Failed to push {target_image}: {e}")
            raise SystemExit(1)

        # Record the push
        db.record_deployment(
            cfg.db_path,
            image=f"{reg}/{image_name}",
            tag=build_tag,
            action="push",
            success=True,
        )

    # Print summary
    if not quiet:
        print()
        print("Build complete:")
        print(f"  Local:  {local_image}")
        if push:
            print(f"  Remote: {target_image}")
