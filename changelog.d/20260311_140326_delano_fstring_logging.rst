Changed
~~~~~~~

- Standardize all logger calls on f-strings (modern Python 3 convention),
  converting 267 %-style format calls across 18 source modules
- Route diagnostic output through Python logging instead of bare print()
- Add CLIFormatter that omits level/module prefix for INFO messages,
  preserving the existing UX for status output
- Add flush_output() and apply_quiet() utilities for subprocess handoff
  and per-command quiet mode

Fixed
~~~~~

- Remove leading newline from "Stopped" log message in instance run command
- Restore dry-run test assertion in test_config_transform using stderr capture
- Fix container name pattern to match Quadlet ContainerName= convention
- Fix image/tag/registry resolution for CLI overrides
