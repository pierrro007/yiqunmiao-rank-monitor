#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_dd.py —— 在中国境内环境定时采集「当当」子榜，回传到 GitHub 仓库。

为什么不用 git commit/push？
  云端 Actions 也在写同一个 rank-history.json（京东部分），两个 writer 用普通
  git 合并会在 JSON 文本上冲突。改用 GitHub Contents API（PUT 带 SHA）做原子覆盖：
  若云端在此期间有新提交（SHA 过期，HTTP 409），自动重新拉取最新 → 重新应用当当数据
  → 再回传，天然规避并发覆盖。

  注：本环境 urllib 直连 API 上传会遇 SSL 中断，故一律走 curl（与仓库其他脚本一致）。

数据流：
  1) 拉取仓库最新 rank-history.json / product-map.json / dd-status.json / dd-changes.json
  2) FETCH_MODE=dd_sub 运行 monitor_run.py（仅抓当当子榜，变化判定+落库；
     monitor_apply 会把本轮变化明细累积写入 dd-changes.json）
  3) 写入 dd-status.json（记录本机今日已运行，供云端判定「当当子榜是否已更新」）
  4) 仅当相关文件内容真变化时才用 Contents API 回传（无变化跳过，避免空提交）
  5) 飞书推送统一由云端 feishu_daily.py 负责（保证「全部更新才发」），本机不再单独发。

令牌读取（不再明文写在命令行/自动化里）：
  优先级：环境变量 GH_TOKEN → 本地文件 ~/.config/yiqunmiao/token → 本地文件 <仓库>/.sync_token
  （仓库内的 .sync_token 已被 .gitignore 忽略，不会进仓库）
"""
import os
import sys
import json
import base64
import hashlib
import subprocess
import datetime

REPO = "pierrro007/yiqunmiao-rank-monitor"
BASE = os.path.dirname(os.path.abspath(__file__))
RH = os.path.join(BASE, "rank-history.json")
PM = os.path.join(BASE, "product-map.json")
RESULT = os.path.join(BASE, "result.json")
DD_STATUS = os.path.join(BASE, "dd-status.json")
DD_CHANGES = os.path.join(BASE, "dd-changes.json")

# 本机负责同步/回传的文件（rank-history/product-map 由本机写；状态与明细仅本机写）
SYNC_FILES = ["rank-history.json", "product-map.json", "dd-changes.json", "dd-status.json"]

TOKEN = ""  # 在 main() 中由 load_token() 赋值


def load_token():
    # 1) 环境变量（最高优先，便于手动调试）
    t = os.environ.get("GH_TOKEN", "")
    if t:
        return t
    # 2) 本地私有文件（不进 git），依次尝试
    for p in [
        os.path.expanduser("~/.config/yiqunmiao/token"),
        os.path.join(BASE, ".sync_token"),
    ]:
        if os.path.exists(p):
            try:
                return open(p, encoding="utf-8").read().strip()
            except Exception:
                pass
    return ""


def _curl(args):
    cmd = ["curl", "-sS", "--max-time", "60", "-H", "Authorization: token " + TOKEN,
           "-H", "Accept: application/vnd.github+json"] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        raise RuntimeError("curl 失败: " + r.stderr[:200])
    return r.stdout


def api_get(path):
    out = _curl(["https://api.github.com/repos/%s/contents/%s?ref=main" % (REPO, path)])
    if not out.strip():
        return None
    try:
        return json.loads(out)
    except Exception:
        return None


def api_put(path, content_str, sha):
    body = {
        "message": "chore: 当当子榜更新(中国境内 DD 模式)",
        "content": base64.b64encode(content_str.encode("utf-8")).decode("ascii"),
        "branch": "main",
    }
    if sha:
        body["sha"] = sha
    out = _curl([
        "-X", "PUT",
        "-H", "Content-Type: application/json",
        "-d", json.dumps(body),
        "https://api.github.com/repos/%s/contents/%s" % (REPO, path),
    ])
    if "409" in out or "SHA does not match" in out:
        raise RuntimeError("409 SHA does not match")
    try:
        return json.loads(out)
    except Exception:
        raise RuntimeError("api_put 失败: " + out[:200])


def sha256_file(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def beijing_today():
    return datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=8))
    ).strftime("%Y-%m-%d")


def pull():
    """拉取最新四份文件；返回 {name: {"git_sha","orig_sha256","existed"}}。"""
    info = {}
    for name in SYNC_FILES:
        meta = api_get(name)
        if meta and "content" in meta:
            content = base64.b64decode(meta["content"]).decode("utf-8")
            with open(os.path.join(BASE, name), "w", encoding="utf-8") as f:
                f.write(content)
            info[name] = {
                "git_sha": meta.get("sha"),
                "orig_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "existed": True,
            }
        else:
            info[name] = {"git_sha": None, "orig_sha256": None, "existed": False}
    return info


def run_dd():
    """仅抓当当子榜并落库。monitor_run 的 stdout 写入 result.json（备用）。"""
    env = dict(os.environ)
    env["FETCH_MODE"] = "dd_sub"
    r = subprocess.run(
        [sys.executable, "monitor_run.py"], cwd=BASE, env=env,
        capture_output=True, text=True,
    )
    if r.stdout.strip():
        with open(RESULT, "w", encoding="utf-8") as f:
            f.write(r.stdout)
    print("monitor_run:", r.stdout.strip()[:240])
    if r.returncode != 0:
        print("monitor_run 失败:", r.stderr[-300:])


def write_dd_status():
    """记录本机今日已运行当当同步（供云端判定「当当子榜是否已更新」）。"""
    today = beijing_today()
    data = {"date": today}
    if os.path.exists(DD_STATUS):
        try:
            if json.load(open(DD_STATUS, encoding="utf-8")).get("date") == today:
                return  # 今日已写过，无需改动（避免无谓回传）
        except Exception:
            pass
    with open(DD_STATUS, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def changed_files(info):
    """返回内容相对拉取时变化的文件名集合。"""
    out = set()
    for name in SYNC_FILES:
        p = os.path.join(BASE, name)
        if not os.path.exists(p):
            continue
        cur = sha256_file(p)
        orig = info[name]["orig_sha256"]
        if orig is None or cur != orig:
            out.add(name)
    return out


def push_files(info, files):
    for name in files:
        p = os.path.join(BASE, name)
        if not os.path.exists(p):
            continue
        sha = info[name]["git_sha"]
        content = open(p, encoding="utf-8").read()
        api_put(name, content, sha)
        print("✅ 已回传 %s" % name)


def main():
    global TOKEN
    TOKEN = load_token()
    if not TOKEN:
        print("❌ 缺少 GitHub 令牌：请设置 GH_TOKEN 环境变量，或将令牌写入 ~/.config/yiqunmiao/token")
        sys.exit(1)

    for attempt in range(4):
        info = pull()
        run_dd()
        write_dd_status()
        diff = changed_files(info)
        if not diff:
            print("✅ 当当数据及状态均无变化，跳过回传（无空提交）")
            return
        try:
            push_files(info, diff)
            print("✅ 当当同步完成，已回传: %s" % ", ".join(sorted(diff)))
            return
        except RuntimeError as e:
            if "409" in str(e):
                print("⚠️ SHA 过期（云端有新提交），重新拉取并应用后重试 (%d)…" % (attempt + 1))
                continue
            print("❌ 回传失败:", e)
            return
    print("❌ 多次重试仍失败，放弃本次回传")


if __name__ == "__main__":
    main()
