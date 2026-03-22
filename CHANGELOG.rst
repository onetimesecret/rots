CHANGELOG
=========

All notable changes to rots are documented here.

The format is based on `Keep a
Changelog <https://keepachangelog.com/en/1.1.0/>`__, and this project
adheres to `Semantic
Versioning <https://semver.org/spec/v2.0.0.html>`__.

.. raw:: html

   <!--scriv-insert-here-->

.. _changelog-0.5.6:

0.5.6 — 2026-03-12
==================

Added
-----

- Add ``quadlet_schema.py`` spec validator that checks generated Quadlet files
  against the Podman 5.4 key/section specification, covering Container, Image,
  Build, Network, Volume, Pod, and Kube file types.
- Add quadlet generator validation to CI: installs Podman on the Ubuntu runner
  and feeds generated Quadlet output through the actual ``quadlet`` parser to
  catch spec violations that static analysis would miss.
- Add ``render_image_unit()`` and ``write_image_unit()`` for generating
  companion ``.image`` Quadlet units when a private registry is configured.

Changed
-------

- Fix ``[tool.pytest]`` to ``[tool.pytest.ini_options]`` in ``pyproject.toml``
  — the previous section name was silently ignored by pytest, meaning
  ``pythonpath`` and ``testpaths`` were not being applied.

Fixed
-----

- Fix quadlet ``ContainerName=`` using ``@`` character which is invalid in
  podman container names, causing the quadlet generator to silently reject
  the template and produce no systemd units. Replace ``@`` with ``-`` in
  all three templates (web, worker, scheduler) and in
  ``unit_to_container_name()`` conversion function.

- Fix invalid ``AuthFile=`` placement in Quadlet ``.container`` files.
  ``AuthFile`` is only valid in ``[Image]`` (``.image``) and ``[Build]``
  (``.build``) sections per the Podman 5.4 Quadlet spec — not ``[Container]``.
  Replace with a companion ``onetime.image`` Quadlet unit that handles
  registry authentication correctly. (#33)

AI Assistance
-------------

- Implementation, schema validation design, test coverage (4 test layers,
  65 tests), and CI integration developed with Claude Code assistance.

.. _changelog-0.5.3:

0.5.3 — 2026-03-12
==================

Changed
-------

- Change ``--web``, ``--worker``, ``--scheduler`` instance flags from
  boolean switches to comma-separated identifiers
  (e.g. ``--web 7043,7044``), eliminating positional argument ambiguity (#29)
- Centralize all image:tag composition through ``join_image_tag()``
  across config, image, instance, and db commands (~28 call sites) (#29)
- Reject ``IMAGE`` values with embedded tags (e.g. ``IMAGE=ghcr.io/org/app:v1.0``)
  at validation time — tag must be set separately via ``TAG`` or ``--tag`` (#29)

Fixed
-----

- Fix image path truncation that dropped multi-segment paths
  (e.g. ``onetimesecret/onetimesecret`` became ``onetimesecret``).
  Add ``_strip_registry_prefix()`` that uses OCI convention: only strip
  the first component when it contains a ``.`` or ``:`` (#29)
- Fix ``--reference`` flag conflict with variadic positional identifiers
  in ``deploy``/``redeploy`` commands (#29)
- Fix digest references (``@sha256:...``) producing malformed OCI strings
  when joined with ``:`` separator. Add ``join_image_tag()`` that uses
  ``@`` for digest/sentinel tags and ``:`` for named tags (#29)
- Filter empty strings from comma-separated instance flag parsing,
  preventing ghost identifiers from trailing commas (#29)

.. _changelog-0.5.1:

0.5.1 — 2026-03-11
==================

Added
-----

- Add ``rots dns`` command group for multi-provider DNS record management
  via dns-lexicon. Commands: ``add``, ``show``, ``update``, ``remove``,
  ``list``. Supports Cloudflare, Route53, DigitalOcean, Gandi, GoDaddy,
  Hetzner, Linode, Namecheap, Porkbun, Vultr, and DNSimple.
- Auto-detect public IP and DNS provider from native env vars
  (e.g. ``CLOUDFLARE_API_TOKEN``, ``AWS_ACCESS_KEY_ID``)
- Track DNS mutations in SQLite audit trail (``dns_records`` and
  ``dns_current`` tables)

Changed
-------

- Align pyright pre-commit hook with project dependencies by adding
  ``dns-lexicon`` and ``ots-shared`` to ``additional_dependencies``

- Standardize all logger calls on f-strings (modern Python 3 convention),
  converting 267 %-style format calls across 18 source modules
- Route diagnostic output through Python logging instead of bare print()
- Add CLIFormatter that omits level/module prefix for INFO messages,
  preserving the existing UX for status output
- Add flush_output() and apply_quiet() utilities for subprocess handoff
  and per-command quiet mode

Fixed
-----

- Remove leading newline from "Stopped" log message in instance run command
- Restore dry-run test assertion in test_config_transform using stderr capture
- Fix container name pattern to match Quadlet ContainerName= convention
- Fix image/tag/registry resolution for CLI overrides

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
