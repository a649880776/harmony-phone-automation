#!/usr/bin/env python3
"""record_result.py —— 把一次任务结果写回 devin_token.json（成功/拒卡/失败都记）

为什么单独有它：
  编排器中途退出、或者人工在手机上收尾时，也要能补一条记录。
  只从 profile.json 取 email / card_id，敏感值一律不落盘。

用法：
  ./record_result.py --status declined --error "您的银行卡被拒绝了。 请尝试其他卡片。"
  ./record_result.py --status ok --token-tail nwoq --token-name pat-1125-ab12
  ./record_result.py --list                     # 看历史与统计
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import token_store as T

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(ROOT, "profile.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", choices=list(T.STATUSES),
                    help="ok=成功 / declined=被拒卡 / failed=失败 / blocked=需人工")
    ap.add_argument("--stage", default=None, help="停在哪一步，如「提交订阅」")
    ap.add_argument("--error", default=None, help="页面原文或失败原因")
    ap.add_argument("--token-tail", default=None, help="PAT 后四位（脱敏）")
    ap.add_argument("--token-name", default=None)
    ap.add_argument("--card-masked", default=None)
    ap.add_argument("--note", default=None)
    ap.add_argument("--list", action="store_true", help="只列出历史与统计")
    a = ap.parse_args()

    if a.list or not a.status:
        recs = T.load()
        for r in recs:
            print(f"{r.get('created_at') or r.get('recorded_at')}  {r.get('status','?'):9s} "
                  f"{r.get('email')}  card={r.get('card_id')}  "
                  f"{('error=' + str(r.get('error'))[:40]) if r.get('error') else ''}"
                  f"{('token=***' + str(r.get('token_tail') or '')) if r.get('token') else ''}")
        print(json.dumps(T.summary(), ensure_ascii=False))
        return 0

    try:
        prof = json.load(open(PROFILE, encoding="utf-8"))
    except Exception:
        prof = {}
    rec = {"email": prof.get("email"), "card_id": prof.get("card_id"),
           "status": a.status, "stage": a.stage, "token": None,
           "token_name": a.token_name, "card_masked": a.card_masked or None,
           "error": a.error, "note": a.note}
    if a.token_tail:
        rec["token_tail"] = a.token_tail
    n = T.append_event({k: v for k, v in rec.items() if v is not None})
    print(json.dumps({"ok": True, "total": n, "file": T.PATH, "status": a.status},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
