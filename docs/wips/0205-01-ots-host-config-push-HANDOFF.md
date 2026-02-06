# Handoff: `ots host` Config Push Pipeline

**Repo:** `onetimesecret/ots-containers`
**Branch:** `feature/plugin` (2 commits ahead of `main`)
**Directory:** `/Users/d/Projects/opensource/onetime/ots-containers`
**Serena project:** `ots-containers` (activate first)

## What We're Doing

Building `ots host` — a workstation-side command group for pushing config files to 10+ OTS production environments via rsync over SSH, with drift detection before push. Replaces manual copy-paste that causes paste-corruption bugs and has no verification.

Three-agent evaluation completed. Consensus reached on design.

## Key Decisions Made

**Directory layout: flat files with manifest (not mirrored remote paths)**
```
environments/
  prod-us1/
    manifest.conf       # local-name → remote-path mapping
    config.yaml
    auth.yaml
    .env
    Caddyfile
```
Rationale: navigable with `ls`, manifest doubles as documentation, avoids deeply nested trees.

**Environment resolution: SSH Host aliases only.** `prod-us1` is already an SSH Host alias. No dnsmasq, no YAML inventory, no custom resolution. `rsync -e ssh file prod-us1:/path/` just works.

**Two diff tools for different purposes:**
- `ssh host cat /path | diff -u - local/file` → human-readable exploratory tool (`ots host diff`)
- `rsync -n --itemize-changes` → automated safety gate inside `ots host push`

**rsync flags:** `-avz --checksum --backup --suffix=".TIMESTAMP.bak" -e ssh`, dry-run by default.

**macOS rsync caveat:** Apple ships `openrsync` (2.6.9 compat) with unreliable `--checksum`. Tool should check version, warn if < 3.x, accept `RSYNC_PATH` override.

**Skip for now:** SQLite `config_drift` table (live diff sufficient), dnsmasq, paramiko, `ots host use` persistence.

## Current State

**Committed on `feature/plugin`:**
- `6366ed5` — Vision doc (`0205-the-vision.md`) with full design: ambient env context, package structure, layered tooling stack, schema sketches

**Uncommitted:**
- `0205-the-vision.md` has 120 lines of additions: manual process documentation (FHS file layout, all config files, podman secrets, quadlet templates), gaps analysis (environment inventory, post-push health check, rollback), framing section

**Not yet created:**
- `environments/` directory structure
- Shell script prototype (`push-config`)
- Python `commands/host/` sub-app

## Build Order (consensus from 3-agent evaluation)

| Phase | What | Status |
|-------|------|--------|
| 1 | Shell `push-config` + `environments/` layout | Not started |
| 1.5 | Add `--check` drift detection to shell script | Not started |
| 2 | Pre-push diff shell function (`ots_host_diff`) | Not started |
| 3 | Python `ots host push/diff/status` cyclopts sub-app | Not started |
| 4 | SQLite tracking, `--all` parallel push, `ots host audit` | Not started |

## Key Files

- `0205-the-vision.md` — Vision doc (uncommitted changes, the design source of truth)
- `src/ots_containers/cli.py` — Entry point, where `host` sub-app registers
- `src/ots_containers/commands/instance/app.py` — Reference pattern for cyclopts sub-app
- `src/ots_containers/commands/service/app.py` — Another reference pattern
- `src/ots_containers/db.py` — Existing SQLite patterns for deployment tracking
- `src/ots_containers/config.py` — Config dataclass, env var patterns

## Cyclopts Integration Pattern

```python
# In cli.py, after existing registrations:
try:
    from .commands import host
    app.command(host.app)
except ImportError:
    pass
```

```
src/ots_containers/commands/host/
    __init__.py
    app.py              # cyclopts.App(name="host")
    _manifest.py        # manifest parsing
    _rsync.py           # rsync/ssh subprocess wrappers
```

No new Python dependencies — rsync/ssh are system tools via `subprocess.run`.

## Commands to Resume

```bash
cd /Users/d/Projects/opensource/onetime/ots-containers
git --no-pager status
git --no-pager diff 0205-the-vision.md    # see uncommitted vision doc additions

# Activate serena
# read memory: project-overview

# To start Phase 1:
# 1. Create environments/prod-us1/manifest.conf (and one more env)
# 2. Write push-config shell script
# 3. Test with --dry-run against a real environment
```

## Evaluation Artifacts (not persisted, summarized here)

The 3-agent evaluation produced detailed analysis across:
- **rsync evaluator**: Flag compatibility (macOS openrsync vs Homebrew 3.x), 15-min shell function spec, what rsync gives free (atomic transfer, checksums, backup) vs what needs Python (SQLite, orchestration, audit)
- **drift evaluator**: `ssh cat | diff` vs `rsync --dry-run` (complementary not redundant), parallelization via `xargs -P` for shell / `ThreadPoolExecutor` for Python, edge cases (permissions matter for secrets files, content diff sufficient for YAML)
- **command designer**: manifest.conf format, phased UX, "20 files to one command" walkthrough (70% of time is context-switching, not copying), security analysis (rsync strictly safer than manual scp)
