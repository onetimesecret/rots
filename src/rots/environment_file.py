# src/rots/environment_file.py
"""
Environment file parsing and secret management.

This module handles parsing environment files, extracting secrets,
and generating quadlet Secret= directives dynamically based on
the SECRET_VARIABLE_NAMES convention.

Convention:
    - SECRET_VARIABLE_NAMES defines which env vars are secrets
    - Supports comma, space, or colon delimited formats
    - Secret env vars are transformed: STRIPE_API_KEY -> _STRIPE_API_KEY=ots_stripe_api_key
    - Podman secret naming: env var NAME -> ots_name (lowercase with ots_ prefix)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ots_shared.ssh import is_remote as _is_remote

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ots_shared.ssh import Executor

logger = logging.getLogger(__name__)


def _get_executor(executor: Executor | None = None) -> Executor:
    """Return the given executor or a default LocalExecutor."""
    if executor is not None:
        return executor
    from ots_shared.ssh import LocalExecutor

    return LocalExecutor()


# Regex to parse env file lines: KEY=VALUE or KEY="VALUE" or KEY='VALUE'
ENV_LINE_PATTERN = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$")

# Pattern for quoted values
QUOTED_VALUE_PATTERN = re.compile(r'^(["\'])(.*)(\1)$')

SECRET_NAMES_KEY = "SECRET_VARIABLE_NAMES"
SECRET_PREFIX = "ots_"


def env_var_to_secret_name(var_name: str) -> str:
    """Convert environment variable name to podman secret name.

    Example: STRIPE_API_KEY -> ots_stripe_api_key
    """
    return f"{SECRET_PREFIX}{var_name.lower()}"


def secret_name_to_env_var(secret_name: str) -> str:
    """Convert podman secret name back to environment variable name.

    Example: ots_stripe_api_key -> STRIPE_API_KEY
    """
    if secret_name.startswith(SECRET_PREFIX):
        return secret_name[len(SECRET_PREFIX) :].upper()
    return secret_name.upper()


def parse_secret_variable_names(value: str) -> list[str]:
    """Parse SECRET_VARIABLE_NAMES value supporting multiple delimiters.

    Supported formats:
        VAR1,VAR2,VAR3           (comma-separated)
        "VAR1 VAR2 VAR3"         (space-separated, quoted)
        "VAR1:VAR2:VAR3"         (colon-separated, quoted)

    Returns:
        List of variable names (empty list if value is empty)
    """
    if not value:
        return []

    # Strip outer quotes if present
    value = value.strip()
    match = QUOTED_VALUE_PATTERN.match(value)
    if match:
        value = match.group(2)

    # Try each delimiter in order of precedence
    for delimiter in [",", " ", ":"]:
        if delimiter in value:
            return [name.strip() for name in value.split(delimiter) if name.strip()]

    # Single value or no delimiter found
    return [value.strip()] if value.strip() else []


@dataclass
class EnvEntry:
    """Represents a single entry in an environment file."""

    key: str
    value: str
    comment: str = ""  # Comment on the same line
    is_comment_line: bool = False  # True if entire line is a comment
    raw_line: str = ""  # Original line for preservation


@dataclass
class EnvFile:
    """Parsed environment file with secret management capabilities."""

    path: Path
    entries: list[EnvEntry] = field(default_factory=list)
    _variables: dict[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def parse(cls, path: Path | str, *, executor: Executor | None = None) -> EnvFile:
        """Parse an environment file.

        Preserves comments and blank lines for round-trip editing.
        """
        path = Path(path)
        entries: list[EnvEntry] = []
        variables: dict[str, str] = {}

        if _is_remote(executor):
            result = executor.run(["test", "-f", str(path)])  # type: ignore[union-attr]
            if not result.ok:
                return cls(path=path, entries=entries, _variables=variables)
            result = executor.run(["cat", str(path)])  # type: ignore[union-attr]
            if not result.ok:
                return cls(path=path, entries=entries, _variables=variables)
            text = result.stdout
        else:
            if not path.exists():
                return cls(path=path, entries=entries, _variables=variables)
            text = path.read_text()

        for line in text.splitlines():
            raw_line = line
            line = line.strip()

            # Blank line
            if not line:
                entries.append(EnvEntry(key="", value="", raw_line=raw_line))
                continue

            # Full comment line
            if line.startswith("#"):
                entries.append(
                    EnvEntry(
                        key="",
                        value="",
                        comment=line,
                        is_comment_line=True,
                        raw_line=raw_line,
                    )
                )
                continue

            # Try to parse as KEY=VALUE
            match = ENV_LINE_PATTERN.match(line)
            if match:
                key = match.group("key")
                value = match.group("value")

                # Strip quotes from value if present
                quoted_match = QUOTED_VALUE_PATTERN.match(value)
                if quoted_match:
                    value = quoted_match.group(2)

                entries.append(EnvEntry(key=key, value=value, raw_line=raw_line))
                variables[key] = value
            else:
                # Unparseable line, preserve as comment
                entries.append(
                    EnvEntry(
                        key="",
                        value="",
                        comment=line,
                        is_comment_line=True,
                        raw_line=raw_line,
                    )
                )

        return cls(path=path, entries=entries, _variables=variables)

    def get(self, key: str, default: str = "") -> str:
        """Get a variable value."""
        return self._variables.get(key, default)

    def set(self, key: str, value: str) -> None:
        """Set or update a variable value."""
        self._variables[key] = value
        # Update existing entry or add new one
        for entry in self.entries:
            if entry.key == key:
                entry.value = value
                return
        # Add new entry
        self.entries.append(EnvEntry(key=key, value=value, raw_line=f"{key}={value}"))

    def remove(self, key: str) -> str | None:
        """Remove a variable, returning its value if it existed."""
        value = self._variables.pop(key, None)
        self.entries = [e for e in self.entries if e.key != key]
        return value

    def rename(self, old_key: str, new_key: str, new_value: str | None = None) -> bool:
        """Rename a variable in place, preserving its position.

        Args:
            old_key: The current key name
            new_key: The new key name
            new_value: Optional new value (keeps existing value if None)

        Returns:
            True if renamed, False if old_key not found
        """
        if old_key not in self._variables:
            return False

        # Update internal dict
        old_value = self._variables.pop(old_key)
        final_value = new_value if new_value is not None else old_value
        self._variables[new_key] = final_value

        # Update entry in place (preserving position)
        for entry in self.entries:
            if entry.key == old_key:
                entry.key = new_key
                entry.value = final_value
                entry.raw_line = f"{new_key}={final_value}"
                return True

        return False

    def has(self, key: str) -> bool:
        """Check if a variable exists."""
        return key in self._variables

    @property
    def secret_variable_names(self) -> list[str]:
        """Get the list of secret variable names from SECRET_VARIABLE_NAMES."""
        return parse_secret_variable_names(self.get(SECRET_NAMES_KEY))

    def iter_variables(self) -> Iterator[tuple[str, str]]:
        """Iterate over all variables as (key, value) pairs."""
        for entry in self.entries:
            if entry.key and not entry.is_comment_line:
                yield entry.key, entry.value

    def write(self, path: Path | str | None = None, *, executor: Executor | None = None) -> None:
        """Write the environment file.

        Args:
            path: Optional path to write to (defaults to original path)
            executor: Optional executor for remote writes
        """
        path = Path(path) if path else self.path
        lines: list[str] = []

        for entry in self.entries:
            if entry.is_comment_line:
                lines.append(entry.comment)
            elif not entry.key:
                lines.append("")  # Blank line
            else:
                # Quote value if it contains spaces or special chars
                value = entry.value
                if " " in value or '"' in value or "'" in value:
                    value = f'"{value}"'
                lines.append(f"{entry.key}={value}")

        content = "\n".join(lines) + "\n"
        if _is_remote(executor):
            executor.run(["tee", str(path)], input=content)  # type: ignore[union-attr]
        else:
            path.write_text(content)


@dataclass
class SecretSpec:
    """Specification for a secret to be created/managed."""

    env_var_name: str  # Original env var name (e.g., STRIPE_API_KEY)
    secret_name: str  # Podman secret name (e.g., ots_stripe_api_key)
    value: str | None = None  # Secret value (None if already processed)

    @classmethod
    def from_env_var(cls, var_name: str, value: str | None = None) -> SecretSpec:
        """Create a SecretSpec from an environment variable name."""
        return cls(
            env_var_name=var_name,
            secret_name=env_var_to_secret_name(var_name),
            value=value,
        )

    @property
    def quadlet_line(self) -> str:
        """Generate the Secret= line for quadlet template."""
        return f"Secret={self.secret_name},type=env,target={self.env_var_name}"


def is_processed_secret_entry(key: str, value: str) -> bool:
    """Check if an entry represents an already-processed secret.

    Post-processed secrets have format: _VARNAME=ots_varname
    """
    if not key.startswith("_"):
        return False
    original_name = key[1:]  # Remove underscore prefix
    expected_secret = env_var_to_secret_name(original_name)
    return value == expected_secret


def extract_secrets(env_file: EnvFile) -> tuple[list[SecretSpec], list[str]]:
    """Extract secrets from an environment file based on SECRET_VARIABLE_NAMES.

    Returns:
        Tuple of (secrets to create, warnings/messages)

    This function identifies secrets in three states:
    1. Unprocessed: VARNAME=actual_value -> needs secret creation
    2. Already processed: _VARNAME=ots_varname -> already handled
    3. Commented legacy: #VARNAME= -> needs secret but no value available
    """
    secret_names = env_file.secret_variable_names
    secrets: list[SecretSpec] = []
    messages: list[str] = []

    for var_name in secret_names:
        # Check for already-processed entry (_VARNAME=secret_name)
        processed_key = f"_{var_name}"
        if env_file.has(processed_key):
            # Entry exists with underscore prefix - already processed
            # Use actual value (may differ from calculated ots_varname pattern)
            secrets.append(SecretSpec.from_env_var(var_name, value=None))
            continue

        # Check for unprocessed entry (VARNAME=value)
        if env_file.has(var_name):
            value = env_file.get(var_name)
            if value:
                secrets.append(SecretSpec.from_env_var(var_name, value=value))
            else:
                messages.append(f"Warning: {var_name} is empty, skipping secret creation")
            continue

        # No entry found - neither VARNAME nor _VARNAME exists
        # Skip adding to secrets list so quadlet won't reference a non-existent secret
        messages.append(f"Warning: {var_name} listed in SECRET_VARIABLE_NAMES but not found")

    return secrets, messages


def process_env_file(
    env_file: EnvFile,
    *,
    create_secrets: bool = True,
    dry_run: bool = False,
    executor: Executor | None = None,
) -> tuple[list[SecretSpec], list[str]]:
    """Process an environment file: extract secrets and transform entries.

    This is the main entry point for processing. It:
    1. Extracts secrets based on SECRET_VARIABLE_NAMES
    2. Creates podman secrets for any with values (unless dry_run)
    3. Transforms env file entries: VARNAME=value -> _VARNAME=ots_varname
    4. Writes the updated env file only if changes were made (unless dry_run)

    Args:
        env_file: Parsed environment file
        create_secrets: Whether to create podman secrets
        dry_run: If True, don't write changes or create secrets
        executor: Optional executor for remote operations

    Returns:
        Tuple of (processed secrets, messages/warnings)
    """
    secrets, messages = extract_secrets(env_file)
    file_modified = False

    for spec in secrets:
        if spec.value is not None:
            # Has a value to process - this secret needs extraction
            if create_secrets and not dry_run:
                action = ensure_podman_secret(spec.secret_name, spec.value, executor=executor)
                messages.append(f"Podman secret {action}: {spec.secret_name}")

            # Transform the entry in env file (in place, preserving position)
            env_file.rename(spec.env_var_name, f"_{spec.env_var_name}", spec.secret_name)
            file_modified = True

    if not dry_run:
        if file_modified:
            env_file.write(executor=executor)
            messages.append(f"Updated environment file: {env_file.path}")
        else:
            messages.append("No changes needed (secrets already processed)")

    return secrets, messages


def ensure_podman_secret(secret_name: str, value: str, *, executor: Executor | None = None) -> str:
    """Create or replace a podman secret.

    Args:
        secret_name: Name for the secret
        value: Secret value
        executor: Optional executor for remote operations

    Returns:
        "created" if new, "replaced" if existing was overwritten
    """
    from .systemd import require_podman

    require_podman(executor=executor)

    ex = _get_executor(executor)
    result = ex.run(["podman", "secret", "exists", secret_name], timeout=15)
    existed = result.ok

    if existed:
        ex.run(["podman", "secret", "rm", secret_name], check=True, timeout=15)

    ex.run(
        ["podman", "secret", "create", secret_name, "-"],
        input=value,
        check=True,
        timeout=30,
    )

    return "replaced" if existed else "created"


def secret_exists(secret_name: str, *, executor: Executor | None = None) -> bool:
    """Check if a podman secret exists. Returns False if podman is unavailable."""
    try:
        ex = _get_executor(executor)
        result = ex.run(["podman", "secret", "exists", secret_name], timeout=10)
        return result.ok
    except Exception:
        return False


def generate_quadlet_secret_lines(secrets: list[SecretSpec]) -> str:
    """Generate the Secret= section for a quadlet template.

    Args:
        secrets: List of secret specifications

    Returns:
        Multi-line string with Secret= directives
    """
    if not secrets:
        return ""

    lines = [
        "# Secrets via Podman secret store (not on disk)",
        "# These are injected as environment variables at container start",
    ]
    for spec in secrets:
        lines.append(spec.quadlet_line)

    return "\n".join(lines)


def get_secrets_from_env_file(
    path: Path | str, *, executor: Executor | None = None
) -> list[SecretSpec]:
    """Convenience function to get secret specs from an environment file.

    This is useful for quadlet generation - it parses the env file and
    returns the secret specifications without modifying anything.
    """
    env_file = EnvFile.parse(path, executor=executor)
    secrets, _ = extract_secrets(env_file)
    return secrets


# Template for a default environment file
ENV_FILE_TEMPLATE = """\
# /etc/default/onetimesecret
#
# OneTime Secret - Environment Variables
#
# This file is sourced by the systemd quadlet container.
# Secret values listed in SECRET_VARIABLE_NAMES are stored
# in podman secrets (not in this file).
#
# Usage:
#   1. Set SECRET_VARIABLE_NAMES with your secret env var names
#   2. Add secret values as regular entries (STRIPE_API_KEY=sk_live_xxx)
#   3. Run: ots env process
#   4. Secret values are moved to podman secrets
#   5. This file is updated: _STRIPE_API_KEY=ots_stripe_api_key
#

# Secret variable names (comma, space, or colon separated)
SECRET_VARIABLE_NAMES=STRIPE_API_KEY,STRIPE_WEBHOOK_SIGNING_SECRET,SECRET,SESSION_SECRET,AUTH_SECRET,SMTP_PASSWORD

# Connection strings (not secrets - stored here)
AUTH_DATABASE_URL=
RABBITMQ_URL=
REDIS_URL=

# Mail configuration
SMTP_USERNAME=
SMTP_HOST=
SMTP_PORT=587
SMTP_AUTH=login
SMTP_TLS=true

# Core settings
HOST=
COLONEL=

# Runtime flags
AUTHENTICATION_MODE=full
SSL=true
RACK_ENV=production
"""
