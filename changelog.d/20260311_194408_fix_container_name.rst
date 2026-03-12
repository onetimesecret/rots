Fixed
^^^^^

- Fix quadlet ``ContainerName=`` using ``@`` character which is invalid in
  podman container names, causing the quadlet generator to silently reject
  the template and produce no systemd units. Replace ``@`` with ``-`` in
  all three templates (web, worker, scheduler) and in
  ``unit_to_container_name()`` conversion function.
