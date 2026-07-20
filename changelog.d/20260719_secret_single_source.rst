Changed
-------

- Resolve container secrets from the layered EnvironmentFile set
  (``/etc/default/onetimesecret`` plus optional
  ``/etc/default/onetimesecret.local``) as the single source of truth. The
  ad-hoc ``podman run`` paths (``instance shell``, ``boot-test``, config
  transform, ``run --production``) now layer those files with ``--env-file``
  instead of injecting from the podman secret store with ``--secret``, so a
  passing ``boot-test`` guarantees the quadlet sees the same environment
  (``#76``).

Fixed
-----

- Stop the ad-hoc ``podman run`` paths from reading secrets out of the podman
  secret store while the quadlet reads only the EnvironmentFiles. On hosts whose
  secret values lived only in the store (or whose base env file held
  ``_VAR=ots_var`` placeholders from ``ots env process``), ``boot-test`` passed
  on store values but the quadlet-managed container crash-looped on a nil
  secret. Both paths now read the same files (``#76``).
- ``deploy`` and ``redeploy`` verify secrets before rendering: if any name in
  ``SECRET_VARIABLE_NAMES`` does not resolve to a non-empty value in the merged
  EnvironmentFile set, they refuse and name every missing variable rather than
  producing a silently dead container. ``--force`` downgrades the refusal to a
  warning (``#76``).

Documentation
-------------

- Add ``docs/upgrading-0.7.md`` covering the v0.6.x to v0.7.x upgrade for
  store-sourced hosts: symptom, cause, the per-secret ``.local`` remedy, and the
  new fail-loud deploy behavior (``#76``).

AI Assistance
-------------

- Code trace of the ad-hoc ``podman run`` versus quadlet secret paths, upgrade
  note, and changelog fragment developed with AI assistance.
