# src/rots/commands/image/app.py

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

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Annotated

import cyclopts

from rots import context, db
from rots.config import Config, join_image_tag, parse_image_reference
from rots.podman import Podman

from ..common import JsonOutput, Lines, Quiet, Yes
from ..instance._helpers import apply_quiet

logger = logging.getLogger(__name__)

app = cyclopts.App(
    name=["image", "images"],
    help="Manage container images (pull, aliases, rollback).",
)


@app.command
def pull(
    reference: Annotated[
        str | None,
        cyclopts.Parameter(
            help="Full image reference (e.g. registry.io/org/image:tag)",
        ),
    ] = None,
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
        ots image pull registry.io/org/image:tag
        ots image pull registry.io/org/image:tag --current
        ots image pull --tag v0.23.0
        ots image pull --tag latest --current
        TAG=dev ots image pull                  # Use TAG env var
        ots image pull --tag v0.23.0 --image docker.io/onetimesecret/onetimesecret
        ots image pull --tag v0.23.0 --private  # Pull from private registry
        ots image pull --tag dev --platform linux/amd64  # Pull amd64 on Apple Silicon
    """
    apply_quiet(quiet)
    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    p = Podman(executor=ex)

    # Parse positional reference into image/tag components
    ref_image, ref_tag = parse_image_reference(reference) if reference else (None, None)

    # Resolve image and tag: CLI flags > positional reference > env vars
    resolved_image = image or ref_image or cfg.image
    resolved_tag = tag or ref_tag or cfg.tag
    if not resolved_tag:
        logger.error("--tag is required (or set TAG env var)")
        raise SystemExit(1)

    # Reject sentinel and bare alias names as pull targets.
    # '@current', '@rollback' are sentinels; 'current', 'rollback' are alias names.
    # None of these are valid OCI registry tags to pull — they are resolved locally
    # by the DB alias system.  An explicit concrete tag (e.g. 'v0.23.0') is required.
    tag_key = resolved_tag.lstrip("@")
    if tag_key.lower() in ("current", "rollback"):
        alias = db.get_alias(cfg.db_path, tag_key, executor=ex)
        if alias:
            logger.error(
                f"'{resolved_tag}' is a DB alias pointing to"
                f" {join_image_tag(alias.image, alias.tag)}.\n"
                f"To pull that version, use:  ots image pull --tag {alias.tag}"
            )
        else:
            logger.error(
                f"'{resolved_tag}' is a DB alias sentinel but no alias is set.\n"
                "Provide an explicit tag:  ots image pull --tag v0.23.0\n"
                "Or set a CURRENT alias first:  ots image set-current --tag <tag>"
            )
        raise SystemExit(1)

    # Use private registry if requested
    if private:
        if not cfg.private_image:
            logger.error("--private requires OTS_REGISTRY env var to be set")
            raise SystemExit(1)
        resolved_image = cfg.private_image

    full_image = join_image_tag(resolved_image, resolved_tag)

    logger.info(f"Pulling {full_image}...")

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

        p.pull(full_image, **pull_kwargs)
        logger.info(f"Successfully pulled {full_image}")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.error(f"Failed to pull {full_image}: {e}")
        db.record_deployment(
            cfg.db_path,
            image=resolved_image,
            tag=resolved_tag,
            action="pull",
            success=False,
            notes=str(e),
            executor=ex,
        )
        raise SystemExit(1)

    # Record successful pull
    db.record_deployment(
        cfg.db_path,
        image=resolved_image,
        tag=resolved_tag,
        action="pull",
        success=True,
        executor=ex,
    )

    # Set as current if requested
    if set_as_current:
        # Tag in podman before updating the database
        source_ref = join_image_tag(resolved_image, resolved_tag)
        current_alias = db.get_current_image(cfg.db_path, executor=ex)
        try:
            p.tag(
                source_ref,
                join_image_tag(resolved_image, "current"),
                check=True,
                capture_output=True,
                text=True,
            )
            if current_alias:
                prev_image, prev_tag = current_alias
                p.tag(
                    join_image_tag(prev_image, prev_tag),
                    join_image_tag(prev_image, "rollback"),
                    check=True,
                    capture_output=True,
                    text=True,
                )
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.warning(f"podman tag failed ({e}), aliases updated in DB only")

        previous = db.set_current(cfg.db_path, resolved_image, resolved_tag, executor=ex)
        if previous:
            logger.info(f"Set CURRENT to {resolved_tag} (previous: {previous})")
        else:
            logger.info(f"Set CURRENT to {resolved_tag}")


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
    ex = cfg.get_executor(host=context.host_var.get(None))
    p = Podman(executor=ex)

    if json_output:
        import json as json_module

        result = p.images(
            format="json",
            capture_output=True,
            text=True,
        )
        # Filter if not all_tags
        if not all_tags:
            # Use the basename of the configured image for filtering so
            # that both registry-prefixed and local images are matched.
            image_basename = cfg.image.rsplit("/", 1)[-1]
            images = json_module.loads(result.stdout)
            images = [
                img
                for img in images
                if any(image_basename in name for name in img.get("Names", []))
            ]
            print(json_module.dumps(images, indent=2))
        else:
            print(result.stdout)
        return

    if all_tags:
        result = p.images(
            format="table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}\t{{.Created}}",
            capture_output=True,
            text=True,
        )
    else:
        # Use the basename of the configured image for the filter so
        # that both registry-prefixed and local images are matched.
        image_basename = cfg.image.rsplit("/", 1)[-1]
        result = p.images(
            filter=f"reference=*{image_basename}*",
            format="table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}\t{{.Created}}",
            capture_output=True,
            text=True,
        )

    print("Local images:")
    print(result.stdout)

    # Show current aliases
    aliases = db.get_all_aliases(cfg.db_path, executor=ex)
    if aliases:
        print("\nAliases:")
        for alias in aliases:
            print(f"  {alias.alias}: {join_image_tag(alias.image, alias.tag)} (set {alias.set_at})")


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
    import shutil

    apply_quiet(quiet)
    cfg = Config()

    # Check for skopeo
    if not shutil.which("skopeo"):
        logger.error("skopeo not found. Install with: brew install skopeo (macOS)")
        raise SystemExit(1)

    # Resolve registry
    reg = registry or cfg.registry
    if not reg:
        logger.error("Registry URL required. Use --registry or set OTS_REGISTRY env var")
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

    logger.info(f"$ {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        tags = data.get("Tags", [])

        logger.info(f"Tags for {reg}/{image} ({len(tags)} total):")

        # Sort tags (newest-looking first)
        tags_sorted = sorted(tags, reverse=True)
        for tag in tags_sorted:
            print(f"  {tag}")

    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to list tags: {e.stderr}")
        raise SystemExit(1)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse response: {e}")
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
    ex = cfg.get_executor(host=context.host_var.get(None))
    p = Podman(executor=ex)

    resolved_image = image or cfg.image
    source_ref = join_image_tag(resolved_image, tag)

    # Verify the source image exists locally before proceeding
    try:
        p.image.inspect(source_ref, check=True, capture_output=True, text=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.error(f"Image not found locally: {source_ref}")
        logger.error(f"Pull it first: ots image pull --tag {tag}")
        raise SystemExit(1)

    # Tag in podman before updating the database. If podman tag fails,
    # the database remains unchanged.
    current_alias = db.get_current_image(cfg.db_path, executor=ex)
    try:
        p.tag(
            source_ref,
            join_image_tag(resolved_image, "current"),
            check=True,
            capture_output=True,
            text=True,
        )
        if current_alias:
            prev_image, prev_tag = current_alias
            p.tag(
                join_image_tag(prev_image, prev_tag),
                join_image_tag(prev_image, "rollback"),
                check=True,
                capture_output=True,
                text=True,
            )
    except Exception as e:
        logger.error(f"Failed to tag image in podman: {e}")
        raise SystemExit(1)

    previous = db.set_current(cfg.db_path, resolved_image, tag, executor=ex)

    logger.info(f"CURRENT set to {join_image_tag(resolved_image, tag)}")
    if previous:
        logger.info(f"ROLLBACK set to previous: {previous}")
    else:
        logger.info("(No previous CURRENT to roll back to)")


@app.command
def rollback(
    apply: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--apply"],
            help=(
                "Automatically redeploy all running instances after updating the alias. "
                "Without --apply, only the CURRENT/ROLLBACK aliases are updated."
            ),
        ),
    ] = False,
    delay: Annotated[
        int,
        cyclopts.Parameter(
            name=["--delay", "-d"],
            help="Seconds between instance redeployments when --apply is used (default: 30)",
        ),
    ] = 30,
):
    """Roll back to the previous deployment.

    Uses the deployment timeline to find the previous successful deployment,
    NOT environment variables. This ensures consecutive rollbacks work
    correctly by walking back through history.

    The current CURRENT becomes ROLLBACK, and the previous deployment
    becomes the new CURRENT.

    Use --apply to also redeploy all running instances atomically after the
    alias update, avoiding a state mismatch between the alias and what's
    running.

    Examples:
        ots image rollback            # Update aliases only
        ots image rollback --apply    # Update aliases and redeploy
        ots image rollback --apply --delay 10  # Longer delay between instances
    """
    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    p = Podman(executor=ex)

    # Show current state
    current = db.get_current_image(cfg.db_path, executor=ex)
    if current:
        logger.info(f"Current: {join_image_tag(current[0], current[1])}")
    else:
        logger.error("No CURRENT alias set")
        raise SystemExit(1)

    # Get previous tags from timeline for context
    previous = db.get_previous_tags(cfg.db_path, limit=5, executor=ex)
    if len(previous) < 2:
        logger.error("No previous deployment to roll back to")
        raise SystemExit(1)

    logger.info(f"Rolling back to: {join_image_tag(previous[1][0], previous[1][1])}")
    logger.info(f"  (last deployed: {previous[1][2]})")

    result = db.rollback(cfg.db_path, executor=ex)
    if result:
        image, tag = result
        # Update podman tags to reflect the new alias state
        try:
            p.tag(
                join_image_tag(image, tag),
                join_image_tag(image, "current"),
                check=True,
                capture_output=True,
                text=True,
            )
            p.tag(
                join_image_tag(current[0], current[1]),
                join_image_tag(current[0], "rollback"),
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception as e:
            logger.warning(f"podman tag failed ({e}), aliases updated in DB only")
        logger.info("Rollback complete.")
        logger.info(f"  CURRENT: {join_image_tag(image, tag)}")
        logger.info(f"  ROLLBACK: {join_image_tag(current[0], current[1])}")

        if apply:
            logger.info("Applying rollback: redeploying all running instances...")
            from ..instance.app import redeploy

            redeploy(delay=delay)
        else:
            logger.info("To apply: ots instance redeploy")
    else:
        logger.error("Rollback failed - no previous deployment found")
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
    ex = cfg.get_executor(host=context.host_var.get(None))

    deployments = db.get_deployments(cfg.db_path, limit=limit, port=port, executor=ex)

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
    aliases = db.get_all_aliases(cfg.db_path, executor=ex)
    if aliases:
        print("Current aliases:")
        for alias in aliases:
            print(f"  {alias.alias}: {join_image_tag(alias.image, alias.tag)}")
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
    ex = cfg.get_executor(host=context.host_var.get(None))

    aliases_list = db.get_all_aliases(cfg.db_path, executor=ex)

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
        print(f"    Image: {join_image_tag(alias.image, alias.tag)}")
        print(f"    Set:   {alias.set_at}")
    print("-" * 60)

    # Show what commands would resolve to
    print("\nResolution:")
    current = db.get_current_image(cfg.db_path, executor=ex)
    if current:
        print(f"  TAG=current  -> {join_image_tag(current[0], current[1])}")

    rollback_img = db.get_rollback_image(cfg.db_path, executor=ex)
    if rollback_img:
        print(f"  TAG=rollback -> {join_image_tag(rollback_img[0], rollback_img[1])}")


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
    ex = cfg.get_executor(host=context.host_var.get(None))
    p = Podman(executor=ex)

    # Resolve registry from arg or config
    reg = registry or cfg.registry
    if not reg:
        logger.error("Registry URL required. Use --registry or set OTS_REGISTRY env var")
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
        logger.error("Username and password are required")
        raise SystemExit(1)

    logger.info(f"Logging in to {reg}...")

    try:
        p.login(
            reg,
            username=user,
            password_stdin=True,
            authfile=str(cfg.registry_auth_file),
            input=pw,
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info(f"Login successful: {reg}")
    except Exception as e:
        logger.error(f"Login failed: {e}")
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
    apply_quiet(quiet)
    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    p = Podman(executor=ex)

    # Resolve registry from arg or config
    reg = registry or cfg.registry
    if not reg:
        logger.error("Registry URL required. Use --registry or set OTS_REGISTRY env var")
        raise SystemExit(1)

    # Resolve tag and source image from args or env vars
    resolved_tag = tag or cfg.tag
    if not resolved_tag:
        logger.error("Tag required. Use --tag or set TAG env var")
        raise SystemExit(1)
    src = source_image or cfg.image
    # Derive target image name from source basename (strip host prefix if present)
    src_basename = src.split("/")[-1]
    source_full = join_image_tag(src, resolved_tag)
    target_full = join_image_tag(f"{reg}/{src_basename}", resolved_tag)

    logger.info(f"Tagging {source_full} -> {target_full}")

    # Tag the image for the target registry
    try:
        p.tag(source_full, target_full, check=True, capture_output=True, text=True)
    except Exception as e:
        logger.error(f"Failed to tag image: {e}")
        raise SystemExit(1)

    logger.info(f"Pushing {target_full}...")

    # Push to registry
    try:
        p.push(
            target_full,
            authfile=str(cfg.registry_auth_file),
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info(f"Successfully pushed {target_full}")
    except Exception as e:
        logger.error(f"Failed to push {target_full}: {e}")
        raise SystemExit(1)

    # Record the push action
    db.record_deployment(
        cfg.db_path,
        image=f"{reg}/{src_basename}",
        tag=resolved_tag,
        action="push",
        success=True,
        executor=ex,
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
    ex = cfg.get_executor(host=context.host_var.get(None))
    p = Podman(executor=ex)

    # Resolve registry from arg or config
    reg = registry or cfg.registry
    if not reg:
        logger.error("Registry URL required. Use --registry or set OTS_REGISTRY env var")
        raise SystemExit(1)

    logger.info(f"Logging out from {reg}...")

    try:
        p.logout(
            reg,
            authfile=str(cfg.registry_auth_file),
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info(f"Logged out from {reg}")
    except Exception as e:
        logger.error(f"Logout failed: {e}")


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
        logger.error("At least one tag is required")
        raise SystemExit(1)

    if not yes:
        print(f"This will remove images: {', '.join(tags)}")
        response = input("Continue? [y/N] ")
        if response.lower() not in ("y", "yes"):
            print("Aborted")
            return

    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    p = Podman(executor=ex)

    for tag in tags:
        # Try common image patterns, including configured image
        image_basename = cfg.image.split("/")[-1]
        images_to_try = [
            join_image_tag(image_basename, tag),
            join_image_tag(cfg.image, tag),
            join_image_tag(f"localhost/{image_basename}", tag),
        ]
        if cfg.private_image:
            images_to_try.append(join_image_tag(cfg.private_image, tag))

        removed = False
        for image in images_to_try:
            try:
                kwargs = {"check": True, "capture_output": True, "text": True}
                if force:
                    kwargs["force"] = True
                p.rmi(image, **kwargs)
                logger.info(f"Removed {image}")
                removed = True
                break
            except Exception:
                continue

        if not removed:
            logger.warning(f"Image not found: {tag}")


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

    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    p = Podman(executor=ex)

    try:
        kwargs = {"check": True, "capture_output": True, "text": True}
        if all_images:
            kwargs["all"] = True
        result = p.image.prune(**kwargs)
        print("Pruned images:")
        print(result.stdout)
    except Exception as e:
        logger.error(f"Prune failed: {e}")
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


def _format_build_error(e: Exception) -> str:
    """Extract useful error details from build failures.

    CommandError (from executor) wraps a Result with stdout/stderr,
    but str() only shows the command and exit code. This extracts
    the stderr/stdout so the user sees what actually went wrong.
    """
    from ots_shared.ssh.executor import CommandError

    if isinstance(e, CommandError):
        result = e.result
        parts = [str(e)]
        if result.stderr and result.stderr.strip():
            parts.append(result.stderr.strip())
        elif result.stdout and result.stdout.strip():
            parts.append(result.stdout.strip())
        return "\n".join(parts)
    if isinstance(e, subprocess.CalledProcessError):
        if e.stderr:
            return f"{e}\n{e.stderr.strip()}"
        if e.stdout:
            return f"{e}\n{e.stdout.strip()}"
    return str(e)


def _load_oci_build_config(project_dir: Path) -> dict | None:
    """Load .oci-build.json from project dir, or None if absent."""
    config_path = project_dir / ".oci-build.json"
    if not config_path.exists():
        return None
    with config_path.open() as f:
        return json.load(f)


def _build_base(
    p: Podman,
    project_dir: Path,
    base_config: dict,
    platform: str,
    quiet: bool,
) -> str:
    """Build shared base image. Returns local tag for build-context injection."""
    local_tag = f"ots-base:{os.getpid()}"
    p.buildx.build(
        str(project_dir),
        file=str(project_dir / base_config["dockerfile"]),
        platform=platform,
        tag=local_tag,
        check=True,
        capture_output=quiet,
        text=True,
    )
    return local_tag


def _build_variant(
    p: Podman,
    project_dir: Path,
    variant: dict,
    build_tag: str,
    image_name: str,
    platform: str,
    build_args: list[str],
    build_contexts: dict[str, str] | None,
    quiet: bool,
) -> str:
    """Build one variant. Returns the local image tag."""
    suffix = variant.get("suffix", "")
    local_image = join_image_tag(f"{image_name}{suffix}", build_tag)

    build_kwargs: dict = {
        "platform": platform,
        "tag": local_image,
        "build_arg": build_args,
        "check": True,
        "capture_output": quiet,
        "text": True,
    }
    if variant.get("dockerfile"):
        build_kwargs["file"] = str(project_dir / variant["dockerfile"])
    if variant.get("target"):
        build_kwargs["target"] = variant["target"]
    if build_contexts:
        # Podman wrapper expands list kwargs as repeated flags:
        # build_context=["base=container-image://img"] → --build-context base=container-image://img
        build_kwargs["build_context"] = [
            f"{name}=container-image://{img}" for name, img in build_contexts.items()
        ]

    p.buildx.build(str(project_dir), **build_kwargs)
    return local_image


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

    When .oci-build.json is present in the project directory, builds are
    driven by the config: the shared base image is built first, then each
    variant receives --build-context base=container-image://... so that
    FROM base stages resolve correctly.

    Examples:
        # Standard build (no .oci-build.json)
        ots image build --project-dir ~/src/onetimesecret

        # Build lite variant (no .oci-build.json)
        ots image build -d . -f docker/variants/lite.dockerfile --suffix -lite

        # Build all variants from .oci-build.json
        ots image build -d /path/to/project --platform linux/amd64

        # Build single variant from .oci-build.json by suffix
        ots image build --suffix '' -d /path/to/project --platform linux/amd64

        # Build and push to registry
        ots image build -d . --push --registry registry.example.com
    """
    apply_quiet(quiet)
    cfg = Config()
    # Builds always run locally — no executor needed.  Podman() without
    # executor uses subprocess.run directly so build output streams to
    # the terminal in real-time.  Skipping get_executor() also avoids
    # an unnecessary SSH connection when .otsinfra.env is present.
    p = Podman()

    # Resolve project directory
    proj_dir = Path(project_dir) if project_dir else Path.cwd()
    proj_dir = proj_dir.resolve()

    # Check for .oci-build.json — drives whether we use bake-aware or legacy path
    oci_config = _load_oci_build_config(proj_dir)

    # Immediate context feedback
    oci_label = ".oci-build.json" if oci_config is not None else "default"
    logger.info(f"build: {proj_dir} [{oci_label}]")

    if oci_config is not None:
        _build_with_oci_config(
            p=p,
            cfg=cfg,
            proj_dir=proj_dir,
            oci_config=oci_config,
            dockerfile=dockerfile,
            target=target,
            suffix=suffix,
            platform=platform,
            push=push,
            registry=registry,
            tag=tag,
            quiet=quiet,
        )
    else:
        _build_legacy(
            p=p,
            cfg=cfg,
            proj_dir=proj_dir,
            dockerfile=dockerfile,
            target=target,
            suffix=suffix,
            platform=platform,
            push=push,
            registry=registry,
            tag=tag,
            quiet=quiet,
        )


def _build_with_oci_config(
    *,
    p: Podman,
    cfg,
    proj_dir: Path,
    oci_config: dict,
    dockerfile: str | None,
    target: str | None,
    suffix: str | None,
    platform: str,
    push: bool,
    registry: str | None,
    tag: str | None,
    quiet: bool,
) -> None:
    """Bake-aware build driven by .oci-build.json."""
    # Validate — skip dockerfile check since dockerfiles come from config
    _validate_project_dir(proj_dir, skip_dockerfile_check=True)

    build_tag = _determine_build_tag(proj_dir, tag)
    git_hash = _get_git_hash(proj_dir, required=False)
    pkg_version = _read_package_version(proj_dir)

    # Resolve image name from config (strip registry prefix, use basename).
    # Falls back to cfg.image basename when .oci-build.json omits image_name.
    default_image_name = cfg.image.rsplit("/", 1)[-1]
    config_image_name = oci_config.get("image_name", default_image_name)
    image_name = config_image_name.split("/")[-1]

    # Resolve platform: CLI flag takes priority, then config, then default
    resolved_platform = platform
    config_platforms = oci_config.get("platforms", [])
    if platform == "linux/amd64,linux/arm64" and config_platforms:
        resolved_platform = config_platforms[0]

    # Build args
    build_args = [f"VERSION={pkg_version}"]
    if git_hash:
        build_args.append(f"COMMIT_HASH={git_hash}")

    # Build base image if config declares one
    base_tag = None
    base_config = oci_config.get("base")
    if base_config:
        logger.info("Building base image...")
        try:
            base_tag = _build_base(p, proj_dir, base_config, resolved_platform, quiet)
            logger.info(f"  Base: {base_tag}")
        except Exception as e:
            logger.error(f"Base build failed: {_format_build_error(e)}")
            raise SystemExit(1)

    # Determine which variants to build
    variants = oci_config.get("variants", [])
    has_variant_flag = suffix is not None or dockerfile is not None or target is not None

    if has_variant_flag:
        # User specified variant-specific flags — build a single variant
        if suffix is not None:
            matching = [v for v in variants if v.get("suffix", "") == suffix]
            if matching:
                variants_to_build = [matching[0]]
            else:
                # Custom one-off build: use CLI flags, just inject base context
                variants_to_build = [
                    {
                        "suffix": suffix,
                        "dockerfile": dockerfile,
                        "target": target,
                    }
                ]
        else:
            # --dockerfile or --target without --suffix
            variants_to_build = [
                {
                    "suffix": suffix or "",
                    "dockerfile": dockerfile,
                    "target": target,
                }
            ]
    else:
        # No variant flags — build all variants from config
        variants_to_build = variants

    if not variants_to_build:
        logger.error("No variants to build (check .oci-build.json)")
        raise SystemExit(1)

    names = [
        join_image_tag(f"{image_name}{v.get('suffix', '')}", build_tag) for v in variants_to_build
    ]
    logger.info(f"Building {len(variants_to_build)} variant(s): {', '.join(names)}")
    logger.info(f"  Project: {proj_dir}")
    logger.info(f"  Platform: {resolved_platform}")
    logger.info(f"  Version: {pkg_version}")
    logger.info(f"  Commit: {git_hash or 'N/A (no git)'}")

    built_images: list[str] = []
    try:
        # Track completed variant images for inter-variant build contexts
        completed: dict[str, str] = {}  # suffix -> local_image_tag

        for variant in variants_to_build:
            # Build contexts: base + inter-variant dependencies
            build_contexts: dict[str, str] | None = None
            if base_tag:
                build_contexts = {"base": base_tag}

            # Check if variant depends on another variant (e.g., lite depends on main)
            depends_on = variant.get("build_context")
            if depends_on and isinstance(depends_on, dict):
                if build_contexts is None:
                    build_contexts = {}
                for ctx_name, ctx_ref in depends_on.items():
                    # ctx_ref could be "target:main" referring to a completed variant
                    if ctx_ref.startswith("target:"):
                        dep_suffix = ctx_ref.removeprefix("target:")
                        # Find the completed image for that suffix
                        dep_key = dep_suffix if dep_suffix else ""
                        if dep_key in completed:
                            build_contexts[ctx_name] = completed[dep_key]
                        else:
                            logger.warning(
                                f"dependency '{ctx_ref}' not yet built, skipping context"
                            )

            local_image = _build_variant(
                p,
                proj_dir,
                variant,
                build_tag,
                image_name,
                resolved_platform,
                build_args,
                build_contexts,
                quiet,
            )
            built_images.append(local_image)
            completed[variant.get("suffix", "")] = local_image

            logger.info(f"  Built: {local_image}")

            # Record the build
            db.record_deployment(
                cfg.db_path,
                image=f"{image_name}{variant.get('suffix', '')}",
                tag=build_tag,
                action="build",
                success=True,
                notes=f"oci-config, target={variant.get('target')}",
            )
    except Exception as e:
        logger.error(f"Build failed: {_format_build_error(e)}")
        raise SystemExit(1)
    finally:
        # Clean up base image
        if base_tag:
            try:
                p.rmi(base_tag, capture_output=True, text=True)
            except Exception:
                pass  # Best-effort cleanup

    # Push if requested
    if push:
        _push_images(p, cfg, built_images, build_tag, registry, quiet)

    # Print summary
    logger.info("Build complete:")
    for img in built_images:
        logger.info(f"  Local:  {img}")


def _build_legacy(
    *,
    p: Podman,
    cfg,
    proj_dir: Path,
    dockerfile: str | None,
    target: str | None,
    suffix: str | None,
    platform: str,
    push: bool,
    registry: str | None,
    tag: str | None,
    quiet: bool,
) -> None:
    """Legacy build path — no .oci-build.json, direct podman buildx build."""
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

    # Image name with optional suffix (use basename of configured image)
    image_basename = cfg.image.rsplit("/", 1)[-1]
    image_name = f"{image_basename}{suffix or ''}"
    local_image = join_image_tag(image_name, build_tag)

    logger.info(f"Building {local_image}")
    logger.info(f"  Project: {proj_dir}")
    if dockerfile:
        logger.info(f"  Dockerfile: {dockerfile}")
    if target:
        logger.info(f"  Target: {target}")
    logger.info(f"  Platform: {platform}")
    logger.info(f"  Version: {pkg_version}")
    logger.info(f"  Commit: {git_hash or 'N/A (no git)'}")

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

        p.buildx.build(str(proj_dir), **build_kwargs)

        logger.info(f"Successfully built {local_image}")
    except Exception as e:
        logger.error(f"Build failed: {_format_build_error(e)}")
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
        _push_images(p, cfg, [local_image], build_tag, registry, quiet)

    # Print summary
    logger.info("Build complete:")
    logger.info(f"  Local:  {local_image}")


def _push_images(
    p: Podman,
    cfg,
    images: list[str],
    build_tag: str,
    registry: str | None,
    quiet: bool,
) -> None:
    """Tag and push built images to a registry."""
    reg = registry or cfg.registry
    if not reg:
        logger.error("--push requires --registry or OTS_REGISTRY env var")
        raise SystemExit(1)

    for local_image in images:
        # Extract image name (without tag) from local_image
        image_name_part = local_image.rsplit(":", 1)[0]
        target_image = join_image_tag(f"{reg}/{image_name_part}", build_tag)

        logger.info(f"Tagging {local_image} -> {target_image}")

        try:
            p.tag(
                local_image,
                target_image,
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception as e:
            logger.error(f"Failed to tag image: {e}")
            raise SystemExit(1)

        logger.info(f"Pushing {target_image}...")

        try:
            p.push(
                target_image,
                authfile=str(cfg.registry_auth_file),
                check=True,
                capture_output=quiet,
                text=True,
            )
            logger.info(f"Successfully pushed {target_image}")
        except Exception as e:
            logger.error(f"Failed to push {target_image}: {e}")
            raise SystemExit(1)

        db.record_deployment(
            cfg.db_path,
            image=f"{reg}/{image_name_part}",
            tag=build_tag,
            action="push",
            success=True,
        )
