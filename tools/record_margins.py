#!/usr/bin/env python3
"""把当前引擎算出的「阈值距离」写回 tests/cases.json 的期望里。

用例只断言离散标签时，调 priors 调过头（分数在阈值附近来回翻）不会报警。
这个工具在**你确认过建议仍然合理之后**跑一次，把每个断言栏位的 score 与
买入门槛/可考虑线的差值、以及刷新判定与阈值的差值记进用例，之后调参就会撞到
区间断言上。|margin| 太小的栏位会打印出来：那说明用例本身在刀口上，该调局面而不是记数字。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from advisor import advise_shop, build_index, default_priors, load_json
from evaluate import enrich_player, margin_of

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tests", "cases.json")


def main():
    priors = default_priors()
    refdata = load_json(os.path.join(ROOT, "mod", "data", "brotato_data.json"))
    index = build_index(refdata)
    index["_stat_names"] = {k.lower(): v for k, v in refdata.get("stat_names", {}).items()}
    cases = load_json(PATH)
    thin = []
    for case in cases:
        player = enrich_player(dict(case["player"]), index)
        result = advise_shop(player, case["shop"], priors, index)
        for exp in case.get("expect", {}).get("items", []):
            m = round(margin_of(result, exp["index"]), 1)
            exp["margin"] = m
            if abs(m) < 1.5:
                thin.append((case["name"], exp["index"], m))
        if result.get("reroll_expected") is not None:
            case.setdefault("expect", {})["reroll_margin"] = round(result["reroll_margin"], 1)
            if abs(result["reroll_margin"]) < 1.5:
                thin.append((case["name"], "reroll", round(result["reroll_margin"], 1)))
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("已写回 {} 个用例的 margin 期望".format(len(cases)))
    if thin:
        print("以下断言离阈值太近，建议改局面而不是记数字：")
        for t in thin:
            print("   {} slot{} margin={}".format(*t))
    return 0


if __name__ == "__main__":
    sys.exit(main())
