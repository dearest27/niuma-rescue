from __future__ import annotations

import unittest

import config as C
import message_router
import ops


class FakeLark:
    def __init__(self, records: list[dict] | None = None):
        self.records = records or []
        self.created: list[dict] = []
        self.sent: list[tuple[str, str]] = []
        self.cards: list[tuple[str, dict]] = []
        self.updated: list[tuple[str, dict]] = []

    def list_records(self) -> list[dict]:
        return self.records

    def create(self, fields: dict) -> dict:
        record = {"record_id": f"rec_{len(self.records) + 1}", "fields": dict(fields)}
        self.records.append(record)
        self.created.append(dict(fields))
        return record

    def update(self, record_id: str, fields: dict) -> dict:
        self.updated.append((record_id, dict(fields)))
        for record in self.records:
            if record["record_id"] == record_id:
                record["fields"].update(fields)
                return record
        raise AssertionError(f"missing record {record_id}")

    def send_text(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))

    def send_card(self, chat_id: str, card: dict) -> None:
        self.cards.append((chat_id, card))


class MessageRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self._old_lark = message_router.lark
        self._old_ops_lark = ops.lark
        self._old_runs_clear = ops.runs.clear
        self._old_runs_retry_now = ops.runs.retry_now

    def tearDown(self) -> None:
        message_router.lark = self._old_lark
        ops.lark = self._old_ops_lark
        ops.runs.clear = self._old_runs_clear
        ops.runs.retry_now = self._old_runs_retry_now

    def test_parse_intake_supports_agent_and_workspace(self) -> None:
        body, agent, workspace = message_router.parse_intake("需求@Cursor #frontend-app：新增登录页文档")

        self.assertEqual(body, "新增登录页文档")
        self.assertEqual(agent, "cursor")
        self.assertEqual(workspace, "frontend-app")

    def test_handle_message_creates_requirement_once(self) -> None:
        fake = FakeLark()
        message_router.lark = fake

        handled = message_router.handle_message({
            "message_type": "text",
            "chat_id": "oc_1",
            "sender_id": "ou_1",
            "content": "需求@cursor #backend-service：修复登录按钮",
        })

        # 新流程：落在「待选择」人工卡点，不立刻 dispatch（返回 False），等人点「开始澄清」
        self.assertFalse(handled)
        self.assertEqual(len(fake.created), 1)
        fields = fake.created[0]
        self.assertEqual(fields[C.F_STATUS], C.S_SETUP)
        self.assertEqual(fields[C.F_AGENT], "cursor")        # 内联 @agent 预选到执行Agent
        self.assertEqual(fields[C.F_WORKSPACE], "backend-service")
        self.assertEqual(fields[C.F_DESC], "修复登录按钮")
        self.assertEqual(len(fake.cards), 1)
        self.assertIn("选择澄清配置", fake.cards[0][1]["header"]["title"]["content"])

        duplicate = message_router.handle_message({
            "message_type": "text",
            "chat_id": "oc_1",
            "sender_id": "ou_1",
            "content": "需求@cursor #backend-service：修复登录按钮",
        })

        self.assertFalse(duplicate)
        self.assertEqual(len(fake.created), 1)
        self.assertEqual(len(fake.cards), 1)

    def test_start_clarify_button_moves_setup_to_clarify(self) -> None:
        fake = FakeLark([{"record_id": "rec_1", "fields": {
            C.F_CHAT: "oc_1", C.F_STATUS: C.S_SETUP, C.F_TITLE: "需求X", C.F_AGENT: "cursor"}}])
        message_router.lark = fake

        toast, dispatch, _card = message_router.handle_card_action(
            {"record_id": "rec_1", "action": "start_clarify"})

        self.assertTrue(dispatch)            # 触发 dispatcher 跑澄清
        self.assertIn("开始澄清", toast)
        self.assertEqual(fake.records[0]["fields"][C.F_STATUS], C.S_CLARIFY)

    def test_start_clarify_rejected_when_not_setup(self) -> None:
        fake = FakeLark([{"record_id": "rec_1", "fields": {
            C.F_CHAT: "oc_1", C.F_STATUS: C.S_DEV, C.F_TITLE: "需求X"}}])
        message_router.lark = fake

        toast, dispatch, _card = message_router.handle_card_action(
            {"record_id": "rec_1", "action": "start_clarify"})

        self.assertFalse(dispatch)
        self.assertEqual(fake.records[0]["fields"][C.F_STATUS], C.S_DEV)  # 未改动

    def test_start_clarify_text_fallback(self) -> None:
        fake = FakeLark([{"record_id": "rec_1", "fields": {
            C.F_CHAT: "oc_1", C.F_STATUS: C.S_SETUP, C.F_TITLE: "需求X"}}])
        message_router.lark = fake

        handled = message_router.handle_message({
            "message_type": "text", "chat_id": "oc_1", "sender_id": "ou_1", "content": "开始澄清"})

        self.assertTrue(handled)
        self.assertEqual(fake.records[0]["fields"][C.F_STATUS], C.S_CLARIFY)

    def test_status_command_sends_status_card(self) -> None:
        fake = FakeLark([
            {
                "record_id": "rec_1",
                "fields": {
                    C.F_CHAT: "oc_1",
                    C.F_STATUS: C.S_DEV,
                    C.F_TITLE: "修复登录按钮",
                    C.F_WORKSPACE: "backend-service",
                },
            }
        ])
        message_router.lark = fake

        handled = message_router.handle_message({
            "message_type": "text",
            "chat_id": "oc_1",
            "sender_id": "ou_1",
            "content": "状态",
        })

        self.assertFalse(handled)
        self.assertEqual(len(fake.cards), 1)
        self.assertIn("当前需求", fake.cards[0][1]["header"]["title"]["content"])

    def test_diagnose_command_sends_record_summary(self) -> None:
        fake = FakeLark([
            {
                "record_id": "rec_1",
                "fields": {
                    C.F_CHAT: "oc_1",
                    C.F_STATUS: C.S_BLOCKED,
                    C.F_TITLE: "修复登录按钮",
                    C.F_LOG: "旧日志\nreview 未通过",
                    C.F_FAILS: 2,
                },
            }
        ])
        message_router.lark = fake

        handled = message_router.handle_message({
            "message_type": "text",
            "chat_id": "oc_1",
            "sender_id": "ou_1",
            "content": "诊断",
        })

        self.assertFalse(handled)
        self.assertEqual(len(fake.sent), 1)
        self.assertIn("需求诊断", fake.sent[0][1])
        self.assertIn("review 未通过", fake.sent[0][1])

    def test_answer_to_waiting_record_returns_to_clarify(self) -> None:
        fake = FakeLark([
            {
                "record_id": "rec_1",
                "fields": {
                    C.F_CHAT: "oc_1",
                    C.F_STATUS: C.S_ANSWER,
                    C.F_CLARIFY: "问题：影响哪些页面？",
                },
            }
        ])
        message_router.lark = fake

        handled = message_router.handle_message({
            "message_type": "text",
            "chat_id": "oc_1",
            "sender_id": "ou_1",
            "content": "只影响登录页",
        })

        self.assertTrue(handled)
        self.assertEqual(fake.records[0]["fields"][C.F_STATUS], C.S_CLARIFY)
        self.assertIn("【回答】只影响登录页", fake.records[0]["fields"][C.F_CLARIFY])

    def test_card_action_unblock_uses_ops_and_dispatches(self) -> None:
        fake = FakeLark([
            {
                "record_id": "rec_1",
                "fields": {
                    C.F_CHAT: "oc_1",
                    C.F_STATUS: C.S_BLOCKED,
                    C.F_TITLE: "修复登录按钮",
                    C.F_LOG: "",
                    C.F_FAILS: 2,
                },
            }
        ])
        message_router.lark = fake
        ops.lark = fake
        ops.runs.clear = lambda record_id, reason="": True

        toast, dispatch, card = message_router.handle_card_action({"record_id": "rec_1", "action": "unblock_dev"})

        self.assertTrue(dispatch)
        self.assertIn("已解除阻塞", toast)
        self.assertEqual(fake.records[0]["fields"][C.F_STATUS], C.S_DEV)
        self.assertIsNotNone(card)

    def test_card_action_retry_dispatches_for_actionable_record(self) -> None:
        fake = FakeLark([
            {
                "record_id": "rec_1",
                "fields": {
                    C.F_CHAT: "oc_1",
                    C.F_STATUS: C.S_DEV,
                    C.F_TITLE: "修复登录按钮",
                    C.F_LOG: "",
                    C.F_FAILS: 1,
                },
            }
        ])
        message_router.lark = fake
        ops.lark = fake
        ops.runs.retry_now = lambda record_id, reason="": True

        toast, dispatch, _card = message_router.handle_card_action({"record_id": "rec_1", "action": "retry"})

        self.assertTrue(dispatch)
        self.assertIn("立即重试", toast)
        self.assertEqual(fake.records[0]["fields"][C.F_FAILS], 0)


if __name__ == "__main__":
    unittest.main()
