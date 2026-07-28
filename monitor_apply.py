#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
monitor_apply.py —— 变化判定 + 历史落库（云端版，不依赖本地 md）

输入：从 stdin 读取 JSON，形如
{
  "dd_new":   { "小剧场特典版": 1, "历史1-16套装": 6, ... },
  "dd_best":  { "历史1": 439, ... },
  "jd_sales": { "小剧场印签版": 1, ... },
  "jd_new":   { ... },
  "jd_sales_小说文学": { "历史1-16套装": 6, "西游1": 16, ... },   // 京东子榜（26 个）
  ...
}
每个 value：整数排名，或 null（未上榜）。

行为：
- 读取 rank-history.json（同目录）
- 与「最近一个日期」的快照逐 (board, key) 对比
- 有任一变化 -> append 今日记录、更新 last_change_date、changed_today=True
- 全无变化 -> 仅更新 last_probe_date / changed_today=False，不追加日期（折线只含变化日）
- 写回 json，并向 stdout 输出 JSON 结果（供调度器决定是否推送）
"""
import json
import sys
import os
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(BASE, "rank-history.json")

# 产品 key 集合
HISTORY = ["历史%d" % i for i in range(1, 17)]
XY = ["西游1", "西游2"]
SETS = ["历史1-16套装", "西游1-2套装", "历史1-3套装"]
DD_KEYS = ["小剧场特典版", "小剧场普通版"] + HISTORY + XY + SETS
JD_KEYS = ["小剧场印签版", "小剧场京东特典版"] + HISTORY + XY + SETS

JD_SUBCATS = [
    "小说文学", "童书", "学考", "经管", "励志与成功", "人文社科", "生活",
    "青春文学", "艺术", "动漫", "考试", "进口原版", "科技",
]

# 主榜 schema
EXPECTED = {
    "dd_new": DD_KEYS,
    "dd_best": DD_KEYS,
    "jd_sales": JD_KEYS,
    "jd_new": JD_KEYS,
}
# 京东 7 大分类子榜（销量 + 新书）
for cat in JD_SUBCATS:
    EXPECTED["jd_sales_" + cat] = JD_KEYS
    EXPECTED["jd_new_" + cat] = JD_KEYS

BOARDS = list(EXPECTED.keys())

# 全部 board 名（含子榜），用于初始化空历史
ALL_BOARDS = BOARDS


def beijing_now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))


def today_str():
    return beijing_now().strftime("%Y-%m-%d")


def load_history():
    if not os.path.exists(JSON_PATH):
        return {
            "meta": {
                "updated_at": "",
                "last_change_date": "",
                "last_probe_date": "",
                "changed_today": False,
                "dd_fetch": "",
                "jd_fetch": "",
            },
            "dates": [],
            "boards": {b: {} for b in ALL_BOARDS},
        }
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def snapshot_from_input(inp):
    """把输入对齐到 EXPECTED key 集，缺失补 null。"""
    out = {}
    for b in BOARDS:
        src = inp.get(b, {}) or {}
        out[b] = {k: src.get(k, None) for k in EXPECTED[b]}
    return out


def diff_changed(prev_snap, new_snap):
    """prev_snap / new_snap 都是 {board: {key: val}}。返回 (changed, detail_list)"""
    changed = False
    detail = []
    for b in BOARDS:
        prev_board = prev_snap.get(b, {})
        new_board = new_snap.get(b, {})
        for k in EXPECTED[b]:
            pv = prev_board.get(k, None)
            nv = new_board.get(k, None)
            if pv != nv:
                changed = True
                detail.append(
                    "%s/%s: %s -> %s"
                    % (
                        b,
                        k,
                        "未上榜" if pv is None else ("第%d名" % pv),
                        "未上榜" if nv is None else ("第%d名" % nv),
                    )
                )
    return changed, detail


def main():
    raw = sys.stdin.read().strip()
    if not raw:
        print(json.dumps({"ok": False, "error": "empty stdin"}, ensure_ascii=False))
        sys.exit(1)
    try:
        inp = json.loads(raw)
    except Exception as e:
        print(
            json.dumps({"ok": False, "error": "json parse: %s" % e}, ensure_ascii=False)
        )
        sys.exit(1)

    new_snap = snapshot_from_input(inp)
    hist = load_history()
    dates = hist.get("dates", [])
    boards = hist.get("boards", {b: {} for b in ALL_BOARDS})
    # 商品链接（来自抓取快照的 urls 字段；以商品 ID 为锚点，稳定可点击）
    urls = inp.get("urls", {}) or {}

    # 最近一日快照
    prev_snap = {b: {} for b in BOARDS}
    if dates:
        last = dates[-1]
        for b in BOARDS:
            prev_snap[b] = {
                k: (
                    boards.get(b, {}).get(k, [None])[-1]
                    if k in boards.get(b, {})
                    else None
                )
                for k in EXPECTED[b]
            }

    changed, detail = diff_changed(prev_snap, new_snap)
    now = beijing_now()
    today = today_str()

    meta = hist.get("meta", {})
    meta["updated_at"] = now.strftime("%Y-%m-%d %H:%M")
    meta["last_probe_date"] = today

    is_new_day = not dates or dates[-1] != today
    if changed:
        if is_new_day:
            dates.append(today)
        for b in BOARDS:
            for k in EXPECTED[b]:
                arr = boards.setdefault(b, {}).setdefault(k, [])
                # 关键修复：新增 key 在同日（非新的一天）首次写入时数组为空，
                # 不能下标 [-1]，必须 append。
                if is_new_day or not arr:
                    arr.append(new_snap[b][k])
                else:
                    arr[-1] = new_snap[b][k]
        meta["last_change_date"] = today
        meta["changed_today"] = True
    else:
        meta["changed_today"] = False

    hist["dates"] = dates
    hist["boards"] = boards
    hist["meta"] = meta

    # 累积合并商品链接：历史已有的保留，本次抓到的覆盖/新增
    # 结构 {board: {key: url}}，即使商品暂时掉榜也不丢链接
    hist_urls = hist.get("urls", {}) or {}
    for b, kv in urls.items():
        if not isinstance(kv, dict):
            continue
        dst = hist_urls.setdefault(b, {})
        for k, u in kv.items():
            if u:
                dst[k] = u
    hist["urls"] = hist_urls

    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)

    result = {
        "ok": True,
        "changed": changed,
        "last_change_date": meta.get("last_change_date", ""),
        "changed_today": meta.get("changed_today", False),
        "detail": detail,
        "dates_count": len(dates),
    }
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
