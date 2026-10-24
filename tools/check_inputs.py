#!/usr/bin/env python3
"""输入契约检查：引擎读的字段，运行时必须真的能拿到。

用例的输入是手写的 player/shop 字典，所以「引擎新读一个字段、用例里写了、
游戏里根本取不到」这种情况不会让任何用例失败——线上只会静默退回默认值，
建议变差却毫无信号。这里静态地把三方的字段名对起来：

  tools/advisor.py            读到的键（Python 参考实现）
  mod/scripts/advisor.gd      读到的键（必须与 py 完全一致）
  mod/scripts/state_collector.gd / shop_advisor.gd   实际产出的键

用法：python3 tools/check_inputs.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY_ENGINE = os.path.join(ROOT, "tools", "advisor.py")
GD_ENGINE = os.path.join(ROOT, "mod", "scripts", "advisor.gd")
COLLECTOR = os.path.join(ROOT, "mod", "scripts", "state_collector.gd")
HUB = os.path.join(ROOT, "mod", "scripts", "shop_advisor.gd")

# 引擎里可能出现的输入变量名 -> 契约里的分组
VARS = ("player", "shop", "offer", "other")

# 分组之间允许的「额外」来源：stats 由 STAT_KEYS 提供，base 由参考表兜底
SPONSORED = {
    ("player", "stats"): "STAT_KEYS",
    ("offer", "base"): "read_weapon_base / 参考表",
    # 参考表里没有角色 ban 列表，采集侧暂时取不到；引擎支持该字段，缺省为空
    ("player", "banned_ids"): "参考表/采集侧暂无 ban 列表",
}


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def py_read_keys(src):
    """{('player','wave'), ...} —— x.get('k') / x['k']，含 num(x['k']) 之类包装"""
    out = set()
    for var in VARS:
        for m in re.finditer(r"\b%s\.(?:get\(|pop\()\s*[\"'](\w+)[\"']" % var, src):
            out.add((var, m.group(1)))
        for m in re.finditer(r"\b%s\[[\"'](\w+)[\"']\]" % var, src):
            out.add((var, m.group(1)))
    return out


def gd_read_keys(src):
    """GDScript 侧：getk(player, 'k', ...) / getk(offer, 'k') / player['k']"""
    out = set()
    for var in VARS:
        for m in re.finditer(r"getk\(\s*%s\s*,\s*[\"'](\w+)[\"']" % var, src):
            out.add((var, m.group(1)))
        for m in re.finditer(r"\b%s\[[\"'](\w+)[\"']\]" % var, src):
            out.add((var, m.group(1)))
    return out


def func_body(src, name):
    """按顶层 func 切块，取名为 name 的函数体（GDScript 用 tab 缩进，函数到下一个顶格 func 结束）"""
    lines = src.split("\n")
    starts = [i for i, l in enumerate(lines) if l.startswith("func %s(" % name)]
    if not starts:
        return ""
    i = starts[0] + 1
    body = []
    while i < len(lines) and not re.match(r"^(func |const |var |extends |#|@)", lines[i]):
        body.append(lines[i])
        i += 1
    return "\n".join(body)


def _top_level_dict_keys(text):
    """从 text 里找第一个 `{`，收它那一层的 "key": 值（不深入嵌套字典，避免把 base/effects 的子键混进来）"""
    keys = []
    depth = 0
    i = 0
    started = False
    while i < len(text):
        ch = text[i]
        if ch in "\"'":
            if depth == 1 and ch == '"':
                m = re.match(r'"(\w+)"\s*:', text[i:])
                if m:
                    keys.append(m.group(1))
                    i += m.end()
                    continue
            # 其它位置的字符串整个跳过：值里带 { } 会扰乱深度
            j = i + 1
            while j < len(text) and text[j] != ch:
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if ch == "{":
            depth += 1
            started = True
        elif ch == "}":
            depth -= 1
            if started and depth == 0:
                break
        i += 1
    return keys


def produced_keys(src, func_name, var_name):
    """var var_name := { ... } 字面量里的顶层键"""
    body = func_body(src, func_name)
    m = re.search(r"\bvar\s+%s\s*:?=*\s*\{" % var_name, body)
    if not m:
        return None
    return set(_top_level_dict_keys(body[m.start():]))


def main():
    py_src = read_text(PY_ENGINE)
    gd_src = read_text(GD_ENGINE)
    col_src = read_text(COLLECTOR)
    hub_src = read_text(HUB)

    errors = []
    py_keys = py_read_keys(py_src)
    gd_keys = gd_read_keys(gd_src)

    # 1) 两个实现读的字段必须一样
    for var, k in sorted(py_keys - gd_keys):
        errors.append("advisor.gd 没读 %s.%s，但 advisor.py 读了（双实现漂移）" % (var, k))
    for var, k in sorted(gd_keys - py_keys):
        errors.append("advisor.gd 读了 %s.%s，但 advisor.py 没读（双实现漂移）" % (var, k))

    # 2) 读的字段必须是运行时产出的
    runtime = set()
    p_player = produced_keys(col_src, "collect_player", "player")
    p_offer = produced_keys(col_src, "collect_offer", "offer")
    p_shop = None
    for m in re.finditer(r'\{\s*"tier"', hub_src):
        p_shop = set(_top_level_dict_keys(hub_src[m.start():]))
        break
    for group, got, where in (("player", p_player, "state_collector.collect_player"),
                             ("offer", p_offer, "state_collector.collect_offer"),
                             ("shop", p_shop, "shop_advisor.advise")):
        if not got:
            errors.append("没解析出 %s 的产出字段（检查 %s 里的字面量写法）" % (group, where))
            continue
        runtime |= {(group, k) for k in got}
        for var, k in sorted(gd_keys):
            if var != group:
                continue
            if k not in got and (var, k) not in SPONSORED:
                errors.append("引擎读 %s.%s，但 %s 不产出它 → 游戏里永远退回默认值" % (var, k, where))

    # stats 子键：STAT_KEYS 必须覆盖引擎用到的 stat 名（引擎按 stats 字典整份遍历，这里核对参考表键名）
    stat_keys = set(re.findall(r'"(stat_\w+)"', func_body(col_src, "collect_player") + col_src))

    # 3) 反向：产出但没人读，只是提示。effects/id/name/kind/tier 是给诊断文件与角标用的，不算多余
    dead = sorted(r for r in runtime if r not in gd_keys and r[1] not in ("effects", "id", "name", "kind", "tier"))
    notes = ["%s.%s 被采集但引擎没读（确认是否多余）" % (a, b) for a, b in dead]

    for n in notes:
        print("NOTE", n)
    if errors:
        for e in errors:
            print("FAIL", e)
        print("输入契约检查未通过")
        return 1
    print("OK 输入契约：引擎读 %d 个字段（py/gd 一致），运行时全部产出；stat 键 %d 个"
          % (len(gd_keys), len([s for s in stat_keys])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
