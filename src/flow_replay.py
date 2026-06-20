from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import config as C

HUMAN_TRANSITIONS = {
    (C.S_ANSWER, C.S_CLARIFY),
    (C.S_CONFIRM, C.S_DEV),
    (C.S_MERGE, C.S_DONE),
}


@dataclass(frozen=True)
class ReplayStep:
    name: str
    status_from: str
    status_to: str
    note: str


@dataclass(frozen=True)
class ReplayResult:
    record: dict[str, Any]
    steps: list[ReplayStep]


def _append_log(fields: dict[str, Any], line: str) -> None:
    fields[C.F_LOG] = ((fields.get(C.F_LOG) or "") + line + "\n").strip() + "\n"


def _advance(record: dict[str, Any], status: str, note: str, steps: list[ReplayStep], **extra: Any) -> None:
    fields = record["fields"]
    current = fields.get(C.F_STATUS)
    if (
        current != status
        and status not in C.VALID_TRANSITIONS.get(current, set())
        and (current, status) not in HUMAN_TRANSITIONS
    ):
        raise AssertionError(f"illegal replay transition: {current} -> {status}")
    fields.update(extra)
    fields[C.F_STATUS] = status
    _append_log(fields, note)
    steps.append(ReplayStep(note.split("]", 1)[0].lstrip("["), current, status, note))


def run_nominal_with_review_retry() -> ReplayResult:
    """Replay the core lifecycle without Feishu, SCM, or real agent calls.

    The scenario covers the most fragile edges:
    intake -> clarify question -> human answer -> PRD confirm -> develop ->
    review fail -> develop retry -> review pass -> done.
    """
    record: dict[str, Any] = {
        "record_id": "rec_replay_1",
        "fields": {
            C.F_TITLE: "回放测试需求",
            C.F_DESC: "修改面试通知推送方式",
            C.F_STATUS: C.S_CLARIFY,
            C.F_CHAT: "oc_replay",
            C.F_FAILS: 0,
            C.F_LOG: "",
        },
    }
    steps: list[ReplayStep] = []

    _advance(
        record,
        C.S_ANSWER,
        "[clarify:fake] 产出澄清问题，待人回答",
        steps,
        **{C.F_CLARIFY: "QUESTIONS\n1. 推送到飞书还是短信？"},
    )
    _advance(
        record,
        C.S_CLARIFY,
        "[human] 补充澄清回答",
        steps,
        **{C.F_CLARIFY: record["fields"][C.F_CLARIFY] + "\n\n【回答】推送到飞书"},
    )
    _advance(
        record,
        C.S_CONFIRM,
        "[clarify:fake] 信息充分，PRD 已生成，待人确认",
        steps,
        **{C.F_PRD: "## PRD\n将面试通知推送方式调整为飞书消息。"},
    )
    _advance(record, C.S_DEV, "[human] 确认开发", steps)
    _advance(
        record,
        C.S_REVIEW,
        "[code:fake] 完成、测试通过、dry-run",
        steps,
        **{C.F_LINK: "dry-run://branch/rec_replay_1"},
    )
    record["fields"][C.F_FAILS] = 1
    _advance(
        record,
        C.S_DEV,
        "[review:fake] FAIL，打回开发：缺少异常路径说明",
        steps,
    )
    _advance(
        record,
        C.S_REVIEW,
        "[code:fake] 修复 review 意见、测试通过、dry-run",
        steps,
    )
    _advance(record, C.S_MERGE, "[review:fake] PASS，dry-run，待人工合并", steps)
    record["fields"][C.F_STATUS] = C.S_DONE
    _append_log(record["fields"], "[human] 人工标记完成")
    steps.append(ReplayStep("human", C.S_MERGE, C.S_DONE, "[human] 人工标记完成"))

    return ReplayResult(record=record, steps=steps)


def render(result: ReplayResult) -> str:
    lines = ["agent-pipeline replay: nominal flow with review retry"]
    for index, step in enumerate(result.steps, 1):
        lines.append(f"{index:02d}. {step.status_from} -> {step.status_to} | {step.note}")
    fields = result.record["fields"]
    lines.append("")
    lines.append(f"final_status: {fields.get(C.F_STATUS)}")
    lines.append(f"failures: {fields.get(C.F_FAILS) or 0}")
    lines.append(f"link: {fields.get(C.F_LINK) or '-'}")
    return "\n".join(lines)


def assert_replay_ok(result: ReplayResult) -> None:
    fields = result.record["fields"]
    if fields.get(C.F_STATUS) != C.S_DONE:
        raise AssertionError(f"final status should be {C.S_DONE}, got {fields.get(C.F_STATUS)}")
    if not fields.get(C.F_PRD):
        raise AssertionError("PRD should be generated before development")
    if not fields.get(C.F_LINK):
        raise AssertionError("review handoff link should be populated")
    if not any(step.status_from == C.S_REVIEW and step.status_to == C.S_DEV for step in result.steps):
        raise AssertionError("review retry path was not exercised")
