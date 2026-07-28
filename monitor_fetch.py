#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
monitor_fetch.py —— 抓取「四主榜 + 京东 13 子榜」的 一群喵 排名，向 stdout 输出 JSON 快照。

覆盖范围：
  - 当当：新书热卖榜(newhotsales) / 图书畅销榜(bestsellers)，各翻满 25 页 = 前 500 名。
          整页无目标书则提前结束（带一次重试，规避限流误判）。
  - 京东主榜：图书销量榜(moduleType=1) / 新书热卖榜(moduleType=2)，各取前 200。
  - 京东子榜：13 大分类(小说文学/童书/学考/经管/励志与成功/人文社科/生活/青春文学/艺术/动漫/考试/进口原版/科技) × 销量/新书 = 26 榜，各 100 名。
书名匹配同时识别「单册」与「套装/礼盒」(历史1-16套装、西游1-2套装、历史1-3礼盒)，各归一个 key。
每本书同一榜单只记一个最高（最小）排名。
"""
import json
import subprocess
import re
import sys
import time
import urllib.parse

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# 京东图书榜下设的 13 大分类（实测 bookRank 接口支持的全部 categoryFirst）
# 京东图书标准一级分类（计算机/医学/法律/历史等）被聚合进这些二级分类，故 13 个即全量。
JD_SUBCATS = [
    "小说文学", "童书", "学考", "经管", "励志与成功", "人文社科", "生活",
    "青春文学", "艺术", "动漫", "考试", "进口原版", "科技",
]


def curl(url, referer=None, timeout=30):
    cmd = ["curl", "-s", "-L", "--max-time", str(timeout), "-A", UA]
    if referer:
        cmd += ["-e", referer]
    cmd.append(url)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout + 15)
        return r.stdout or b""
    except Exception:
        return b""


def decode_html(b):
    for enc in ["gb18030", "gbk", "utf-8"]:
        try:
            return b.decode(enc)
        except Exception:
            pass
    return b.decode("utf-8", "ignore")


# ---------------- 书名 -> key 映射 ----------------
def _is_set(title, word, span_re):
    """判断是否为某系列的套装（word=历史/西游，span_re 匹配册数区间，须用负向预查排除页码范围如 1-156）。"""
    if word not in title:
        return False
    return bool(re.search(span_re, title))


def dd_book_key(title):
    if "一群喵" not in title and "如果历史" not in title and "如果西游" not in title:
        return None
    # 注意：(?![0-9]) 排除「1-156」这类单册页码范围，避免误判为 1-16 套装
    if _is_set(title, "历史", r"1[-—]16(?![0-9])"):
        return "历史1-16套装"
    if _is_set(title, "西游", r"1[-—]2(?![0-9])"):
        return "西游1-2套装"
    if _is_set(title, "历史", r"1[-—]3(?![0-9])"):
        return "历史1-3套装"
    m = re.search(r"如果历史是一群喵(\d+)", title)
    if m:
        return "历史" + m.group(1)
    m = re.search(r"如果西游是一群喵(\d+)", title)
    if m:
        return "西游" + m.group(1)
    if "小剧场" in title:
        return "小剧场特典版" if "特典版" in title else "小剧场普通版"
    return None


def jd_book_key(title):
    if _is_set(title, "历史", r"1[-—]16(?![0-9])"):
        return "历史1-16套装"
    if _is_set(title, "西游", r"1[-—]2(?![0-9])"):
        return "西游1-2套装"
    if _is_set(title, "历史", r"1[-—]3(?![0-9])"):
        return "历史1-3套装"
    m = re.search(r"如果历史是一群喵(\d+)", title)
    if m:
        return "历史" + m.group(1)
    m = re.search(r"如果西游是一群喵(\d+)", title)
    if m:
        return "西游" + m.group(1)
    if "小剧场" in title or "一群喵小剧场" in title:
        if "京东特典版" in title:
            return "小剧场京东特典版"
        if "印签" in title:
            return "小剧场印签版"
        return "小剧场京东特典版"
    return None


# ---------------- 当当 ----------------
_BOOK_RE = [
    r'title="([^"]*如果历史是一群喵\d+[^"]*)"',
    r'title="([^"]*如果西游是一群喵\d+[^"]*)"',
    r'title="([^"]*一群喵小剧场[^"]*)"',
    r'alt="([^"]*如果历史是一群喵\d+[^"]*)"',
    r'alt="([^"]*如果西游是一群喵\d+[^"]*)"',
    r'alt="([^"]*一群喵小剧场[^"]*)"',
    r"<a[^>]*>([^<]*如果历史是一群喵\d+[^<]*)</a>",
    r"<a[^>]*>([^<]*如果西游是一群喵\d+[^<]*)</a>",
    r"<a[^>]*>([^<]*一群喵小剧场[^<]*)</a>",
]


def _dd_page_books(html):
    """返回该页 (title, rank_or_None, idx) 列表。"""
    out = []
    lis = re.findall(r"<li[^>]*>(.*?)</li>", html, re.S)
    i = 0
    for li in lis:
        title = None
        for pat in _BOOK_RE:
            m = re.search(pat, li)
            if m:
                title = m.group(1)
                break
        if not title:
            continue
        i += 1
        rm = re.search(r'class="list_num[^"]*"[^>]*>(\d+)\.', li)
        rank = int(rm.group(1)) if rm else None
        out.append((title, rank, i))
    return out


def fetch_dangdang(template, max_pages):
    result = {}
    for page in range(1, max_pages + 1):
        html = decode_html(curl(template.replace("{page}", str(page))))
        # 空响应：可能是限流，重试一次，仍空则结束
        if not html:
            time.sleep(4)
            html = decode_html(curl(template.replace("{page}", str(page))))
            if not html:
                break
        lis = re.findall(r"<li[^>]*>(.*?)</li>", html, re.S)
        page_items = [li for li in lis if "list_num" in li]
        # 仅当整页「无任何榜单条目」才视为到末尾（目标书可能散落在深页，不能因本页无目标而停）
        if page > 1 and len(page_items) == 0:
            time.sleep(4)
            html2 = decode_html(curl(template.replace("{page}", str(page))))
            if not html2:
                break
            lis2 = re.findall(r"<li[^>]*>(.*?)</li>", html2, re.S)
            if len([li for li in lis2 if "list_num" in li]) == 0:
                break
        i = 0
        for li in lis:
            title = None
            for pat in _BOOK_RE:
                m = re.search(pat, li)
                if m:
                    title = m.group(1)
                    break
            if not title:
                continue
            i += 1
            rm = re.search(r'class="list_num[^"]*"[^>]*>(\d+)\.', li)
            rank = int(rm.group(1)) if rm else ((page - 1) * 20 + i)
            key = dd_book_key(title)
            if key and key not in result:
                result[key] = rank
        time.sleep(2)
    return result


# ---------------- 京东 ----------------
def _jd_call(body):
    body_enc = urllib.parse.quote(json.dumps(body, separators=(",", ":")), safe="")
    ts = "%d" % int(time.time() * 1000)
    url = (
        "https://gw-e.jd.com/client.action?callback=func&body=%s"
        "&functionId=bookRank&client=e.jd.com&_=%s" % (body_enc, ts)
    )
    txt = curl(url, referer="https://pro.m.jd.com/")
    if not txt:
        return []
    s = txt.decode("utf-8", "ignore")
    if s.startswith("func("):
        s = s[5:]
    if s.rstrip().endswith(")"):
        s = s.rstrip()[:-1]
    try:
        data = json.loads(s)
    except Exception:
        return []
    return (data.get("data") or {}).get("books") or []


def _extract(results, books, keyfn):
    for bk in books:
        seq = bk.get("sequence")
        title = bk.get("bookName") or bk.get("name") or ""
        key = keyfn(title)
        if key and key not in results and seq:
            results[key] = seq


def fetch_jd(module_type, max_pages=2):
    result = {}
    for page in range(1, max_pages + 1):
        books = _jd_call(
            {"moduleType": module_type, "page": page, "pageSize": 100, "scopeType": 1}
        )
        if not books:
            break
        _extract(result, books, jd_book_key)
        time.sleep(0.5)
    return result


def fetch_jd_sub(module_type, category, page_size=100):
    result = {}
    books = _jd_call(
        {
            "moduleType": module_type,
            "page": 1,
            "pageSize": page_size,
            "scopeType": 1,
            "categoryFirst": category,
            "categorySecond": "",
            "categoryThree": "",
        }
    )
    _extract(result, books, jd_book_key)
    time.sleep(0.3)
    return result


def main():
    # 当当：两榜各翻满前 500
    dd_new = fetch_dangdang(
        "http://bang.dangdang.com/books/newhotsales/01.00.00.00.00.00-24hours-0-0-1-{page}",
        25,
    )
    dd_best = fetch_dangdang(
        "http://bang.dangdang.com/books/bestsellers/01.00.00.00.00.00-24hours-0-0-1-{page}",
        25,
    )
    # 京东主榜
    jd_sales = fetch_jd(1)
    jd_new = fetch_jd(2)

    out = {
        "dd_new": dd_new,
        "dd_best": dd_best,
        "jd_sales": jd_sales,
        "jd_new": jd_new,
    }
    # 京东 7 大分类子榜
    for cat in JD_SUBCATS:
        out["jd_sales_" + cat] = fetch_jd_sub(1, cat)
        out["jd_new_" + cat] = fetch_jd_sub(2, cat)

    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
