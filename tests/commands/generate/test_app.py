# tests/commands/generate/test_app.py
"""Tests for the generate command - standalone unit file export."""

from rots.commands.generate.app import (
    _ENV_TEMPLATE_FILENAME,
    _SCHEDULER_FILENAME,
    _WEB_FILENAME,
    _WORKER_FILENAME,
    _render_all_selected,
    _write_directory,
    _write_stdout,
)
from rots.config import Config


class TestRenderAllSelected:
    """Test template rendering selection logic."""

    def test_renders_web_only(self, tmp_path):
        cfg = Config(var_dir=tmp_path / "var")
        files = _render_all_selected(cfg, web=True, worker=False, scheduler=False, force=True)

        filenames = [f[0] for f in files]
        assert _WEB_FILENAME in filenames
        assert _WORKER_FILENAME not in filenames
        assert _SCHEDULER_FILENAME not in filenames

    def test_renders_worker_only(self, tmp_path):
        cfg = Config(var_dir=tmp_path / "var")
        files = _render_all_selected(cfg, web=False, worker=True, scheduler=False, force=True)

        filenames = [f[0] for f in files]
        assert _WORKER_FILENAME in filenames
        assert _WEB_FILENAME not in filenames

    def test_renders_scheduler_only(self, tmp_path):
        cfg = Config(var_dir=tmp_path / "var")
        files = _render_all_selected(cfg, web=False, worker=False, scheduler=True, force=True)

        filenames = [f[0] for f in files]
        assert _SCHEDULER_FILENAME in filenames
        assert _WEB_FILENAME not in filenames

    def test_renders_all_types(self, tmp_path):
        cfg = Config(var_dir=tmp_path / "var")
        files = _render_all_selected(cfg, web=True, worker=True, scheduler=True, force=True)

        filenames = [f[0] for f in files]
        assert _WEB_FILENAME in filenames
        assert _WORKER_FILENAME in filenames
        assert _SCHEDULER_FILENAME in filenames

    def test_rendered_content_contains_image(self, tmp_path, monkeypatch):
        monkeypatch.setenv("IMAGE", "ghcr.io/onetimesecret/onetimesecret")
        monkeypatch.setenv("TAG", "v1.0.0")
        cfg = Config(var_dir=tmp_path / "var")
        files = _render_all_selected(cfg, web=True, worker=False, scheduler=False, force=True)

        _name, content = files[0]
        assert "Image=ghcr.io/onetimesecret/onetimesecret:v1.0.0" in content

    def test_rendered_web_has_unit_sections(self, tmp_path):
        cfg = Config(var_dir=tmp_path / "var")
        files = _render_all_selected(cfg, web=True, worker=False, scheduler=False, force=True)

        _name, content = files[0]
        assert "[Unit]" in content
        assert "[Service]" in content
        assert "[Container]" in content
        assert "[Install]" in content

    def test_includes_image_unit_for_private_registry(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")
        monkeypatch.setenv("TAG", "v1.0.0")
        cfg = Config(var_dir=tmp_path / "var")

        # Mock the registry auth file check
        monkeypatch.setattr(cfg, "get_registry_auth_file", lambda **kw: "/etc/containers/auth.json")

        files = _render_all_selected(cfg, web=True, worker=False, scheduler=False, force=True)
        filenames = [f[0] for f in files]
        assert "onetime.image" in filenames

    def test_no_image_unit_without_registry(self, tmp_path):
        cfg = Config(var_dir=tmp_path / "var")
        files = _render_all_selected(cfg, web=True, worker=True, scheduler=True, force=True)

        filenames = [f[0] for f in files]
        assert "onetime.image" not in filenames


class TestWriteDirectory:
    """Test writing generated files to a directory."""

    def test_creates_output_directory(self, tmp_path):
        dest = tmp_path / "output" / "units"
        files = [("test.container", "[Unit]\nDescription=Test\n")]

        _write_directory(dest, files)

        assert dest.exists()
        assert (dest / "test.container").exists()

    def test_writes_file_content(self, tmp_path):
        dest = tmp_path / "output"
        content = "[Unit]\nDescription=Test Unit\n"
        files = [("test.container", content)]

        _write_directory(dest, files)

        assert (dest / "test.container").read_text() == content

    def test_writes_multiple_files(self, tmp_path):
        dest = tmp_path / "output"
        files = [
            ("web.container", "web content"),
            ("worker.container", "worker content"),
            ("scheduler.container", "scheduler content"),
        ]

        _write_directory(dest, files)

        assert (dest / "web.container").read_text() == "web content"
        assert (dest / "worker.container").read_text() == "worker content"
        assert (dest / "scheduler.container").read_text() == "scheduler content"

    def test_dry_run_does_not_create_files(self, tmp_path):
        dest = tmp_path / "output"
        files = [("test.container", "content")]

        _write_directory(dest, files, dry_run=True)

        assert not dest.exists()

    def test_overwrites_existing_files(self, tmp_path):
        dest = tmp_path / "output"
        dest.mkdir()
        (dest / "test.container").write_text("old content")

        _write_directory(dest, [("test.container", "new content")])

        assert (dest / "test.container").read_text() == "new content"


class TestWriteStdout:
    """Test stdout output mode."""

    def test_writes_with_header(self, capsys):
        files = [("test.container", "[Unit]\nDescription=Test\n")]

        _write_stdout(files)

        captured = capsys.readouterr()
        assert "# --- test.container ---" in captured.out
        assert "[Unit]" in captured.out

    def test_multiple_files_separated(self, capsys):
        files = [
            ("web.container", "web\n"),
            ("worker.container", "worker\n"),
        ]

        _write_stdout(files)

        captured = capsys.readouterr()
        assert "# --- web.container ---" in captured.out
        assert "# --- worker.container ---" in captured.out
        assert "web\n" in captured.out
        assert "worker\n" in captured.out


class TestWithEnvTemplate:
    """Test env template inclusion."""

    def test_env_template_written_to_directory(self, tmp_path):
        dest = tmp_path / "output"
        from rots.environment_file import ENV_FILE_TEMPLATE

        files = [(_ENV_TEMPLATE_FILENAME, ENV_FILE_TEMPLATE)]

        _write_directory(dest, files)

        path = dest / _ENV_TEMPLATE_FILENAME
        assert path.exists()
        content = path.read_text()
        assert "SECRET_VARIABLE_NAMES" in content

    def test_env_template_to_stdout(self, capsys):
        from rots.environment_file import ENV_FILE_TEMPLATE

        files = [(_ENV_TEMPLATE_FILENAME, ENV_FILE_TEMPLATE)]

        _write_stdout(files)

        captured = capsys.readouterr()
        assert "SECRET_VARIABLE_NAMES" in captured.out


class TestGenerateIntegration:
    """Integration tests for the generate command function."""

    def test_generate_to_directory(self, tmp_path):
        """Full generate flow writing to a directory."""
        cfg = Config(var_dir=tmp_path / "var")
        files = _render_all_selected(cfg, web=True, worker=True, scheduler=True, force=True)

        dest = tmp_path / "output"
        _write_directory(dest, files)

        assert (dest / _WEB_FILENAME).exists()
        assert (dest / _WORKER_FILENAME).exists()
        assert (dest / _SCHEDULER_FILENAME).exists()

        # Verify content is valid quadlet
        web_content = (dest / _WEB_FILENAME).read_text()
        assert "[Container]" in web_content
        assert "Network=host" in web_content

    def test_generate_with_env_template(self, tmp_path):
        """Generate with --with-env-template includes the env file."""
        from rots.environment_file import ENV_FILE_TEMPLATE

        cfg = Config(var_dir=tmp_path / "var")
        files = _render_all_selected(cfg, web=True, worker=False, scheduler=False, force=True)
        files.append((_ENV_TEMPLATE_FILENAME, ENV_FILE_TEMPLATE))

        dest = tmp_path / "output"
        _write_directory(dest, files)

        assert (dest / _WEB_FILENAME).exists()
        assert (dest / _ENV_TEMPLATE_FILENAME).exists()
