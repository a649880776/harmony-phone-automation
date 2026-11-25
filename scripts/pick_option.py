#!/usr/bin/env python3
"""pick_option.py —— 在下拉列表里滚动并点选指定选项
用法：./pick_option.py 美国 [--max 40] [--step 1100]
"""
import argparse, json, os, subprocess, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PH = os.path.join(ROOT, "scripts", "ph.sh")
SHOTS = os.environ.setdefault("SHOTS_DIR", "/tmp/phshots")

def ph(*a):
    subprocess.run([PH, *a], env=dict(os.environ), check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def dump(tag):
    p = os.path.join(SHOTS, f"{tag}.json")
    ph("ui", tag)
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return None

def parse(b):
    try:
        q = b.replace("][", ",").strip("[]").split(","); return [int(float(x)) for x in q]
    except Exception: return None

def find(d, text):
    hit = []
    def walk(n):
        a = n.get("attributes", {}); t = (a.get("text") or "").strip()
        if a.get("type") == "Option" and t == text:
            b = parse(a.get("bounds", ""))
            if b and b[2] > b[0]: hit.append(((b[0]+b[2])//2, (b[1]+b[3])//2))
        for c in n.get("children") or []: walk(c)
    walk(d)
    return hit[0] if hit else None

def has_options(d):
    found = [False]
    def walk(n):
        if n.get("attributes", {}).get("type") == "Option": found[0] = True
        for c in n.get("children") or []: walk(c)
    walk(d); return found[0]

CACHE = os.path.join(ROOT, ".pickcache.json")

def load_cache():
    try: return json.load(open(CACHE, encoding="utf-8"))
    except Exception: return {}

def save_cache(c):
    try: json.dump(c, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception: pass

ap = argparse.ArgumentParser(); ap.add_argument("text")
ap.add_argument("--max", type=int, default=40); ap.add_argument("--step", type=int, default=1100)
ap.add_argument("--fling", type=int, default=0, help="先用大惯性快速翻滚的次数（学到的位置）")
a = ap.parse_args()

d = dump("pick0")
if not d or not has_options(d):
    print("列表未打开（无 Option 节点）", file=sys.stderr); sys.exit(3)

cache = load_cache()
key = "list:" + a.text
known = cache.get(key, 0)
start_flings = a.fling or known
if start_flings:
    for _ in range(start_flings):
        ph("fling", "580", "2100", "580", "700", "40000", "300"); time.sleep(0.35)
    print(f"按缓存先快翻 {start_flings} 次", file=sys.stderr)

for i in range(1, a.max + 1):
    d = dump(f"pick{i}")
    pos = find(d, a.text) if d else None
    if pos:
        cache[key] = start_flings + max(0, i - 1)
        save_cache(cache)
        ph("tap", str(pos[0]), str(pos[1])); print(f"已选择 {a.text} @ {pos}（缓存翻页 {cache[key]}）"); sys.exit(0)
    ph("drag", "580", "2100", "580", str(2100 - a.step), "300"); time.sleep(0.25)
print(f"滚 {a.max} 次未找到「{a.text}」", file=sys.stderr); sys.exit(4)
