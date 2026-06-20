from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path

import config as C


class ConfigFilesTest(unittest.TestCase):
    KEYS = (
        "PIPELINE_FIELDS_FILE",
        "PIPELINE_AGENTS_FILE",
        "PIPELINE_ENGINE_CLARIFY",
        "PIPELINE_ENGINE_CODE",
        "PIPELINE_ENGINE_REVIEW",
    )

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_env = {key: os.environ.get(key) for key in self.KEYS}

    def tearDown(self) -> None:
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(C)
        self.tmp.cleanup()

    def _write_json(self, name: str, data: dict) -> Path:
        path = Path(self.tmp.name) / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def test_fields_file_overrides_base_column_names(self) -> None:
        fields = self._write_json("fields.json", {
            "title": "标题",
            "description": "描述",
            "agent_code": "实现Agent",
        })
        os.environ["PIPELINE_FIELDS_FILE"] = str(fields)

        importlib.reload(C)

        self.assertEqual(C.F_TITLE, "标题")
        self.assertEqual(C.F_DESC, "描述")
        self.assertEqual(C.F_AGENT_CODE, "实现Agent")
        self.assertEqual(C.F_STATUS, "状态")

    def test_agents_file_overrides_defaults_commands_and_aliases(self) -> None:
        agents = self._write_json("agents.json", {
            "defaults": {"clarify": "toy", "code": "toy", "review": "gemini"},
            "commands": {"toy": ["toy-agent", "--run"]},
            "aliases": {"小玩具": "toy"},
        })
        os.environ["PIPELINE_AGENTS_FILE"] = str(agents)
        os.environ["PIPELINE_ENGINE_CLARIFY"] = ""
        os.environ["PIPELINE_ENGINE_CODE"] = ""

        importlib.reload(C)

        self.assertEqual(C.ENGINE_CLARIFY, "toy")
        self.assertEqual(C.ENGINE_CODE, "toy")
        self.assertEqual(C.AGENT_CMDS["toy"], ["toy-agent", "--run"])
        self.assertEqual(C.AGENT_ALIASES["小玩具"], "toy")


if __name__ == "__main__":
    unittest.main()
