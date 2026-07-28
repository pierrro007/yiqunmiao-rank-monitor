#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把监测用的 markdown 文件解析成 chart-data.js（供 index.html 的折线图使用）。

数据来源：
  - 一群喵小剧场_当当排名.md        -> 小剧场（当当新书热卖榜），特典版 + 普通版 两个 SKU
  - 如果历史是一群喵_当当畅销榜.md   -> 历史1-16（当当图书畅销榜，前500）；每卷可能含多个 SKU
  - 当当排名总表-*.md              -> 西游1-2 的兜底（仅最新一日，自动监测尚未积累时）
  - 如果历史是一群喵_京东排名.md     -> 京东图书销量榜 + 京东新书热卖榜；小剧场含 印签版/京东特典版 等多 SKU

多 SKU 口径：同一书名的不同版本（特典版/普通版/印签版/京东特典版）只要进了排行榜，
都作为独立序列统计（用户要求：只要进了排行榜的都要统计）。

运行：python update_chart_data.py
输出：chart-data.js
"""
import os, re, glob, json

BASE = os.path.dirname(os.path.abspath(__file__))

# SKU 版本关键词（用于从商品名中识别版本后缀）
SKU_KEYWORDS = ['京东特典版', '纪念特典版', '当当特典版', '特典版',
                '印签版', '亲签版', '特装版', '当当独家']


def sku_of(name):
    """从商品名提取版本后缀；普通版/无版本返回空串。"""
    for kw in SKU_KEYWORDS:
        if kw in name:
            return kw
    return ''


def parse_rank(s):
    if s is None:
        return None
    s = s.strip()
    if s in ("", "—", "-", "未上榜", "无"):
        return None
    m = re.search(r"第\s*(\d+)\s*名", s)
    if m:
        return int(m.group(1))
    m = re.search(r"\d+", s)
    return int(m.group(0)) if m else None


def parse_xiaodang(path):
    """返回 {'特典版':{date:rank}, '普通版':{date:rank}} 当当小剧场新书榜两 SKU。"""
    out = {'特典版': {}, '普通版': {}}
    if not os.path.exists(path):
        return out
    for line in open(path, encoding="utf-8"):
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        if cells[0] == "日期时间" or "排名" in cells[0]:
            continue
        date = cells[0].split()[0] if cells[0] else ""
        if re.match(r"\d{4}-\d{2}-\d{2}", date):
            out['特典版'][date] = parse_rank(cells[1])
            out['普通版'][date] = parse_rank(cells[3])
    return out


def parse_changxiao(path):
    """返回 {date: {key: rank}}  key=历史N[+SKU] 或 西游N[+SKU]。"""
    out = {}
    cur = None
    if not os.path.exists(path):
        return out
    for line in open(path, encoding="utf-8"):
        s = line.strip()
        if s.startswith("### "):
            cur = s[4:].split()[0] if s[4:] else None
            continue
        if not s.startswith("|") or cur is None:
            continue
        cells = [c.strip() for c in s.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        if cells[0] == "卷" or "最高排名" in cells[0]:
            continue
        m = re.match(r"(\d+)", cells[0])
        if not m:
            continue
        vol = int(m.group(1))
        name = cells[-1] if len(cells) >= 4 else ""
        sku = sku_of(name)
        if "西游" in name:
            mm = re.search(r"西游(\d+)", name)
            key = "西游%d%s" % (int(mm.group(1)) if mm else vol, sku)
        else:
            key = "历史%d%s" % (vol, sku)
        out.setdefault(cur, {})[key] = parse_rank(cells[1])
    return out


def parse_xiyou_from_total(path):
    """返回 (date, {1: rank, 2: rank}) 取最新一日西游卷（兜底）。"""
    if not path or not os.path.exists(path):
        return None, {}
    text = open(path, encoding="utf-8").read()
    dm = re.search(r"数据时间[:：]\s*(\d{4}-\d{2}-\d{2})", text)
    date = dm.group(1) if dm else None
    rows = re.findall(
        r"\|\s*\d+\s*\|\s*([^|]*西游[^|]*)\s*\|\s*[^|]*\s*\|\s*([^|]*第\s*\d+\s*名[^|]*|未上榜)",
        text,
    )
    res, order = {}, 1
    for _name, rank in rows:
        if order > 2:
            break
        res[order] = parse_rank(rank)
        order += 1
    return date, res


def parse_jd(path):
    """
    返回 {date: {'sales': {key: rank}, 'new': {key: rank}}}
    key=小剧场印签版/小剧场京东特典版/历史N/西游N。
    依据子标题 #### 京东图书销量榜 / #### 京东新书热卖榜 区分两个榜单。
    """
    out = {}
    if not os.path.exists(path):
        return out
    cur = None
    board = None  # 'sales' 或 'new'
    for line in open(path, encoding="utf-8"):
        s = line.strip()
        if s.startswith("### "):
            m = re.search(r"\d{4}-\d{2}-\d{2}", s)
            cur = m.group(0) if m else None
            board = None
            continue
        if s.startswith("#### "):
            if "销量" in s:
                board = "sales"
            elif "新书" in s:
                board = "new"
            continue
        if not s.startswith("|") or cur is None or board is None:
            continue
        cells = [c.strip() for c in s.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        if cells[0] == "产品" or "京东排名" in cells[0]:
            continue
        if set(cells[0]) <= set("-—| "):
            continue
        name = cells[0]
        mm = re.match(r"(历史|西游)(\d+)", name)
        key = mm.group(1) + mm.group(2) if mm else name
        out.setdefault(cur, {}).setdefault(board, {})[key] = parse_rank(cells[1])
    return out


def extract_fetch(path):
    """提取文件中最后一次出现的「抓取时间：YYYY-MM-DD HH:MM」；找不到返回空串。"""
    if not os.path.exists(path):
        return ""
    last = ""
    for line in open(path, encoding="utf-8"):
        m = re.search(r"抓取时间[：:]\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", line)
        if m:
            last = m.group(1)
    return last


def sort_key(k):
    if k.startswith("小剧场"):
        return (0, 0, k)
    m = re.match(r"(历史|西游)(\d+)(.*)", k)
    if m:
        return (1 if m.group(1) == "历史" else 2, int(m.group(2)), m.group(3))
    return (9, 0, k)


def main():
    xiao = parse_xiaodang(os.path.join(BASE, "一群喵小剧场_当当排名.md"))
    chang = parse_changxiao(os.path.join(BASE, "如果历史是一群喵_当当畅销榜.md"))
    jd = parse_jd(os.path.join(BASE, "如果历史是一群喵_京东排名.md"))
    dates = sorted(
        set(xiao['特典版']) | set(xiao['普通版']) | set(chang)
        | set(jd)
    )
    latest = dates[-1] if dates else None

    # 西游：优先取畅销榜 md（若含西游行），否则兜底总表最新一日
    xiyou_md = {}
    for d, volmap in chang.items():
        for k, v in volmap.items():
            if isinstance(k, str) and k.startswith("西游"):
                xiyou_md.setdefault(d, {})[k] = v
    total_files = sorted(glob.glob(os.path.join(BASE, "当当排名总表-*.md")), reverse=True)
    _td, xiyou_total = parse_xiyou_from_total(total_files[0]) if total_files else (None, {})

    series = {}
    series["小剧场特典版"] = [xiao['特典版'].get(d) for d in dates]
    series["小剧场普通版"] = [xiao['普通版'].get(d) for d in dates]
    hist_keys = sorted({k for d in dates for k in chang.get(d, {}) if k.startswith("历史")}, key=sort_key)
    for k in hist_keys:
        series[k] = [chang.get(d, {}).get(k) for d in dates]
    for v in (1, 2):
        key = "西游%d" % v
        series[key] = [
            (xiyou_md.get(d, {}).get(key) if d in xiyou_md else (xiyou_total.get(v) if d == latest else None))
            for d in dates
        ]

    # 京东双榜
    series_jd_sales = {}
    series_jd_new = {}
    jd_keys_sales = sorted({k for d in dates for k in jd.get(d, {}).get('sales', {})}, key=sort_key)
    jd_keys_new = sorted({k for d in dates for k in jd.get(d, {}).get('new', {})}, key=sort_key)
    for k in jd_keys_sales:
        series_jd_sales[k] = [jd.get(d, {}).get('sales', {}).get(k) for d in dates]
    for k in jd_keys_new:
        series_jd_new[k] = [jd.get(d, {}).get('new', {}).get(k) for d in dates]

    # 抓取时间（来自各监测 md 的「抓取时间：」行；自动任务写入，基线为手动补录）
    dd_fetch = extract_fetch(os.path.join(BASE, "一群喵小剧场_当当排名.md")) or \
               extract_fetch(os.path.join(BASE, "如果历史是一群喵_当当畅销榜.md"))
    jd_fetch = extract_fetch(os.path.join(BASE, "如果历史是一群喵_京东排名.md"))

    header = (
        "// 排名图表数据（由 update_chart_data.py 自动生成，也可手动编辑）\n"
        "// dates: 日期数组（升序）；series: 每本书/SKU一个数组，与 dates 等长；null = 未上榜（不连线）\n"
    )
    content = (
        header
        + "const CHART_META = "
        + json.dumps({"dd_fetch": dd_fetch, "jd_fetch": jd_fetch}, ensure_ascii=False, indent=2)
        + ";\n"
        + header
        + "const CHART_DATA = "
        + json.dumps({"dates": dates, "series": series}, ensure_ascii=False, indent=2)
        + ";\n"
        + header
        + "const CHART_DATA_JD_SALES = "
        + json.dumps({"dates": dates, "series": series_jd_sales}, ensure_ascii=False, indent=2)
        + ";\n"
        + header
        + "const CHART_DATA_JD_NEW = "
        + json.dumps({"dates": dates, "series": series_jd_new}, ensure_ascii=False, indent=2)
        + ";\n"
    )

    with open(os.path.join(BASE, "chart-data.js"), "w", encoding="utf-8") as f:
        f.write(content)
    print("已生成 chart-data.js | 日期数:%d %s | 当当序列数:%d | 京东销量序列数:%d | 京东新书序列数:%d | 当当抓取:%s | 京东抓取:%s" %
          (len(dates), dates, len(series), len(series_jd_sales), len(series_jd_new), dd_fetch, jd_fetch))


if __name__ == "__main__":
    main()
