# Testing Guide

This guide helps contributors write tests that work reliably across development machines and production-like environments.

## The Problem

This tool manages **real system resources**: podman containers, systemd services, filesystem paths owned by root. Tests mock the commands (subprocess calls) but the code may still interact with real paths derived from mock responses.

**Example failure:**
```python
# Test mocks subprocess.run but returns a REAL path in stdout
mock_run.return_value = CompletedProcess(
    stdout="/var/lib/containers/storage/volumes/static_assets/_data\n"
)

# Code does this with the path:
assets_dir = Path(result.stdout.strip())
manifest = assets_dir / "web/dist/.vite/manifest.json"
if manifest.exists():  # <-- HITS REAL FILESYSTEM
```

On a dev machine without podman: path doesn't exist, test passes.
On production with podman: path exists, owned by root, **PermissionError**.

## Principles

### 1. Mock responses should use fake paths

**Bad:** Real system paths that may exist with different permissions
```python
stdout="/var/lib/containers/storage/volumes/static_assets/_data\n"
stdout="/etc/onetimesecret/config.yaml\n"
```

**Good:** Use `tmp_path` fixture or clearly fake paths
```python
fake_volume = tmp_path / "volume_data"
fake_volume.mkdir()
stdout=f"{fake_volume}\n"
```

### 2. Understand what the code actually touches

When mocking `subprocess.run`, trace what happens with the return value:

| Mock returns | Code does | Real filesystem touched? |
|--------------|-----------|-------------------------|
| Container ID | `podman.rm(container_id)` | No (also mocked) |
| Path string | `Path(path).exists()` | **YES** |
| Path string | `Path(path).read_text()` | **YES** |
| Path string | `Path(path).mkdir()` | **YES** |

If the code uses a path from a mock response for filesystem operations, you must either:
- Use `tmp_path` and create the structure
- Mock the filesystem operation too (e.g., `mocker.patch("pathlib.Path.exists")`)

### 3. Test permissions matter

Tests run as your user. Production runs as root or a service user.

| Path | Dev machine | Production |
|------|-------------|------------|
| `/var/lib/containers/...` | Doesn't exist | Exists, root-owned |
| `/etc/onetimesecret/...` | Doesn't exist | Exists, maybe root-owned |
| `/var/lib/onetimesecret/...` | Doesn't exist | Created by tool |
| `tmp_path` | Exists, user-owned | Exists, user-owned |

### 4. Real-world error scenarios to test

The tool runs in production with:
- **Existing volumes** that may have stale data
- **Permission boundaries** between root and service users
- **Partial state** from interrupted operations
- **Concurrent instances** on different ports

Good tests verify:
```python
def test_handles_existing_volume_with_old_assets(self, tmp_path):
    """Tool should work when volume already has data from old version."""

def test_handles_missing_config_directory(self, tmp_path):
    """Should fail gracefully if /etc/onetimesecret doesn't exist."""

def test_handles_env_file_permission_denied(self, mocker, tmp_path):
    """Should report clear error if can't write to /var/lib/..."""
```

## Patterns

### Pattern: Mock subprocess + use tmp_path for paths

```python
def test_update_extracts_assets(self, mocker, tmp_path):
    mock_run = mocker.patch("subprocess.run")

    # Use tmp_path, not real system paths
    fake_volume = tmp_path / "volume"
    fake_volume.mkdir()

    mock_run.side_effect = [
        CompletedProcess(stdout=f"{fake_volume}\n", ...),  # volume mount
        CompletedProcess(stdout="abc123\n", ...),          # create
        CompletedProcess(...),                              # cp
        CompletedProcess(...),                              # rm
    ]

    assets.update(cfg)

    # Can now safely check fake_volume contents if needed
```

### Pattern: Test config validation with real tmp structure

```python
def test_validate_missing_env_template(self, tmp_path):
    """Should fail if .env template missing."""
    (tmp_path / "config.yaml").touch()  # exists
    # .env does NOT exist

    cfg = Config(config_dir=tmp_path)

    with pytest.raises(SystemExit) as exc:
        cfg.validate()

    assert ".env" in str(exc.value)
```

### Pattern: Mock both subprocess and filesystem when needed

```python
def test_skips_manifest_check_when_not_found(self, mocker, tmp_path, capsys):
    mock_run = mocker.patch("subprocess.run")

    fake_volume = tmp_path / "volume"
    fake_volume.mkdir()
    # Note: NOT creating web/dist/.vite/manifest.json

    mock_run.side_effect = [...]

    assets.update(cfg)

    captured = capsys.readouterr()
    assert "Warning: manifest not found" in captured.out
```

## Checklist for new tests

- [ ] Any path in mock responses uses `tmp_path`, not real system paths
- [ ] If code does `Path(x).exists/read/write`, the path is either fake or that operation is mocked
- [ ] Test docstring explains what real-world scenario it covers
- [ ] Failure modes (permission denied, missing file, etc.) have explicit tests
- [ ] Tests don't assume specific podman/systemd state on the machine

## Running tests

```bash
# Local - fast, may miss permission issues
pytest tests/

# CI - runs as different user, closer to production
# (handled by GitHub Actions)

# On a real server - best validation
# Clone repo, run pytest as non-root user
```

## Common fixtures

| Fixture | Use for |
|---------|---------|
| `tmp_path` | Fake filesystem paths |
| `mocker` | Patching subprocess, systemd calls |
| `capsys` | Capturing print output |
| `monkeypatch` | Environment variables (IMAGE, TAG) |
