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
  3) 仅当数据确实变化时才用 Contents API 回传（无变化跳过，避免空提交/空推送）
  4) 运行飞书推送（仅当当当侧有变化时；需 FEISHU_WEBHOOK 环境变量/本地配置）

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

REPO = "pierrro007/yiqunmiao-rank-monitor"
BASE = os.path.dirname(os.path.abspath(__file__))
RH = os.path.join(BASE, "rank-history.json")
PM = os.path.join(BASE, "product-map.json")
RESULT = os.path.join(BASE, "result.json")

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


def sha256_file(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def download(path, local):
    meta = api_get(path)
    data = base64.b64decode(meta["content"]).decode("utf-8")
    with open(local, "w", encoding="utf-8") as f:
        f.write(data)
    return meta["sha"]


def pull():
    """拉取仓库最新两份文件，返回 (sha_rh, sha_pm, 本地内容哈希_rh, 本地内容哈希_pm)。"""
    sha_rh = download("rank-history.json", RH)
    sha_pm = download("product-map.json", PM)
    return sha_rh, sha_pm, sha256_file(RH), sha256_file(PM)


def run_dd():
    """仅抓当当子榜并落库。monitor_run 的 stdout 写入 result.json 供飞书读取。"""
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


def push_feishu():
    """运行飞书推送（仅当 result.json 标记有变化时才会真正推送）。"""
    try:
        subprocess.run([sys.executable, "feishu_push.py"], cwd=BASE, env=os.environ)
    except Exception as e:
        print("飞书推送异常(忽略):", e)


def main():
    global TOKEN
    TOKEN = load_token()
    if not TOKEN:
        print("❌ 缺少 GitHub 令牌：请设置 GH_TOKEN 环境变量，或将令牌写入 ~/.config/yiqunmiao/token")
        sys.exit(1)

    sha_rh, sha_pm, orig_rh, orig_pm = pull()
    run_dd()

    # 检测数据是否真的变了（避免无意义的空回传/空提交）
    cur_rh = sha256_file(RH)
    cur_pm = sha256_file(PM)
    if cur_rh == orig_rh and cur_pm == orig_pm:
        print("✅ 当当数据无变化，跳过回传（无空提交）")
        push_feishu()  # 仍跑飞书：changed=False 时会自动跳过推送
        return

    for attempt in range(4):
        try:
            if cur_rh != orig_rh:
                api_put("rank-history.json", open(RH, encoding="utf-8").read(), sha_rh)
            if cur_pm != orig_pm:
                api_put("product-map.json", open(PM, encoding="utf-8").read(), sha_pm)
            print("✅ 已回传当当数据到仓库")
            break
        except Exception as e:
            msg = str(e)
            # curl 拿到 409 时 GitHub 返回 JSON，含 "message":"SHA does not match"
            if "409" in msg or "SHA does not match" in msg:
                print("⚠️ SHA 过期（云端有新提交），重新拉取并应用后重试 (%d)…" % (attempt + 1))
                sha_rh, sha_pm, orig_rh, orig_pm = pull()
                run_dd()
                cur_rh = sha256_file(RH)
                cur_pm = sha256_file(PM)
                continue
            print("❌ 回传失败:", msg[:200])
            break

    push_feishu()


if __name__ == "__main__":
    main()
