#!/usr/bin/env python3
"""fetch_card.py —— 按真实契约拉卡：POST /rentCardInfo

契约（以接口方为准）：
  POST {CARD_API_BASE}{CARD_API_FETCH_PATH}?token={CARD_API_TOKEN}
  Content-Type: application/json
  body: {"card_id": "<卡代号，如 <CARD>>", "userid": <int>, "code": <6 位 TOTP>}
  响应 data: {
    card_id: 170, card_key: "<CARD>",
    card_no: "<16 位明文卡号>",            ← 明文（接口方只加密了 cvv）
    cvv: "<base64 RSA 密文>",       ← 需要用私钥解
    expiration: "11/31",
    holder_name, street, state, zipcode, bill_address, card_area, cvv_uid
  }

本脚本做的事：
  1. 用 .env 的 2FA_KEY 本地算 TOTP 码；
  2. 发请求（token 只能走 query，这是接口限制，已记录为已知风险）；
  3. 卡号/有效期直接用；CVV 用 CARD_RSA_KEY_PATH 私钥在内存里解；
  4. 只输出脱敏视图；可选把账单地址写回 profile.json（不含任何密文）；
  5. 绝不打印卡号全量、CVV 明文、响应体原文。

用法：
  ./fetch_card.py --self-test                      # 本地自测（造密钥 + 假响应，不联网）
  ./fetch_card.py --card-id <CARD>                 # 拉卡，打印脱敏
  ./fetch_card.py --card-id <CARD> --json          # 脱敏 JSON
  ./fetch_card.py --card-id <CARD> \
      --fill pan=609,2184 --fill exp=358,2325 --fill cvc=860,2325 \
      --fill name=609,944 --fill address1=609,1226 --fill zip=609,1789 --fill state=609,1620
  ./fetch_card.py --card-id <CARD> --sync-profile  # 把 holder/street/state/zip 写进 profile.json

退出码：0 成功；2 缺配置；3 请求失败；4 CVV 解密失败；5 响应缺卡号。
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(SKILL_ROOT, "scripts")
DEFAULT_ENV = os.path.join(SKILL_ROOT, ".env")
DEFAULT_KEY = os.path.expanduser("~/.config/devin-vault/card_private.pem")
PROFILE = os.path.join(SKILL_ROOT, "profile.json")

STATE_CODE_TO_NAME = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}

STATE_NAME_TO_CODE = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}


# ---------------- 配置 ----------------
def load_env(path=DEFAULT_ENV):
    env = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[7:]
                k, v = line.split("=", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                env[k.strip()] = v
    except FileNotFoundError:
        pass
    return env


def cfg(env, *names, default=None):
    for n in names:
        v = os.environ.get(n) or env.get(n)
        if v:
            return v
    return default


# ---------------- TOTP ----------------
def make_totp(secret):
    sys.path.insert(0, SCRIPTS)
    from totp import normalize_secret, totp
    return totp(normalize_secret(secret))


# ---------------- 请求 ----------------
def rent_card_info(url, body, timeout=25):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return 0, {"_error": type(e).__name__}


# ---------------- CVV 解密 ----------------
def load_private_key(path):
    with open(os.path.expanduser(path), "rb") as f:
        pem = f.read()
    pw = os.environ.get("CARD_KEY_PASSPHRASE", "").encode() or None
    if b"ENCRYPTED" in pem and not pw:
        raise ValueError("私钥有口令，请设置 CARD_KEY_PASSPHRASE")
    return serialization.load_pem_private_key(pem, password=pw)


def _b64(s):
    s = str(s).strip()
    return base64.b64decode(s + "=" * ((4 - len(s) % 4) % 4))


def decrypt_cvv(blob, private_key):
    """依次尝试 OAEP-SHA256 → OAEP-SHA1 → PKCS#1 v1.5（兼容旧服务端）。"""
    ct = _b64(blob)
    attempts = [
        ("OAEP-SHA256", padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                     algorithm=hashes.SHA256(), label=None)),
        ("OAEP-SHA1", padding.OAEP(mgf=padding.MGF1(hashes.SHA1()),
                                   algorithm=hashes.SHA1(), label=None)),
        ("PKCS1v15", padding.PKCS1v15()),
    ]
    last = None
    for name, pad in attempts:
        try:
            return private_key.decrypt(ct, pad), name
        except Exception as e:  # noqa: PERF203
            last = e
    raise ValueError(f"三种填充都解不开：{type(last).__name__}")


# ---------------- 字段归一 ----------------
def normalize(data):
    def g(*ks):
        return next((data[k] for k in ks if data.get(k) not in (None, "")), "")

    state_raw = str(g("state", "state_name", "province") or "").strip()
    state_code = STATE_NAME_TO_CODE.get(state_raw.lower(),
                                        state_raw.upper() if len(state_raw) == 2 else "")
    bill = str(g("bill_address", "billing_address") or "")
    parts = [p.strip() for p in bill.split(",") if p.strip()]
    city = str(g("city", "town")).strip()
    # bill_address 常见两种：street,state,zip,US（无城市）或 street,city,state,zip,US
    if not city and len(parts) >= 5:
        city = parts[1]

    return {
        "pan": str(g("card_no", "card_number", "pan")).strip(),
        "exp": str(g("expiration", "expiry", "exp_date")).strip(),
        "cvv_cipher": str(g("cvv", "cvv_cipher", "cvc")).strip(),
        "card_key": str(g("card_key")).strip(),
        "card_id": data.get("card_id"),
        "holder": str(g("holder_name", "name")).strip(),
        "street": str(g("street", "address1", "address") or (parts[0] if parts else "")).strip(),
        "state": state_raw,
        "state_code": state_code,
        "state_name": STATE_CODE_TO_NAME.get(state_code, state_raw),
        "zip": str(g("zipcode", "zip", "postal_code")).strip(),
        "country": str(g("country") or (parts[-1] if parts and len(parts[-1]) == 2 else "US")).upper(),
        "bill_address": bill,
        "card_area": str(g("card_area")),
        "city": city,
    }


# ---------------- 脱敏输出 ----------------
def mask_pan(pan):
    d = re.sub(r"\D", "", pan or "")
    return f"{d[:4]} **** **** {d[-4:]}" if len(d) >= 10 else "*" * len(d)


def masked_view(c):
    city = c.get("city") or "（接口未给城市）"
    return (f"卡号 {mask_pan(c['pan'])} · 有效期 {c['exp']} · CVV ***\n"
            f"卡代号 {c.get('card_key') or '-'}（内部 id {c.get('card_id')}）· 区域 {c.get('card_area') or '-'}\n"
            f"持卡人 {c.get('holder') or '-'}\n"
            f"账单 {c.get('street')}, {city}, {c.get('state')} {c.get('zip')}, {c.get('country')}")


# ---------------- 写 profile ----------------
def sync_profile(c):
    try:
        prof = json.load(open(PROFILE, encoding="utf-8"))
    except Exception:
        prof = {}
    b = prof.setdefault("billing", {})
    if c.get("holder"):
        b["name"] = c["holder"]
    if c.get("street"):
        b["address1"] = c["street"]
    if c.get("state"):
        b["state"] = c["state_code"] or c["state"]
        b["state_name"] = c.get("state_name") or c["state"]
    if c.get("zip"):
        b["zip"] = c["zip"]
    if c.get("country"):
        b["country"] = c["country"]
    if c.get("city"):
        b["city"] = c["city"]
    if c.get("card_key"):
        prof["card_id"] = c["card_key"]
        prof.pop("card_api", None)      # 老结构清掉，避免两处漂移
    tmp = PROFILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(prof, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PROFILE)
    os.chmod(PROFILE, 0o644)
    return PROFILE


# ---------------- 打进手机 ----------------
def fill_phone(values, targets):
    ph = os.path.join(SCRIPTS, "ph.sh")
    env = dict(os.environ, SHOTS_DIR=os.path.join(SKILL_ROOT, "shots"))
    for field, xy in targets:
        v = values.get(field, "")
        if not v:
            print(f"  ! 跳过 {field}：没有值", file=sys.stderr)
            continue
        try:
            x, y = (int(t) for t in xy.split(","))
        except ValueError:
            print(f"  ! 跳过 {field}：坐标格式应为 x,y", file=sys.stderr)
            continue
        subprocess.run([ph, "tap", str(x), str(y)], env=env, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.6)
        subprocess.run([ph, "text", str(v)], env=env, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shown = {"cvc": "***", "cvv": "***", "exp": "**/**", "pan": mask_pan(str(v))}.get(field, "✓")
        print(f"  已填 {field}: {shown}", file=sys.stderr)


# ---------------- 自测 ----------------
def self_test():
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cvv_pt = b"695"
    blob = key.public_key().encrypt(
        cvv_pt, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))

    fake = {"card_id": 170, "card_key": "<CARD>",
            "card_no": "4242424242424242", "cvv": base64.b64encode(blob).decode(),
            "expiration": "11/31", "holder_name": "Test Holder",
            "bill_address": "123 Main St,Missouri,00000,US",
            "street": "123 Main St", "state": "Missouri", "zipcode": "00000",
            "card_area": "SGP", "cvv_uid": 5}

    c = normalize(fake)
    pt, how = decrypt_cvv(c["cvv_cipher"], key)
    ok = (pt == cvv_pt and c["pan"].endswith("4242") and c["state_code"] == "MO"
          and c["zip"] == "00000" and c["holder"] == "Test Holder")
    print(f"字段归一 : 尾四位 {c['pan'][-4:]} · exp {c['exp']} · state {c['state']}->{c['state_code']} · zip {c['zip']}")
    print(f"CVV 解密 : {'OK' if pt == cvv_pt else 'FAIL'}（填充 {how}，长度 {len(pt)}，不显示值）")
    print(f"整体     : {'OK' if ok else 'FAIL'}")
    print(masked_view(c))
    return 0 if ok else 4


# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=DEFAULT_ENV)
    ap.add_argument("--card-id", help="卡代号，如 <CARD>（默认取 profile.json 的 card_ref）")
    ap.add_argument("--userid", help="数字用户 id（默认 .env 的 CARD_API_USER_ID）")
    ap.add_argument("--totp", help="手动指定 6 位码（默认本地算）")
    ap.add_argument("--key", help="RSA 私钥路径")
    ap.add_argument("--no-cvc", action="store_true", help="不解 CVV（无私钥时）")
    ap.add_argument("--fill", action="append", default=[], metavar="field=x,y")
    ap.add_argument("--sync-profile", action="store_true", help="把账单地址写回 profile.json")
    ap.add_argument("--json", action="store_true", help="输出脱敏 JSON")
    ap.add_argument("--show-request", action="store_true", help="只打印请求形状（token 打码）")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        return self_test()

    env = load_env(a.env)
    base = cfg(env, "CARD_API_BASE", "RENTCARD_BASE", default="https://agent.hiaihk.com/prod/api")
    path = cfg(env, "CARD_API_FETCH_PATH", default="/rentCardInfo")
    token = cfg(env, "CARD_API_TOKEN", "RENTCARD_TOKEN")
    userid = a.userid or cfg(env, "CARD_API_USER_ID")
    card_id = a.card_id
    if not card_id and os.path.exists(PROFILE):
        try:
            prof = json.load(open(PROFILE, encoding="utf-8"))
            # 扁平优先：card_id → card_ref → 老的 card_api.card_id
            card_id = (prof.get("card_id") or prof.get("card_ref")
                       or (prof.get("card_api") or {}).get("card_id"))
        except Exception:
            pass
    secret = cfg(env, "TOTP_SECRET", "2FA_KEY", "TWOFA_KEY", "TOTP_KEY")
    key_path = a.key or cfg(env, "CARD_RSA_KEY_PATH", default=DEFAULT_KEY)

    missing = [n for n, v in [("CARD_API_TOKEN", token), ("CARD_API_USER_ID", userid),
                              ("card_id", card_id), ("2FA_KEY", secret)] if not v]
    if missing:
        print("缺少配置：" + ", ".join(missing), file=sys.stderr)
        return 2

    code = a.totp or make_totp(secret)
    try:
        userid_int = int(str(userid).strip())
    except ValueError:
        print(f"CARD_API_USER_ID 必须是数字，当前是「{userid}」", file=sys.stderr)
        return 2
    body = {"card_id": card_id, "userid": userid_int,
            "code": (str(code) if str(code).startswith("0") else int(code)) if str(code).isdigit() else code}
    url = f"{base.rstrip('/')}/{path.lstrip('/')}?token={urllib.parse.quote(str(token))}"

    if a.show_request:
        print(json.dumps({"url": url.replace(str(token), "***"), "body": body}, ensure_ascii=False))
        return 0

    status, resp = rent_card_info(url, body)
    if status != 200 or not isinstance(resp, dict) or resp.get("_error"):
        err = resp.get("_error", "") if isinstance(resp, dict) else ""
        print(f"请求失败：HTTP {status} {err}".strip(), file=sys.stderr)
        return 3
    if resp.get("success") is not True:
        print(f"接口返回失败：{resp.get('message','')} (code={resp.get('code')})", file=sys.stderr)
        return 3

    c = normalize(resp.get("data") or {})
    if not c["pan"]:
        print("响应里没有 card_no", file=sys.stderr)
        return 5

    if c["cvv_cipher"] and not a.no_cvc:
        try:
            key = load_private_key(key_path)
            pt, how = decrypt_cvv(c["cvv_cipher"], key)
            c["cvc"] = pt.decode("utf-8", "replace").strip()
            pt = b"\x00" * len(pt)
        except FileNotFoundError:
            print(f"没找到私钥 {key_path}，CVV 无法解（其余字段照常）", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"CVV 解密失败：{type(e).__name__}: {e}", file=sys.stderr)
            return 4

    if a.json:
        print(json.dumps({"pan": mask_pan(c["pan"]), "exp": c["exp"],
                          "cvc": "***" if c.get("cvc") else "",
                          "card_key": c["card_key"], "holder": c["holder"],
                          "street": c["street"], "city": c["city"], "state": c["state"],
                          "zip": c["zip"], "area": c["card_area"]}, ensure_ascii=False))
    else:
        print(masked_view(c))

    if a.sync_profile:
        print(f"已写入账单地址到 {sync_profile(c)}（不含密文）", file=sys.stderr)

    if a.fill:
        values = {"pan": c["pan"], "exp": re.sub(r"\D", "", c["exp"]),
                  "cvc": c.get("cvc", ""), "name": c["holder"], "address1": c["street"],
                  "zip": c["zip"], "state": c["state"], "state_name": c["state_name"], "city": c["city"]}
        targets = []
        for item in a.fill:
            if "=" not in item:
                print(f"  ! --fill 格式应为 field=x,y：{item}", file=sys.stderr)
                continue
            f, xy = item.split("=", 1)
            targets.append((f.strip(), xy.strip()))
        fill_phone(values, targets)
    return 0


if __name__ == "__main__":
    sys.exit(main())
