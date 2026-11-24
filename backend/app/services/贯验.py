"""窗贯验：缓冲/切片/迟到互证。"""
from __future__ import annotations
from app.db import 连接
from app.seed import 此刻
from app.modules import 迟到旁路保留


def 跑一次() -> dict:
    conn = 连接()
    try:
        缓冲 = conn.execute(
            "SELECT COUNT(*) AS c FROM 入窗缓冲 WHERE 命运='在缓冲'"
        ).fetchone()["c"]
        已物化缓冲 = conn.execute(
            "SELECT COUNT(*) AS c FROM 入窗缓冲 WHERE 命运='已物化'"
        ).fetchone()["c"]
        切片事件 = conn.execute(
            "SELECT COALESCE(SUM(事件数),0) AS c FROM 窗切片账"
        ).fetchone()["c"]
        迟到 = conn.execute("SELECT COUNT(*) AS c FROM 迟到匣").fetchone()["c"]
        # 基线口径：已物化缓冲事件数应等于切片事件数之和
        对齐 = 1 if int(已物化缓冲) == int(切片事件) else 0
        if 对齐:
            说明 = "缓冲物化与切片事件数一致"
        else:
            说明 = f"撕裂：已物化缓冲={已物化缓冲} 切片事件={切片事件}"
        # 迟到旁路协同：迟到只进旁路，不参与切片口径；但三表互证损坏也算撕裂，
        # 避免迟到被切片误伤的同时，旁路丢失要能被贯验抓到。
        旁路错 = 迟到旁路保留.extra_verify_checks({})
        if 旁路错:
            对齐 = 0
            说明 = f"{说明}；旁路互证失败：{'；'.join(旁路错[:3])}"
        elif 迟到:
            说明 = f"{说明}；旁路同票完整（迟到 {迟到} 笔，不计入切片）"
        时刻 = 此刻()
        conn.execute(
            """INSERT INTO 窗贯验(时刻,缓冲笔数,切片笔数,迟到笔数,是否对齐,说明)
               VALUES (?,?,?,?,?,?)""",
            (时刻, 缓冲, int(切片事件), 迟到, 对齐, 说明),
        )
        conn.commit()
        return {
            "时刻": 时刻,
            "在缓冲": 缓冲,
            "已物化缓冲": int(已物化缓冲),
            "切片事件": int(切片事件),
            "迟到": 迟到,
            "是否对齐": bool(对齐),
            "说明": 说明,
            "旁路检查": 旁路错,
        }
    finally:
        conn.close()
