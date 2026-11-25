#!/usr/bin/env python3
"""sanitize.py —— 入库前把真实业务数据替换成占位符（幂等）

背景：本仓库是公开仓库，而技能目录里的原件含真实 token / 账号 / 卡号 / 身份资料。
sync.sh 在 rsync 之后会调用本脚本，保证任何一次同步都不会把敏感数据推上去。

敏感字面量不写在本文件里（否则等于把要藏的东西又搬进公开仓库），
而是放在本地文件 sanitize.local.json（已被 .gitignore 屏蔽），结构见 sanitize.local.example.json。

用法：
  ./sanitize.py            # 就地处理本仓库副本
  ./sanitize.py --check    # 只体检，有残留则退出码 1
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL = ROOT / "SKILL.md"
SCRIPTS = ROOT / "scripts"
LOCAL = ROOT / "sanitize.local.json"

# 通用规则：不需要知道具体值即可识别
GENERIC = [
    # 打码卡号：4111 **** **** 1111 / 4111 …1111（必须含 * 或 …，避免误伤普通数字）
    (re.compile(r"\b\d{4}[ *….\s]{0,12}[*…][ *….\s]{0,12}\d{4}\b"), "<CARD_PAN>"),
    # 完整 16 位卡号（测试卡号 4242… 除外）
    (re.compile(r"\b(?!4242424242424242\b)\d{16}\b"), "<CARD_PAN>"),
    # 接口 token（已是占位符的不动）
    (re.compile(r"token=(?!<|\{)[A-Za-z0-9]{6,}"), "token=<CARD_API_TOKEN>"),
    # 账号邮箱
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "<account>"),
    # 11 位手机号
    (re.compile(r"\b1[3-9]\d{9}\b"), "<PHONE>"),
    # PAT 尾号：…abcd / 尾号 `abcd`
    (re.compile(r"…[a-z0-9]{4}\b"), "…<PAT>"),
    (re.compile(r"(尾号\s*`)[a-z0-9]{3,6}(`)"), r"\1<PAT>\2"),
    # hdc 设备序列号：9CN0224207000879
    (re.compile(r"\b[0-9]{1,2}[A-Z]{2}\d{10,}\b"), "<DEVICE_ID>"),
]

# 体检用：这些形态一旦出现即判定为残留
CHECK_GENERIC = [
    re.compile(r"\b(?!4242424242424242\b)\d{16}\b"),
    re.compile(r"[A-Za-z0-9._%+-]+@(?!example\.)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"token=(?!<|\{)[A-Za-z0-9]{6,}"),
    re.compile(r"\b1[3-9]\d{9}\b"),
    re.compile(r"\b[0-9]{1,2}[A-Z]{2}\d{10,}\b"),
    re.compile(r"…[a-z0-9]{4}\b"),
]


def targets() -> list[Path]:
    """需要脱敏的文件：SKILL.md + scripts/ 下的脚本。"""
    out = [SKILL] if SKILL.exists() else []
    if SCRIPTS.is_dir():
        out += sorted(p for p in SCRIPTS.iterdir() if p.suffix in {".py", ".sh"})
    return out


def load_local() -> dict:
    if not LOCAL.exists():
        print(f"! 缺少 {LOCAL.name}：只能做通用规则脱敏，卡代号等字面量会漏", file=sys.stderr)
        return {"literal": [], "literal_after": []}
    data = json.loads(LOCAL.read_text(encoding="utf-8"))
    return {
        "literal": data.get("literal", []),
        "literal_after": data.get("literal_after", []),
    }


def sanitize_text(text: str, rules: dict):
    counts: dict[str, int] = {}
    for old, new in rules["literal"]:
        n = text.count(old)
        if n:
            text = text.replace(old, new)
            counts[new] = counts.get(new, 0) + n
    for pat, rep in GENERIC:
        text, n = pat.subn(rep, text)
        if n:
            counts[rep] = counts.get(rep, 0) + n
    for old, new in rules["literal_after"]:
        n = text.count(old)
        if n:
            text = text.replace(old, new)
            counts[new] = counts.get(new, 0) + n
    return text, counts


def leftovers(path: Path, rules: dict) -> list[str]:
    bad = []
    literals = [old for old, _ in rules["literal"] + rules["literal_after"] if len(old) >= 5]
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if any(old in line for old in literals) or any(p.search(line) for p in CHECK_GENERIC):
            bad.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()[:90]}")
    return bad


def main() -> int:
    rules = load_local()

    if "--check" in sys.argv:
        bad = []
        for f in targets():
            bad += leftovers(f, rules)
        if bad:
            print("✗ 发现疑似敏感残留：")
            print("\n".join(bad[:30]))
            return 1
        print("✓ 未发现残留")
        return 0

    for f in targets():
        text, counts = sanitize_text(f.read_text(encoding="utf-8"), rules)
        total = sum(counts.values())
        if total:
            f.write_text(text, encoding="utf-8")
        print(f"{f.relative_to(ROOT)} 脱敏 {total} 处" + ("" if total else "（无变化）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
