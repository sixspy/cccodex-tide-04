# -*- coding: utf-8 -*-
"""迟到旁路保留：形态乙（双写两轨）红绿测。

覆盖：
- 纯迟到：迟到匣摘要行 + 旁路凭证（冻原事件时）+ 只读归档包（完整事件）三写齐；
- 只读接口字段：事件编号、原事件时、凭证或归档键、valid；
- 回读可区分凭证表与归档包两个来源；
- 同号补灌/并发再迟到不得双匣（约束层护栏，不靠应用锁）；
- 写迟到匣与观测命运同成败（归档故障整体回滚，不留孤儿）；
- 窗内成片不进旁路，贯验不被迟到误伤；旁路损坏贯验即报撕裂。
"""
from pathlib import Path
import os
import sqlite3
import tempfile

import pytest

_临时库 = Path(tempfile.mkdtemp()) / "旁路保留.db"
os.environ.setdefault("数据目录", str(_临时库.parent))

from app.db import 连接  # noqa: E402
import app.db as _db  # noqa: E402

_db.库路径 = _临时库  # 与主链测试物理隔库

from app import seed  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import 迟到旁路保留  # noqa: E402
from app.services import 装载, 关窗, 贯验, 目录, 夹具  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402


@pytest.fixture(autouse=True)
def _库():
    seed.初始化库()
    conn = 连接()
    for 表 in ("旁路归档", "旁路凭证", "迟到匣", "入窗缓冲", "窗切片账",
               "观测点", "装载批次", "关窗批次", "窗贯验"):
        conn.execute(f"DELETE FROM {表}")
    conn.commit()
    conn.close()
    yield


def _迟到事件(编号: str = "旁-E1", 事件时: str = "2026-11-24T02:10:00Z") -> dict:
    return {
        "事件编号": 编号,
        "运单键": "运单-旁",
        "网点键": "网点-东站",
        "事件时": 事件时,
    }


def _计数(sql: str, 参数=()) -> int:
    conn = 连接()
    try:
        return conn.execute(sql, 参数).fetchone()[0]
    finally:
        conn.close()


def test_纯迟到双写两轨齐_凭证冻原事件时():
    ev = _迟到事件()
    报告 = 装载.装一批({"批次编号": "旁批-1", "事件": [ev]})
    assert 报告["迟到"] == 1

    # 迟到匣：一行摘要，带旁路标签（不止改角标，另有凭证与归档）
    匣 = 目录.迟到列表()
    assert len(匣) == 1
    assert 匣[0]["事件编号"] == "旁-E1"
    assert 匣[0]["事件时"] == ev["事件时"]
    assert 匣[0]["旁路标签"] == 迟到旁路保留.保留形态名

    # 旁路凭证：冻结原事件时，valid=1
    assert _计数("SELECT COUNT(*) FROM 旁路凭证") == 1
    conn = 连接()
    try:
        凭证 = conn.execute("SELECT * FROM 旁路凭证 WHERE 事件编号=?", ("旁-E1",)).fetchone()
        assert 凭证["原事件时"] == ev["事件时"]
        assert 凭证["valid"] == 1
        assert 凭证["保留形态"] == 迟到旁路保留.保留形态名
        assert 凭证["凭证键"] and 凭证["归档键"]
        # 只读归档包：完整事件原文，原事件时一致
        归档 = conn.execute("SELECT * FROM 旁路归档 WHERE 归档键=?", (凭证["归档键"],)).fetchone()
        assert 归档 is not None
        assert 归档["只读"] == 1
        assert 归档["原事件时"] == ev["事件时"]
        import json

        原文 = json.loads(归档["事件原文"])
        assert 原文["事件编号"] == "旁-E1"
        assert 原文["网点键"] == "网点-东站"
    finally:
        conn.close()

    # 观测流命运与旁路同票
    观测 = [r for r in 目录.观测列表() if r["事件编号"] == "旁-E1"][0]
    assert 观测["命运"] == "已迟到"
    assert 观测["旁路保留"] == 迟到旁路保留.保留形态名
    assert 观测["旁路有效"] == 1


def test_只读接口字段齐_策略可读():
    装载.装一批({"事件": [_迟到事件("旁-E2")]})
    with TestClient(app) as c:
        r = c.get("/api/迟到旁路保留")
    assert r.status_code == 200
    体 = r.json()
    assert 体["策略"]["空桩"] == "迟到旁路保留"
    assert 体["策略"]["保留形态"] == 迟到旁路保留.保留形态名
    条目 = {x["事件编号"]: x for x in 体["条目"]}
    行 = 条目["旁-E2"]
    # 题面要求四字段：事件编号、原事件时、凭证或归档键、valid
    assert 行["原事件时"] == "2026-11-24T02:10:00Z"
    assert 行["凭证键"].startswith("凭证/")
    assert 行["归档键"].startswith("归档/")
    assert 行["valid"] is True


def test_回读来源可区分_凭证与归档不同引用():
    装载.装一批({"事件": [_迟到事件("旁-E3")]})
    回 = 迟到旁路保留.回读("旁-E3")
    assert 回 is not None
    assert 回["原事件时"] == "2026-11-24T02:10:00Z"
    assert 回["凭证"]["来源"] == "旁路凭证表"
    assert 回["归档"]["来源"] == "只读归档包"
    assert 回["凭证"]["键"] != 回["归档"]["键"]
    assert 回["凭证"]["键"].startswith("凭证/")
    assert 回["归档"]["键"].startswith("归档/")
    assert 回["归档"]["事件原文"]["运单键"] == "运单-旁"
    assert 回["valid"] is True


def test_同号再迟到_服务层与约束层都不双匣():
    ev = _迟到事件("旁-E4")
    assert 装载.装一批({"批次编号": "旁批-2a", "事件": [ev]})["迟到"] == 1
    # 服务层补灌：幂等驳回，不新增匣/凭证/归档
    再 = 装载.装一批({"批次编号": "旁批-2b", "事件": [dict(ev)]})
    assert 再["驳回"] == 1
    assert _计数("SELECT COUNT(*) FROM 迟到匣 WHERE 事件编号=?", ("旁-E4",)) == 1
    assert _计数("SELECT COUNT(*) FROM 旁路凭证 WHERE 事件编号=?", ("旁-E4",)) == 1
    assert _计数("SELECT COUNT(*) FROM 旁路归档 WHERE 事件编号=?", ("旁-E4",)) == 1

    # 约束层：绕开上层幂等，两个连接并发同号写旁路，无一加锁
    c1, c2 = 连接(), 连接()
    try:
        迟到旁路保留.写旁路(c1, _迟到事件("旁-E5"), "2026-11-24T05:00:00Z", "W-demo")
        c1.commit()
        with pytest.raises(sqlite3.IntegrityError):
            迟到旁路保留.写旁路(c2, _迟到事件("旁-E5"), "2026-11-24T05:01:00Z", "W-demo")
        c2.rollback()
    finally:
        c1.close()
        c2.close()
    assert _计数("SELECT COUNT(*) FROM 迟到匣 WHERE 事件编号=?", ("旁-E5",)) == 1
    assert _计数("SELECT COUNT(*) FROM 旁路凭证 WHERE 事件编号=?", ("旁-E5",)) == 1
    assert _计数("SELECT COUNT(*) FROM 旁路归档 WHERE 事件编号=?", ("旁-E5",)) == 1


def test_写旁路与观测命运同成败_故障整体回滚():
    conn = 连接()
    conn.execute(
        "CREATE TRIGGER 归档故障 BEFORE INSERT ON 旁路归档 "
        "BEGIN SELECT RAISE(FAIL, '模拟归档包不可写'); END"
    )
    conn.commit()
    conn.close()
    try:
        with pytest.raises(Exception):
            装载.装一批({"事件": [_迟到事件("旁-E6")]})
    finally:
        conn = 连接()
        conn.execute("DROP TRIGGER 归档故障")
        conn.commit()
        conn.close()
    # 同成败：观测点、迟到匣、凭证、归档全部不留行
    assert _计数("SELECT COUNT(*) FROM 观测点 WHERE 事件编号=?", ("旁-E6",)) == 0
    assert _计数("SELECT COUNT(*) FROM 迟到匣 WHERE 事件编号=?", ("旁-E6",)) == 0
    assert _计数("SELECT COUNT(*) FROM 旁路凭证 WHERE 事件编号=?", ("旁-E6",)) == 0
    assert _计数("SELECT COUNT(*) FROM 旁路归档 WHERE 事件编号=?", ("旁-E6",)) == 0


def test_窗内成片不进旁路_贯验不被迟到误伤():
    报告 = 装载.装一批(夹具.读取("窗内成片样例"))
    assert 报告["入缓冲"] == 3
    assert _计数("SELECT COUNT(*) FROM 旁路凭证") == 0
    assert _计数("SELECT COUNT(*) FROM 旁路归档") == 0
    关 = 关窗.关当前窗()
    assert 关["成片数"] == 2

    # 纯迟到落在切片之外：贯验仍对齐，迟到不计入切片撕裂
    装载.装一批(夹具.读取("迟到样例"))
    验 = 贯验.跑一次()
    assert 验["是否对齐"] is True
    assert 验["迟到"] == 1
    assert "撕裂" not in 验["说明"]
    assert "旁路" in 验["说明"]


def test_旁路凭证损坏_贯验协同报撕裂():
    装载.装一批({"事件": [_迟到事件("旁-E7")]})
    assert 贯验.跑一次()["是否对齐"] is True
    conn = 连接()
    conn.execute("DELETE FROM 旁路凭证 WHERE 事件编号=?", ("旁-E7",))
    conn.commit()
    conn.close()
    验 = 贯验.跑一次()
    assert 验["是否对齐"] is False
    assert "旁路" in 验["说明"]

    # 模块直检：给出可定位的中文错误
    错 = 迟到旁路保留.extra_verify_checks({})
    assert any("旁-E7" in e and "凭证" in e for e in 错)
