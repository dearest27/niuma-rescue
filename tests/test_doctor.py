from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

import doctor


class DoctorGitRemoteTest(unittest.TestCase):
    def setUp(self) -> None:
        self._old_run = doctor.subprocess.run

    def tearDown(self) -> None:
        doctor.subprocess.run = self._old_run

    def _remote(self, url: str) -> str:
        def fake_run(cmd, capture_output=True, text=True):
            return subprocess.CompletedProcess(cmd, 0, stdout=url + "\n", stderr="")

        doctor.subprocess.run = fake_run
        return doctor._git_remote_host(Path("/repo"))

    def test_extracts_http_host_with_port(self) -> None:
        self.assertEqual(
            self._remote("http://192.168.1.111:18080/group/project.git"),
            "192.168.1.111:18080",
        )

    def test_extracts_ssh_host(self) -> None:
        self.assertEqual(
            self._remote("git@gitlab.example.com:group/project.git"),
            "gitlab.example.com",
        )


if __name__ == "__main__":
    unittest.main()
