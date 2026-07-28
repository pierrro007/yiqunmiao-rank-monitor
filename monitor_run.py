#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
monitor_run.py —— 一键编排：抓取四榜 -> 变化判定 -> 落库。
设计目标：让云端自动化「不依赖 Mac 本地文件」也能跑。
  - 若本地 rank-history.json 不存在（云端首次/工作区被回收），
    先尝试从线上 LIVE_URL 拉取当前历史作为种子；拉取失败则初始化空历史。
  - 复用 monitor_fetch.py（抓取快照）与 monitor_apply.py（变化判定+落库），
    不修改这两个脚本。
  - 向 stdout 输出 monitor_apply.py 的结果 JSON（changed/detail 等），
    供上层调度器决定是否重部署与推送。
"""
import json
import os
import sys
import subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(BASE, "rank-history.json")
LIVE_URL = os.environ.get(
    "LIVE_URL",
    "https://3000-1eb612389fbf4238aa6286be6b6d35b3.e2b.ap-beijing.sandbox.cloudstudio.club/rank-history.json",
)

# 京东 13 大分类子榜（与 monitor_fetch.py / monitor_apply.py 保持一致）
JD_SUBCATS = [
    "小说文学", "童书", "学考", "经管", "励志与成功", "人文社科", "生活",
    "青春文学", "艺术", "动漫", "考试", "进口原版", "科技",
]


def seed_history():
    """保证 rank-history.json 存在：本地优先，否则从线上拉取，再否则初始化空。"""
    if os.path.exists(JSON_PATH):
        return "local"
    try:
        import urllib.request

        req = urllib.request.Request(
            LIVE_URL, headers={"User-Agent": "Mozilla/5.0"}
        )
        data = urllib.request.urlopen(req, timeout=30).read()
        with open(JSON_PATH, "wb") as f:
            f.write(data)
        return "seed_from_live"
    except Exception:
        empty = {
            "meta": {
                "updated_at": "",
                "last_change_date": "",
                "last_probe_date": "",
                "changed_today": False,
                "dd_fetch": "",
                "jd_fetch": "",
            },
            "dates": [],
            "boards": {
                b: {}
                for b in (
                    ["dd_new", "dd_best", "jd_sales", "jd_new"]
                    + ["jd_sales_%s" % c for c in JD_SUBCATS]
                    + ["jd_new_%s" % c for c in JD_SUBCATS]
                )
            },
        }
        with open(JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(empty, f, ensure_ascii=False, indent=2)
        return "init_empty"


def main():
    seed_src = seed_history()
    # 抓取四榜快照
    snap = subprocess.run(
        [sys.executable, "monitor_fetch.py"],
        cwd=BASE,
        capture_output=True,
        text=True,
    )
    if not snap.stdout.strip():
        print(json.dumps(
            {"ok": False, "error": "fetch empty", "stderr": snap.stderr[-500:]},
            ensure_ascii=False,
        ))
        sys.exit(1)
    # 变化判定 + 落库
    res = subprocess.run(
        [sys.executable, "monitor_apply.py"],
        cwd=BASE,
        input=snap.stdout,
        capture_output=True,
        text=True,
    )
    try:
        out = json.loads(res.stdout)
    except Exception:
        out = {"ok": False, "error": "apply output parse fail", "stdout": res.stdout[-500:]}
    out["seed_source"] = seed_src
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
