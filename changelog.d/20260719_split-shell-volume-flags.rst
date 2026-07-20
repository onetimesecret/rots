Changed
-------

- Split ``rots instance shell``'s single ``--volume``/``-v`` flag into two
  distinct flags (#78). ``--data-volume`` (long form only, no short alias)
  keeps the previous behavior: bind-mount a host directory at the fixed
  ``/app/data`` target as ``rw,U`` with auto-create of the host path.
  ``--volume``/``-v`` is now a general-purpose, repeatable bind-mount flag
  accepting Podman-style ``HOST:CONTAINER[:OPTS]`` specs.
- **BREAKING:** the old bare form ``-v ./data`` (a value with no colon) is
  no longer accepted and now errors immediately, suggesting ``--data-volume``.
- General ``-v`` mounts require an **absolute** container target, reject
  ``/app/data`` (always occupied by the managed data mount), and **fail loud**
  when the host path does not exist (no directory is created). ``OPTS`` are
  passed through **verbatim** — no implicit ``U`` or ``rw`` is added. Relative
  host paths are still resolved to absolute for local execution.
- Against a **remote** host, relative host paths for both ``--data-volume`` and
  ``-v`` are now rejected (they cannot be resolved locally and Podman would
  silently treat them as named volumes rather than bind mounts). ``-v`` specs
  are also fully validated before any ``--data-volume`` directory is created, so
  an invalid ``-v`` no longer leaves a stray directory behind.

AI Assistance
-------------

- Flag split design, validation rules (fail-loud host existence, absolute
  target enforcement, ``/app/data`` rejection), implementation, and remote/local
  branch tests developed with AI assistance.
