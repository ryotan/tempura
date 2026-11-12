# tempura

tempura maintains shared repository configuration and reusable GitHub Actions workflows.
Repositories receive updates through Renovate pull requests.

## How changes reach projects

1. A tempura maintainer changes the shared settings or workflows and publishes a new version tag.
2. Renovate opens PRs in each project to update preset references and uses Copier to update generated files.
3. The project maintainer reviews and merges the PRs to adopt the changes.

Copier generates project files from [`template/`](template/) using each project's answers and records those
answers with the adopted version. Renovate reuses them for subsequent updates.
Generated workflows call tempura's reusable workflows at that version's commit SHA. Copier updates these
references with the other generated files, including releases that change only the shared workflow implementation.

## First-time setup

Use a published tempura tag as `VERSION`. These instructions assume that Renovate is enabled for the project.

### 1. Add the Renovate presets

Replace duplicated shared settings in the project's Renovate configuration with these preset references.
Retain project-specific rules and intentional overrides.

```json5
{
  "extends": [
    "github>ryotan/tempura//renovate/default.json5#VERSION",
    "github>ryotan/tempura//renovate/copier.json5#VERSION"
  ]
}
```

The Copier preset also selects the Copier version for every template in that project. Check compatibility with
any existing Copier templates before adding it.

### 2. Generate the project files

`PROJECT_DIR` is the absolute path to the project repository's root. Check out the selected tempura version in
another directory and install its generation tools.

```sh
git clone --branch VERSION https://github.com/ryotan/tempura.git TEMPURA_DIR
cd TEMPURA_DIR
mise trust
mise install --locked python uv
mise run generate:settings -- --vcs-ref VERSION https://github.com/ryotan/tempura.git PROJECT_DIR
```

Answer the [Copier questions](copier.yml) for the project. Copier records the answers for future updates.
For non-interactive generation, add `--defaults` and supply project-specific values with `--data`, such as
`--data default_branch=develop`.

### 3. Review and commit

Review the generated changes against existing files, retaining required project-specific settings, jobs, and
triggers. Keep generated relative paths and the Copier answers file so later updates apply to the same files.
Commit the resulting files and the project's Renovate configuration together.

## Review and apply updates

The default preset applies a seven-day minimum release age to normal updates. Renovate proposes eligible
updates in each project. Review the changes and merge the PRs to apply them. Confirm that required generated
files exist and intended project-specific settings are preserved.

Copier can leave conflicts after exiting successfully and preserve local file deletions.

- Resolve generated-file conflicts or missing files on the update branch, check the result, then merge the PR.
- If the update is blocked by configuration on the project's default branch or the selected Copier engine,
  fix that prerequisite first, then request Renovate's rebase or retry.

A requested Renovate rebase regenerates the update branch and replaces manual edits on that branch.

To roll back an update, revert the complete update commit, including its generated files and Copier answers.
Copier rejects updates to an older version tag.

### Change project settings

From the selected tempura checkout, update the project with the new answers. `:current:` retains its adopted
source version. Copier updates the answers and generated files together.

```sh
mise run update:settings -- --vcs-ref :current: --data default_branch=develop PROJECT_DIR
```

When adopting a version that introduces new questions, supply values appropriate to the project. For an older
adoption without `default_branch`, select the new `VERSION` in this command and supply the actual branch.
Review any existing manual branch edits in the resulting diff.

### Copier engine updates

When an update requires a new Copier version, merge the compatible Copier preset update first, then rebase or
retry the template update PR. The Copier preset and template have separate update lifecycles.

## Maintain tempura

- Maintain shared policies in `renovate/`, project files in `template/`, and reusable workflows and their
  validation configuration in `.github/`.
- Keep the Copier versions in `pyproject.toml` and `renovate/copier.json5` aligned. Before publishing an engine
  change, verify that the new engine can update projects using the previous template version.
- Publish changes as new version tags. Keep published tags immutable.

The tool lockfile covers Linux x64 and macOS arm64.

### Setup and checks

```sh
mise trust
mise install --locked
bun install --frozen-lockfile
bun run setup:validation

mise run check:presets
mise run check:workflows
mise run check:workflows:online
mise run check:templates
```

### Python dependency updates

uv applies a seven-day release-age filter to Copier and its dependencies. Renovate applies seven days to normal
Pyflakes updates and zero days to security fixes. Manual Pyflakes pin changes bypass uv's filter.
