Fixed
^^^^^

- Fix invalid ``AuthFile=`` placement in Quadlet ``.container`` files.
  ``AuthFile`` is only valid in ``[Image]`` (``.image``) and ``[Build]``
  (``.build``) sections per the Podman 5.4 Quadlet spec — not ``[Container]``.
  Replace with a companion ``onetime.image`` Quadlet unit that handles
  registry authentication correctly. (#33)

Added
^^^^^

- Add ``quadlet_schema.py`` spec validator that checks generated Quadlet files
  against the Podman 5.4 key/section specification, covering Container, Image,
  Build, Network, Volume, Pod, and Kube file types.
- Add quadlet generator validation to CI: installs Podman on the Ubuntu runner
  and feeds generated Quadlet output through the actual ``quadlet`` parser to
  catch spec violations that static analysis would miss.
- Add ``render_image_unit()`` and ``write_image_unit()`` for generating
  companion ``.image`` Quadlet units when a private registry is configured.

Changed
^^^^^^^

- Fix ``[tool.pytest]`` to ``[tool.pytest.ini_options]`` in ``pyproject.toml``
  — the previous section name was silently ignored by pytest, meaning
  ``pythonpath`` and ``testpaths`` were not being applied.

AI Assistance
^^^^^^^^^^^^^

- Implementation, schema validation design, test coverage (4 test layers,
  65 tests), and CI integration developed with Claude Code assistance.
