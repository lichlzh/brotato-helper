#!/usr/bin/env python3
"""GDScript 结构体检：本机没有 Godot 3，编译器跑不起来，很多问题只能靠静态检查兜底。

检查项都是「一旦写错游戏里直接编译失败」的那类：
  1. 缩进必须全用 tab（GDScript 混用空格会直接报错）
  2. 括号配对（去掉注释和字符串后再数）
  3. 同一文件里 func 不能重名
  4. 同一函数里 var 不能重声明 —— Godot 3 一个函数只有一个作用域，
     if/for 里再声明同名变量是编译错误（写 advisor.gd 时真的踩过）
  5. 局部变量不能和脚本级 var/const 同名（shadow 警告，游戏若开了 warnings-as-errors 就崩）
  6. 不能出现 Godot 4 专属写法（@export/@onready/class_name/super./is_empty()/TYPE_FLOAT）
     以及需要词边界的 TYPE_DICT（Godot 3 只有 TYPE_DICTIONARY）；
     3.5 没有的方法（如 Array.sub_array()）也在这里拦
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD = os.path.join(ROOT, "mod")

GODOT4_TOKENS = [
    "@export", "@onready", "@export_node_path", "class_name ", "super.",
    ".is_empty(", "TYPE_FLOAT", "@tool", "@icon",
]
# 需要词边界的：TYPE_DICTIONARY 才是 Godot 3 的写法，按字面量匹配会把它误判成 TYPE_DICT。
# Array.sub_array() 在 3.5 不存在（已在 3.5.3 实测），想取前 N 项用 slice(0, N-1)（slice 的 end 是闭区间）。
GODOT4_PATTERNS = [r"\bTYPE_DICT\b", r"\bTYPE_STRING_NAME\b", r"\bis_instance_of\(",
                   r"\.sub_array\(", r"\bJSON\.new\(", r"\berror_string\("]

VAR_RE = re.compile(r'^var\s+([A-Za-z_][A-Za-z0-9_]*)')
FUNC_RE = re.compile(r'^func\s+([A-Za-z_][A-Za-z0-9_]*)')


def strip_comments_and_strings(line):
    """返回 (去掉字符串后的代码, 字符串是否成对闭合)。"""
    out = []
    i = 0
    in_str = None
    while i < len(line):
        c = line[i]
        if in_str:
            if c == "\\" and i + 1 < len(line):
                i += 2
                continue
            if c == in_str:
                in_str = None
            i += 1
            continue
        if c == "#":
            break
        if c in "\"'":
            in_str = c
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out), in_str is None


def parse(path):
    """→ (问题列表)。"""
    problems = []
    rel = os.path.relpath(path, ROOT)
    lines = open(path, encoding="utf-8").read().splitlines()

    code = []            # [(行号, 缩进层级, 去字符串后的代码)]
    depth_open = 0       # 跨行的括号不平衡累计
    for n, raw in enumerate(lines, 1):
        if raw.strip() == "":
            continue
        lead = raw[:len(raw) - len(raw.lstrip(" \t"))]
        if " " in lead:
            problems.append("{}:{} 缩进里混了空格：{!r}".format(rel, n, lead))
        stripped, closed = strip_comments_and_strings(raw.lstrip(" \t"))
        if not closed:
            problems.append("{}:{} 字符串没闭合".format(rel, n))
        body = stripped.strip()
        if not body:
            continue
        indent = len(lead.replace("\t", " ")) // 4 if " " in lead else lead.count("\t")
        code.append((n, indent, body))

    # 括号配对
    for n, _i, body in code:
        for ch in body:
            if ch in "([{":
                depth_open += 1
            elif ch in ")]}":
                depth_open -= 1
        if depth_open < 0:
            problems.append("{}:{} 括号多余".format(rel, n))
            depth_open = 0
    if depth_open > 0:
        problems.append("{} 括号少 {} 个闭合".format(rel, depth_open))

    # 作用域：func 名唯一 + 函数内 var 唯一 + 不与脚本级 var/const 同名
    funcs = {}
    class_names = set()
    for n, indent, body in code:
        if indent == 0:
            m = VAR_RE.match(body)
            if m or body.startswith("const "):
                nm = body.split()[1].rstrip(":=").split("(")[0]
                class_names.add(nm)

    current = None
    locals_in = {}
    for n, indent, body in code:
        if indent == 0 and FUNC_RE.match(body):
            fname = FUNC_RE.match(body).group(1)
            if current is not None:
                _check_locals(current, locals_in[current], rel, problems)
            if fname in funcs:
                problems.append("{}:{} func {} 重复声明（{}: 已有）".format(rel, n, fname, funcs[fname]))
            else:
                funcs[fname] = n
            current = fname
            locals_in.setdefault(fname, [])
            continue
        if indent == 0:
            if current is not None:
                _check_locals(current, locals_in[current], rel, problems)
            current = None
            continue
        if current is None:
            continue
        m = VAR_RE.match(body)
        if m:
            nm = m.group(1)
            locals_in[current].append((n, nm))
            if nm in class_names:
                problems.append("{}:{} 函数 {} 里 var {} 与脚本级同名（shadow）".format(rel, n, current, nm))
    if current is not None:
        _check_locals(current, locals_in[current], rel, problems)

    src_seen = set()
    for tok in GODOT4_TOKENS:
        for n, _i, body in code:
            key = (n, tok)
            if tok in body and key not in src_seen:
                src_seen.add(key)
                problems.append("{}:{} 疑似 Godot 4 写法 {}".format(rel, n, tok))
    for pat in GODOT4_PATTERNS:
        rx = re.compile(pat)
        for n, _i, body in code:
            if rx.search(body):
                problems.append("{}:{} 疑似 Godot 4 写法 {}".format(rel, n, pat))
    return problems


def _check_locals(fname, decls, rel, problems):
    seen = {}
    for n, nm in decls:
        if nm in seen:
            problems.append("{}: 函数 {} 里 var {} 重复声明（{} 行已声明，Godot 3 同函数只有一个作用域）"
                            .format(rel, fname, nm, seen[nm]))
        else:
            seen[nm] = n


def main():
    files = []
    for root, dirs, names in os.walk(MOD):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for name in sorted(names):
            if name.endswith(".gd"):
                files.append(os.path.join(root, name))
    if not files:
        print("FAIL 没找到任何 .gd")
        return 1
    failures = []
    for path in files:
        failures.extend(parse(path))
    for f in failures:
        print("FAIL", f)
    if failures:
        return 1
    print("OK GDScript 结构检查通过：files={}".format(len(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
