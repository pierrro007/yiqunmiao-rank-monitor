#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
monitor_apply.py 的回归测试。

核心待测逻辑（最复杂、最易错，此前为零测试覆盖）：
  1) 部分抓取（JD-only / DD-only）时，未抓取的榜应被 carry-forward，
     且所有 board 的数组长度必须与 len(dates) 对齐（否则前端表格错位）。
  2) 无排名变化时，monitor_apply 不应重写 rank-history.json（消除每日空提交）。
  3) 商品链接（urls）应跨次累积合并，旧链接不丢。
  4) 异常输入（空 stdin）应优雅退出，不崩。

测试方式：把 monitor_apply.py 与真实 rank-history.json（作为种子）复制到临时目录，
通过 subprocess 喂 JSON 到 stdin，断言 stdout 结果 + 落库后的文件状态。
（monitor_apply 的 JSON_PATH 基于 __file__ 所在目录，故副本写入 tmp 不会污染仓库。）
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
APPLY = REPO / "monitor_apply.py"
SEED = REPO / "rank-history.json"
assert APPLY.exists(), "monitor_apply.py 未找到"
assert SEED.exists(), "rank-history.json 种子缺失"


@pytest.fixture
def workdir(tmp_path):
    shutil.copy(APPLY, tmp_path / "monitor_apply.py")
    shutil.copy(SEED, tmp_path / "rank-history.json")
    return tmp_path


def run_apply(wd, inp):
    r = subprocess.run(
        [sys.executable, str(wd / "monitor_apply.py")],
        input=json.dumps(inp, ensure_ascii=False),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, "stderr: " + r.stderr[-500:]
    return json.loads(r.stdout), json.load(open(wd / "rank-history.json", encoding="utf-8"))


def base_input(boards, only_prefix=None):
    """构造一个输入：取各 board 最近一日值；若 only_prefix 给定则只保留该前缀的榜。"""
    inp = {}
    for b, kv in boards.items():
        if only_prefix and not b.startswith(only_prefix):
            continue
        inp[b] = {k: (a[-1] if a else None) for k, a in kv.items()}
    return inp


def align_invariant(hist):
    """断言所有 board 数组长度 == len(dates)。"""
    n = len(hist["dates"])
    bad = [
        (b, k, len(a))
        for b, yy in hist["boards"].items()
        for k, a in yy.items()
        if len(a) != n
    ]
    assert not bad, "数组长度未对齐: " + str(bad[:3])


# ---------------------------------------------------------------------------
def test_jd_only_keeps_dd_aligned(workdir):
    """京东-only 运行：当当榜应被 carry-forward，且整体数组对齐。"""
    h0 = json.load(open(workdir / "rank-history.json", encoding="utf-8"))
    n0 = len(h0["dates"])
    inp = base_input(h0["boards"], only_prefix="jd_")
    # 触发一次变化（改动某京东 key）
    b0 = next(b for b in inp if inp[b])
    k0 = next(iter(inp[b0]))
    old = inp[b0][k0]
    inp[b0][k0] = (old if old is not None else 1) + 1
    out, h1 = run_apply(workdir, inp)
    assert out["changed"] is True
    # 核心不变量：所有 board 数组长度 == len(dates)
    align_invariant(h1)
    # 当当榜被 carry-forward：长度对齐且末值保持运行前末值
    for b in ["dd_new", "dd_best", "dd_best_动漫幽默", "dd_new_动漫幽默"]:
        if b in h0["boards"]:
            a0 = h0["boards"][b].get("历史1", [])
            a1 = h1["boards"][b].get("历史1", [])
            assert len(a1) == len(h1["dates"]), b
            assert (a0[-1] if a0 else None) == (a1[-1] if a1 else None), b
    # 日期：同天更新末值则不增加，跨天则 +1（取决于运行时的“今天”）
    assert len(h1["dates"]) in (n0, n0 + 1)


def test_dd_only_keeps_jd_aligned(workdir):
    """当当-only 运行（含子榜数据）：京东榜应被 carry-forward，整体对齐。"""
    h0 = json.load(open(workdir / "rank-history.json", encoding="utf-8"))
    n0 = len(h0["dates"])
    inp = base_input(h0["boards"], only_prefix="dd")
    # 注入子榜数据（用 999 确保与 seed 末值不同 → 确定触发变化）
    inp["dd_best_动漫幽默"] = {"西游2": 999, "西游1": 2, "历史16": 3}
    inp["dd_new_动漫幽默"] = {"小剧场特典版": 1, "小剧场普通版": 4}
    inp["urls"] = {
        "dd_best_动漫幽默": {"西游2": "https://product.dangdang.com/30019679.html"},
        "dd_new_动漫幽默": {"小剧场特典版": "https://product.dangdang.com/30081621.html"},
    }
    out, h1 = run_apply(workdir, inp)
    assert out["changed"] is True
    align_invariant(h1)
    # 京东榜被 carry-forward
    for b in ["jd_sales", "jd_new", "jd_sales_动漫"]:
        if b in h0["boards"]:
            a0 = h0["boards"][b].get("历史1", [])
            a1 = h1["boards"][b].get("历史1", [])
            assert len(a1) == len(h1["dates"]), b
            assert (a0[-1] if a0 else None) == (a1[-1] if a1 else None), b
    # 子榜已落库（取末值，兼容 seed 是否已有历史）
    assert h1["boards"]["dd_best_动漫幽默"]["西游2"][-1] == 999
    assert "dd_best_动漫幽默" in h1["urls"]
    assert len(h1["dates"]) in (n0, n0 + 1)


def test_no_change_no_file_write(workdir):
    """无排名变化时不应重写 rank-history.json（消除每日空提交）。"""
    h0 = json.load(open(workdir / "rank-history.json", encoding="utf-8"))
    before = open(workdir / "rank-history.json", "rb").read()
    inp = base_input(h0["boards"])  # 所有值 == 末值 → 无变化
    out, _ = run_apply(workdir, inp)
    assert out["changed"] is False
    after = open(workdir / "rank-history.json", "rb").read()
    assert before == after, "无变化时不应重写文件"


def test_urls_accumulate_across_runs(workdir):
    """商品链接应跨多次运行累积合并，旧链接不丢。"""
    h0 = json.load(open(workdir / "rank-history.json", encoding="utf-8"))
    inp = base_input(h0["boards"])
    # 第一次：触发变化 + 写入 dd_new 链接
    b0 = next(b for b in inp if inp[b] and b.startswith("jd_"))
    k0 = next(iter(inp[b0]))
    inp[b0][k0] = (inp[b0][k0] or 1) + 1
    inp["urls"] = {"dd_new": {"小剧场特典版": "https://a.example/x"}}
    out1, h1 = run_apply(workdir, inp)
    assert h1["urls"]["dd_new"]["小剧场特典版"] == "https://a.example/x"
    # 第二次：触发变化 + 写入 dd_best 链接（不覆盖第一次的）
    inp2 = base_input(h1["boards"])
    b1 = next(b for b in inp2 if inp2[b] and b.startswith("jd_") and b != b0)
    k1 = next(iter(inp2[b1]))
    inp2[b1][k1] = (inp2[b1][k1] or 1) + 1
    inp2["urls"] = {"dd_best": {"历史1": "https://b.example/y"}}
    out2, h2 = run_apply(workdir, inp2)
    assert h2["urls"]["dd_new"]["小剧场特典版"] == "https://a.example/x"  # 旧保留
    assert h2["urls"]["dd_best"]["历史1"] == "https://b.example/y"        # 新加


def test_empty_stdin_exits_cleanly(workdir):
    """空 stdin 应优雅退出（returncode=1），不崩溃。"""
    r = subprocess.run(
        [sys.executable, str(workdir / "monitor_apply.py")],
        input="",
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1
    assert "empty stdin" in r.stdout
