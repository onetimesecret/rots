# src/ots_containers/assets.py

import subprocess
from pathlib import Path

from .config import Config
from .podman import podman
from .systemd import require_podman

TEMP_CONTAINER_NAME = "ots-asset-sync-tmp"


def update(cfg: Config, create_volume: bool = True) -> None:
    require_podman()
    if create_volume:
        podman.volume.create("static_assets", capture_output=True, check=False)

    try:
        result = podman.volume.mount("static_assets", capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip() if e.stderr else "unknown error"
        raise SystemExit(f"Failed to mount volume 'static_assets': {stderr}")
    assets_dir = Path(result.stdout.strip())

    # Verify the image exists locally before proceeding
    image_ref = cfg.resolved_image_with_tag
    result = podman.image.exists(image_ref, capture_output=True)
    if result.returncode != 0:
        # Distinguish unresolved alias from missing image
        if cfg.tag.lower() in ("current", "rollback"):
            raise SystemExit(
                f"No '{cfg.tag}' image alias found. "
                f"Run 'ots-containers image set-current <tag>' after pulling an image."
            )
        raise SystemExit(
            f"Image '{image_ref}' not found locally. "
            f"Pull it first with 'ots-containers image pull'."
        )

    # Remove any leftover temp container from a previous interrupted run
    podman.rm(TEMP_CONTAINER_NAME, capture_output=True, check=False)

    try:
        result = podman.create(
            image_ref,
            name=TEMP_CONTAINER_NAME,
            image_volume="ignore",
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip() if e.stderr else "unknown error"
        raise SystemExit(f"Failed to create temporary container from '{image_ref}': {stderr}")
    container_id = result.stdout.strip()

    try:
        podman.cp(f"{container_id}:/app/public/.", str(assets_dir), check=True)
        manifest = assets_dir / "web/dist/.vite/manifest.json"
        if manifest.exists():
            print(f"Manifest found: {manifest}")
        else:
            print(f"Warning: manifest not found at {manifest}")
    finally:
        podman.rm(container_id, check=True)
