# src/ots_containers/assets.py

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from .config import Config
from .podman import Podman
from .systemd import require_podman

if TYPE_CHECKING:
    from ots_shared.ssh import Executor

TEMP_CONTAINER_NAME = "ots-asset-sync-tmp"


def _is_remote(executor: Executor | None) -> bool:
    """Return True if executor targets a remote host."""
    if executor is None:
        return False
    from ots_shared.ssh import LocalExecutor

    return not isinstance(executor, LocalExecutor)


def update(cfg: Config, create_volume: bool = True, *, executor: Executor | None = None) -> None:
    require_podman(executor=executor)

    p = Podman(executor=executor)

    if create_volume:
        p.volume.create("static_assets", capture_output=True, check=False)

    try:
        result = p.volume.mount("static_assets", capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, Exception) as e:
        stderr = getattr(e, "stderr", "") or ""
        if hasattr(stderr, "strip"):
            stderr = stderr.strip()
        if not stderr and hasattr(e, "result"):
            stderr = e.result.stderr.strip()  # type: ignore[union-attr]
        raise SystemExit(f"Failed to mount volume 'static_assets': {stderr or e}")
    assets_dir = Path(result.stdout.strip())

    # Verify the image exists locally before proceeding
    image_ref = cfg.resolved_image_with_tag
    result = p.image.exists(image_ref, capture_output=True)
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
    p.rm(TEMP_CONTAINER_NAME, capture_output=True, check=False)

    try:
        result = p.create(
            image_ref,
            name=TEMP_CONTAINER_NAME,
            image_volume="ignore",
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, Exception) as e:
        stderr = getattr(e, "stderr", "") or ""
        if hasattr(stderr, "strip"):
            stderr = stderr.strip()
        if not stderr and hasattr(e, "result"):
            stderr = e.result.stderr.strip()  # type: ignore[union-attr]
        raise SystemExit(f"Failed to create temporary container from '{image_ref}': {stderr or e}")
    container_id = result.stdout.strip()

    try:
        p.cp(f"{container_id}:/app/public/.", str(assets_dir), check=True)
        manifest = assets_dir / "web/dist/.vite/manifest.json"
        if _is_remote(executor):
            check_result = executor.run(["test", "-f", str(manifest)])  # type: ignore[union-attr]
            if check_result.ok:
                print(f"Manifest found: {manifest}")
            else:
                print(f"Warning: manifest not found at {manifest}")
        else:
            if manifest.exists():
                print(f"Manifest found: {manifest}")
            else:
                print(f"Warning: manifest not found at {manifest}")
    finally:
        p.rm(container_id, check=True)
