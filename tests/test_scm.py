from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

import scm
import workspaces


def cp(cmd: list[str], stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)


class ScmAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        self.calls: list[tuple[list[str], Path | None, bool]] = []
        self._old_run = scm._run

        def fake_run(cmd: list[str], cwd: Path | None = None, check: bool = True):
            self.calls.append((cmd, cwd, check))
            if cmd[:2] == ["svn", "status"]:
                return cp(cmd, stdout="M       changed.py\n?       new.py\n!       gone.py\n")
            if cmd[:3] == ["glab", "mr", "create"]:
                return cp(cmd, stdout="https://gitlab.example.com/group/project/-/merge_requests/1\n")
            return cp(cmd)

        scm._run = fake_run

    def tearDown(self) -> None:
        scm._run = self._old_run
        self.tmp.cleanup()

    def test_gitlab_after_review_creates_mr_with_target_branch_and_repo(self) -> None:
        ws = workspaces.Workspace(
            key="gitlab-app",
            path=self.work,
            base_ref="origin/release",
            scm="git",
            pr_enabled=True,
            pr_provider="gitlab",
            gitlab_repo="group/project",
            target_branch="release",
        )

        result = scm.after_review(ws, self.work, "feat/req-1", "Title", "Body")

        self.assertTrue(result.ok)
        self.assertEqual(result.link, "https://gitlab.example.com/group/project/-/merge_requests/1")
        cmd = self.calls[-1][0]
        self.assertEqual(cmd[:3], ["glab", "mr", "create"])
        self.assertIn("--source-branch", cmd)
        self.assertIn("feat/req-1", cmd)
        self.assertIn("--target-branch", cmd)
        self.assertIn("release", cmd)
        self.assertIn("--repo", cmd)
        self.assertIn("group/project", cmd)

    def test_svn_changed_files_includes_unversioned_and_missing_files(self) -> None:
        ws = workspaces.Workspace(
            key="svn-app",
            path=self.work,
            base_ref="https://svn.example.com/project/trunk",
            scm="svn",
        )

        self.assertEqual(scm.changed_files(ws, self.work), ["changed.py", "new.py", "gone.py"])

    def test_svn_commit_stages_adds_and_deletes_before_commit(self) -> None:
        ws = workspaces.Workspace(
            key="svn-app",
            path=self.work,
            base_ref="https://svn.example.com/project/trunk",
            scm="svn",
            push_enabled=True,
        )

        result = scm.after_review(ws, self.work, "req-rec1", "Title", "Body")

        self.assertTrue(result.ok)
        commands = [call[0] for call in self.calls]
        self.assertIn(["svn", "add", "--parents", "new.py"], commands)
        self.assertIn(["svn", "delete", "gone.py"], commands)
        self.assertTrue(any(cmd[:2] == ["svn", "commit"] for cmd in commands))

    def test_workspace_target_branch_defaults_from_base_ref(self) -> None:
        self.assertEqual(workspaces._target_from_base("origin/main"), "main")
        self.assertEqual(workspaces._target_from_base("refs/heads/release"), "release")
        self.assertEqual(workspaces._target_from_base("main"), "main")


if __name__ == "__main__":
    unittest.main()
