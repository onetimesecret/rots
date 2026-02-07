# src/ots_containers/assets.py

import subprocess
from pathlib import Path

from .config import Config
from .podman import podman


def update(cfg: Config, create_volume: bool = True) -> None:
    if create_volume:
        podman.volume.create("static_assets", capture_output=True, check=False)

    try:
        result = podman.volume.mount("static_assets", capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip() if e.stderr else "unknown error"
        raise SystemExit(f"Failed to mount volume 'static_assets': {stderr}")
    assets_dir = Path(result.stdout.strip())

    try:
        result = podman.create(
            cfg.resolved_image_with_tag, capture_output=True, text=True, check=True
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip() if e.stderr else "unknown error"
        raise SystemExit(
            f"Failed to create temporary container from '{cfg.resolved_image_with_tag}': {stderr}"
        )
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
