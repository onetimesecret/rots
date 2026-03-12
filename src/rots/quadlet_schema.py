# src/rots/quadlet_schema.py
"""
Validation of rendered Podman Quadlet files against the specification.

Podman Quadlet files (.container, .image, .volume, etc.) use an INI-style
format with section headers that vary by file type.  A common source of
bugs is placing a key or section that belongs to one file type into
another — for example, putting ``AuthFile=`` (valid in ``.image`` files)
into a ``[Build]`` section inside a ``.container`` file.

This module provides schema dictionaries derived from the Podman 5.4
specification (``podman-systemd.unit(5)``) and validation functions that
check rendered quadlet content against those schemas at both test time
and runtime.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

__all__ = [
    "REQUIRED_CONTAINER_KEYS",
    "VALID_CONTAINER_KEYS",
    "VALID_IMAGE_KEYS",
    "VALID_SECTIONS",
    "parse_quadlet_sections",
    "validate_container_file",
    "validate_image_file",
]

# ---------------------------------------------------------------------------
# Schema definitions
# ---------------------------------------------------------------------------

# Valid section headers per Quadlet file type.
# Each file type has its own type-specific section (e.g. [Container] for
# .container files) plus the common systemd pass-through sections.
VALID_SECTIONS: dict[str, set[str]] = {
    "container": {"Unit", "Service", "Container", "Install", "Quadlet"},
    "image": {"Unit", "Service", "Image", "Install", "Quadlet"},
    "volume": {"Unit", "Service", "Volume", "Install", "Quadlet"},
    "network": {"Unit", "Service", "Network", "Install", "Quadlet"},
    "build": {"Unit", "Service", "Build", "Install", "Quadlet"},
    "kube": {"Unit", "Service", "Kube", "Install", "Quadlet"},
    "pod": {"Unit", "Service", "Pod", "Install", "Quadlet"},
}

# Valid keys per section for .container files.
#
# The [Container] key set is exhaustive per the Podman 5.4 specification.
# Unknown keys in this section are errors — they indicate a typo or a key
# that belongs to a different file type.
#
# The [Unit] and [Service] sections are pass-through to systemd, so only
# a relaxed set of commonly-used keys is listed.  Unknown keys in these
# sections produce warnings rather than errors, since systemd accepts many
# keys we don't enumerate here.
VALID_CONTAINER_KEYS: dict[str, set[str]] = {
    "Container": {
        # From Podman 5.4 docs, podman-systemd.unit(5) — [Container] section
        "AddCapability",
        "AddDevice",
        "AddHost",
        "Annotation",
        "AutoUpdate",
        "CgroupsMode",
        "ContainerName",
        "ContainersConfModule",
        "DNS",
        "DNSOption",
        "DNSSearch",
        "DropCapability",
        "Entrypoint",
        "Environment",
        "EnvironmentFile",
        "EnvironmentHost",
        "Exec",
        "ExposeHostPort",
        "GIDMap",
        "GlobalArgs",
        "Group",
        "GroupAdd",
        "HealthCmd",
        "HealthInterval",
        "HealthLogDestination",
        "HealthMaxLogCount",
        "HealthMaxLogSize",
        "HealthOnFailure",
        "HealthRetries",
        "HealthStartPeriod",
        "HealthStartupCmd",
        "HealthStartupInterval",
        "HealthStartupRetries",
        "HealthStartupSuccess",
        "HealthStartupTimeout",
        "HealthTimeout",
        "HostName",
        "Image",
        "IP",
        "IP6",
        "Label",
        "LogDriver",
        "LogOpt",
        "Mask",
        "Mount",
        "Network",
        "NetworkAlias",
        "NoNewPrivileges",
        "Notify",
        "PidsLimit",
        "Pod",
        "PodmanArgs",
        "PublishPort",
        "Pull",
        "ReadOnly",
        "ReadOnlyTmpfs",
        "Rootfs",
        "RunInit",
        "SeccompProfile",
        "Secret",
        "SecurityLabelDisable",
        "SecurityLabelFileType",
        "SecurityLabelLevel",
        "SecurityLabelNested",
        "SecurityLabelType",
        "ShmSize",
        "StartWithPod",
        "StopSignal",
        "StopTimeout",
        "SubGIDMap",
        "SubUIDMap",
        "Sysctl",
        "Timezone",
        "Tmpfs",
        "UIDMap",
        "Ulimit",
        "Unmask",
        "User",
        "UserNS",
        "Volume",
        "WorkingDir",
    },
    # [Unit] and [Service] are pass-through to systemd.  The sets below
    # cover commonly-used keys; unknown keys in these sections produce
    # warnings (not errors) since systemd accepts many more directives
    # than are listed here.
    "Unit": {
        "Description",
        "After",
        "Before",
        "Wants",
        "Requires",
        "BindsTo",
        "Conflicts",
        "Documentation",
        "PartOf",
        "Requisite",
        "StopWhenUnneeded",
    },
    "Service": {
        "Restart",
        "RestartSec",
        "TimeoutStopSec",
        "TimeoutStartSec",
        "Type",
        "ExecStartPre",
        "ExecStartPost",
        "ExecStop",
        "ExecStopPost",
        "ExecReload",
        "Environment",
        "EnvironmentFile",
        "WorkingDirectory",
        "User",
        "Group",
        "MemoryMax",
        "CPUQuota",
        "LimitNOFILE",
        "LimitNPROC",
        "Delegate",
        "KillMode",
        "KillSignal",
        "SyslogIdentifier",
        "StandardOutput",
        "StandardError",
        "RemainAfterExit",
    },
    "Install": {
        "WantedBy",
        "RequiredBy",
        "Also",
        "Alias",
        "DefaultInstance",
    },
    "Quadlet": {
        "DefaultDependencies",
    },
}

# Valid keys for [Image] section in .image files.
VALID_IMAGE_KEYS: dict[str, set[str]] = {
    "Image": {
        "AllTags",
        "Arch",
        "AuthFile",
        "CertDir",
        "ContainersConfModule",
        "Creds",
        "DecryptionKey",
        "GlobalArgs",
        "Image",
        "ImageTag",
        "OS",
        "PodmanArgs",
        "TLSVerify",
        "Variant",
    },
}

# Required keys per section for .container files.
REQUIRED_CONTAINER_KEYS: dict[str, set[str]] = {
    "Container": {"Image"},
}

# Sections that are pass-through to systemd (unknown keys are warnings).
_PASSTHROUGH_SECTIONS = {"Unit", "Service"}

# Regex for section headers: [SectionName]
_SECTION_RE = re.compile(r"^\[([A-Za-z]+)\]\s*$")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_quadlet_sections(content: str) -> dict[str, dict[str, list[str]]]:
    """Parse a quadlet file into ``{section: {key: [values]}}``.

    Strips comments and blank lines.  Handles keys that appear multiple
    times (e.g. multiple ``Volume=`` or ``Secret=`` lines) by collecting
    all values into a list.

    Lines that appear before any section header are silently discarded
    (quadlet files should not have pre-section content, but comments
    before the first ``[Unit]`` are common and harmless).

    Returns:
        A dict mapping section names to dicts of key -> list-of-values.
        For example::

            {
                "Unit": {"Description": ["My Service"], "After": ["network.target"]},
                "Container": {"Image": ["docker.io/library/nginx"], "Volume": ["/a:/b", "/c:/d"]},
            }
    """
    sections: dict[str, dict[str, list[str]]] = {}
    current_section: str | None = None

    for line in content.splitlines():
        stripped = line.strip()

        # Skip blanks and comments
        if not stripped or stripped.startswith("#"):
            continue

        # Check for section header
        match = _SECTION_RE.match(stripped)
        if match:
            section_name = match.group(1)
            if section_name is not None:
                current_section = section_name
                if section_name not in sections:
                    sections[section_name] = {}
            continue

        # Key=value pair — only meaningful if we're inside a section
        if current_section is None or "=" not in stripped:
            continue

        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        sections[current_section].setdefault(key, []).append(value)

    return sections


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def _validate_sections_and_keys(
    content: str,
    file_type: str,
    type_specific_keys: dict[str, set[str]],
    required_keys: dict[str, set[str]] | None = None,
) -> list[str]:
    """Shared validation logic for any quadlet file type.

    Args:
        content: The rendered quadlet file content.
        file_type: One of the keys in ``VALID_SECTIONS`` (e.g. "container").
        type_specific_keys: Key sets for the type-specific section(s)
            (e.g. ``VALID_CONTAINER_KEYS`` or ``VALID_IMAGE_KEYS``).
        required_keys: Optional dict of ``{section: {required_key, ...}}``.

    Returns:
        A list of error/warning strings.  Empty means valid.
        Warnings are prefixed with ``"warning: "``; everything else is
        an error.
    """
    errors: list[str] = []
    valid_sections = VALID_SECTIONS.get(file_type)
    if valid_sections is None:
        errors.append(f"Unknown file type: {file_type!r}")
        return errors

    # Detect duplicate section headers (the parser merges them, so we
    # need a separate pass over the raw content).
    seen_sections: list[str] = []
    for line in content.splitlines():
        match = _SECTION_RE.match(line.strip())
        if match:
            section_name = match.group(1)
            if section_name in seen_sections:
                errors.append(f"Duplicate section header [{section_name}]")
            seen_sections.append(section_name)

    sections = parse_quadlet_sections(content)

    # Check for invalid sections
    for section_name in sections:
        if section_name not in valid_sections:
            errors.append(
                f"Invalid section [{section_name}] in .{file_type} file "
                f"(valid sections: {', '.join(sorted(valid_sections))})"
            )

    # Build the combined key lookup: type-specific keys + common keys
    # from VALID_CONTAINER_KEYS (which includes Unit, Service, Install, Quadlet)
    all_known_keys: dict[str, set[str]] = {}
    # Start with the pass-through / common sections from VALID_CONTAINER_KEYS
    for section_name in ("Unit", "Service", "Install", "Quadlet"):
        if section_name in VALID_CONTAINER_KEYS:
            all_known_keys[section_name] = VALID_CONTAINER_KEYS[section_name]
    # Layer on the type-specific keys
    all_known_keys.update(type_specific_keys)

    # Validate keys within each section
    for section_name, keys_dict in sections.items():
        if section_name not in valid_sections:
            # Already reported as an invalid section; skip key checks
            continue

        known_keys = all_known_keys.get(section_name)
        if known_keys is None:
            # No key set defined for this section — skip validation
            continue

        for key in keys_dict:
            if key not in known_keys:
                if section_name in _PASSTHROUGH_SECTIONS:
                    # Warn only — systemd accepts many keys we don't enumerate
                    errors.append(
                        f"warning: Unknown key {key!r} in [{section_name}] "
                        f"(not in known {section_name} key set; may be valid "
                        f"for systemd)"
                    )
                else:
                    errors.append(
                        f"Invalid key {key!r} in [{section_name}] section of .{file_type} file"
                    )

    # Check required keys
    if required_keys:
        for section_name, req_keys in required_keys.items():
            section_data = sections.get(section_name, {})
            for req_key in req_keys:
                if req_key not in section_data:
                    errors.append(f"Missing required key {req_key!r} in [{section_name}]")

    return errors


def validate_container_file(content: str) -> list[str]:
    """Validate a rendered ``.container`` quadlet file against the Podman spec.

    Returns a list of error/warning strings.  An empty list means the
    file is valid.

    Checks performed:

    - Only sections valid for ``.container`` files are present
    - Keys within ``[Container]`` are from the known Podman 5.4 key set
    - Keys in ``[Unit]`` / ``[Service]`` that are not in the relaxed
      known set produce warnings (prefixed ``"warning: "``) rather
      than errors, since these sections pass through to systemd
    - Required keys (e.g. ``Image`` in ``[Container]``) are present
    - No duplicate section headers
    """
    return _validate_sections_and_keys(
        content,
        file_type="container",
        type_specific_keys={"Container": VALID_CONTAINER_KEYS["Container"]},
        required_keys=REQUIRED_CONTAINER_KEYS,
    )


def validate_image_file(content: str) -> list[str]:
    """Validate a rendered ``.image`` quadlet file against the Podman spec.

    Returns a list of error/warning strings.  An empty list means the
    file is valid.

    Checks performed:

    - Only sections valid for ``.image`` files are present
    - Keys within ``[Image]`` are from the known Podman 5.4 key set
    - No duplicate section headers
    """
    return _validate_sections_and_keys(
        content,
        file_type="image",
        type_specific_keys=VALID_IMAGE_KEYS,
    )
