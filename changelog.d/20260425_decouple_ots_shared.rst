Changed
-------

- Decouple ``ots-shared`` from the rots workspace. The shared library
  now lives in its own repository at
  ``https://github.com/onetimesecret/ots-shared`` and is consumed via
  PyPI (``ots-shared[ssh]>=0.4.0``). Removed the
  ``packages/ots-shared/`` source tree and the
  ``[tool.uv.workspace]``/``[tool.uv.sources]`` entries that pinned it
  as a workspace member.

Removed
-------

- ``packages/ots-shared/`` source tree (history preserved in the
  standalone ots-shared repository).
