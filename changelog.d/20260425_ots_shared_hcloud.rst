Added
-----

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

- Move the hcloud library layer out of ``lots.hcloud`` into
  ``ots_shared.hcloud``. Lots CLI commands now import ``Config`` and
  helpers from the shared package; presentation helpers
  (``print_*``) stay in ``lots/hcloud/commands/server/_output.py``.
  (``#55``)

Fixed
-----

- ``load_cloud_init_user_data`` now writes its "Running cloud-init
  command" diagnostic to stderr so ``lots hcloud server create
  --json`` output stays clean. (``#55``)

AI Assistance
-------------

- Library/CLI carve, import rewrites, test migration, and code
  review (``#55``) coordinated across explore, python-dev,
  qa-automation-engineer, and code-reviewer subagents.
