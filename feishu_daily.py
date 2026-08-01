#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feishu_daily.py —— 云端「裁判 + 发信人」。

职责（均在 GitHub Actions 云端 7x24 运行，每小时随 daily.yml 触发）：
  1) 读取状态：jd-status.json（云端今日是否跑过）+ dd-status.json（当当子榜今日是否更新）
  2) 全部更新（两侧今日都跑过）且今日尚未发过 -> 发「今日全部榜单已更新」飞书，
     并附带当天各榜变化明细（来自 cloud-changes.json + dd-changes.json）
  3) 若到北京时间 22:00 仍未全部更新（= 当当子榜未更新，通常是你没开电脑）-> 发提醒飞书
  4) 用 feishu-state.json 保证每天每条消息最多发一次

前置：FEISHU_WEBHOOK 环境变量（GitHub Actions Secret）。未配置则跳过（不影响主流程）。
"""
import json
import os
import sys
import time
import hmac
import hashlib
import base64
import urllib.request
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
JD_STATUS = os.path.join(BASE, "jd-status.json")
DD_STATUS = os.path.join(BASE, "dd-status.json")
CLOUD_CHANGES = os.path.join(BASE, "cloud-changes.json")
DD_CHANGES = os.path.join(BASE, "dd-changes.json")
FEISHU_STATE = os.path.join(BASE, "feishu-state.json")
RH = os.path.join(BASE, "rank-history.json")

PAGE_URL = "https://pierrro007.github.io/yiqunmiao-rank-monitor/"


def beijing_now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))


def today_str():
    return beijing_now().strftime("%Y-%m-%d")


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def read_status_date(path):
    d = load_json(path, {})
    return d.get("date", "") if isinstance(d, dict) else ""


def classify(board):
    if board.startswith("jd_"):
        return "京东"
    if board in ("dd_best", "dd_new"):
        return "当当·主榜"
    if board.startswith("dd_best_") or board.startswith("dd_new_"):
        return "当当·子榜"
    return "其他"


def pretty(board):
    return (
        board.replace("jd_sales_", "京东销量·")
        .replace("jd_new_", "京东新书·")
        .replace("dd_new_", "当当新书榜·")
        .replace("dd_best_", "当当畅销榜·")
        .replace("dd_new", "当当新书榜")
        .replace("dd_best", "当当畅销榜")
        .replace("jd_sales", "京东销量榜")
        .replace("jd_new", "京东新书榜")
    )


def send(wh, secret, text):
    payload = {"msg_type": "text", "content": {"text": text}}
    if secret:
        timestamp = str(int(time.time()))
        string_to_sign = "%s\n%s" % (timestamp, secret)
        hmac_code = hmac.new(
            secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256
        ).digest()
        sign = base64.b64encode(hmac_code).decode("utf-8")
        payload["timestamp"] = timestamp
        payload["sign"] = sign
    req = urllib.request.Request(
        wh,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15).read().decode("utf-8")
        print("飞书推送返回:", resp)
    except Exception as e:
        print("飞书推送失败:", e)


def compose_daily(detail):
    groups = {}
    for d in detail:
        if "/" in d:
            board, rest = d.split("/", 1)
        else:
            board, rest = "其他", d
        groups.setdefault(classify(board), []).append(
            "- %s / %s" % (pretty(board), rest)
        )
    meta = load_json(RH, {}).get("meta", {}) or {}
    updated_at = meta.get("updated_at", today_str())
    parts = ["✅ 一群喵排行榜 · 今日全部榜单已更新", "（更新时间：%s）" % updated_at]
    if not detail:
        parts.append("\n今日各榜排名均无变动。")
    else:
        for g in ["京东", "当当·主榜", "当当·子榜", "其他"]:
            if g in groups:
                lines = groups[g]
                if len(lines) > 40:
                    lines = lines[:40] + ["…（其余 %d 条略）" % (len(groups[g]) - 40)]
                parts.append("\n【%s】" % g)
                parts.extend(lines)
    parts.append("\n网页：" + PAGE_URL)
    return "\n".join(parts)


def main():
    wh = os.environ.get("FEISHU_WEBHOOK", "")
    if not wh:
        print("未配置 FEISHU_WEBHOOK 密钥，跳过飞书推送")
        return
    secret = os.environ.get("FEISHU_SECRET", "")

    today = today_str()
    jd_done = read_status_date(JD_STATUS) == today
    dd_done = read_status_date(DD_STATUS) == today
    all_updated = jd_done and dd_done

    # 状态防重复
    state = load_json(FEISHU_STATE, {})
    if not isinstance(state, dict) or state.get("date") != today:
        state = {"date": today, "daily_sent": False, "reminder_sent": False}

    is_manual = os.environ.get("GITHUB_EVENT_NAME", "") == "workflow_dispatch"

    # 手动触发：先发一条「状态核对」消息，便于验证通路；
    # 不 return —— 继续走下方每日/提醒逻辑，使手动触发也能补发当日消息（feishu-state 防重复）
    if is_manual:
        text = (
            "【一群喵榜单·状态核对】\n"
            "云端(京东+当当主榜)今日是否已更新：%s\n"
            "本机(当当子榜)今日是否已更新：%s\n"
            "全部更新：%s\n\n"
            "飞书每日推送将仅在「全部更新」后发送；若今晚 22:00 仍未全部更新，会自动提醒你打开电脑。"
            % ("是" if jd_done else "否", "是" if dd_done else "否", "是" if all_updated else "否")
        )
        send(wh, secret, text)

    if all_updated and not state["daily_sent"]:
        cloud = (load_json(CLOUD_CHANGES, {}).get(today) or [])
        dd = (load_json(DD_CHANGES, {}).get(today) or [])
        detail = cloud + dd
        send(wh, secret, compose_daily(detail))
        state["daily_sent"] = True
    elif beijing_now().hour >= 22 and not all_updated and not state["reminder_sent"]:
        text = (
            "⏰ 提醒：当当子榜（动漫/幽默）今日尚未更新\n\n"
            "请打开电脑并启动 WorkBuddy（保持联网、在中国境内），"
            "本机定时任务会在整点或半点自动补抓当当子榜。\n"
            "打开后通常几分钟内即会更新，届时会自动推送「今日全部榜单已更新」消息。\n\n"
            "（京东全榜与当当主榜已在云端自动更新，仅缺当当子榜这一块。）"
        )
        send(wh, secret, text)
        state["reminder_sent"] = True

    with open(FEISHU_STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
