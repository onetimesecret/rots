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
