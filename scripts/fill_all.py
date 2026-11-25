#!/usr/bin/env python3
"""已弃用：被 fill_checkout2.py 取代（后者按标签定位 + 阶段监控）

fill_all.py —— 一次执行填完 Stripe 结账页（按标签定位，不依赖固定坐标顺序）

顺序遵循业务要求：卡号/有效期/CVV → 全名 → 国家 → 地址1 → 地址2(留空) → 城市 → 州 → 邮编
国家/州是下拉列表，脚本会滚动查找目标文本并点选。
"""
import json, os, re, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_card as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PH = os.path.join(ROOT, "scripts", "ph.sh")
SHOTS = os.environ.get("SHOTS_DIR", "/tmp/phshots")
os.makedirs(SHOTS, exist_ok=True)

def ph(*a, w=0.0):
    subprocess.run([PH, *a], env=dict(os.environ), check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if w: time.sleep(w)

def dump(tag="ui_cur"):
    p = os.path.join(SHOTS, tag + ".json")
    ph("ui", tag)
    for _ in range(3):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            time.sleep(0.2)
    return None

def b2y(b):
    try: return int(float(b.replace("][", ",").strip("[]").split(",")[1]))
    except Exception: return -1

def b2xy(b):
    try:
        q = b.replace("][", ",").strip("[]").split(",")
        x1, y1, x2, y2 = (int(float(v)) for v in q)
        return (x1 + x2) // 2, (y1 + y2) // 2
    except Exception:
        return None

def fields(d):
    """返回 {label: (x,y)}，靠 labelText 与字段共享 y1 来配对"""
    labels, boxes = {}, []
    def walk(n):
        a = n.get("attributes", {})
        t = (a.get("text") or "").strip()
        ty = a.get("type")
        if ty == "labelText" and t:
            labels[b2y(a.get("bounds", ""))] = t
        elif ty in ("textField", "comboBoxSelect"):
            boxes.append((b2y(a.get("bounds", "")), ty, b2xy(a.get("bounds", ""))))
        for c in n.get("children") or []: walk(c)
    walk(d)
    out = {}
    for y, lab in labels.items():
        for by, ty, xy in boxes:
            if by == y and xy:
                out[lab] = (xy, ty)
                break
    return out

def fill(lab, val, d):
    f = fields(d).get(lab)
    if not f:
        print(f"  跳过 {lab}: 找不到字段", file=sys.stderr); return False
    (x, y), ty = f
    ph("tap", str(x), str(y), w=0.75)
    ph("text", str(val), w=0.55)
    ph("key", "Back", w=0.5)
    return True

def clear_field(lab, d, n=12):
    f = fields(d).get(lab)
    if not f: return False
    (x, y), ty = f
    ph("tap", str(x), str(y), w=0.6)
    for _ in range(n): ph("key", "2055", w=0.12)
    ph("key", "Back", w=0.4)
    return True

def pick(lab, text, d, max_scroll=30, step=1100):
    f = fields(d).get(lab)
    if not f:
        print(f"  跳过 {lab}: 找不到下拉", file=sys.stderr); return False
    (x, y), ty = f
    ph("tap", str(x), str(y), w=1.4)
    for i in range(max_scroll):
        dd = dump(f"pick_{lab}_{i}")
        if not dd: continue
        hit = []
        def walk(n):
            a = n.get("attributes", {}); t = (a.get("text") or "").strip()
            if a.get("type") == "Option" and t == text:
                xy = b2xy(a.get("bounds", ""))
                if xy: hit.append(xy)
            for c in n.get("children") or []: walk(c)
        walk(dd)
        if hit:
            ph("tap", str(hit[0][0]), str(hit[0][1]), w=1.0)
            print(f"  已选 {lab}={text}", file=sys.stderr); return True
        ph("drag", "580", "2100", "580", str(2100 - step), "300")   # 下拉内部滚动
        time.sleep(0.2)
    print(f"  ! {lab} 没找到「{text}」", file=sys.stderr); return False

# ---- 取卡 ----
env = F.load_env()
card_id = json.load(open(os.path.join(ROOT, "profile.json"), encoding="utf-8"))["card_id"]
base = F.cfg(env, "CARD_API_BASE", default="https://agent.hiaihk.com/prod/api")
path = F.cfg(env, "CARD_API_FETCH_PATH", default="/rentCardInfo")
token = F.cfg(env, "CARD_API_TOKEN", "RENTCARD_TOKEN")
userid = os.environ.get("CARD_API_USER_ID") or F.cfg(env, "CARD_API_USER_ID", default="8")
secret = F.cfg(env, "2FA_KEY", "TOTP_SECRET")
key_path = F.cfg(env, "CARD_RSA_KEY_PATH", default=F.DEFAULT_KEY)
url = f"{base.rstrip('/')}/{path.lstrip('/')}?token={token}"
code = F.make_totp(secret)
st, resp = F.rent_card_info(url, {"card_id": card_id, "userid": int(userid),
                                  "code": (str(code) if code.startswith("0") else int(code))})
if st != 200 or not resp or resp.get("success") is not True:
    print("取卡失败:", (resp or {}).get("message", st), file=sys.stderr); sys.exit(3)
c = F.normalize(resp.get("data") or {})
pt, _ = F.decrypt_cvv(c["cvv_cipher"], F.load_private_key(key_path))
c["cvc"] = pt.decode().strip(); pt = b"\x00" * len(pt)
exp = re.sub(r"\D", "", c["exp"])
b = json.load(open(os.path.join(ROOT, "profile.json"), encoding="utf-8"))["billing"]
t0 = time.time()

d = dump("ui0")

# 卡号输入框 = 最靠上的、值里不含 @ 的 textField（排除邮箱框）
def card_field(dd):
    cands = []
    def walk(n):
        a = n.get("attributes", {})
        if a.get("type") == "textField":
            t = (a.get("text") or "")
            xy = b2xy(a.get("bounds", ""))
            if xy and "@" not in t:
                cands.append((xy[1], xy))
        for c in n.get("children") or []: walk(c)
    walk(dd)
    return min(cands)[1] if cands else None

card_xy = card_field(d)
if not card_xy:
    print("找不到卡号输入框", file=sys.stderr); sys.exit(2)
ph("tap", str(card_xy[0]), str(card_xy[1]), w=0.7)
ph("text", c["pan"], w=0.5)
ph("text", exp, w=0.45)
ph("text", c["cvc"], w=0.45)
ph("key", "Back", w=0.5)
d = dump("ui1")

# 全名
fill("全名", b["name"], d); d = dump("ui2")
# 国家（下拉里是中文，美国）
if "国家或地区" in fields(d):
    pick("国家或地区", "美国", d); d = dump("ui3")
# 地址1 / 城市
fill("地址第 1 行", b["address1"], d); d = dump("ui4")
fill("城市", b["city"], d); d = dump("ui5")
# 州
if "州" in fields(d):
    pick("州", b["state_name"], d); d = dump("ui6")
elif "省" in fields(d):
    pick("省", b["state_name"], d); d = dump("ui6")
# 邮编
for lab in ("邮政编码", "邮编"):
    if lab in fields(d):
        fill(lab, b["zip"], d); break

print(f"完成，用时 {time.time()-t0:.1f}s", file=sys.stderr)
print(f"卡 {F.mask_pan(c['pan'])} · {c['exp']} · CVV *** | {b['name']} | "
      f"{b['address1']} | {b['city']} | {b['state_name']} | {b['zip']} | {b['country']}")
