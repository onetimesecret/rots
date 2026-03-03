CHANGELOG
=========

All notable changes to rots are documented here.

The format is based on `Keep a
Changelog <https://keepachangelog.com/en/1.1.0/>`__, and this project
adheres to `Semantic
Versioning <https://semver.org/spec/v2.0.0.html>`__.

.. raw:: html

   <!--scriv-insert-here-->

.. _changelog-0.4.0:

0.4.0 — 2026-03-02
==================

Added
-----

- Surface container health in ``instances list`` — combined status like "active (healthy)"
- Add ``instances ps`` subcommand for podman-native container view
- Add ``proxy push`` command for remote Caddyfile template deployment
- Add ``_path_exists`` and ``_copy_template`` helpers in init module

- Unify image reference parsing with ``parse_image_reference()`` supporting registry ports and digest refs
- Add positional ``reference: ImageRef`` parameter on deploy, redeploy, run, shell, and config-transform commands
- Define precedence chain: positional ref > --tag flag > IMAGE/TAG env vars > @current alias > defaults

Changed
-------

- Skip SSH connection for local-only ``build`` command
- Print immediate context feedback showing project dir and config mode during build

- Remove ``--remote`` flag from instance run/shell/config-transform; always use config-based image lookup
- Replace hardcoded image names with config-driven resolution throughout

- Rename package from ots-containers to rots across source, tests, CI, and pyproject.toml
- Add ots-shared as co-located workspace package under ``packages/ots-shared/``
- Add uv workspace config so ots-shared resolves from local path

Fixed
-----

- DRY ``_get_error_stderr()`` helper in assets.py to deduplicate exception handling (#20)
- Replace fragile ``type(exc).__name__`` string matching with ``isinstance()`` and lazy paramiko import
- Fix Caddyfile.template to valkey.conf entry in manifest

Security
--------

- Fix credential exposure in ``podman login`` — use ``--password-stdin`` instead of CLI flag
- Wire ``Config.validate()`` into ``__post_init__`` (was dead code)
- Reject path traversal in IMAGE_RE and REGISTRY_RE patterns
- Add OTS_REGISTRY hostname validation
- Add MEMORY_MAX/CPU_QUOTA newline injection prevention
- Add VALKEY_SERVICE systemd unit name validation
- Add defense-in-depth checks in quadlet.py

Documentation
-------------

- Add ADR-002: Split scheduler into rufus (in-process) and systemd timers (batch)

- Update repository URLs and stale references from ots-containers to rots

AI Assistance
-------------

- Leverage AI for security analysis, test coverage development, and implementation for image reference overhaul
