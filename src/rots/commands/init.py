# src/rots/commands/init.py

"""Reminder that environment initialization lives in lots/pots.

rots operates on the server side. To create an .otsinfra.yaml
environment marker on the operator workstation, use ``lots init``
or ``pots init``.

Server-side initialization (directories, database) is at ``rots env init``.
"""

from __future__ import annotations

import logging

import cyclopts

logger = logging.getLogger(__name__)

app = cyclopts.App(
    name="init",
    help="Environment and server initialization.",
)


@app.default
def init() -> None:
    """Show initialization options.

    Environment marker:  lots init / pots init  (operator workstation)
    Server setup:        rots env init           (remote host)
    """
    logger.info("rots init routes:")
    logger.info("")
    logger.info("  Environment marker (.otsinfra.yaml):")
    logger.info("    lots init [environment]     Create marker in current directory")
    logger.info("    pots init [environment]     (same, from inventory tool)")
    logger.info("")
    logger.info("  Server setup (directories, database, env file):")
    logger.info("    rots env init               Initialize rots on a host")
    logger.info("    rots --host <h> env init    Initialize rots on a remote host")
