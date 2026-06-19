#!/usr/bin/env python3
"""一键建飞书多维表格：建 Base + 全部字段，并把 base_token/table_id 写回 .env。

首次部署：
  1. 在项目 .env 配好 FEISHU_APP_ID / FEISHU_APP_SECRET（app 须有 bitable + im 权限）
  2. python3 bootstrap.py
建完即可 python3 doctor.py 自检 → python3 listener.py + python3 dispatcher.py 跑起来。

已配置过 BASE_TOKEN 时默认拒绝（避免覆盖在用的表）；确需新建另一张表加 --force。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import config as C
import lark  # 复用 tenant_access_token + 通用 _api（config 现在 import 不强求 BASE_TOKEN）

_ENV = Path(__file__).resolve().parent / ".env"

# 字段 schema：(字段名, 飞书字段 type, property)
# type: 1=多行文本 2=数字 3=单选 11=人员 1002=最后更新时间
_STATUS_OPTIONS = [C.S_CLARIFY, C.S_ANSWER, C.S_CONFIRM, C.S_DEV,
                   C.S_REVIEW, C.S_MERGE, C.S_DONE, C.S_BLOCKED]
_FIELDS = [
    (C.F_STATUS, 3, {"options": [{"name": s} for s in _STATUS_OPTIONS]}),
    (C.F_DESC, 1, None),
    (C.F_CLARIFY, 1, None),
    (C.F_PRD, 1, None),
    (C.F_LINK, 1, None),
    (C.F_LOG, 1, None),
    (C.F_FAILS, 2, None),
    (C.F_OWNER, 11, None),
    (C.F_CHAT, 1, None),
    (C.F_WORKSPACE, 1, None),
    (C.F_AGENT, 1, None),
    (C.F_AGENT_CLARIFY, 1, None),
    (C.F_AGENT_CODE, 1, None),
    (C.F_AGENT_REVIEW, 1, None),
    ("更新时间", 1002, {"date_formatter": "yyyy-MM-dd HH:mm"}),
]


def main() -> None:
    if os.getenv("PIPELINE_BASE_TOKEN") and "--force" not in sys.argv:
        sys.exit("已配置 PIPELINE_BASE_TOKEN（.env 里已有一张表）。\n"
                 "确需新建另一张表请加 --force：python3 bootstrap.py --force")

    # 0. 凭据
    try:
        lark._tenant_access_token()
    except Exception as e:
        sys.exit(f"✗ 取飞书 token 失败：{e}\n  请在项目 .env 配 FEISHU_APP_ID/FEISHU_APP_SECRET")
    print("✓ 飞书凭据 OK")

    # 1. 建 Base
    app = lark._api("POST", "/open-apis/bitable/v1/apps", {"name": "需求流水线"})["app"]
    app_token = app["app_token"]
    print(f"✓ 已建 Base：{app_token}  {app.get('url', '')}")

    # 2. 默认数据表
    tables = lark._api("GET", f"/open-apis/bitable/v1/apps/{app_token}/tables")["items"]
    table_id = tables[0]["table_id"]
    base = f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}"
    print(f"✓ 默认数据表：{table_id}")

    # 3. 主字段改名为 需求标题，其余默认字段删掉
    fields = lark._api("GET", f"{base}/fields")["items"]
    lark._api("PUT", f"{base}/fields/{fields[0]['field_id']}",
              {"field_name": C.F_TITLE, "type": 1})
    print(f"✓ 主字段 → {C.F_TITLE}")
    for f in fields[1:]:
        try:
            lark._api("DELETE", f"{base}/fields/{f['field_id']}")
        except Exception as e:
            print(f"  ! 删默认字段 {f.get('field_name')} 失败（忽略）：{e}")

    # 4. 建其余字段（单个失败不中断）
    for name, ftype, prop in _FIELDS:
        body = {"field_name": name, "type": ftype}
        if prop:
            body["property"] = prop
        try:
            lark._api("POST", f"{base}/fields", body)
            print(f"  + {name}")
        except Exception as e:
            print(f"  ! 建字段 {name} 失败（忽略，可手动补）：{e}")

    # 5. 写回 .env
    _write_env(app_token, table_id)
    print(f"\n✓ 完成！已写回 {_ENV}")
    print(f"  PIPELINE_BASE_TOKEN={app_token}")
    print(f"  PIPELINE_TABLE_ID={table_id}")
    print("\n下一步：python3 doctor.py 自检 → python3 dispatcher.py 跑起来。")


def _write_env(app_token: str, table_id: str) -> None:
    """就地更新 .env 的 PIPELINE_BASE_TOKEN / PIPELINE_TABLE_ID，保留其余行。"""
    lines = _ENV.read_text(encoding="utf-8").splitlines() if _ENV.exists() else []
    out, seen = [], set()
    for line in lines:
        s = line.strip()
        if s.startswith("PIPELINE_BASE_TOKEN="):
            out.append(f"PIPELINE_BASE_TOKEN={app_token}"); seen.add("b")
        elif s.startswith("PIPELINE_TABLE_ID="):
            out.append(f"PIPELINE_TABLE_ID={table_id}"); seen.add("t")
        else:
            out.append(line)
    if "b" not in seen:
        out.append(f"PIPELINE_BASE_TOKEN={app_token}")
    if "t" not in seen:
        out.append(f"PIPELINE_TABLE_ID={table_id}")
    _ENV.write_text("\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
