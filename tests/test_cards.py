from __future__ import annotations

import unittest

import cards
import config as C


def rec(status: str = C.S_CONFIRM) -> dict:
    return {
        "record_id": "rec_1",
        "fields": {
            C.F_TITLE: "修改面试通知",
            C.F_STATUS: status,
            C.F_WORKSPACE: "backend-service",
            C.F_PRD: "CLEAR\nPRD content",
            C.F_LINK: "https://example.com/review/1",
            C.F_FAILS: 1,
        },
    }


class CardsTest(unittest.TestCase):
    def test_confirm_card_contains_confirm_action(self) -> None:
        card = cards.confirm_card(rec())

        self.assertEqual(card["header"]["template"], "blue")
        action = card["elements"][-1]["actions"][0]
        self.assertEqual(action["value"], {"record_id": "rec_1", "action": "confirm"})
        self.assertIn("确认开发", action["text"]["content"])

    def test_merge_card_links_review_and_done_action(self) -> None:
        card = cards.merge_card(rec(C.S_MERGE))

        content = "\n".join(
            element.get("text", {}).get("content", "")
            for element in card["elements"]
            if element.get("tag") == "div"
        )
        self.assertIn("打开链接", content)
        action = card["elements"][-1]["actions"][0]
        self.assertEqual(action["value"]["action"], "done")

    def test_status_card_uses_status_template(self) -> None:
        card = cards.status_card(rec(C.S_BLOCKED))

        self.assertEqual(card["header"]["template"], "red")
        self.assertTrue(any(element.get("fields") for element in card["elements"]))
        actions = card["elements"][-1]["actions"]
        self.assertEqual([action["value"]["action"] for action in actions], ["unblock_dev", "restart_clarify", "clear_lock"])

    def test_status_card_for_actionable_record_has_retry_controls(self) -> None:
        card = cards.status_card(rec(C.S_DEV))

        actions = card["elements"][-1]["actions"]
        self.assertEqual([action["value"]["action"] for action in actions], ["retry", "clear_lock"])

    def test_intake_card_is_non_action_receipt(self) -> None:
        card = cards.intake_card({
            C.F_TITLE: "修复登录",
            C.F_STATUS: C.S_CLARIFY,
            C.F_WORKSPACE: "frontend-app",
            C.F_AGENT_CLARIFY: "cursor",
        })

        self.assertEqual(card["header"]["template"], "wathet")
        self.assertFalse(any(element.get("tag") == "action" for element in card["elements"]))


def board_rec(rid: str, status: str, title: str, fails: int = 0, log: str = "") -> dict:
    return {"record_id": rid, "fields": {
        C.F_TITLE: title, C.F_STATUS: status, C.F_FAILS: fails, C.F_LOG: log,
    }}


class BoardCardTest(unittest.TestCase):
    def _records(self) -> list[dict]:
        return [
            board_rec("r_done", C.S_DONE, "已完成的"),
            board_rec("r_dev", C.S_DEV, "正在开发的"),
            board_rec("r_blocked", C.S_BLOCKED, "卡住的", fails=2, log="第一行\n测试未通过: lint 报错 → 已阻塞"),
            board_rec("r_confirm", C.S_CONFIRM, "等确认的"),
        ]

    def test_excludes_done_and_orders_attention_first(self) -> None:
        card = cards.board_card(self._records())
        # 完成的不进看板；阻塞排最前、待确认其次、开发中靠后
        self.assertIn("3 条在途", card["header"]["title"]["content"])
        joined = "\n".join(e.get("text", {}).get("content", "") for e in card["elements"] if e.get("tag") == "div")
        self.assertNotIn("已完成的", joined)
        self.assertLess(joined.index("卡住的"), joined.index("等确认的"))
        self.assertLess(joined.index("等确认的"), joined.index("正在开发的"))

    def test_blocked_row_shows_last_log_and_recovery_buttons(self) -> None:
        card = cards.board_card(self._records())
        joined = "\n".join(e.get("text", {}).get("content", "") for e in card["elements"] if e.get("tag") == "div")
        self.assertIn("lint 报错", joined)            # 最后一行日志 = 最后发生了什么
        self.assertNotIn("第一行", joined)             # 只取最后一行
        actions = [a for e in card["elements"] if e.get("tag") == "action" for a in e["actions"]]
        vals = [(a["value"]["action"], a["value"]["record_id"]) for a in actions]
        self.assertIn(("unblock_dev", "r_blocked"), vals)
        self.assertIn(("confirm", "r_confirm"), vals)   # 按钮自带各自 record_id

    def test_empty_board_when_all_done(self) -> None:
        card = cards.board_card([board_rec("r1", C.S_DONE, "x")])
        self.assertIn("无在途需求", card["header"]["title"]["content"])

    def test_board_text_fallback(self) -> None:
        text = cards.board_text(self._records())
        self.assertIn("3 条在途", text)
        self.assertIn("卡住的", text)
        self.assertNotIn("已完成的", text)


class BlockedAndStatusLogTest(unittest.TestCase):
    def test_blocked_card_shows_reason_and_recovery_buttons(self) -> None:
        r = board_rec("rb", C.S_BLOCKED, "卡住的", fails=2, log="老日志\n测试未通过: lint 报错")
        card = cards.blocked_card(r, "测试未通过: lint 报错")
        self.assertEqual(card["header"]["template"], "red")
        joined = "\n".join(e.get("text", {}).get("content", "") for e in card["elements"] if e.get("tag") == "div")
        self.assertIn("lint 报错", joined)
        actions = [a["value"]["action"] for e in card["elements"] if e.get("tag") == "action" for a in e["actions"]]
        self.assertIn("unblock_dev", actions)
        self.assertIn("clear_lock", actions)

    def test_status_card_includes_recent_log(self) -> None:
        r = board_rec("rs", C.S_DEV, "开发中的", log="很久以前\n最近一条日志在这")
        card = cards.status_card(r)
        joined = "\n".join(e.get("text", {}).get("content", "") for e in card["elements"] if e.get("tag") == "div")
        self.assertIn("最近一条日志在这", joined)


class SettingsCardTest(unittest.TestCase):
    def _btns(self, card: dict) -> list[dict]:
        return [a for e in card["elements"] if e.get("tag") == "action" for a in e["actions"]]

    def test_buttons_carry_agent_and_workspace_choice(self) -> None:
        card = cards.settings_card(board_rec("rc", C.S_CONFIRM, "待确认的"), ["wsA", "wsB"])
        btns = self._btns(card)
        agents = {b["value"]["agent"] for b in btns if b["value"]["action"] == "set_agent"}
        self.assertEqual(agents, set(cards.AGENT_CHOICES))
        wss = {b["value"]["workspace"] for b in btns if b["value"]["action"] == "set_workspace"}
        self.assertEqual(wss, {"wsA", "wsB"})

    def test_confirm_status_includes_confirm_button(self) -> None:
        btns = self._btns(cards.settings_card(board_rec("rc", C.S_CONFIRM, "x"), []))
        self.assertTrue(any(b["value"]["action"] == "confirm" for b in btns))

    def test_current_agent_is_highlighted(self) -> None:
        r = board_rec("rc", C.S_DEV, "x")
        r["fields"][C.F_AGENT] = "cursor"
        btns = [b for b in self._btns(cards.settings_card(r, [])) if b["value"].get("action") == "set_agent"]
        cursor_btn = next(b for b in btns if b["value"]["agent"] == "cursor")
        self.assertEqual(cursor_btn["type"], "primary")

    def test_dev_status_warns_workspace_wont_migrate(self) -> None:
        card = cards.settings_card(board_rec("rc", C.S_DEV, "x"), ["wsA"])
        joined = "\n".join(e.get("text", {}).get("content", "") for e in card["elements"] if e.get("tag") == "div")
        self.assertIn("不会迁移", joined)

    def test_confirm_card_exposes_open_settings(self) -> None:
        btns = [a for e in cards.confirm_card(rec(C.S_CONFIRM))["elements"]
                if e.get("tag") == "action" for a in e["actions"]]
        self.assertTrue(any(b["value"]["action"] == "open_settings" for b in btns))


if __name__ == "__main__":
    unittest.main()
