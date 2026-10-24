#!/usr/bin/env python3
"""检查非 stat 特殊效果的覆盖率与方向。

引擎对特殊效果只有三种处理：按点数乘权重（special_keys）、固定分（special_flat_keys）、
以及未登记键的小额按符号兜底。参考表更新后如果冒出新键，兜底分不会报错，只会让建议
悄悄变差——这里强制每个真实键都被显式分类，避免「85% 的效果靠 +4 猜」。

同时校验方向：权重可以为负（值越大越糟），负权重是表达 enemy_health / lose_hp_per_second
这类效果的唯一方式。方向写反不会让用例失败，所以这里对手算过的几个键做定点断言。

用法：python3 tools/check_special_coverage.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIORS = os.path.join(ROOT, "mod", "data", "priors.json")
DATA = os.path.join(ROOT, "mod", "data", "brotato_data.json")

EMPTY_KEY_SHARE_MAX = 0.20   # 无键名效果（炮塔/召唤物）占比上限，超过说明参考表解析退化


def classify(key, per, flat):
    if key.startswith("stat_"):
        return "stat"
    if key in flat:
        return "flat"
    if key in per:
        return "per"
    if key == "":
        return "empty"
    return "unknown"


def term(key, val, per, flat, unk_per, unk_cap):
    if key in flat:
        return float(flat[key])
    if key in per:
        return float(val) * float(per[key])
    if key == "":
        return 0.0
    t = float(val) * float(unk_per)
    return max(-float(unk_cap), min(float(unk_cap), t))


def main():
    priors = json.load(open(PRIORS, encoding="utf-8"))
    data = json.load(open(DATA, encoding="utf-8"))
    per = priors.get("special_keys") or {}
    flat = priors.get("special_flat_keys") or {}
    unk_per = priors.get("special_unknown_per_point")
    unk_cap = priors.get("special_unknown_cap")
    if not isinstance(unk_per, (int, float)) or not isinstance(unk_cap, (int, float)):
        print("FAIL priors.json 缺少 special_unknown_per_point / special_unknown_cap")
        return 1

    used = set()
    unknown = {}
    total = 0
    empty = 0
    for group in ("weapons", "items"):
        for entry in data.get(group, []):
            for fx in entry.get("effects", []):
                key = fx.get("key", "")
                cls = classify(key, per, flat)
                if cls == "stat":
                    continue
                total += 1
                if key == "":
                    empty += 1
                    continue
                used.add(key)
                if cls == "unknown":
                    unknown.setdefault(key, []).append(entry.get("id", "?"))

    errors = []
    for key, ids in sorted(unknown.items()):
        errors.append("未分类效果键 %s（%d 处，如 %s）→ 请在 special_keys/"
                      "special_flat_keys 里显式分类" % (key, len(ids), ", ".join(ids[:3])))
    if total and float(empty) / total > EMPTY_KEY_SHARE_MAX:
        errors.append("无键名效果占 %.0f%%，超过上限 %.0f%%：参考表的键名解析可能退化了"
                      % (100.0 * empty / total, 100.0 * EMPTY_KEY_SHARE_MAX))

    # 方向定点断言：这些键的语义反写会让分数反向，但不会触发任何用例
    checks = [
        ("items_price", -5, "+"),      # 价格降低是好事
        ("reroll_price", -25, "+"),
        ("number_of_enemies", -5, "+"),  # 敌人变少是好事
        ("number_of_enemies", 10, "-"),
        ("enemy_health", 10, "-"),
        ("enemy_damage", 10, "-"),
        ("enemy_speed", 25, "-"),
        ("lose_hp_per_second", 3, "-"),
        ("hp_start_wave", -50, "-"),
        ("consumable_heal", -1, "-"),
        ("xp_gain", 10, "+"),
    ]
    for key, val, sign in checks:
        cls = classify(key, per, flat)
        if cls == "unknown":
            errors.append("方向断言用的键 %s 未分类" % key)
            continue
        got = term(key, val, per, flat, unk_per, unk_cap)
        if sign == "+" and got <= 0:
            errors.append("方向反了：%s=%s 应≥0，实际 %.2f" % (key, val, got))
        if sign == "-" and got >= 0:
            errors.append("方向反了：%s=%s 应≤0，实际 %.2f" % (key, val, got))

    # 未使用的分类项只是提示（其它版本/DLC 可能还会出现）
    unused = sorted((set(per) | set(flat)) - used)
    if unused:
        print("NOTE 这些键在当前参考表里没出现（保留兼容）：%s" % ", ".join(unused))

    covered = total - empty - sum(len(v) for v in unknown.values())
    print("效果实例 %d：已分类 %d（%.1f%%），无键名 %d（%.1f%%），未分类 %d"
          % (total, covered, 100.0 * covered / max(1, total),
             empty, 100.0 * empty / max(1, total),
             sum(len(v) for v in unknown.values())))
    if errors:
        for e in errors:
            print("FAIL", e)
        return 1
    print("OK 特殊效果：%d 个键全部显式分类，方向抽检通过" % len(used))
    return 0


if __name__ == "__main__":
    sys.exit(main())
