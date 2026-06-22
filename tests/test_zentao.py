from __future__ import annotations

import unittest

import config as C
import sync_zentao
import zentao


class ZentaoTest(unittest.TestCase):
    def test_extracts_bugs_from_common_payload_shapes(self) -> None:
        payload = {"data": {"bugs": [{"id": 7, "title": "登录失败"}]}}

        items = zentao._bugs_payload_items(payload)
        bug = zentao.normalize_bug(items[0], "https://zentao.example.com")

        self.assertIsNotNone(bug)
        assert bug is not None
        self.assertEqual(bug.id, "7")
        self.assertEqual(bug.title, "登录失败")
        self.assertEqual(bug.url, "https://zentao.example.com/bug-view-7.html")

    def test_base_fields_include_external_identity_and_marker(self) -> None:
        bug = zentao.ZentaoBug(id="9", title="按钮报错", status="active")
        cfg = zentao.ZentaoConfig(base_url="https://zentao.example.com", workspace="backend", agent="cursor")

        fields = zentao.base_fields_for_bug(bug, cfg)

        self.assertEqual(fields[C.F_STATUS], C.S_SETUP)
        self.assertEqual(fields[C.F_EXTERNAL_SOURCE], "zentao")
        self.assertEqual(fields[C.F_EXTERNAL_ID], "9")
        self.assertEqual(fields[C.F_WORKSPACE], "backend")
        self.assertIn("【外部来源】zentao bug #9", fields[C.F_DESC])

    def test_existing_bug_ids_uses_field_or_description_marker(self) -> None:
        records = [
            {"fields": {C.F_EXTERNAL_SOURCE: "zentao", C.F_EXTERNAL_ID: "1"}},
            {"fields": {C.F_DESC: "xxx\n【外部来源】zentao bug #2\n标题：x"}},
        ]

        self.assertEqual(sync_zentao._existing_bug_ids(records), {"1", "2"})


if __name__ == "__main__":
    unittest.main()
