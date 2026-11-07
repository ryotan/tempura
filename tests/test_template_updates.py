"""Verify the managed-file contract through native Copier CLI operations.

The varying factor is the target's relation to its adopted template; the five
equivalence partitions below determine the operation. No independent flags vary.
Update cases advance from v1.0.0 to v1.1.0 with one root answers file, no template
questions or trusted features, and Copier's native inline conflict handling.

Target relation             Operation  Expected result
No adoption                 copy       Exactly EditorConfig and native answers
Unchanged adoption          update     New content; unrelated file preserved
Non-overlapping local edit  update     Both edits; unrelated file preserved
Overlapping local edit      update     Both alternatives and unmerged index
Deleted managed file        update     Deletion preserved; answers advance

These tests require Git and the Copier version used by the project. CI installs
Copier on its remote runner; local installation is not required for onboarding.
Synthetic changes test update mechanics, not approval of policy exceptions.
Git commits created by these tests belong only to disposable fixtures.
"""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ANSWERS_FILE = ".copier-answers.shared-settings.yml"
INITIAL_TAG = "v1.0.0"
UPDATED_TAG = "v1.1.0"
UNRELATED_CONTENT = b"Repository-owned content\n"


class TemplateUpdates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("copier") is None:
            raise RuntimeError("Copier CLI is required; use the version pinned by the project.")

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

    def copy(self):
        self.run_command(
            ["copier", "copy", "--defaults", "--vcs-ref", INITIAL_TAG, str(self.source), str(self.target)],
            self.directory,
        )

    def adopt(self):
        self.copy()
        (self.target / "repository-owned.txt").write_bytes(UNRELATED_CONTENT)
        self.git(self.target, "init", "--quiet", "--initial-branch=main")
        self.commit(self.target, "Fixture adoption")

    def new_release(self):
        updated = self.initial_content.replace("indent_size = 2\n", "indent_size = 4\n", 1)
        self.assertNotEqual(updated, self.initial_content)
        (self.source / "template/.editorconfig").write_text(updated, encoding="utf-8")
        self.release(UPDATED_TAG)
        return updated

    def update(self):
        self.run_command(
            [
                "copier", "update", "--skip-answered", "--defaults", "--answers-file", ANSWERS_FILE,
                "--vcs-ref", UPDATED_TAG,
            ],
            self.target,
        )

    def files(self):
        return {
            path.relative_to(self.target).as_posix(): path.read_bytes()
            for path in self.target.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(self.target).parts
        }

    def assert_metadata(self, tag):
        lines = (self.target / ANSWERS_FILE).read_text(encoding="utf-8").splitlines()
        self.assertCountEqual(
            [line for line in lines if line],
            [f"_commit: {tag}", f"_src_path: {self.source}"],
        )

    def assert_updated(self, content):
        self.assert_metadata(UPDATED_TAG)
        self.assertEqual(self.files(), {
            ".editorconfig": content.encode(),
            ANSWERS_FILE: (self.target / ANSWERS_FILE).read_bytes(),
            "repository-owned.txt": UNRELATED_CONTENT,
        })

    def test_copy_creates_only_managed_files_and_native_metadata(self):
        self.copy()
        self.assert_metadata(INITIAL_TAG)
        self.assertEqual(self.files(), {
            ".editorconfig": self.initial_content.encode(),
            ANSWERS_FILE: (self.target / ANSWERS_FILE).read_bytes(),
        })

    def test_update_applies_common_change_and_preserves_unrelated_file(self):
        self.adopt()
        updated = self.new_release()
        self.update()
        self.assert_updated(updated)
        self.assertEqual(self.git(self.target, "diff", "--name-only", "--diff-filter=U"), "")

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
        })
        self.assertEqual(self.git(self.target, "diff", "--name-only", "--diff-filter=U"), "")


if __name__ == "__main__":
    unittest.main()
