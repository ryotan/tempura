# Repository guidance

tempura maintains shared Renovate presets, Copier templates, and reusable GitHub Actions workflows.
Read [README.md](README.md) for adoption, updates, and recovery.

## Language

- Write repository documentation in English.

## Design and compatibility

- Keep one authoritative definition for each shared policy and tool version.
- Prefer native Renovate and Copier interfaces and reusable GitHub Actions components.
- Keep product-specific rules and exceptions in consumer repositories.
- Keep generated workflow references at the same source commit as their adopted Copier template.
- Verify shared-behavior changes through documented setup, update, conflict, and recovery paths.

## Validation

Use the repository's mise tasks for the affected configuration.

| Configuration | Check |
|---|---|
| Renovate presets | `mise run check:presets` |
| GitHub Actions workflows | `mise run check:workflows` and `mise run check:workflows:online` for online checks |
| Copier templates | `mise run check:templates` |
