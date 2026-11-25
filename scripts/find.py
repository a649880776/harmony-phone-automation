#!/usr/bin/env python3
"""find.py —— 在鸿蒙控件树 (uitest dumpLayout 产物) 里按文本/ID 找元素中心坐标。

用法：
  python3 find.py <layout.json> <查询词> [--all]

输出每行：<type>\t<文本>\t<x>,<y>\t<bounds>
默认只输出第一个命中；--all 输出全部。
匹配规则：先精确匹配 text/accessibilityId/id，再退化为包含匹配（忽略大小写）。
"""
import json
import sys


def load_nodes(path):
    with open(path, "r", encoding="utf-8") as f:
        root = json.load(f)
    out = []

    def walk(n):
        a = n.get("attributes") or {}
        text = a.get("text") or ""
        desc = a.get("description") or ""
        aid = a.get("accessibilityId") or ""
        nid = a.get("id") or ""
        bounds = a.get("bounds") or ""
        if text or desc or aid or nid:
            out.append({
                "text": text,
                "desc": desc,
                "aid": aid,
                "id": nid,
                "type": a.get("type") or "",
                "bounds": bounds,
                "clickable": a.get("clickable"),
            })
        for c in n.get("children") or []:
            walk(c)

    walk(root)
    return out


def center(bounds):
    # "[x1,y1][x2,y2]"
    try:
        parts = bounds.replace("][", ",").strip("[]").split(",")
        x1, y1, x2, y2 = (int(float(p)) for p in parts)
        return (x1 + x2) // 2, (y1 + y2) // 2
    except Exception:
        return None


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    path, q = sys.argv[1], sys.argv[2]
    show_all = "--all" in sys.argv[3:]
    ql = q.lower()
    nodes = load_nodes(path)

    exact = [n for n in nodes if q in (n["text"], n["aid"], n["id"])]
    fuzzy = [n for n in nodes if ql in (n["text"] or "").lower()
             or ql in (n["desc"] or "").lower()
             or ql in (n["aid"] or "").lower()]
    hits = exact or fuzzy
    if not hits:
        # 没有命中时把候选给出来，便于人工判断
        print(f"[no match] {q}", file=sys.stderr)
        return 2

    shown = hits if show_all else hits[:1]
    for n in shown:
        c = center(n["bounds"])
        label = n["text"] or n["desc"] or n["aid"] or n["id"]
        pos = f"{c[0]},{c[1]}" if c else "?"
        print(f"{n['type']}\t{label}\t{pos}\t{n['bounds']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
