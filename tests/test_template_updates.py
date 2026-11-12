"""Verify the managed-file contract through native Copier CLI operations.

The varying factor is the target's relation to its adopted template; the
equivalence partitions below determine the operation. Additional cases cover
annotated tags, source-only changes and recovery of the paired caller reference.
Release updates advance from v1.0.0 or legacy v0.9.0 to v1.1.0; answer-only
updates retain the current tag. Fixtures use one root answers file, a saved
default-branch answer, no trusted features and Copier's native inline conflicts.

Target relation             Operation  Expected result
No adoption                 copy       Exactly EditorConfig, caller and native answers
Unchanged adoption          update     New content; unrelated file preserved
Non-overlapping local edit  update     Both edits; unrelated file preserved
Overlapping local edit      update     Both alternatives and unmerged index
Deleted managed file        update     Deletion preserved; answers advance

Default-branch factor        Operation  Expected result
Omitted                      copy       Main trigger and saved default answer
Explicit main/develop        copy       Selected trigger and saved answer
Saved develop                update     Answer preserved for content/workflow changes
Changed answer               update     Trigger and answer change together
Legacy main                  update     Default answer added with no conflict
Legacy local develop         update     Explicit answer saved; both conflict alternatives retained
Empty input                  copy       Rejected before any target file is written
Empty replacement answer     update     Rejected without changing the saved adoption
YAML booleans, quotes, slash  copy       Literal string preserved in JSON-quoted YAML
Leading/internal ! and +     copy       GitHub filter metacharacters escaped
Source generation task       copy       Project answer reaches the generated files
Source update task           update     Saved answers found and branch changed together

Run these tests with mise run check:templates, which uses the pinned Python and
supplies the selected Copier version in an isolated uv environment.
Git commits created by these tests belong only to disposable fixtures.
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
ANSWERS_FILE = ".copier-answers.shared-settings.yml"
INITIAL_TAG = "v1.0.0"
UPDATED_TAG = "v1.1.0"
LEGACY_TAG = "v0.9.0"
UNRELATED_CONTENT = b"Repository-owned content\n"
CALLER_FILE = ".github/workflows/workflow-lint.yaml"
LEGACY_CONFIGURATION = """_subdirectory: template
_answers_file: .copier-answers.shared-settings.yml
_min_copier_version: '9.19.1'
"""
CALLER = """name: Workflow Lint

on:
  push:
    branches: [BRANCH_FILTER]
  pull_request:

permissions:
  contents: read

concurrency:
  group: workflow-lint-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  workflow-lint:
    name: Workflow Lint
    uses: ryotan/tempura/.github/workflows/workflow-lint.yaml@COMMIT_SHA # VERSION
"""


class TemplateUpdates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("copier") is None:
            raise RuntimeError("Run mise run check:templates to supply the selected Copier version.")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="copier-delivery-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.source = self.directory / "source"
        self.target = self.directory / "target"
        self.source.mkdir()
        self.target.mkdir()
        self.environment = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Copier fixture",
            "GIT_AUTHOR_EMAIL": "copier-fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Copier fixture",
            "GIT_COMMITTER_EMAIL": "copier-fixture@example.invalid",
        }
        shutil.copyfile(ROOT / "copier.yml", self.source / "copier.yml")
        shutil.copytree(ROOT / "template", self.source / "template")
        workflow = self.source / CALLER_FILE
        workflow.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / CALLER_FILE, workflow)
        (self.source / "source-only.txt").write_text("Must not be distributed\n", encoding="utf-8")
        self.git(self.source, "init", "--quiet", "--initial-branch=main")
        self.initial_content = (self.source / "template/.editorconfig").read_text(encoding="utf-8")
        self.release(INITIAL_TAG)

    def run_command(self, arguments, cwd):
        result = subprocess.run(
            arguments, cwd=cwd, env=self.environment, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120, check=False,
        )
        self.assertEqual(result.returncode, 0, f"{arguments!r}\n{result.stdout}")
        return result.stdout

    def git(self, repository, *arguments):
        return self.run_command(
            ["git", "-C", str(repository), "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *arguments],
            self.directory,
        ).strip()

    def commit(self, repository, message):
        self.git(repository, "add", "--all")
        self.git(repository, "commit", "--quiet", "-m", message)

    def release(self, tag):
        self.commit(self.source, f"Fixture release {tag}")
        self.git(self.source, "tag", tag)

    def copy(self, tag=INITIAL_TAG, *arguments):
        self.run_command(
            ["copier", "copy", "--defaults", "--vcs-ref", tag, *arguments, str(self.source), str(self.target)],
            self.directory,
        )

    def adopt(self, tag=INITIAL_TAG, *arguments):
        self.copy(tag, *arguments)
        (self.target / "repository-owned.txt").write_bytes(UNRELATED_CONTENT)
        self.git(self.target, "init", "--quiet", "--initial-branch=main")
        self.commit(self.target, "Fixture adoption")

    def adopt_legacy(self):
        configuration = self.source / "copier.yml"
        caller = self.source / "template" / f"{CALLER_FILE}.jinja"
        current_configuration = configuration.read_text(encoding="utf-8")
        current_caller = caller.read_text(encoding="utf-8")
        configuration.write_text(LEGACY_CONFIGURATION, encoding="utf-8")
        caller.write_text(
            re.sub(r"(?m)^    branches: .*", "    branches: [main]", current_caller), encoding="utf-8",
        )
        (self.source / "source-only.txt").write_text("Legacy source fixture\n", encoding="utf-8")
        self.release(LEGACY_TAG)
        self.adopt(LEGACY_TAG)
        self.assertEqual(self.answers(), {"_commit": LEGACY_TAG, "_src_path": str(self.source)})
        configuration.write_text(current_configuration, encoding="utf-8")
        caller.write_text(current_caller, encoding="utf-8")

    def new_release(self):
        updated = self.initial_content.replace("indent_size = 2\n", "indent_size = 4\n", 1)
        self.assertNotEqual(updated, self.initial_content)
        (self.source / "template/.editorconfig").write_text(updated, encoding="utf-8")
        self.release(UPDATED_TAG)
        return updated

    def update(self, *arguments, tag=UPDATED_TAG):
        self.run_command(
            [
                "copier", "update", "--skip-answered", "--defaults", "--answers-file", ANSWERS_FILE,
                "--vcs-ref", tag, *arguments,
            ],
            self.target,
        )

    def files(self):
        return {
            path.relative_to(self.target).as_posix(): path.read_bytes()
            for path in self.target.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(self.target).parts
        }

    def answers(self):
        return yaml.safe_load((self.target / ANSWERS_FILE).read_text(encoding="utf-8"))

    def assert_metadata(self, tag, default_branch="main"):
        self.assertEqual(self.answers(), {
            "_commit": tag, "_src_path": str(self.source), "default_branch": default_branch,
        })

    def expected_caller(self, tag, branch_filter="main"):
        sha = self.git(self.source, "rev-parse", f"{tag}^{{commit}}")
        return (
            CALLER.replace("COMMIT_SHA", sha).replace("VERSION", tag)
            .replace("BRANCH_FILTER", json.dumps(branch_filter)).encode()
        )

    def assert_updated(self, content, caller=None, default_branch="main"):
        self.assert_metadata(UPDATED_TAG, default_branch)
        self.assertEqual(self.files(), {
            ".editorconfig": content.encode(),
            ANSWERS_FILE: (self.target / ANSWERS_FILE).read_bytes(),
            "repository-owned.txt": UNRELATED_CONTENT,
            CALLER_FILE: self.expected_caller(UPDATED_TAG) if caller is None else caller,
        })

    def assert_branch_copy(self, branch, branch_filter):
        self.copy(INITIAL_TAG, "--data", f"default_branch={branch}")
        self.assert_metadata(INITIAL_TAG, branch)
        caller = (self.target / CALLER_FILE).read_text(encoding="utf-8")
        serialized_filter = re.search(r"(?m)^    branches: \[(.*)\]$", caller)
        self.assertIsNotNone(serialized_filter, caller)
        self.assertEqual(json.loads(serialized_filter.group(1)), branch_filter)
        self.assertEqual(self.files(), {
            ".editorconfig": self.initial_content.encode(),
            ANSWERS_FILE: (self.target / ANSWERS_FILE).read_bytes(),
            CALLER_FILE: self.expected_caller(INITIAL_TAG, branch_filter),
        })

    def test_copy_creates_only_managed_files_and_native_metadata(self):
        self.copy()
        self.assert_metadata(INITIAL_TAG)
        self.assertEqual(self.files(), {
            ".editorconfig": self.initial_content.encode(),
            ANSWERS_FILE: (self.target / ANSWERS_FILE).read_bytes(),
            CALLER_FILE: self.expected_caller(INITIAL_TAG),
        })

    def test_copy_records_explicit_main_branch(self):
        self.assert_branch_copy("main", "main")

    def test_copy_records_develop_branch(self):
        self.assert_branch_copy("develop", "develop")

    def test_source_generation_task_applies_project_answer(self):
        self.run_command(
            ["mise", "run", "generate:settings", "--", "--defaults", "--data", "default_branch=develop",
             "--vcs-ref", INITIAL_TAG, str(self.source), str(self.target)],
            ROOT,
        )
        self.assert_metadata(INITIAL_TAG, "develop")
        self.assertEqual(self.files(), {
            ".editorconfig": self.initial_content.encode(),
            ANSWERS_FILE: (self.target / ANSWERS_FILE).read_bytes(),
            CALLER_FILE: self.expected_caller(INITIAL_TAG, "develop"),
        })

    def test_copy_quotes_yaml_true_branch(self):
        self.assert_branch_copy("true", "true")

    def test_copy_quotes_yaml_on_branch(self):
        self.assert_branch_copy("on", "on")

    def test_copy_escapes_quotes_in_branch(self):
        self.assert_branch_copy('feature/"topic"', 'feature/"topic"')

    def test_copy_preserves_slash_in_branch(self):
        self.assert_branch_copy("feature/topic", "feature/topic")

    def test_copy_escapes_leading_exclamation_in_branch_filter(self):
        self.assert_branch_copy("!topic", r"\!topic")

    def test_copy_escapes_internal_exclamation_in_branch_filter(self):
        self.assert_branch_copy("feature/hello!world", r"feature/hello\!world")

    def test_copy_escapes_plus_in_branch_filter(self):
        self.assert_branch_copy("release/v1+fix", r"release/v1\+fix")

    def test_copy_rejects_empty_branch_before_writing_target_files(self):
        result = subprocess.run(
            ["copier", "copy", "--defaults", "--vcs-ref", INITIAL_TAG,
             "--data", "default_branch=", str(self.source), str(self.target)],
            cwd=self.directory, env=self.environment, text=True, capture_output=True, timeout=120, check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("default_branch", result.stdout + result.stderr)
        self.assertEqual(self.files(), {})

    def test_update_applies_common_change_and_preserves_unrelated_file(self):
        self.adopt()
        updated = self.new_release()
        self.update()
        self.assert_updated(updated)
        self.assertEqual(self.git(self.target, "diff", "--name-only", "--diff-filter=U"), "")

    def test_content_update_preserves_saved_develop_branch(self):
        self.adopt(INITIAL_TAG, "--data", "default_branch=develop")
        updated = self.new_release()
        self.update()
        self.assert_updated(updated, self.expected_caller(UPDATED_TAG, "develop"), "develop")
        self.assertEqual(self.git(self.target, "diff", "--name-only", "--diff-filter=U"), "")

    def test_update_changes_saved_branch_and_caller_together(self):
        self.adopt()
        self.update("--data", "default_branch=develop", tag=":current:")
        self.assert_metadata(INITIAL_TAG, "develop")
        self.assertEqual(self.files(), {
            ".editorconfig": self.initial_content.encode(),
            ANSWERS_FILE: (self.target / ANSWERS_FILE).read_bytes(),
            "repository-owned.txt": UNRELATED_CONTENT,
            CALLER_FILE: self.expected_caller(INITIAL_TAG, "develop"),
        })
        self.assertEqual(self.git(self.target, "diff", "--name-only", "--diff-filter=U"), "")

    def test_source_update_task_finds_saved_answers_and_changes_branch(self):
        self.adopt(INITIAL_TAG, "--data", "default_branch=develop")
        self.run_command(
            ["mise", "run", "update:settings", "--", "--vcs-ref", ":current:",
             "--data", "default_branch=main", str(self.target)],
            ROOT,
        )
        self.assert_metadata(INITIAL_TAG)
        self.assertEqual(self.files(), {
            ".editorconfig": self.initial_content.encode(),
            ANSWERS_FILE: (self.target / ANSWERS_FILE).read_bytes(),
            "repository-owned.txt": UNRELATED_CONTENT,
            CALLER_FILE: self.expected_caller(INITIAL_TAG),
        })
        self.assertEqual(self.git(self.target, "diff", "--name-only", "--diff-filter=U"), "")

    def test_update_rejects_empty_branch_without_changing_adoption(self):
        self.adopt(INITIAL_TAG, "--data", "default_branch=develop")
        original = self.files()
        result = subprocess.run(
            ["copier", "update", "--skip-answered", "--defaults", "--answers-file", ANSWERS_FILE,
             "--vcs-ref", ":current:", "--data", "default_branch="],
            cwd=self.target, env=self.environment, text=True, capture_output=True, timeout=120, check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("default_branch", result.stdout + result.stderr)
        self.assertEqual(self.files(), original)
        self.assertEqual(self.git(self.target, "status", "--porcelain"), "")

    def test_legacy_main_adoption_gains_default_branch_answer(self):
        self.adopt_legacy()
        updated = self.new_release()
        self.update()
        self.assert_updated(updated)
        self.assertEqual(self.git(self.target, "diff", "--name-only", "--diff-filter=U"), "")

    def test_legacy_develop_migration_retains_both_conflict_alternatives(self):
        self.adopt_legacy()
        caller = self.target / CALLER_FILE
        caller.write_bytes(caller.read_bytes().replace(b"branches: [main]", b"branches: [develop]"))
        local = caller.read_text(encoding="utf-8")
        self.commit(self.target, "Fixture legacy branch customization")
        updated = self.new_release()
        self.update("--data", "default_branch=develop")
        content = caller.read_text(encoding="utf-8")
        match = re.search(r"(?ms)^<<<<<<< [^\n]*\n(.*?)^=======\n(.*?)^>>>>>>> [^\n]*\n", content)
        self.assertIsNotNone(match, content)
        before, after = match.groups()
        old_sha = self.git(self.source, "rev-parse", LEGACY_TAG)
        new_sha = self.git(self.source, "rev-parse", UPDATED_TAG)
        expected_local = local.replace(old_sha, new_sha).replace(LEGACY_TAG, UPDATED_TAG)
        self.assertEqual(content[:match.start()] + before + content[match.end():],
                         expected_local)
        self.assertEqual(content[:match.start()] + after + content[match.end():],
                         self.expected_caller(UPDATED_TAG, "develop").decode())
        self.assert_updated(updated, content.encode(), "develop")
        self.assertEqual(self.git(self.target, "diff", "--name-only", "--diff-filter=U"), CALLER_FILE)

    def test_update_preserves_non_overlapping_local_edit(self):
        self.adopt()
        local = self.initial_content.replace("quote_type = single\n", "quote_type = double\n")
        self.assertNotEqual(local, self.initial_content)
        (self.target / ".editorconfig").write_text(local, encoding="utf-8")
        self.commit(self.target, "Fixture local edit")
        updated = self.new_release()
        self.update()
        self.assert_updated(updated.replace("quote_type = single\n", "quote_type = double\n"))
        self.assertEqual(self.git(self.target, "diff", "--name-only", "--diff-filter=U"), "")

    def test_update_exposes_overlapping_changes_for_manual_review(self):
        self.adopt()
        local = self.initial_content.replace("indent_size = 2\n", "indent_size = 8\n", 1)
        self.assertNotEqual(local, self.initial_content)
        (self.target / ".editorconfig").write_text(local, encoding="utf-8")
        self.commit(self.target, "Fixture conflicting edit")
        updated = self.new_release()
        self.update()
        actual = (self.target / ".editorconfig").read_text(encoding="utf-8")
        match = re.search(r"(?ms)^<<<<<<< [^\n]*\n(.*?)^=======\n(.*?)^>>>>>>> [^\n]*\n", actual)
        self.assertIsNotNone(match, actual)
        before, after = match.groups()
        self.assertEqual(actual[:match.start()] + before + actual[match.end():], local)
        self.assertEqual(actual[:match.start()] + after + actual[match.end():], updated)
        self.assert_updated(actual)
        self.assertEqual(self.git(self.target, "diff", "--name-only", "--diff-filter=U"), ".editorconfig")

    def test_update_preserves_deletion_without_repair(self):
        self.adopt()
        (self.target / ".editorconfig").unlink()
        self.commit(self.target, "Fixture target deletion")
        self.new_release()
        self.update()
        self.assert_metadata(UPDATED_TAG)
        self.assertEqual(self.files(), {
            ANSWERS_FILE: (self.target / ANSWERS_FILE).read_bytes(),
            "repository-owned.txt": UNRELATED_CONTENT,
            CALLER_FILE: self.expected_caller(UPDATED_TAG),
        })
        self.assertEqual(self.git(self.target, "diff", "--name-only", "--diff-filter=U"), "")

    def test_annotated_tag_uses_commit_sha_instead_of_tag_object(self):
        tag = "v1.0.1"
        self.git(self.source, "tag", "-a", tag, "-m", "Fixture annotated tag")
        self.assertNotEqual(self.git(self.source, "rev-parse", tag),
                            self.git(self.source, "rev-parse", f"{tag}^{{commit}}"))
        self.copy(tag)
        self.assert_metadata(tag)
        self.assertEqual(self.files(), {
            ".editorconfig": self.initial_content.encode(),
            ANSWERS_FILE: (self.target / ANSWERS_FILE).read_bytes(),
            CALLER_FILE: self.expected_caller(tag),
        })

    def test_workflow_only_change_updates_reference_without_template_edit(self):
        self.adopt()
        workflow = self.source / CALLER_FILE
        workflow.write_text(workflow.read_text() + "\n# Next workflow release\n")
        self.release(UPDATED_TAG)
        self.update()
        self.assert_updated(self.initial_content)
        self.assertEqual(set(self.git(self.target, "diff", "--name-only").splitlines()),
                         {ANSWERS_FILE, CALLER_FILE})

    def test_workflow_only_update_preserves_saved_develop_branch(self):
        self.adopt(INITIAL_TAG, "--data", "default_branch=develop")
        workflow = self.source / CALLER_FILE
        workflow.write_text(workflow.read_text() + "\n# Next workflow release\n")
        self.release(UPDATED_TAG)
        self.update()
        self.assert_updated(self.initial_content, self.expected_caller(UPDATED_TAG, "develop"), "develop")
        self.assertEqual(set(self.git(self.target, "diff", "--name-only").splitlines()),
                         {ANSWERS_FILE, CALLER_FILE})

    def test_update_preserves_non_overlapping_caller_edit(self):
        self.adopt()
        caller = self.target / CALLER_FILE
        caller.write_bytes(caller.read_bytes().replace(b'branches: ["main"]', b'branches: ["develop"]'))
        self.commit(self.target, "Fixture caller customization")
        updated = self.new_release()
        self.update()
        expected = self.expected_caller(UPDATED_TAG, "develop")
        self.assert_updated(updated, expected)

    def test_local_caller_reference_change_exposes_conflict(self):
        self.adopt()
        caller = self.target / CALLER_FILE
        sha = self.git(self.source, "rev-parse", INITIAL_TAG)
        local = caller.read_text().replace(sha, "0" * 40)
        caller.write_text(local)
        self.commit(self.target, "Fixture caller reference edit")
        updated = self.new_release()
        self.update()
        content = caller.read_text()
        match = re.search(r"(?ms)^<<<<<<< [^\n]*\n(.*?)^=======\n(.*?)^>>>>>>> [^\n]*\n", content)
        self.assertIsNotNone(match, content)
        before, after = match.groups()
        self.assertEqual(content[:match.start()] + before + content[match.end():], local)
        self.assertEqual(content[:match.start()] + after + content[match.end():],
                         self.expected_caller(UPDATED_TAG).decode())
        self.assert_updated(updated, content.encode())
        self.assertEqual(self.git(self.target, "diff", "--name-only", "--diff-filter=U"), CALLER_FILE)

    def test_deleted_caller_remains_deleted(self):
        self.adopt()
        (self.target / CALLER_FILE).unlink()
        self.commit(self.target, "Fixture caller deletion")
        updated = self.new_release()
        self.update()
        self.assert_metadata(UPDATED_TAG)
        self.assertEqual(self.files(), {
            ".editorconfig": updated.encode(),
            ANSWERS_FILE: (self.target / ANSWERS_FILE).read_bytes(),
            "repository-owned.txt": UNRELATED_CONTENT,
        })

    def test_downgrade_is_rejected_without_changing_adoption(self):
        self.adopt()
        updated = self.new_release()
        self.update()
        self.commit(self.target, "Fixture update")
        result = subprocess.run(
            ["copier", "update", "--skip-answered", "--defaults", "--answers-file", ANSWERS_FILE,
             "--vcs-ref", INITIAL_TAG], cwd=self.target, env=self.environment, text=True,
            capture_output=True, timeout=120, check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Downgrades are not supported", result.stdout + result.stderr)
        self.assert_updated(updated)
        self.assertEqual(self.git(self.target, "status", "--porcelain"), "")

    def test_reverting_update_restores_files_answers_and_caller_reference(self):
        self.adopt()
        original = self.files()
        self.new_release()
        self.update()
        self.commit(self.target, "Fixture update")
        self.git(self.target, "revert", "--no-edit", "HEAD")
        self.assertEqual(self.files(), original)


if __name__ == "__main__":
    unittest.main()
