#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_dd.py —— 在中国境内环境定时采集「当当」榜单（含子榜），回传到 GitHub 仓库。

为什么不用 git commit/push？
  云端 Actions 也在写同一个 rank-history.json（京东部分），两个 writer 用普通
  git 合并会在 JSON 文本上冲突。改用 GitHub Contents API（PUT 带 SHA）做原子覆盖：
  若云端在此期间有新提交（SHA 过期，HTTP 409），自动重新拉取最新 → 重新应用当当数据
  → 再回传，天然规避并发覆盖。

  注：本环境 urllib 直连 API 上传会遇 SSL 中断，故一律走 curl（与仓库其他脚本一致）。

数据流：
  1) 拉取仓库最新 rank-history.json / product-map.json 到本地（基于云端最新版）
  2) FETCH_MODE=dd 运行 monitor_run.py（仅抓当当，变化判定+落库，京东部分 carry-forward 保留）
  3) 用 Contents API 把更新后的两个文件回传（带 SHA，409 自动重试）
  4) 运行飞书推送（有变化时；需 FEISHU_WEBHOOK 环境变量）

环境变量：
  GH_TOKEN         —— GitHub PAT（repo 权限），必填
  FEISHU_WEBHOOK   —— 飞书机器人地址，选填（不填则跳过飞书推送）
"""
import os
import sys
import json
import base64
import subprocess

TOKEN = os.environ.get("GH_TOKEN", "")
REPO = "pierrro007/yiqunmiao-rank-monitor"
BASE = os.path.dirname(os.path.abspath(__file__))
RH = os.path.join(BASE, "rank-history.json")
PM = os.path.join(BASE, "product-map.json")


def _curl(args):
    cmd = ["curl", "-sS", "--max-time", "60", "-H", "Authorization: token " + TOKEN,
           "-H", "Accept: application/vnd.github+json"] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        raise RuntimeError("curl 失败: " + r.stderr[:200])
    return r.stdout


def api_get(path):
    out = _curl(["https://api.github.com/repos/%s/contents/%s?ref=main" % (REPO, path)])
    return json.loads(out)


def api_put(path, content_str, sha):
    body = {
        "message": "chore: 当当榜单更新(中国境内 DD 模式)",
        "content": base64.b64encode(content_str.encode("utf-8")).decode("ascii"),
        "sha": sha,
        "branch": "main",
    }
    out = _curl([
        "-X", "PUT",
        "-H", "Content-Type: application/json",
        "-d", json.dumps(body),
        "https://api.github.com/repos/%s/contents/%s" % (REPO, path),
    ])
    return json.loads(out)


def download(path, local):
    meta = api_get(path)
    data = base64.b64decode(meta["content"]).decode("utf-8")
    with open(local, "w", encoding="utf-8") as f:
        f.write(data)
    return meta["sha"]


def run_dd():
    """拉取最新 → 仅抓当当并落库。返回 (sha_rh, sha_pm)。"""
    sha_rh = download("rank-history.json", RH)
    sha_pm = download("product-map.json", PM)
    env = dict(os.environ)
    env["FETCH_MODE"] = "dd"
    r = subprocess.run(
        [sys.executable, "monitor_run.py"], cwd=BASE, env=env,
        capture_output=True, text=True,
    )
    print("monitor_run:", r.stdout.strip()[:240])
    if r.returncode != 0:
        print("monitor_run 失败:", r.stderr[-300:])
    return sha_rh, sha_pm


def main():
    if not TOKEN:
        print("❌ 缺少 GH_TOKEN 环境变量"); sys.exit(1)
    sha_rh, sha_pm = run_dd()
    for attempt in range(4):
        try:
            api_put("rank-history.json", open(RH, encoding="utf-8").read(), sha_rh)
            api_put("product-map.json", open(PM, encoding="utf-8").read(), sha_pm)
            print("✅ 已回传当当数据到仓库")
            break
        except Exception as e:
            msg = str(e)
            # curl 拿到 409 时 GitHub 返回 JSON，含 "message":"SHA does not match"
            if "409" in msg or "SHA does not match" in msg:
                print("⚠️ SHA 过期（云端有新提交），重新拉取并应用后重试 (%d)…" % (attempt + 1))
                sha_rh, sha_pm = run_dd()
                continue
            print("❌ 回传失败:", msg[:200]); break
    # 飞书推送（有变化时；需 FEISHU_WEBHOOK 环境变量）
    try:
        subprocess.run([sys.executable, "feishu_push.py"], cwd=BASE, env=os.environ)
    except Exception as e:
        print("飞书推送异常(忽略):", e)


if __name__ == "__main__":
    main()
