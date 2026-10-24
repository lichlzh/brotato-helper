#!/usr/bin/env python3
"""Fetch and slim Brotato data from mojimoon/brotato into mod/data/brotato_data.json.

The source JSON is generated from the game's decompiled .tres files and tracks the
Abyssal Terrors version. We keep only what the Shop Advisor needs:
ids, names (en+zh), tier, price value, dlc flag, type, weapon stats, stat effects.
"""
import json
import os
import sys
import urllib.request

BASE = "https://raw.githubusercontent.com/mojimoon/brotato/main/public/data"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "mod", "data", "brotato_data.json")
CACHE = "/tmp/brotato_data.{}.json"


def fetch(lang):
    path = CACHE.format(lang)
    if not os.path.exists(path) or os.path.getsize(path) < 100_000:
        url = "{}/brotato_data.{}.json".format(BASE, lang)
        print("fetching", url)
        with urllib.request.urlopen(url, timeout=120) as r:
            data = r.read()
        with open(path, "wb") as f:
            f.write(data)
    return json.loads(open(path, encoding="utf-8").read())


def text_of(effect, texts):
    for lang in ("zh", "en"):
        if lang in texts:
            raw = texts[lang]
            import re
            return re.sub(r"<[^>]+>", "", raw).replace("{0}", str(effect.get("value", "")))
    return effect.get("key", "")


def slim_entry(entry, kind, zh_names, stat_names):
    name_key = entry.get("name_key", "")
    effects = []
    desc = []
    for e in entry.get("effects", []):
        key = e.get("key", "")
        value = e.get("value", 0)
        effects.append({"key": key, "value": value})
        if key.startswith("stat_"):
            sign = "+" if value >= 0 else ""
            desc.append("{} {}{}".format(stat_names.get(key.upper(), key), sign, value))
        else:
            t = e.get("text", {})
            line = text_of(e, t)
            if line:
                desc.append(line)
    out = {
        "id": entry["id"],
        "name": entry.get("name", ""),
        "name_zh": zh_names.get(name_key, entry.get("name", "")),
        "kind": kind,
        "tier": entry.get("tier", 0),
        "price": entry.get("value", 0),
        "dlc": entry.get("dlc", 0),
        "cursed": entry.get("is_cursed", False),
        "effects": effects,
        "desc": " / ".join(d for d in desc if d)[:200],
    }
    if kind == "weapon":
        out["weapon_type"] = entry.get("type", "melee")
        out["sets"] = list(entry.get("sets", []))
        stats = entry.get("stats", {})
        out["base"] = {
            "damage": stats.get("damage", 0),
            "cooldown": stats.get("cooldown", 1),
            "crit_chance": stats.get("crit_chance", 0),
            "crit_damage": stats.get("crit_damage", 1.5),
            "scaling": dict(stats.get("scaling_stats", [])),
        }
    return out


def slim_sets(source, zh_names):
    """武器职业套装：sets[职业] = 5 档效果，第 i 档 = 持有 i+1 把该职业武器时的**累计**加成。
    引擎只用「买了这件之后从 k 档跳到 k+1 档的增量」，所以累计/递增的读法都不会重复计数。"""
    out = {}
    for cls, tiers in source.get("sets", {}).items():
        rows = []
        for tier in tiers:
            rows.append([{"key": e.get("key", ""), "value": e.get("value", 0)}
                         for e in tier if str(e.get("key", "")).startswith("stat_")])
        out[cls] = {"tiers": rows, "name_zh": zh_names.get(cls, cls.replace("WEAPON_CLASS_", "").lower())}
    return out


def main():
    en = fetch("en")
    zh = fetch("zh")
    zh_names = {}
    for group in ("weapons", "items", "characters"):
        for e in zh.get(group, []):
            zh_names[e.get("name_key", "")] = e.get("name", "")
    zh_names.update(zh.get("translations", {}))
    stat_names = {}
    for k, v in zh.get("translations", {}).items():
        if k.startswith("STAT_"):
            stat_names[k] = v

    data = {
        "source": BASE,
        "fetched_from": "mojimoon/brotato",
        "stat_names": stat_names,
        "weapons": [slim_entry(w, "weapon", zh_names, stat_names) for w in en["weapons"]],
        "items": [slim_entry(i, "item", zh_names, stat_names) for i in en["items"]],
        "characters": [slim_entry(c, "character", zh_names, stat_names) for c in en["characters"]],
        "sets": slim_sets(en, zh_names),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print("wrote", OUT, os.path.getsize(OUT), "bytes")
    print("weapons={} items={} characters={} sets={}".format(
        len(data["weapons"]), len(data["items"]), len(data["characters"]), len(data["sets"])))
    dlcs = sum(1 for x in data["items"] if x["dlc"] == 1)
    print("dlc items included:", dlcs)


if __name__ == "__main__":
    sys.exit(main())
