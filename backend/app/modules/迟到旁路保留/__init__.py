# -*- coding: utf-8 -*-
"""空桩落地：迟到旁路保留（形态乙——双写两轨）。

形态选择（题面形态甲/乙择一，本实现为形态乙）：
- 迟到匣：只记一行摘要（现行缓冲不吃该点，升格回放不读迟到匣全文）；
- 旁路凭证表：冻结原事件时，供页回读，后续「迟到升格回放」改读凭证表对窗；
- 只读归档包：完整事件原文，键与凭证不同，回读来源可区分。

硬约束（与题面一一对应）：
- 写迟到匣与观测命运同成败：三写与观测点命运更新在同一连接同一事务，
  任一失败整体回滚，不留孤儿；
- 补灌同号并发不双匣：凭证表 事件编号 UNIQUE + 迟到匣侧由装载幂等前置，
  约束层直接 IntegrityError，不靠应用锁；
- 禁止只改迟到角标：旁路必须有凭证行与归档行，贯验按三表互证。
"""
from __future__ import annotations

import json
import uuid

from app.db import 连接
from app.seed import 此刻

NAME = "迟到旁路保留"
CONTRACT = "迟到匣条目带旁路标签，旁路凭证冻原事件时，归档包留完整事件"
保留形态名 = "形态乙-双写两轨"

HOOKS = (
    "before_load",
    "after_load",
    "decide_window",
    "before_close",
    "after_close",
    "extra_verify_checks",
)


def describe() -> dict:
    return {
        "空桩": NAME,
        "契约": CONTRACT,
        "钩子": list(HOOKS),
        "默认启用": True,
        "保留形态": 保留形态名,
        "说明": (
            "出窗事件三写同事务：迟到匣一行摘要 + 旁路凭证冻原事件时 + "
            "只读归档包存完整事件；同号并发由凭证 UNIQUE 约束挡双匣，不加应用锁。"
        ),
    }


def before_load(batch: dict) -> dict:
    """装载前：默认透传。"""
    return batch


def after_load(report: dict) -> dict:
    """装载后：默认透传，旁路统计已在装载报告明细里。"""
    return report


def decide_window(event: dict, rules: dict) -> str:
    """入窗判定仍由装载服务按事件时统一裁定，本桩不旁路该判定。"""
    return "入缓冲"


def before_close(window_id: str) -> None:
    return None


def after_close(report: dict) -> dict:
    return report


def _生键(事件编号: str) -> tuple[str, str]:
    尾 = uuid.uuid4().hex[:10]
    return f"凭证/{事件编号}/{尾}", f"归档/{事件编号}/{尾}"


def 写旁路(conn, ev: dict, 处理时: str, 窗号: str) -> dict:
    """在调用方事务内完成三写。

    顺序刻意先凭证（UNIQUE 事件编号作并发护栏，撞号即 IntegrityError 整体回滚），
    再迟到匣摘要、再只读归档；三写与调用方对观测点的命运更新同票。
    返回写入的凭证/归档键。
    """
    编号 = ev["事件编号"]
    原事件时 = ev["事件时"]
    凭证键, 归档键 = _生键(编号)
    conn.execute(
        """INSERT INTO 旁路凭证
           (凭证键,事件编号,运单键,原事件时,归档键,窗号,保留形态,valid,入匣时)
           VALUES (?,?,?,?,?,?,?,1,?)""",
        (凭证键, 编号, ev["运单键"], 原事件时, 归档键, 窗号, 保留形态名, 处理时),
    )
    conn.execute(
        """INSERT INTO 迟到匣(事件编号,运单键,原因,事件时,入匣时,旁路标签)
           VALUES (?,?,?,?,?,?)""",
        (编号, ev["运单键"], "事件时出窗", 原事件时, 处理时, 保留形态名),
    )
    conn.execute(
        """INSERT INTO 旁路归档(归档键,事件编号,原事件时,事件原文,只读,归档时)
           VALUES (?,?,?,?,1,?)""",
        (
            归档键,
            编号,
            原事件时,
            json.dumps(ev, ensure_ascii=False, sort_keys=True),
            处理时,
        ),
    )
    return {"凭证键": 凭证键, "归档键": 归档键}


def 旁路列表() -> dict:
    """新增旁路保留只读接口数据。

    条目含题面四要素：事件编号、原事件时、凭证或归档键、valid。
    """
    conn = 连接()
    try:
        rows = conn.execute(
            """SELECT 凭证键,事件编号,运单键,原事件时,归档键,窗号,保留形态,valid,入匣时
               FROM 旁路凭证 ORDER BY 入匣时 DESC, 凭证键 DESC LIMIT 200"""
        ).fetchall()
        return {
            "策略": describe(),
            "条目": [
                {
                    "事件编号": r["事件编号"],
                    "运单键": r["运单键"],
                    "原事件时": r["原事件时"],
                    "凭证键": r["凭证键"],
                    "归档键": r["归档键"],
                    "窗号": r["窗号"],
                    "保留形态": r["保留形态"],
                    "valid": bool(r["valid"]),
                    "入匣时": r["入匣时"],
                }
                for r in rows
            ],
        }
    finally:
        conn.close()


def 回读(事件编号: str) -> dict | None:
    """按事件编号回读，凭证表与归档包两个来源、两个键，可区分。"""
    conn = 连接()
    try:
        凭证 = conn.execute(
            "SELECT * FROM 旁路凭证 WHERE 事件编号=?", (事件编号,)
        ).fetchone()
        if 凭证 is None:
            return None
        归档 = conn.execute(
            "SELECT * FROM 旁路归档 WHERE 归档键=?", (凭证["归档键"],)
        ).fetchone()
        原文 = json.loads(归档["事件原文"]) if 归档 is not None else None
        return {
            "事件编号": 事件编号,
            "原事件时": 凭证["原事件时"],
            "valid": bool(凭证["valid"]),
            "保留形态": 凭证["保留形态"],
            "凭证": {
                "来源": "旁路凭证表",
                "键": 凭证["凭证键"],
                "原事件时": 凭证["原事件时"],
            },
            "归档": {
                "来源": "只读归档包",
                "键": 凭证["归档键"],
                "原事件时": 归档["原事件时"] if 归档 is not None else None,
                "只读": bool(归档["只读"]) if 归档 is not None else False,
                "事件原文": 原文,
            },
        }
    finally:
        conn.close()


def extra_verify_checks(snapshot: dict) -> list[str]:
    """旁路三表互证。空列表表示通过；返回中文错误文案。"""
    错: list[str] = []
    conn = 连接()
    try:
        匣行 = conn.execute(
            "SELECT 事件编号,事件时,旁路标签 FROM 迟到匣 ORDER BY 编号"
        ).fetchall()
        for r in 匣行:
            编号 = r["事件编号"]
            凭证 = conn.execute(
                "SELECT * FROM 旁路凭证 WHERE 事件编号=?", (编号,)
            ).fetchone()
            if 凭证 is None:
                错.append(f"迟到匣事件 {编号} 缺旁路凭证：原事件时无冻结留痕")
                continue
            if 凭证["原事件时"] != r["事件时"]:
                错.append(
                    f"迟到匣事件 {编号} 凭证原事件时 {凭证['原事件时']} "
                    f"与匣记 {r['事件时']} 不符"
                )
            if 凭证["valid"] != 1:
                错.append(f"迟到匣事件 {编号} 旁路凭证 valid 失效")
            归档 = conn.execute(
                "SELECT * FROM 旁路归档 WHERE 归档键=?", (凭证["归档键"],)
            ).fetchone()
            if 归档 is None:
                错.append(f"迟到匣事件 {编号} 缺只读归档包：完整事件丢失")
                continue
            if 归档["原事件时"] != 凭证["原事件时"]:
                错.append(f"迟到匣事件 {编号} 归档原事件时与凭证不符")
            if 归档["只读"] != 1:
                错.append(f"迟到匣事件 {编号} 归档包只读标记被破坏")
            try:
                原文 = json.loads(归档["事件原文"])
            except (TypeError, ValueError):
                错.append(f"迟到匣事件 {编号} 归档事件原文无法解析")
                continue
            if 原文.get("事件时") != 凭证["原事件时"]:
                错.append(f"迟到匣事件 {编号} 归档原文事件时与冻结原事件时不符")
        # 反向：有凭证无匣（孤儿凭证）
        孤儿 = conn.execute(
            """SELECT p.事件编号 FROM 旁路凭证 p
               LEFT JOIN 迟到匣 l ON l.事件编号=p.事件编号
               WHERE l.编号 IS NULL"""
        ).fetchall()
        for r in 孤儿:
            错.append(f"旁路凭证 {r['事件编号']} 无迟到匣摘要行：双写不完整")
    finally:
        conn.close()
    return 错
