# src/ots_containers/commands/assets.py

"""Asset management commands for OTS containers."""

from typing import Annotated

import cyclopts

from .. import assets as assets_module
from ..config import Config

app = cyclopts.App(name="assets", help="Extract web assets from container image to volume")


@app.command
def sync(
    create_volume: Annotated[
        bool,
        cyclopts.Parameter(help="Create volume if it doesn't exist (use on first deploy)"),
    ] = False,
):
    """Copy /app/public from container image to static_assets podman volume.

    Extracts web assets (JS, CSS, images) from the OTS container image
    to a shared volume that Caddy serves directly. Use --create-volume
    on initial setup.
    """
    cfg = Config()
    ex = cfg.get_executor()
    assets_module.update(cfg, create_volume=create_volume, executor=ex)
