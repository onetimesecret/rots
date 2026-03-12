# tests/test_quadlet_schema.py
"""Validate rendered Podman Quadlet files against the quadlet schema.

Covers four test layers:
  1. INI-aware section validation of rendered output
  2. Template static analysis (pre-render, raw template strings)
  3. _build_fmt_vars output validation
  4. Integration with podman-system-generator (conditional, requires podman)

Plus schema validator integration tests against real rendered output.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from rots.quadlet import (
    IMAGE_TEMPLATE,
    SCHEDULER_TEMPLATE,
    WEB_TEMPLATE,
    WORKER_TEMPLATE,
    _build_fmt_vars,
    render_image_template,
    render_scheduler_template,
    render_web_template,
    render_worker_template,
)
from rots.quadlet_schema import (
    VALID_SECTIONS,
    parse_quadlet_sections,
    validate_container_file,
    validate_image_file,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Section header regex (mirrors the one in quadlet_schema.py)
_SECTION_RE = re.compile(r"^\[([A-Za-z]+)\]\s*$")

# The set of sections valid for .container files
_VALID_CONTAINER_SECTIONS = VALID_SECTIONS["container"]

# Conventional section order for .container quadlet files
_CONVENTIONAL_ORDER = ["Unit", "Service", "Container", "Install"]


def _make_cfg(mocker, tmp_path, *, registry=None):
    """Return a minimal Config mock consistent with the project convention."""
    from rots.config import Config

    cfg = mocker.MagicMock(spec=Config)
    cfg.existing_config_files = []
    cfg.memory_max = None
    cfg.cpu_quota = None
    cfg.valkey_service = None
    cfg.registry = registry
    cfg.config_dir = tmp_path / "etc"
    cfg.resolved_image_with_tag.return_value = "ghcr.io/test/image:v1.0.0"
    cfg.get_existing_config_files.return_value = []
    if registry:
        cfg.get_registry_auth_file.return_value = Path("/etc/containers/auth.json")
    return cfg


def _extract_section_names(content: str) -> list[str]:
    """Return an ordered list of section header names found in content."""
    names = []
    for line in content.splitlines():
        m = _SECTION_RE.match(line.strip())
        if m:
            names.append(m.group(1))
    return names


# ---------------------------------------------------------------------------
# Layer 1: INI-aware section validation of rendered output
# ---------------------------------------------------------------------------


class TestSectionValidation:
    """.container rendered output must only contain valid sections in order."""

    @pytest.fixture(
        params=[
            pytest.param("web", id="web"),
            pytest.param("worker", id="worker"),
            pytest.param("scheduler", id="scheduler"),
        ]
    )
    def rendered_container(self, request, mocker, tmp_path):
        """Render a .container template (no registry) and return its content."""
        cfg = _make_cfg(mocker, tmp_path)
        if request.param == "web":
            return render_web_template(cfg, force=True)
        elif request.param == "worker":
            return render_worker_template(cfg, force=True)
        else:
            return render_scheduler_template(cfg, force=True)

    @pytest.fixture(
        params=[
            pytest.param("web", id="web"),
            pytest.param("worker", id="worker"),
            pytest.param("scheduler", id="scheduler"),
        ]
    )
    def rendered_container_with_registry(self, request, mocker, tmp_path):
        """Render a .container template (with registry) and return its content."""
        cfg = _make_cfg(mocker, tmp_path, registry="registry.example.com")
        if request.param == "web":
            return render_web_template(cfg, force=True)
        elif request.param == "worker":
            return render_worker_template(cfg, force=True)
        else:
            return render_scheduler_template(cfg, force=True)

    def test_only_valid_container_sections(self, rendered_container):
        """Rendered .container files must only contain valid .container sections."""
        sections = set(_extract_section_names(rendered_container))
        invalid = sections - _VALID_CONTAINER_SECTIONS
        assert not invalid, f"Invalid sections in .container file: {invalid}"

    def test_no_build_section(self, rendered_container):
        """Rendered .container files must not have a [Build] section."""
        sections = _extract_section_names(rendered_container)
        assert "Build" not in sections

    def test_no_image_section(self, rendered_container):
        """Rendered .container files must not have an [Image] section."""
        sections = _extract_section_names(rendered_container)
        assert "Image" not in sections

    def test_only_valid_sections_with_registry(self, rendered_container_with_registry):
        """Registry-configured .container files must also only contain valid sections."""
        sections = set(_extract_section_names(rendered_container_with_registry))
        invalid = sections - _VALID_CONTAINER_SECTIONS
        assert not invalid, f"Invalid sections in .container file (registry): {invalid}"

    def test_no_build_section_with_registry(self, rendered_container_with_registry):
        """Registry-configured .container files must not have [Build]."""
        sections = _extract_section_names(rendered_container_with_registry)
        assert "Build" not in sections

    def test_no_duplicate_section_headers(self, rendered_container):
        """No section header should appear more than once."""
        sections = _extract_section_names(rendered_container)
        duplicates = [s for s in set(sections) if sections.count(s) > 1]
        assert not duplicates, f"Duplicate section headers: {duplicates}"

    def test_no_duplicate_sections_with_registry(self, rendered_container_with_registry):
        """No section header should appear more than once (registry variant)."""
        sections = _extract_section_names(rendered_container_with_registry)
        duplicates = [s for s in set(sections) if sections.count(s) > 1]
        assert not duplicates, f"Duplicate section headers: {duplicates}"

    def test_conventional_section_order(self, rendered_container):
        """Sections should appear in conventional order: Unit, Service, Container, Install."""
        sections = _extract_section_names(rendered_container)
        # Filter to only sections that are in the conventional order list
        ordered = [s for s in sections if s in _CONVENTIONAL_ORDER]
        assert ordered == _CONVENTIONAL_ORDER, (
            f"Expected section order {_CONVENTIONAL_ORDER}, got {ordered}"
        )


# ---------------------------------------------------------------------------
# Layer 2: Template static analysis (pre-render)
# ---------------------------------------------------------------------------


class TestTemplateStaticAnalysis:
    """Validate raw template strings before any rendering."""

    @pytest.fixture(
        params=[
            pytest.param(WEB_TEMPLATE, id="web"),
            pytest.param(WORKER_TEMPLATE, id="worker"),
            pytest.param(SCHEDULER_TEMPLATE, id="scheduler"),
        ]
    )
    def container_template(self, request):
        return request.param

    def test_has_all_required_sections(self, container_template):
        """Each container template must have [Unit], [Service], [Container], [Install]."""
        sections = _extract_section_names(container_template)
        for required in ("Unit", "Service", "Container", "Install"):
            assert required in sections, f"Missing [{required}] in template"

    def test_no_build_section_in_template(self, container_template):
        """No container template should contain a [Build] section."""
        sections = _extract_section_names(container_template)
        assert "Build" not in sections

    def test_placeholder_braces_balanced(self, container_template):
        """All placeholder braces should be balanced (no stray { or })."""
        # Python str.format uses {name} for placeholders. A literal brace
        # is escaped as {{ or }}. After removing escaped braces, every { must
        # have a matching }.
        cleaned = container_template.replace("{{", "").replace("}}", "")
        opens = cleaned.count("{")
        closes = cleaned.count("}")
        assert opens == closes, f"Unbalanced braces: {opens} opening vs {closes} closing"

    def test_image_placeholder_in_container_section(self, container_template):
        """{image} placeholder must appear within [Container] section body."""
        lines = container_template.splitlines()
        in_container = False
        found = False
        for line in lines:
            m = _SECTION_RE.match(line.strip())
            if m:
                in_container = m.group(1) == "Container"
                continue
            if in_container and "{image}" in line:
                found = True
                break
        assert found, "{image} placeholder not found within [Container] section"

    def test_image_template_has_image_section(self):
        """IMAGE_TEMPLATE must have an [Image] section."""
        sections = _extract_section_names(IMAGE_TEMPLATE)
        assert "Image" in sections

    def test_image_template_no_container_section(self):
        """IMAGE_TEMPLATE must not have a [Container] section."""
        sections = _extract_section_names(IMAGE_TEMPLATE)
        assert "Container" not in sections


# ---------------------------------------------------------------------------
# Layer 3: _build_fmt_vars output validation
# ---------------------------------------------------------------------------


class TestBuildFmtVarsSchema:
    """Validate that _build_fmt_vars returns safe, correct values."""

    def test_no_section_headers_in_values(self, mocker, tmp_path):
        """No fmt_var value should contain INI section headers like [Build]."""
        cfg = _make_cfg(mocker, tmp_path)
        fmt_vars = _build_fmt_vars(cfg, None, force=True)
        for key, value in fmt_vars.items():
            value_str = str(value)
            matches = _SECTION_RE.findall(value_str)
            assert not matches, f"fmt_var {key!r} contains section header(s): {matches}"

    def test_no_section_headers_in_values_with_registry(self, mocker, tmp_path):
        """With registry set, no fmt_var value should contain section headers."""
        cfg = _make_cfg(mocker, tmp_path, registry="registry.example.com")
        fmt_vars = _build_fmt_vars(cfg, None, force=True)
        for key, value in fmt_vars.items():
            value_str = str(value)
            matches = _SECTION_RE.findall(value_str)
            assert not matches, f"fmt_var {key!r} contains section header(s): {matches}"

    def test_auth_section_not_in_fmt_vars(self, mocker, tmp_path):
        """The auth_section key was removed and must not appear in the returned dict."""
        cfg = _make_cfg(mocker, tmp_path)
        fmt_vars = _build_fmt_vars(cfg, None, force=True)
        assert "auth_section" not in fmt_vars

    def test_auth_section_not_in_fmt_vars_with_registry(self, mocker, tmp_path):
        """auth_section must not appear even when registry is configured."""
        cfg = _make_cfg(mocker, tmp_path, registry="registry.example.com")
        fmt_vars = _build_fmt_vars(cfg, None, force=True)
        assert "auth_section" not in fmt_vars

    def test_image_is_onetime_image_with_registry(self, mocker, tmp_path):
        """When registry is set, image should be 'onetime.image' (quadlet reference)."""
        cfg = _make_cfg(mocker, tmp_path, registry="registry.example.com")
        fmt_vars = _build_fmt_vars(cfg, None, force=True)
        assert fmt_vars["image"] == "onetime.image"

    def test_image_is_fqin_without_registry(self, mocker, tmp_path):
        """Without registry, image should be the fully-qualified image name."""
        cfg = _make_cfg(mocker, tmp_path)
        fmt_vars = _build_fmt_vars(cfg, None, force=True)
        assert fmt_vars["image"] == "ghcr.io/test/image:v1.0.0"

    def test_contains_expected_keys(self, mocker, tmp_path):
        """fmt_vars must contain the keys needed by container templates."""
        cfg = _make_cfg(mocker, tmp_path)
        fmt_vars = _build_fmt_vars(cfg, None, force=True)
        for expected in (
            "image",
            "config_dir",
            "secrets_section",
            "config_volumes_section",
            "resource_limits_section",
        ):
            assert expected in fmt_vars, f"Missing expected key: {expected}"


# ---------------------------------------------------------------------------
# Layer 4: Integration with podman-system-generator (conditional)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(
    not shutil.which("podman"),
    reason="podman not installed; skipping generator integration test",
)
class TestPodmanGeneratorIntegration:
    """Write rendered quadlet to tmp dir and run the podman generator."""

    @pytest.fixture(
        params=[
            pytest.param("web", id="web"),
            pytest.param("worker", id="worker"),
            pytest.param("scheduler", id="scheduler"),
        ]
    )
    def quadlet_file(self, request, mocker, tmp_path):
        """Render a container template and write it to a temp .container file."""
        cfg = _make_cfg(mocker, tmp_path)
        if request.param == "web":
            content = render_web_template(cfg, force=True)
            filename = "onetime-web@.container"
        elif request.param == "worker":
            content = render_worker_template(cfg, force=True)
            filename = "onetime-worker@.container"
        else:
            content = render_scheduler_template(cfg, force=True)
            filename = "onetime-scheduler@.container"

        quadlet_dir = tmp_path / "quadlets"
        quadlet_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        path = quadlet_dir / filename
        path.write_text(content)
        return quadlet_dir, output_dir, path

    def test_generator_accepts_quadlet(self, quadlet_file):
        """podman-system-generator should process the quadlet without errors."""
        quadlet_dir, output_dir, path = quadlet_file

        # The generator binary location varies by installation
        generator = shutil.which("/usr/lib/podman/quadlet") or shutil.which(
            "/usr/libexec/podman/quadlet"
        )
        if generator is None:
            pytest.skip("quadlet generator binary not found")

        result = subprocess.run(
            [generator, "--dryrun"],
            env={
                **dict(__import__("os").environ),
                "QUADLET_UNIT_DIRS": str(quadlet_dir),
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        # Check for "Unsupported key" warnings in stderr
        unsupported = [
            line
            for line in result.stderr.splitlines()
            if "Unsupported key" in line or "Unsupported section" in line
        ]
        assert not unsupported, "Generator reported unsupported keys/sections:\n" + "\n".join(
            unsupported
        )
        assert result.returncode == 0, f"Generator exited {result.returncode}:\n{result.stderr}"


# ---------------------------------------------------------------------------
# Schema validator integration tests
# ---------------------------------------------------------------------------


class TestValidateContainerRendered:
    """validate_container_file() against real rendered template output."""

    @pytest.mark.parametrize("template_name", ["web", "worker", "scheduler"])
    def test_valid_rendered_template(self, template_name, mocker, tmp_path):
        """validate_container_file should return no errors for all three templates."""
        cfg = _make_cfg(mocker, tmp_path)
        if template_name == "web":
            content = render_web_template(cfg, force=True)
        elif template_name == "worker":
            content = render_worker_template(cfg, force=True)
        else:
            content = render_scheduler_template(cfg, force=True)

        errors = validate_container_file(content)
        # Filter out warnings -- only actual errors matter for validity
        real_errors = [e for e in errors if not e.startswith("warning:")]
        assert not real_errors, "Validation errors:\n" + "\n".join(real_errors)

    @pytest.mark.parametrize("template_name", ["web", "worker", "scheduler"])
    def test_valid_rendered_template_with_registry(self, template_name, mocker, tmp_path):
        """With registry, validate_container_file should also return no errors."""
        cfg = _make_cfg(mocker, tmp_path, registry="registry.example.com")
        if template_name == "web":
            content = render_web_template(cfg, force=True)
        elif template_name == "worker":
            content = render_worker_template(cfg, force=True)
        else:
            content = render_scheduler_template(cfg, force=True)

        errors = validate_container_file(content)
        real_errors = [e for e in errors if not e.startswith("warning:")]
        assert not real_errors, "Validation errors:\n" + "\n".join(real_errors)

    def test_catches_injected_build_section(self, mocker, tmp_path):
        """validate_container_file must flag a [Build] section."""
        cfg = _make_cfg(mocker, tmp_path)
        content = render_web_template(cfg, force=True)
        # Inject a [Build] section at the end
        content += "\n[Build]\nAuthFile=/etc/containers/auth.json\n"

        errors = validate_container_file(content)
        build_errors = [e for e in errors if "Build" in e]
        assert build_errors, "Expected an error about [Build] section"

    def test_catches_unknown_container_key(self, mocker, tmp_path):
        """validate_container_file must flag unknown keys in [Container]."""
        cfg = _make_cfg(mocker, tmp_path)
        content = render_web_template(cfg, force=True)
        # Inject a bogus key into the [Container] section
        content = content.replace(
            "Network=host",
            "Network=host\nBogusKey=bogus_value",
        )

        errors = validate_container_file(content)
        bogus_errors = [e for e in errors if "BogusKey" in e]
        assert bogus_errors, "Expected an error about unknown key 'BogusKey'"

    def test_catches_authfile_in_container_section(self, mocker, tmp_path):
        """Regression: AuthFile= inside [Container] is invalid (original bug).

        AuthFile is only valid in [Image] (for .image files) and [Build]
        (for .build files). Placing it in [Container] was the first form
        of the bug that shipped to production.
        """
        cfg = _make_cfg(mocker, tmp_path)
        content = render_web_template(cfg, force=True)
        content = content.replace(
            "Network=host",
            "AuthFile=/etc/containers/auth.json\nNetwork=host",
        )

        errors = validate_container_file(content)
        auth_errors = [e for e in errors if "AuthFile" in e]
        assert auth_errors, (
            "Expected an error about AuthFile in [Container] — "
            "AuthFile is only valid in [Image] and [Build] sections"
        )

    def test_catches_build_section_with_authfile(self, mocker, tmp_path):
        """Regression: [Build] section with AuthFile= in a .container file (second bug).

        The attempted fix moved AuthFile= into a [Build] section, but [Build]
        is only valid in .build files, not .container files.
        """
        cfg = _make_cfg(mocker, tmp_path)
        content = render_web_template(cfg, force=True)
        # Inject exactly what the second bug produced
        content = content.replace(
            "[Install]",
            "[Build]\nAuthFile=/etc/containers/auth.json\n\n[Install]",
        )

        errors = validate_container_file(content)
        build_errors = [e for e in errors if "Build" in e]
        assert build_errors, "Expected an error about [Build] section in .container file"


class TestValidateImageRendered:
    """validate_image_file() against real rendered IMAGE_TEMPLATE output."""

    def test_valid_rendered_image_template(self, mocker, tmp_path):
        """validate_image_file should return no errors for a rendered IMAGE_TEMPLATE."""
        cfg = _make_cfg(mocker, tmp_path, registry="registry.example.com")
        content = render_image_template(cfg)

        errors = validate_image_file(content)
        real_errors = [e for e in errors if not e.startswith("warning:")]
        assert not real_errors, "Validation errors:\n" + "\n".join(real_errors)

    def test_catches_injected_container_section(self, mocker, tmp_path):
        """validate_image_file must flag a [Container] section in .image content."""
        cfg = _make_cfg(mocker, tmp_path, registry="registry.example.com")
        content = render_image_template(cfg)
        content += "\n[Container]\nImage=test\n"

        errors = validate_image_file(content)
        container_errors = [e for e in errors if "Container" in e]
        assert container_errors, "Expected an error about [Container] in .image file"


class TestParseQuadletSections:
    """Test the parse_quadlet_sections() parser itself."""

    def test_multi_value_keys(self):
        """Multiple Volume= lines should all be collected."""
        content = """\
[Container]
Image=test
Volume=/a:/b
Volume=/c:/d
Volume=/e:/f
"""
        sections = parse_quadlet_sections(content)
        assert "Container" in sections
        assert sections["Container"]["Volume"] == ["/a:/b", "/c:/d", "/e:/f"]

    def test_ignores_comments(self):
        """Lines starting with # should be ignored."""
        content = """\
# This is a comment
[Unit]
# Another comment
Description=Test
# Yet another comment
"""
        sections = parse_quadlet_sections(content)
        assert "Unit" in sections
        assert sections["Unit"]["Description"] == ["Test"]
        # No key named '#' or similar should exist
        for key in sections["Unit"]:
            assert not key.startswith("#")

    def test_ignores_blank_lines(self):
        """Blank lines should be ignored without error."""
        content = """\

[Unit]

Description=Test

[Container]

Image=test

"""
        sections = parse_quadlet_sections(content)
        assert "Unit" in sections
        assert "Container" in sections
        assert sections["Unit"]["Description"] == ["Test"]
        assert sections["Container"]["Image"] == ["test"]

    def test_pre_section_lines_discarded(self):
        """Lines before the first section header should be silently discarded."""
        content = """\
# File header comment
some_orphan_key=orphan_value

[Unit]
Description=Test
"""
        sections = parse_quadlet_sections(content)
        assert "Unit" in sections
        assert sections["Unit"]["Description"] == ["Test"]
        # The orphan key should not appear in any section
        for section_data in sections.values():
            assert "some_orphan_key" not in section_data

    def test_real_rendered_output(self, mocker, tmp_path):
        """Parser should handle real rendered template output correctly."""
        cfg = _make_cfg(mocker, tmp_path)
        content = render_web_template(cfg, force=True)
        sections = parse_quadlet_sections(content)

        assert "Unit" in sections
        assert "Service" in sections
        assert "Container" in sections
        assert "Install" in sections
        assert sections["Container"]["Image"] == ["ghcr.io/test/image:v1.0.0"]
        assert sections["Container"]["Network"] == ["host"]
