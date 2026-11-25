#!/usr/bin/env python3
"""profile_validate.py —— 校验 profile.json（填表前先挡掉脏数据）

用法：
  ./profile_validate.py                    # 校验 skills 根目录的 profile.json
  ./profile_validate.py --file path.json
  ./profile_validate.py --show             # 校验并打印脱敏视图（给我核对用）

退出码：0 通过；1 有错误（逐条列出字段名，不打印敏感值）。
"""
import argparse
import json
import os
import re
import sys

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.path.join(SKILL_ROOT, "profile.json")

US_STATES = set("""
AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM
NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY
AS GU MP PR VI AA AE AP
""".split())

US_STATE_NAMES = {
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

REQUIRED = {
    "email": "邮箱",

    "billing.name": "持卡人姓名",
    "billing.country": "国家代码（2 位）",
    "billing.state": "州/省代码",
    "billing.city": "城市",
    "billing.address1": "地址第 1 行",
    "billing.zip": "邮编",
    "billing.phone": "电话",
}

# 敏感字段白名单：profile.json 里出现这些键直接报错（它们必须进 Keychain）
FORBIDDEN_KEYS = {"pan", "cardnumber", "card_number", "number", "cvc", "cvv",
                  "securitycode", "security_code", "csc", "token", "pat",
                  "apikey", "api_key", "secret", "privatekey", "private_key"}


def dig(obj, path):
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def scan_forbidden(obj, prefix=""):
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in FORBIDDEN_KEYS:
                hits.append(f"{prefix}{k}")
            hits += scan_forbidden(v, f"{prefix}{k}.")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits += scan_forbidden(v, f"{prefix}[{i}].")
    return hits


def validate(d):
    errs = []
    if not (d.get("card_id") or d.get("card_ref")):
        errs.append("缺字段 card_id（卡代号，业务标识，不含卡号）")
    for path, label in REQUIRED.items():
        v = dig(d, path)
        if v in (None, ""):
            errs.append(f"缺字段 {path}（{label}）")
    email = dig(d, "email") or ""
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", email):
        errs.append("email 格式不合法")
    country = (dig(d, "billing.country") or "").upper()
    if country and not re.fullmatch(r"[A-Z]{2}", country):
        errs.append("billing.country 必须是 2 位字母（US/CN…）")
    state_raw = str(dig(d, "billing.state") or "").strip()
    state = US_STATE_NAMES.get(state_raw.lower(),
                               state_raw.upper() if len(state_raw) == 2 else state_raw.upper())
    if country == "US" and state and state not in US_STATES:
        errs.append(f"billing.state「{state}」不是合法美州区码（如 MT / CA / NY）")
    zip_ = str(dig(d, "billing.zip") or "")
    if country == "US" and zip_ and not re.fullmatch(r"\d{5}(-\d{4})?", zip_):
        errs.append("billing.zip 美区必须是 5 位（或 ZIP+4）")
    phone = re.sub(r"\D", "", str(dig(d, "billing.phone") or ""))
    if phone and not (10 <= len(phone) <= 15):
        errs.append("billing.phone 位数不对（去掉符号后 10–15 位）")
    exp = dig(d, "billing.exp") or dig(d, "expiryDate")
    if exp and not re.fullmatch(r"(0[1-9]|1[0-2])\s*/\s*\d{2}", str(exp).strip()):
        errs.append("expiryDate 必须是 MM/YY（如 11/31 表示 2031-11）")
    for f in scan_forbidden(d):
        errs.append(f"profile.json 不应存敏感字段 {f}（请放 Keychain）")
    return errs


def masked_view(d):
    m = re.sub(r"\d", "*", str(dig(d, "billing.phone") or ""))
    return (f"email: {dig(d,'email')}\n"
            f"card_id: {d.get('card_id') or d.get('card_ref')}\n"
            f"billing: {dig(d,'billing.name')} / {dig(d,'billing.city')}, "
            f"{dig(d,'billing.state')} {dig(d,'billing.zip')} / {dig(d,'billing.country')}\n"
            f"address1: {dig(d,'billing.address1')}\n"
            f"phone: {m}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=DEFAULT)
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    if not os.path.exists(a.file):
        print(f"找不到 {a.file}（可从 profile.example.json 复制）", file=sys.stderr)
        return 1
    try:
        d = json.load(open(a.file, encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"JSON 解析失败：{e}", file=sys.stderr)
        return 1
    errs = validate(d)
    if errs:
        print("❌ 校验未通过：", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("✅ profile.json 校验通过")
    if a.show:
        print(masked_view(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
