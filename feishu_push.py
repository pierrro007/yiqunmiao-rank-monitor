#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feishu_push.py —— 有变化时向飞书自定义机器人推送变化摘要。

输入：同目录下的 result.json（由 monitor_run.py 的 stdout 落盘）。
配置（GitHub Actions Secrets，二选一）：
  - FEISHU_WEBHOOK : 飞书群机器人 webhook 地址（必填）
  - FEISHU_SECRET  : 若机器人开启了「签名校验」，则填此密钥（可选）
行为：
  - 未配置 webhook -> 直接跳过（不影响主流程）。
  - changed=False -> 跳过推送。
  - changed=True  -> 组装摘要文本，POST 到 webhook。
"""
import json
import os
import sys
import time
import hmac
import hashlib
import base64
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
RESULT = os.path.join(BASE, "result.json")


def pretty_board(board):
    return (
        board.replace("jd_sales_", "京东销量·")
        .replace("jd_new_", "京东新书·")
        .replace("dd_new", "当当新书榜")
        .replace("dd_best", "当当畅销榜")
        .replace("jd_sales", "京东销量榜")
        .replace("jd_new", "京东新书榜")
    )


def main():
    wh = os.environ.get("FEISHU_WEBHOOK", "")
    if not wh:
        print("未配置 FEISHU_WEBHOOK 密钥，跳过飞书推送")
        return

    try:
        with open(RESULT, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print("result.json 解析失败，跳过推送:", e)
        return

    if not data.get("changed"):
        print("本次无变化，跳过飞书推送")
        return

    detail = data.get("detail") or []
    lines = []
    for d in detail[:40]:
        if "/" in d:
            board, rest = d.split("/", 1)
            lines.append("- %s / %s" % (pretty_board(board), rest))
        else:
            lines.append("- " + d)

    text = (
        "【一群喵榜单日报 %s】\n"
        "今日检测到 %d 处排名变化：\n%s\n"
        "最近变化日：%s｜历史采样天数：%s\n"
        "网页：https://pierrro007.github.io/yiqunmiao-rank-monitor/"
        % (
            data.get("last_change_date", ""),
            len(detail),
            "\n".join(lines) if lines else "（详见网页）",
            data.get("last_change_date", ""),
            data.get("dates_count", ""),
        )
    )

    payload = {"msg_type": "text", "content": {"text": text}}

    # 若开启了签名校验，补充 timestamp + sign
    secret = os.environ.get("FEISHU_SECRET", "")
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


if __name__ == "__main__":
    main()
