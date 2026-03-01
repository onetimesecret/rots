# CLI Style Guide

## Command Structure

We follow a Heroku-style `topic:command` pattern:

```
rots <topic> <command> [identifiers] [flags]
```

## Instance Types

Three container types, each with explicit naming:

| Type        | Systemd Unit             | Identifier     | Use             |
| ----------- | ------------------------ | -------------- | --------------- |
| `web`       | `onetime-web@{port}`     | Port number    | HTTP servers    |
| `worker`    | `onetime-worker@{id}`    | Name or number | Background jobs |
| `scheduler` | `onetime-scheduler@{id}` | Name or number | Scheduled tasks |

## Command Syntax

```bash
# Positional identifiers with type flag
ots instances restart --web 7043 7044
ots instances restart --worker billing emails
ots instances restart --scheduler main

# Auto-discover all types when no args
ots instances stop                  # stops ALL running instances
ots instances status                # shows ALL configured instances

# Type-specific discovery
ots instances status --web          # only web instances
ots instances logs --scheduler -f   # only scheduler logs
```

## Topics

Each topic is a separate module with its own `cyclopts.App`:

| Topic       | Purpose                                 |
| ----------- | --------------------------------------- |
| `instance`  | Container lifecycle and runtime control |
| `service`   | Native systemd services (Valkey, Redis) |
| `image`     | Container image management              |
| `assets`    | Static asset management                 |
| `cloudinit` | Cloud-init configuration generation     |
| `env`       | Environment file management             |

To add a new topic, create a module and register it in `cli.py`.

## Two-Level Abstraction

Commands are categorized by their impact:

### High-level (affects config + state)

Commands that modify quadlet templates, database records, or both:

- `deploy`, `redeploy`, `undeploy`

These commands should document their config impact in the docstring.

### Low-level (runtime control only)

Commands that only interact with systemd, no config changes:

- `start`, `stop`, `restart`, `status`, `logs`, `enable`, `disable`, `exec`

These commands should explicitly state they do NOT refresh config.

## Naming Conventions

| Pattern            | Example                            | Use for                 |
| ------------------ | ---------------------------------- | ----------------------- |
| Verb               | `deploy`, `sync`                   | Actions                 |
| `--flag`           | `--force`, `--yes`                 | Boolean options         |
| `--option VALUE`   | `--delay 5`, `--lines 50`          | Value options           |
| `--type` shortcuts | `--web`, `--worker`, `--scheduler` | Instance type selection |

## Default Commands

Use `@app.default` for the "list" operation when invoking a topic without a subcommand:

```python
@app.default
def list_instances(
    identifiers: Identifiers = (),
    web: WebFlag = False,
    worker: WorkerFlag = False,
    scheduler: SchedulerFlag = False,
):
    """List instances with status and deployment info."""
    ...
```

This follows Heroku's pattern where `heroku apps` lists apps.

## Help Text

First line: Brief imperative description.
Blank line, then: Config impact and usage notes.
Include Examples section with common use cases:

```python
@app.command
def redeploy(...):
    """Regenerate quadlet and restart containers.

    Rewrites quadlet config, restarts service. Records to timeline for audit.
    Use --force to fully teardown and recreate.

    Examples:
        ots instances redeploy                      # Redeploy all running
        ots instances redeploy --web                # Redeploy web instances
        ots instances redeploy --web 7043 7044      # Redeploy specific web
        ots instances redeploy --scheduler main     # Redeploy specific scheduler
    """
```

## Adding Commands

1. Add to existing topic module, or create new topic
2. Use shared helpers from `_helpers.py` (`resolve_identifiers`, `for_each_instance`)
3. Use type annotations from `annotations.py` (`Identifiers`, `WebFlag`, etc.)
4. Document config impact and include Examples in docstring
5. Register new topics in `cli.py` via `app.command(topic.app)`
