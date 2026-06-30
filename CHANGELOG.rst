CHANGELOG
=========

All notable changes to rots are documented here.

The format is based on `Keep a
Changelog <https://keepachangelog.com/en/1.1.0/>`__, and this project
adheres to `Semantic
Versioning <https://semver.org/spec/v2.0.0.html>`__.

.. raw:: html

   <!--scriv-insert-here-->

.. _changelog-0.7.5:

0.7.5 — 2026-06-29
==================

Fixed
-----

- Stop emitting an invalid ``EnvironmentFile=-/etc/default/onetimesecret.local``
  line in generated Quadlet ``[Container]`` units. Quadlet does not honor
  systemd's ``-`` ("optional") prefix and passed it to ``podman --env-file``
  verbatim, which resolved to a non-existent relative path and crashed the
  container with exit 125 (restart loop). The optional ``.local`` host override
  is now emitted only when the file exists on the target host. Regression from
  v0.7.4.

AI Assistance
-------------

- Root-cause diagnosis (podman exit 125 from the unsupported ``-`` prefix),
  fix design mirroring ``get_config_volumes_section``, and regression tests
  developed with AI assistance.

.. _changelog-0.7.4:

0.7.4 — 2026-05-04
==================

Fixed
-----

- Fix authfile handling for container image login/pull operations (#72)

.. _changelog-0.7.3:

0.7.3 — 2026-04-26
==================

Added
-----

- ``rots instance render <out>`` subcommand emits Quadlet template
  files (``onetime-web@.container``, ``onetime-worker@.container``,
  ``onetime-scheduler@.container``, plus ``onetime.image`` when a
  registry is configured) to a local directory with no host I/O — no
  SSH, no systemd, no DB write. Intended for offline assembly of
  trees that ``lots`` then ships to hosts (``#67``).

Changed
-------

- Render writes are atomic per file (stage as ``<name>.tmp`` then
  ``os.replace``) and rendered fully to memory before any disk I/O,
  so a render failure cannot leave a half-populated tree on disk
  (``#67``).
- Render now removes managed Quadlet artifacts from prior runs that
  are not in the current payload (e.g. a stale ``onetime.image`` left
  over after ``OTS_REGISTRY`` is unset). Files outside the managed
  set are left untouched (``#67``).

Fixed
-----

- ``--config-source`` default path corrected from ``confext/`` to
  ``confexts/`` (plural), matching the monorepo convention. Operators
  running ``rots instance render`` from an environment directory now
  pick up ``Volume=`` lines without an explicit ``--config-source``
  flag (``#67``).

.. _changelog-0.7.2:

0.7.2 — 2026-04-26
==================

Added
-----

- Two-phase sidecar provisioning: RPC handlers for ``postgres``,
  ``valkey``, ``secrets``, and ``backup`` with role-based gating, plus
  an operator bootstrap path (``#55``).
- ``postgres.ping`` idempotent connectivity probe handler, role-gated
  to ``{"db"}`` (``#59``).
- ``ots-shared`` 0.4.0 ships ``ots_shared.hcloud`` — Hetzner Cloud
  ``Config``/``Client`` factory, ``api_errors`` context manager,
  ``KNOWN_ZONES``/``LOCATION_TO_ZONE``, network plan reconciliation
  (``NetworkSpec``, ``DesiredState``, ``parse_marker``, ``diff_state``),
  and marker-backed server defaults (``HostDefaults``,
  ``CloudInitPayload``, ``resolve_host_defaults``,
  ``load_cloud_init_user_data``). Other workspace tools can now
  consume the Hetzner client directly without shelling out to lots.
  (``#55``)

Changed
-------

- Decouple ``ots-shared`` from the rots workspace. The shared library
  now lives in its own repository at
  ``https://github.com/onetimesecret/ots-shared`` and is consumed via
  PyPI (``ots-shared[ssh]>=0.4.0``). Removed the
  ``packages/ots-shared/`` source tree and the
  ``[tool.uv.workspace]``/``[tool.uv.sources]`` entries that pinned it
  as a workspace member.
- Move the hcloud library layer out of ``lots.hcloud`` into
  ``ots_shared.hcloud``. Lots CLI commands now import ``Config`` and
  helpers from the shared package; presentation helpers
  (``print_*``) stay in ``lots/hcloud/commands/server/_output.py``.
  (``#55``)
- Pin completion-install command name to ``--install-completions`` so
  the flag is stable across cyclopts upgrades.
- Harden sidecar RPC handlers per PR ``#55`` review feedback.

Removed
-------

- Wave-2 env bootstrap and ``infra_marker`` scaffolding —
  superseded by the sidecar RPC handlers (``#59``).
- ``packages/ots-shared/`` source tree (history preserved in the
  standalone ots-shared repository).

Fixed
-----

- Flaky postgres handler test caused by an alpine init double-restart
  race; improve ``_wait_for_postgres_ready`` error diagnostics
  (``#62``).
- Sidecar/valkey: correct auth-model docstring and bootstrap-auth
  tests (``#55``).
- ``load_cloud_init_user_data`` now writes its "Running cloud-init
  command" diagnostic to stderr so ``lots hcloud server create
  --json`` output stays clean (``#55``).

Documentation
-------------

- Sidecar spec: align Phase 2 command vocabulary with the implemented
  handlers; trim assumptions introduced by ``#55`` (``#59``).

AI Assistance
-------------

- Library/CLI carve, import rewrites, test migration, and code
  review (``#55``) coordinated across explore, python-dev,
  qa-automation-engineer, and code-reviewer subagents.

.. _changelog-0.7.1:

0.7.1 — 2026-04-20
==================

Changed
-------

- Centralize ``OTS_REGISTRY`` image reference resolution in
  ``resolve_image_tag``; add ``localhost`` registry support and an
  ``_apply_registry_override`` helper (``#56``).
- Default env path is now ``/etc/default/onetimesecret`` (``#54``).
- Bump ``ots-shared`` workspace pin to 0.3.0.

Removed
-------

- ``cloudinit`` subcommand and its templates (``#48``).
- Stale references to old package names and paths from the
  ``ots_containers`` → ``rots`` migration.

.. _changelog-0.7.0:

0.7.0 — 2026-04-12
==================

Added
-----

- Add PostgreSQL service package, plus sidecar service discovery via the
  RabbitMQ fanout exchange.
- Add RabbitMQ support to cloud-init: repository wiring, sidecar install, and a
  ``--rabbitmq`` apt repository option.
- Add ``rots doctor`` RabbitMQ health check.
- Add singleton ``ServicePackage`` support so services like RabbitMQ can be
  modelled as a single instance rather than port-indexed.
- Add ``RemoteExecutor`` protocol and ``TypeGuard`` on ``is_remote()`` for
  semantically accurate local/remote dispatch.
- Wire ``rots.*`` commands into the sidecar dispatcher so the full CLI surface
  is reachable over RabbitMQ.
- Add ``provision-socks`` command for SOCKS proxy SSH key exchange.
- Add shell completion support via cyclopts (``#0410``).
- Add ``rots env init`` command for writing the environment marker, along with
  environment scaffold helpers in ``ots-shared`` (``#0411``).
- Re-introduce ``.otsinfra.yaml`` and improve environment discovery walk-up.
- Add command history logging and serialization.
- Add onboarding doc and cloud-init reference.
- Add tests for ``create_marker`` hosts param, default environment fallback,
  and ``common.py`` re-exports.

Changed
-------

- Refactor cloud-init templates to a Composition/Builder pattern.
- Harden sidecar dispatcher and singleton service handling.
- Include config-file discovery in ``list_instances --json`` output;
  deduplicate ``config_dir`` resolution and add structured logging (``#11``).
- Strip the ``--image`` flag from relevant commands and use
  ``_strip_registry_prefix`` in ``push`` and ``list-remote`` (``#44``).
- Extract a shared ``init`` sub-app and remove duplicate re-exports in
  ``common.py``.
- Update file headers across Python modules to use repo-relative paths.

Fixed
-----

- Fix cloud-init provisioning for Debian 13 (``#11``).
- Fix RabbitMQ ``bind_config_key`` collision.
- Fix misleading ``discover`` docstring to reference the fanout exchange.
- Fix N+1 lookup, unsafe string operations, and unsorted remote listing
  surfaced in PR #47 review.
- Fix SQL escaping, YAML quoting, fallback parser, and type hints surfaced in
  PR #49 review (``#0411``).
- Remove ``type: ignore`` comments now that ``ots-shared`` 0.2.1 ships a real
  ``TypeGuard``.

.. _changelog-0.6.3:

0.6.3 — 2026-04-05
==================

Added
-----

- Add ``workflow`` command for fleet deployment orchestration, including a
  ``workflow trigger`` subcommand for CI/automation (``#STEP4``).
- Add ``generate`` command for standalone Quadlet file export (``#34``).
- Add ``--backend dbus|cli`` flag for explicit systemd backend selection.
- Add ``--socket`` and ``--rabbitmq`` transport flags to ``sidecar send``.
- Add ``sidecar publish`` command with per-host queue binding for fleet
  targeting.
- Add git source support for ``self upgrade`` and repeated-args parsing.
- Add sidecar documentation and ``.otsinfra.env`` walk-up discovery for
  ``RABBITMQ_URL``.
- Add ``pika`` dependency and health/status sidecar handlers.
- Auto-detect the ``rots`` binary path for sidecar install.
- Add remote-executor auto-detect test and clarify ``unit_file_exists``
  docstring.

Changed
-------

- Replace ``systemctl`` CLI parsing with the systemd D-Bus API; defer
  ``pystemd`` imports to call time to keep ``pyright`` happy.
- Extract streaming execution loop to a shared
  ``run_plan_with_progress()`` and shared deployment reporting utilities
  (``#STEP4``).
- Modernize lifecycle handlers with a ``@register_handler`` pattern
  (``#STEP4``).
- Optimize CLI startup and test collection with lazy imports; parallelize
  pre-push hooks and optimize the pytest pre-commit hook (``#STEP4``).
- Mark additional command tests ``@pytest.mark.quick``.
- Extract ``_parse_key_value_args`` helper and tighten README consistency
  (``#STEP3``).

Fixed
-----

- Normalize unit names, gate root-only operations, and address related review
  feedback.
- Disable ``ProtectHome`` for pipx compatibility, with an inline doc explaining
  the rationale and git-branch install docs.
- Address PR review feedback for security, docs, and tests (``#STEP3``).
- Fix a type hint and add file validation in ``ots-shared`` (``#STEP3``).

.. _changelog-0.6.2:

0.6.2 — 2026-03-21
==================

Changed
-------

- Version metadata bump. No functional changes over 0.6.0; the 0.6.0 and 0.6.1
  tags were cut from the same commit and 0.6.2 re-tagged after version-bump
  housekeeping.

.. _changelog-0.6.1:

0.6.1 — 2026-03-21
==================

Changed
-------

- Re-tag of 0.6.0 with no code changes.

.. _changelog-0.6.0:

0.6.0 — 2026-03-21
==================

Added
-----

- Add sidecar daemon for remote OTS instance control, including generic
  ``rots`` CLI invocation, dispatch integration tests, and a sidecar overview
  doc.
- Add ``rots self upgrade`` command for pipx-based self-management.
- Add scriv-managed changelog.

Changed
-------

- Switch sidecar config to a denylist approach.
- Address PR #35 review feedback and transport-layer issues.
- Tidy docs, rename an outdated variable, and ignore the schemathesis scratch
  directory.

Fixed
-----

- Fix ruff ``UP042`` by switching to ``StrEnum`` instead of ``str, Enum``.
- Fix the ``ots-shared`` repo URL and a type hint surfaced in PR review.

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
