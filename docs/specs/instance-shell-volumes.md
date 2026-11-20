# Ticket: Split `rots instance shell` volume flags — `--data-volume` + generalized `--volume`

## Summary

Rename the current `--volume`/`-v` flag (a host-dir bind-mount fixed at `/app/data`)
to `--data-volume`, and introduce a **new, general-purpose** `--volume`/`-v` that
accepts a full Podman-style `HOST:CONTAINER[:OPTS]` spec, repeatable, for mounting
arbitrary host paths at arbitrary container paths.

```bash
# New general mount (repeatable, arbitrary target):
rots instance shell -v ../host/path/2/dir:/app/public/branding
rots instance shell -v ./certs:/app/etc/tls:ro -v ./data:/app/data

# Old behavior, now under a dedicated flag:
rots instance shell --data-volume ./data      # bind-mount host ./data at /app/data (rw,U)
```

## Motivation

Today `--volume` only takes a host path and always mounts it at `/app/data` with
fixed `rw,U` options (`src/rots/commands/instance/app.py:2155-2163`). There is no
way to mount, say, a branding override at `/app/public/branding` or read-only TLS
material at a custom path. Operators fall back to raw `podman run`, losing the
env-file/secrets/config wiring the `shell` command provides.

This splits the two concerns:
- `--data-volume` — the ergonomic, single-purpose "give me a persistent/host
  `/app/data`" path (keeps the `U` id-mapping chown that makes files writable).
- `--volume` — the escape hatch for arbitrary mounts, matching Podman semantics.

## Current behavior (baseline)

`src/rots/commands/instance/app.py:2047-2168`:

- `--volume`/`-v` (`str | None`): host path → `-v {resolved}:/app/data:rw,U`.
  Local runs `.resolve()` + `mkdir(parents, exist_ok)`; remote runs `mkdir -p` on
  the host and use the path verbatim.
- `--persistent`/`-p` (`str | None`): named volume `ots-migration-{name}:/app/data`.
- Default: `--tmpfs /app/data`.
- `--persistent` and `--volume` are mutually exclusive (exit 1).

## Proposed behavior

### `--data-volume` (renamed from old `--volume`)

- Type `str | None`. **Loses the `-v` short alias** (goes to the new flag).
  Suggested short alias: `-D`, or none.
- Identical implementation to today's `--volume` block: bind-mount host path at
  `/app/data` with `rw,U`, local resolve+mkdir / remote `mkdir -p`.
- Remains mutually exclusive with `--persistent` (both target `/app/data`).

### `--volume` / `-v` (new, general)

- Type `tuple[str, ...]` (repeatable, like `--env`).
- Each value is a Podman mount spec: `HOST:CONTAINER[:OPTS]`.
  - **Split from the right on the first two colons max**, or document that Windows
    drive paths are unsupported (host is POSIX here). Parse as: everything up to the
    last `:CONTAINER[:OPTS]`. Simplest correct rule: `spec.rsplit(":", 2)` then
    reassemble — but a `HOST:CONTAINER` with no opts must not be mis-split. Prefer:
    - Split on `:`. If 2 parts → `host, container`, no opts.
    - If 3 parts → `host, container, opts`.
    - If >3 or <2 → error with a clear message.
  - `CONTAINER` **must be absolute** (`/...`); reject relative container targets.
  - `OPTS` passed through untouched (`ro`, `rw`, `z`, `U`, etc.). No implicit `U` —
    that's `--data-volume`'s job; general mounts are literal.
- Host path handling mirrors `--data-volume`:
  - Local: `Path(host).resolve()`, `mkdir(parents=True, exist_ok=True)` **only if it
    looks like a dir mount** — see Open Questions on auto-create vs. fail-loud.
  - Remote: `mkdir -p` the host path via executor.
- Each parsed spec appends `["-v", f"{host}:{container}[:{opts}]"]` to `cmd`.
- Ordering: emit `--data-volume`/`--persistent`/`--tmpfs` (the `/app/data` mount)
  first, then general `--volume` mounts, so an explicit `-v ...:/app/data` from the
  user visibly conflicts rather than silently interleaving. **Reject** a general
  `--volume` whose target is `/app/data` when `--data-volume`/`--persistent` is also
  set (double-mount of the same target).

## Backwards compatibility

**Breaking**: anyone using `rots instance shell -v ./data` expecting the old
"mount at /app/data" behavior now gets a parse error (`./data` has no `:CONTAINER`).
Options:

1. **Hard break** (recommended, pre-1.0 tool): document in changelog, done.
2. **Soft migration**: if a `--volume` value contains no `:`, treat it as the old
   `--data-volume` form and emit a deprecation warning. Remove after one minor
   release.

Pick one in review. Recommendation: **hard break** — the tool is internal/private
and the flag audience is small (operators run migrations, not scripts). Cleaner than
carrying dual semantics on one flag.

## Files to change

- `src/rots/commands/instance/app.py`
  - `shell()` signature (`app.py:2047`): rename `volume` param → `data_volume`
    (flag `--data-volume`), add new `volume: tuple[str, ...] = ()` (flags
    `--volume`, `-v`).
  - Mutual-exclusion check (`app.py:2101`): update message to `--data-volume`.
  - Data-volume block (`app.py:2154-2168`): rename local var, unchanged logic.
  - Add general-volume parse+append loop after the data mount.
  - Docstring examples (`app.py:2080-2095`): update `-v ./data` examples, add
    `--data-volume` and general `-v host:container` examples.
- `tests/commands/instance/test_shell.py`
  - Update `test_shell_builds_persistent_volume_command` (unaffected; `/app/data`).
  - Rename/retarget any `-v ./data`→`/app/data` assertion to `--data-volume`.
  - Add: general `-v a:/b` single mount; repeatable `-v a:/b -v c:/d:ro`;
    absolute-target validation (reject `-v a:rel`); malformed spec (`-v foo`) errors;
    conflict when general target is `/app/data` alongside `--data-volume`;
    remote-host `mkdir -p` path.
- `docs/` — any operator runbook referencing `instance shell -v` (grep found none
  beyond the source; verify at implementation time).

## Acceptance criteria

- [ ] `rots instance shell --data-volume ./data` produces `-v {abs}/data:/app/data:rw,U`.
- [ ] `rots instance shell -v ../x:/app/public/branding` produces
      `-v {abs}/../x:/app/public/branding` (host resolved, target verbatim).
- [ ] `-v` is repeatable; multiple mounts all appear in the podman cmd.
- [ ] `:ro`/`:z`/etc. opts pass through unchanged; no implicit `U` on general mounts.
- [ ] Relative container target → clear error, exit 1.
- [ ] Malformed spec (no `:`, or >3 colon parts) → clear error, exit 1.
- [ ] General `-v ...:/app/data` combined with `--data-volume`/`--persistent` → error.
- [ ] `--persistent` + `--data-volume` still mutually exclusive.
- [ ] Remote executor creates host dirs via `mkdir -p` for both flags.
- [ ] `--help` documents both flags distinctly.
- [ ] Changelog fragment records the breaking `-v` semantics change.

## Open questions

1. **Short alias for `--data-volume`?** `-D`, or leave it long-only. (Leaning
   long-only — the general `-v` is the common case now.)
2. **Auto-create host paths for general mounts?** The data-volume path auto-mkdirs.
   For arbitrary `-v`, auto-creating a mistyped host path silently mounts an empty
   dir. Consider: fail loud if the host source doesn't exist (no mkdir) for general
   `--volume`, reserving auto-create for `--data-volume`. Recommend **fail-loud**.
3. **Hard break vs. soft `no-colon` migration** on `--volume` (see Compat). Recommend
   hard break.
4. **`U` / id-mapping on general mounts** — leave to the operator via explicit
   `:U` opt. Confirm that's acceptable for the branding-override use case (files may
   be owned by host user; container reads them — `ro` usually fine without `U`).
