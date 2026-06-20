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


if __name__ == "__main__":
    unittest.main()
