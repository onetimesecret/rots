Added
-----

- Quadlet units now mount recognized subdirectories of
  ``/etc/onetimesecret`` into containers by convention:
  ``branding/`` at ``/app/etc/branding`` and ``tls/`` at
  ``/app/etc/tls`` (read-only). Only subdirectories present on the
  host are mounted; the convention table is fixed in code, with no
  operator-supplied paths (``#80``).

AI Assistance
-------------

- Mount-convention design, quadlet and config implementation, and tests
  developed with AI assistance (``#80``).
