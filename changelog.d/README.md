# Changelog Fragments

This directory contains changelog fragments managed by [scriv](https://scriv.readthedocs.io/).

## Developer Workflow

```bash
# Create a new fragment
scriv create

# Edit the generated fragment file
# Add entries under the appropriate categories

# Commit the fragment with your code changes
git add changelog.d/*.rst
git commit
```

## Fragment Categories

- **Added** — New features
- **Changed** — Changes to existing functionality
- **Deprecated** — Features marked for future removal
- **Removed** — Features removed in this release
- **Fixed** — Bug fixes
- **Security** — Vulnerability fixes or security improvements
- **Documentation** — Documentation updates
- **AI Assistance** — Work developed with AI assistance (security analysis, implementation, testing, code review)

## Release Process

```bash
# Aggregate all fragments into CHANGELOG.rst
scriv collect
```

This merges all fragment files into `CHANGELOG.rst` under a versioned heading
and removes the individual fragment files.

## Guidelines

- One fragment per PR or logical change
- Use present tense ("Add feature" not "Added feature")
- Reference related issues/PRs where relevant (e.g., `#42`)
- Keep entries concise but descriptive
- Use the **AI Assistance** category to document AI contributions transparently
