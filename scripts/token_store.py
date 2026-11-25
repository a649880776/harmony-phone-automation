#!/usr/bin/env python3
"""token_store.py —— 任务结果「追加不覆盖」

devin_token.json 统一为**数组**（历史全留，成功与失败都记）；老格式（单对象）读取时自动包成数组。

字段约定（每条记录必带 `status`）：
  status = "ok"        → 成功：带 token / token_name（PAT 尾号）
  status = "declined"  → 被拒卡：账号作废，token 为 null，带 error（页面原文）
  status = "failed"    → 其它失败：带 stage + error（卡片过期/3DS/必填缺失…）
  status = "blocked"   → 需要人工决策而停下（不明原因）

用法：
  from token_store import append_token, append_event
  append_event({"email":..., "card_id":..., "status":"declined",
                "stage":"提交订阅", "error":"您的银行卡被拒绝了。 请尝试其他卡片。"})
"""
import json
import os
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "devin_token.json")

STATUSES = ("ok", "declined", "failed", "blocked")


def load(path=PATH):
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:
        return []
    if isinstance(d, dict):
        return [d]
    return d if isinstance(d, list) else []


def append_token(rec, path=PATH):
    recs = load(path)
    recs.append(rec)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".tok-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(recs, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return len(recs)


def append_event(rec, path=PATH):
    """记录一次任务结果（成功/拒卡/失败都记）。

    * status 不在 STATUSES 里就补成 "blocked"，避免出现无状态记录；
    * 自动补 recorded_at；
    * 同一个 (email, card_id, status) 连续重复时不重复写（重跑同一流程不刷屏）。
    """
    rec = dict(rec)
    st = rec.get("status") or "blocked"
    rec["status"] = st if st in STATUSES else "blocked"
    rec.setdefault("recorded_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    rec.setdefault("token", None)
    recs = load(path)
    if recs:
        last = recs[-1]
        same = (last.get("email") == rec.get("email")
                and str(last.get("card_id")) == str(rec.get("card_id"))
                and last.get("status") == rec["status"])
        if same:
            return len(recs)
    return append_token(rec, path)


def find_by_card(card_id, path=PATH):
    return [r for r in load(path) if str(r.get("card_id")) == str(card_id)]


def summary(path=PATH):
    """按状态统计，便于一眼看清哪些账号成了、哪些废了"""
    recs = load(path)
    out = {}
    for r in recs:
        out[r.get("status") or "unknown"] = out.get(r.get("status") or "unknown", 0) + 1
    return {"total": len(recs), "by_status": out}

def migrate(path=PATH):
    """给 2026-11-25 之前的老记录补 status：有 token 视为 ok，否则 blocked。

    老记录是扁平对象（email/card_id/token/token_name/created_at），没有 status，
    统计里会落成 unknown —— 这里一次性补齐，之后再写就都带 status 了。
    """
    recs = load(path)
    changed = 0
    for r in recs:
        if not r.get("status"):
            r["status"] = "ok" if r.get("token") else "blocked"
            if r.get("token") and not r.get("token_tail"):
                r["token_tail"] = str(r["token"])[-4:]
            changed += 1
    if changed:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".tok-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(recs, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return changed
