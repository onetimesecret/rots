Removed
-------

- **Breaking:** Remove the ``--host`` / ``-H`` remote-execution flag. rots is now
  local-only — it runs on the host it manages, talking to the local podman and
  systemd. The SSH machinery (``SSHExecutor``, ``resolve_host``, ``ssh_connect``,
  the SSH connection cache) and every ``is_remote`` branch are gone;
  ``get_executor()`` now unconditionally returns ``LocalExecutor``. (#74)
- **Breaking:** Remove the ``rots host`` command group (SSH/rsync config push).
  Config now arrives on the host via the lots confext overlay
  (``/etc/onetimesecret/``) rather than being pushed over SSH. (#74)
- Drop the ``[ssh]`` extra from the ``ots-shared`` dependency, so installing rots
  no longer pulls paramiko. (#74)
- Remove ``docs/remote-execution.md``. (#74)

Changed
-------

- ``rots proxy push`` and ``rots env push`` were remote-only; they now exit with
  an error explaining that pushing to a remote host is no longer supported.
  ``rots workflow deploy`` is unaffected — it already runs over the
  sidecar/RabbitMQ transport, not SSH. (#74)

AI Assistance
-------------

- Scope analysis, the ``is_remote`` branch-collapse audit, source and test
  removal, and verification developed with AI assistance. (#74)
