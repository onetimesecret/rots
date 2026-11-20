Fixed
-----

- ``instance render`` with a registry configured no longer writes the
  rendering workstation's registry credential path into ``onetime.image``.
  Off-host renders now emit ``AuthFile=/etc/containers/auth.json`` and
  resolve the image without the deployment database, rejecting the
  ``current`` and ``rollback`` aliases before any local state is read
  (``!83``).

Documentation
-------------

- Add the instance shell volumes spec
  (``docs/specs/instance-shell-volumes.md``) (``!83``).
