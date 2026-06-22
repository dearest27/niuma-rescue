#!/usr/bin/env python3
"""Sync ZenTao bugs into Feishu Base."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any

import config as C
import lark
import zentao

OPTIONAL_FIELDS = {
    C.F_EXTERNAL_SOURCE,
    C.F_EXTERNAL_ID,
    C.F_EXTERNAL_URL,
    C.F_EXTERNAL_TYPE,
    C.F_SYNC_STATUS,
    C.F_WORKSPACE,
    C.F_AGENT,
}


@dataclass(frozen=True)
class PullResult:
    fetched: int
    created: int
    skipped: int
    dry_run: bool
    preview: list[dict[str, Any]]


def _existing_bug_ids(records: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for rec in records:
        fields = rec.get("fields", {})
        if str(fields.get(C.F_EXTERNAL_SOURCE) or "").lower() == "zentao" and fields.get(C.F_EXTERNAL_ID):
            ids.add(str(fields.get(C.F_EXTERNAL_ID)))
            continue
        text = "\n".join(str(fields.get(k) or "") for k in (C.F_DESC, C.F_LOG))
        marker = "【外部来源】zentao bug #"
        if marker in text:
            tail = text.split(marker, 1)[1].splitlines()[0].strip()
            if tail:
                ids.add(tail)
    return ids


def _is_missing_field_error(exc: Exception, field_name: str) -> bool:
    text = str(exc)
    return "FieldNameNotFound" in text or f"fields.{field_name}" in text or field_name in text


def _create_with_optional_fallback(fields: dict[str, Any]) -> dict:
    remaining = dict(fields)
    for _ in range(len(OPTIONAL_FIELDS) + 1):
        try:
            return lark.create(remaining)
        except Exception as exc:
            removed = False
            for field in list(remaining):
                if field in OPTIONAL_FIELDS and _is_missing_field_error(exc, field):
                    remaining.pop(field, None)
                    removed = True
                    break
            if not removed:
                raise
    return lark.create(remaining)


def _config_with_limit(cfg: zentao.ZentaoConfig, limit: int | None) -> zentao.ZentaoConfig:
    if limit is not None:
        query = dict(cfg.bug_query or {})
        query["limit"] = limit
        cfg = zentao.ZentaoConfig(**{**cfg.__dict__, "bug_query": query})
    return cfg


def pull_bugs(config: str | None = None, *, limit: int | None = None, dry_run: bool = False) -> PullResult:
    cfg = _config_with_limit(zentao.load_config(config), limit)
    bugs = zentao.fetch_bugs(cfg)
    records = lark.list_records() if not dry_run else []
    existing = _existing_bug_ids(records)
    created = 0
    skipped = 0
    preview: list[dict[str, Any]] = []
    for bug in bugs:
        if bug.id in existing:
            skipped += 1
            continue
        fields = zentao.base_fields_for_bug(bug, cfg)
        if dry_run:
            preview.append(fields)
        else:
            _create_with_optional_fallback(fields)
            created += 1
            existing.add(bug.id)
    return PullResult(fetched=len(bugs), created=created, skipped=skipped, dry_run=dry_run, preview=preview)


def pull(args: argparse.Namespace) -> int:
    result = pull_bugs(args.config, limit=args.limit, dry_run=args.dry_run)
    for fields in result.preview:
        print(json.dumps(fields, ensure_ascii=False))
    print(
        f"zentao pull: fetched={result.fetched} created={result.created} "
        f"skipped={result.skipped} dry_run={result.dry_run}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync ZenTao bugs into Feishu Base")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_pull = sub.add_parser("pull")
    p_pull.add_argument("--config", help="path to zentao.json")
    p_pull.add_argument("--limit", type=int)
    p_pull.add_argument("--dry-run", action="store_true")
    p_pull.set_defaults(func=pull)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
