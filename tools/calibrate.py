#!/usr/bin/env python3
"""标定审计：本机的数据到底能不能校验我们拍脑袋定的属性权重？

问一（售价）：参考表里物品的效果与售价都来自游戏 .tres，若售价按属性价值定，
NNLS 拟合 price ≈ Σ β_k·每点效果 + 档位底价 就能读出「游戏认为 1 点该属性值多少金」。
实测答案：**没有信号**。档位单独就解释 R²≈0.88，加全部属性点只多解释 ~2% 的价差，
同档位内「属性点数 vs 售价」的 Spearman 只有 ±0.2。Brotato 按档位定价、不按内容定价，
β 主要是共线性分摊出来的噪声 —— 拿它调 priors 等于拿噪声调参。signal_check 会自己宣判。

问二（物品内部的 ± 兑换比）：115 件物品同时带正效果和负效果，
这就是设计者亲手写下的「用什么换什么」。若我们的权重与设计者的价值度量自洽，
按我们的效用函数算出来 正效用/负效用 应落在窄带里；某属性站在代价侧时比值系统性地偏，
就说明它的相对权重拍错了。这条是本机唯一有信息量的路径，脚本默认输出它。

用法：python3 tools/calibrate.py [--quiet|--force-table]
纯本机数据分析，不改任何建议逻辑。
"""
import json
import math
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from advisor import default_priors, num, utility_from_stat_gain  # noqa: E402

TIERS = (0, 1, 2, 3, 4)

# 判决门槛：属性相对档位至少要多解释这么多方差、档内相关至少这么强，比值才有意义
MIN_EXTRA_R2 = 0.15
MIN_RHO = 0.35
LAMS = (0.3, 1.0, 3.0, 10.0)  # 岭强度网格，按留出法挑
MIN_INCID = 8  # 属性在正负两边合计出现这么多次，α 才有自己的样本，不然被邻居绑架


def rows_from_items(data):
    """只取效果全为正数值、且不是白拿的普通物品：混着负效果的物品价格被缺点压低，会污染拟合"""
    out = []
    for it in data["items"]:
        effs = [(e["key"], num(e.get("value", 0))) for e in it.get("effects", [])
                if str(e.get("key", "")).startswith("stat_")]
        if not effs or any(v <= 0 for _k, v in effs):
            continue
        price = num(it.get("price", 0))
        if price <= 1.0:
            continue  # 事件白拿/特殊条目，不是商店定价样本
        vec = {}
        for k, v in effs:
            vec[k] = vec.get(k, 0.0) + v
        tier = int(it.get("tier", 0))
        for t in TIERS:
            vec["tier_%d" % t] = 1.0 if t == tier else 0.0
        out.append((vec, price))
    return out


def features(rows):
    keys = {}
    for vec, _p in rows:
        for k in vec:
            keys[k] = keys.get(k, 0.0) + vec[k]
    # 只拟合出现够多的属性：样本太少的 β 会被单件物品牵着走
    return sorted([k for k, tot in keys.items() if k.startswith("tier_") or keys[k] >= 12.0])


def identifiability(rows, cols):
    """每个属性有多少件、其中多少件是「除了档位没别的属性」的干净样本。

    NNLS 只能从变化里认出演系数：某个属性如果从来没单独出现过，永远和其它属性绑在
    一件物品上，那它的 β 是两者分摊出来的，值多少金完全由共线性决定 —— 不能当证据用。
    """
    stats_cols = [c for c in cols if not c.startswith("tier_")]
    all_cols = set(stats_cols)
    for vec, _p in rows:
        for k in vec:
            if not k.startswith("tier_"):
                all_cols.add(k)
    all_cols = sorted(all_cols)
    out = {}
    for c in stats_cols:
        n = 0
        solo = 0
        for vec, _p in rows:
            if vec.get(c, 0.0) == 0.0:
                continue
            n += 1
            others = [o for o in all_cols if o != c and vec.get(o, 0.0) != 0.0]
            if not others:
                solo += 1
        out[c] = (n, solo)
    return out


def nnls(rows, cols, iters=400):
    """坐标下降式非负最小二乘：β_j = max(0, Σx·resid_j / Σx²)"""
    X = [[vec.get(c, 0.0) for c in cols] for vec, _p in rows]
    y = [p for _v, p in rows]
    beta = [0.0] * len(cols)
    denom = [sum(X[i][j] ** 2 for i in range(len(X))) or 1e-9 for j in range(len(cols))]
    for _pass in range(iters):
        max_delta = 0.0
        for j in range(len(cols)):
            s = 0.0
            for i in range(len(X)):
                if X[i][j] == 0.0:
                    continue
                pred = sum(X[i][k] * beta[k] for k in range(len(cols)))
                s += X[i][j] * (y[i] - pred + X[i][j] * beta[j])
            new = max(0.0, s / denom[j])
            max_delta = max(max_delta, abs(new - beta[j]))
            beta[j] = new
        if max_delta < 1e-7:
            break
    return beta


def sse_of(cols, rows, beta):
    return sum((sum(vec.get(c, 0.0) * beta.get(c, 0.0) for c in cols) - price) ** 2
               for vec, price in rows)


def r2_of(cols, rows, beta, sst):
    return 1.0 - sse_of(cols, rows, beta) / sst if sst > 0 else 0.0


def spearman(xs, ys):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        out = [0.0] * len(v)
        for pos, idx in enumerate(order):
            out[idx] = pos
        return out
    if len(xs) != len(ys) or len(xs) < 4:
        return float("nan")
    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num_ = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num_ / den if den > 0 else float("nan")


def signal_check(rows, cols, sst):
    """价格表到底含不含「按属性价值定价」的信号 —— 这是整个审计能不能用的前提"""
    tier_cols = [c for c in cols if c.startswith("tier_")]
    beta_tier = nnls(rows, tier_cols)
    beta_full = nnls(rows, cols)
    extra = (sse_of(tier_cols, rows, dict(zip(tier_cols, beta_tier)))
             - sse_of(cols, rows, dict(zip(cols, beta_full)))) / sst if sst > 0 else 0.0
    by_tier = {}
    for vec, price in rows:
        for t in TIERS:
            if vec.get("tier_%d" % t) == 1.0:
                pts = sum(v for k, v in vec.items() if not k.startswith("tier_"))
                by_tier.setdefault(t, ([], []))[0].append(pts)
                by_tier[t][1].append(price)
    rhos = {}
    for t, (xs, ys) in sorted(by_tier.items()):
        if len(xs) >= 6:
            rhos[t] = spearman(xs, ys)
    best = max(rhos.values()) if rhos else float("nan")
    usable = extra >= MIN_EXTRA_R2 and best >= MIN_RHO
    return {"extra_r2": extra, "rhos": rhos, "best": best, "usable": usable,
            "r2_tier": r2_of(tier_cols, rows, dict(zip(tier_cols, beta_tier)), sst),
            "r2_full": r2_of(cols, rows, dict(zip(cols, beta_full)), sst)}


def canonical_situations():
    """几个代表性局面：我们的效用曲线随波次与当前属性变化，单点取值会把形状读成噪声"""
    out = []
    for wave in (3, 8, 15):
        for hp_pct in (1.0, 0.5):
            out.append({
                "wave": wave, "gold": 60, "danger": 5, "melee_share": 0.5, "total_dps": 60.0,
                "stats": {"stat_max_hp": 100, "stat_armor": 8, "stat_dodge": 10, "stat_speed": 0,
                          "stat_lifesteal": 0, "stat_hp_regeneration": 0, "stat_harvesting": 5,
                          "stat_luck": 5, "stat_engineering": 0, "stat_melee_damage": 10,
                          "stat_ranged_damage": 0, "stat_elemental_damage": 0,
                          "stat_percent_damage": 10, "stat_damage": 0, "stat_attack_speed": 0,
                          "stat_crit_chance": 5, "stat_range": 0, "stat_xp_gain": 0,
                          "stat_curse": 0, "stat_all": 0},
            })
    return out


def our_utility(cols, priors):
    """引擎认为 1 点该属性值多少效用"""
    situations = canonical_situations()
    out = {}
    for c in cols:
        if c.startswith("tier_"):
            continue
        vals = [utility_from_stat_gain(c, 1.0, s, priors) for s in situations]
        out[c] = statistics.median(vals)
    return out


def gold_per_utility(rows, util):
    """把「效用」折算成金：全体样本上 中位(价格) / 中位(模型效用)"""
    pairs = []
    for vec, price in rows:
        u = sum(vec.get(k, 0.0) * util.get(k, 0.0) for k in vec if not k.startswith("tier_"))
        if u >= 8.0:
            pairs.append(price / u)
    pairs.sort()
    return statistics.median(pairs) if pairs else 1.0


def mixed_rows(data):
    """既有正效果又有负效果的物品：设计者在这里写下「用什么换什么」，
    这是本机能量到的唯一一份属性间相对价值的证据（价格表给不了，见 signal_check）"""
    out = []
    for it in data["items"]:
        effs = [(e["key"], num(e.get("value", 0))) for e in it.get("effects", [])
                if str(e.get("key", "")).startswith("stat_")]
        pos = [(k, v) for k, v in effs if v > 0]
        neg = [(k, -v) for k, v in effs if v < 0]
        if pos and neg:
            out.append((pos, neg, int(it.get("tier", 0))))
    return out


def mean_util(stat, value, situations, priors):
    return sum(utility_from_stat_gain(stat, value, s, priors) for s in situations) / len(situations)


def tradeoff_rows(data, priors):
    """每件有正有负的物品 → (正边各项效用, 负边各项效用)（按我们引擎的曲线算）"""
    situations = canonical_situations()
    out = []
    for pos, neg, tier in mixed_rows(data):
        p = [(k, mean_util(k, v, situations, priors)) for k, v in pos]
        n = [(k, abs(mean_util(k, -v, situations, priors))) for k, v in neg]
        p = [(k, u) for k, u in p if u > 1e-6]
        n = [(k, u) for k, u in n if u > 1e-6]
        if p and n:
            out.append((p, n, tier))
    return out


def solve(A, b):
    """对称正定小方程组的高斯消元（属性最多 17 个，直接解法足够）"""
    m = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(m):
        piv = max(range(col, m), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            M[col][col] += 1e-6
            piv = col
        M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        for r in range(m):
            if r == col:
                continue
            f = M[r][col] / pv
            if f:
                for c in range(col, m + 1):
                    M[r][c] -= f * M[col][c]
    return [M[i][m] / M[i][i] for i in range(m)]


def obs_of(rows):
    """(正边各项, 负边各项, 正边总效用, 负边总效用, log 比值, 档位)"""
    out = []
    for p, n, tier in rows:
        sp = sum(u for _k, u in p)
        sn = sum(u for _k, u in n)
        out.append((p, n, sp, sn, math.log(sp / sn), tier))
    return out


def tier_cols(obs):
    return sorted({"T%d" % o[5] for o in obs})


def col_key(stat, side, split):
    """列名：不分侧时每属性一列（就用属性名），分侧时 stat|+ / stat|-。
    档位控制列形如 T2，与 stat_ 前缀天然不撞名。"""
    return stat if not split else "%s|%s" % (stat, side)


def column_set(obs, split, control_tier=False):
    cols = set()
    for o in obs:
        for k, _u in o[0]:
            cols.add(col_key(k, "+", split))
        for k, _u in o[1]:
            cols.add(col_key(k, "-", split))
    if control_tier:
        cols.update(tier_cols(obs))
    return sorted(cols)


def design_rows(obs, cols, split, control_tier=False):
    """每行一件物品，每列一个属性；值 = 该属性占所属一边效用的比例（代价侧带负号）。
    split=True 时同一属性的好处/代价各占一列 —— 好处与代价的不对称就不必塞进「层级」里。
    control_tier=True 时再加档位常数项：高档位物品可能两边都给得多，
    那种「档位效应」不是权重误差，不控制的话会被 α 冒领。"""
    idx = {c: i for i, c in enumerate(cols)}
    out = []
    for o in obs:
        p, n, sp, sn = o[0], o[1], o[2], o[3]
        row = [0.0] * len(cols)
        for k, u in p:
            row[idx[col_key(k, "+", split)]] += u / sp
        for k, u in n:
            row[idx[col_key(k, "-", split)]] -= u / sn
        if control_tier:
            row[idx["T%d" % o[5]]] = 1.0
        out.append(row)
    return out


def ridge(x_rows, targets, lam, free=()):
    m = len(x_rows[0])
    A = [[0.0] * m for _ in range(m)]
    b = [0.0] * m
    for x, t in zip(x_rows, targets):
        for i in range(m):
            xi = x[i]
            if xi == 0.0:
                continue
            b[i] += xi * t
            row = A[i]
            for j in range(m):
                if x[j]:
                    row[j] += xi * x[j]
    for i in range(m):
        A[i][i] += 0.01 if i in free else lam
    return solve(A, b)


def cv_scores(obs, split, control_tier=False, folds=5, lams=LAMS):
    """留出法：α 只有在没见过的物品上也能压低残差，才不是把噪声拟成形状。

    列集合按全样本定（只是「有哪些属性」，不是估计值），系数只用训练折拟合。
    基线 = 什么都不拟合。基线 ≈ 最佳拟合 ⇒ α 没有泛化能力，别拿它调权重。
    """
    cols = column_set(obs, split, control_tier)
    X = design_rows(obs, cols, split, control_tier)
    free = {i for i, c in enumerate(cols) if c.startswith("T")} if control_tier else set()
    y = [o[4] for o in obs]
    out = {}
    for lam in lams:
        errs = []
        for f in range(folds):
            tr = [i for i in range(len(obs)) if i % folds != f]
            te = [i for i in range(len(obs)) if i % folds == f]
            center = statistics.median(y[i] for i in tr)
            beta = ridge([X[i] for i in tr], [y[i] - center for i in tr], lam, free)
            errs.append(statistics.median(abs(y[i] - center - sum(a * b for a, b in zip(beta, X[i])))
                                          for i in te))
        out[lam] = statistics.mean(errs)
    center = statistics.median(y)
    out["baseline"] = statistics.mean([
        statistics.median(abs(y[i] - statistics.median([y[j] for j in range(len(obs))
                                                       if j % folds != f]))
                          for i in range(len(obs)) if i % folds == f)
        for f in range(folds)])
    out["best_lam"] = min(lams, key=lambda l: out[l])
    out["cols"] = cols
    out["free"] = free
    return out


def fit_alpha(obs, cols, split, lam, control_tier=False, free=()):
    X = design_rows(obs, cols, split, control_tier)
    y = [o[4] for o in obs]
    center = statistics.median(y)
    beta = ridge(X, [v - center for v in y], lam, free)
    return dict(zip(cols, beta)), center


def incidences(obs):
    inc = {}
    for o in obs:
        p, n = o[0], o[1]
        for k, _u in p:
            inc.setdefault(k, [0, 0])[0] += 1
        for k, _u in n:
            inc.setdefault(k, [0, 0])[1] += 1
    return inc


def report_tradeoff(data, priors):
    rows = tradeoff_rows(data, baseline_priors(priors))
    obs = obs_of(rows)
    if not obs:
        print("\n== 第二问：没有同时带正负效果的物品，无从审计 ==")
        return None
    inc = incidences(obs)
    situations = canonical_situations()
    print("\n== 第二问（本机唯一有信息量的路径）：物品内部的「好处 vs 代价」是否等价 ==")
    print("样本 %d 件同时带正负效果。设计者若按「这个换那个」平衡，用我们的效用算出来"
          "\n正边/负边应当只是个常数；各件对这个常数的偏离就是权重误差。" % len(obs))
    variants = [(split, ctrl) for ctrl in (False, True) for split in (False, True)]
    verdicts = {}
    for split, ctrl in variants:
        cv = cv_scores(obs, split, ctrl)
        gain = 1.0 - cv[cv["best_lam"]] / cv["baseline"] if cv["baseline"] > 0 else 0.0
        print("\n[%s%s] 留出法中位残差：不拟合 %.3f → λ=%s 时 %.3f（压低 %.0f%%）"
              % ("好处/代价分列" if split else "每属性一个层级",
                 " + 控档位" if ctrl else "",
                 cv["baseline"], cv["best_lam"], cv[cv["best_lam"]], 100.0 * gain))
        verdicts[(split, ctrl)] = (cv, gain)
    # 解释权重时必须控档位：档位是混杂因子，不是候选解释。控了以后残差降幅变小是应该的，
    # 那部分降幅本来属于「高档位物品两边都给得多」，不该记在属性权重头上。
    controlled = [(sp, True) for sp in (False, True)]
    best = max(controlled, key=lambda v: verdicts[v][1])
    cv, gain = verdicts[best]
    unc_gain = max(verdicts[(sp, False)][1] for sp in (False, True))
    if gain < 0.10:
        print("\n判决：控住档位后，α 在没见过的物品上只能把残差压低 %.0f%% —— 这些物品并不是按等价"
              "\n平衡设计的（不控档位时的 %.0f%% 来自档位，不来自权重）。本机数据校验不了属性权重，"
              "只能靠游戏内实跑（见 README 的诊断落盘：先在真机上把建议对着看一遍再调 priors）。"
              % (100.0 * gain, 100.0 * unc_gain))
        return None
    alpha, center = fit_alpha(obs, cv["cols"], best[0], cv["best_lam"], True, cv["free"])
    print("\n判决：控住档位后 α 仍能把留出集残差压低 %.0f%%（不控时 %.0f%%），是有泛化能力的信号。"
          % (100.0 * gain, 100.0 * unc_gain))
    print("下面读「控档位 + %s」这一版。" % ("好处/代价分列" if best[0] else "每属性一个层级"))
    print("中位比值 e^%.2f = %.2f：设计者本来就允许物品净赚，这个常数不用管。" % (center, math.exp(center)))
    for c in sorted((c for c in alpha if c.startswith("T")), key=lambda x: -alpha[x]):
        print("  档位截距 %s：%+.2f（物品净赚幅度随档位的变化 —— 这就是不控档位时会被 α 冒领的那部分）"
              % (c[1:], alpha[c]))
    print("α > 0 = 设计者比我们的权重更看重它（该把对应那侧的效用乘 e^α）；α < 0 反之。\n")
    if not best[0]:
        print("  %-22s %7s %7s %7s %10s" % ("属性", "α", "该乘", "件数", "正/负出现"))
        stats = sorted({c.split("|")[0] for c in cv["cols"] if "|" in c or c.startswith("stat_")})
        for k in sorted(stats, key=lambda x: -abs(alpha.get(x, 0.0))):
            al = alpha[k]
            pos_n, neg_n = inc.get(k, (0, 0))
            n = pos_n + neg_n
            note = "样本少（%d 件），不采信" % n if n < MIN_INCID else (
                "← 明显偏" if abs(al) >= 0.4 else "")
            print("  %-22s %+7.2f %7.2f %7d %4d/%-4d %s"
                  % (k.replace("stat_", ""), al, math.exp(al), n, pos_n, neg_n, note))
    else:
        print("  %-20s %8s %8s %7s %11s %11s"
              % ("属性", "α好处", "α代价", "n+/n-", "我们罚/赏", "设计罚/赏"))
        stats = sorted({c.split("|")[0] for c in column_set(obs, True) if "|" in c})
        for k in stats:
            ap = alpha.get(col_key(k, "+", True), 0.0)
            an = alpha.get(col_key(k, "-", True), 0.0)
            pos_n, neg_n = inc.get(k, (0, 0))
            up = mean_util(k, 1.0, situations, priors)
            un = abs(mean_util(k, -1.0, situations, priors))
            ours = un / up if up > 1e-9 else float("nan")
            design = ours * math.exp(an - ap) if up > 1e-9 else float("nan")
            n = pos_n + neg_n
            note = "样本少" if n < MIN_INCID else (
                "← 一侧明显偏" if max(abs(ap), abs(an)) >= 0.4 else "")
            print("  %-20s %+8.2f %+8.2f %4d/%-3d %11.2f %11.2f %s"
                  % (k.replace("stat_", ""), ap, an, pos_n, neg_n, ours, design, note))
        print("\n「罚/赏」= 同 1 点，代价侧效用 / 好处侧效用。我们这边普遍 >1（风险回避，故意的）；"
              "\n设计者的罚/赏接近 1 只说明物品按点数对拍，不代表我们该去掉风险回避。")
    print("\nα 只是相对尺度上的校正，不含绝对金额；要落到 priors 上得逐条改，"
          "\n每条都跑 evaluate.py 确认标签与建议仍然合理，再 record_margins 写回。")
    return alpha


def baseline_priors(priors):
    """拟合必须在「没乘 stat_weight_adj 的原始权重」上做：
    带着已采用的修正去重拟合，α 会自我抵消成 0，检查就变成自证。"""
    out = dict(priors)
    out.pop("stat_weight_adj", None)
    return out


def fit_controlled(data, priors):
    """控档位 + 留出法挑 λ 的单层级 α —— --check 和报告读的都是这一版"""
    obs = obs_of(tradeoff_rows(data, baseline_priors(priors)))
    if not obs:
        return None, None
    cv = cv_scores(obs, False, True)
    alpha, center = fit_alpha(obs, cv["cols"], False, cv["best_lam"], True, cv["free"])
    return {"cv": cv, "alpha": alpha, "center": center,
            "gain": 1.0 - cv[cv["best_lam"]] / cv["baseline"] if cv["baseline"] > 0 else 0.0}, obs


def check(data, priors):
    """build.sh 用的守卫：已经写进 priors 的修正，方向必须还和当前数据拟合出的 α 一致。

    换数据源、重抓参考表、或者改了效用曲线形状，都可能让某个属性的 α 变号；
    那时 priors 里那条修正就从「校准」变成了「偏见」，必须显式失败而不是悄悄留着。
    """
    bad = []
    fitres, _obs = fit_controlled(data, priors)
    if fitres is None:
        print("FAIL 交换比回归没有样本（参考表里可能没有同时带正负效果的物品了）")
        return 1
    if fitres["gain"] < 0.10:
        print("FAIL 交换比回归在留出集上只剩 %.0f%% 的残差降幅（<10%%），"
              "α 已经不泛化了 —— priors.stat_weight_adj 需要重新评估"
              % (100.0 * fitres["gain"]))
        return 1
    adj = priors.get("stat_weight_adj") or {}
    for key in sorted(adj):
        al = fitres["alpha"].get(key)
        if al is None:
            bad.append("%s：参考表里现在没有同时带正负效果的样本，拟合不出 α（该删掉这条修正）" % key)
            continue
        want_up = adj[key] > 1.0
        if (al > 0) != want_up and abs(al) >= 0.15:
            bad.append("%s：priors 乘 %.2f（α=%+.2f 时定的），现在拟合出 α=%+.2f，方向反了"
                       % (key, adj[key], math.log(adj[key]) * 2.0, al))
    rows_p = rows_from_items(data)
    sig = signal_check(rows_p, features(rows_p),
                       sum((p - statistics.mean([q for _v, q in rows_p])) ** 2 for _v, p in rows_p))
    if sig["usable"]:
        bad.append("售价这次有信号了（属性额外解释 %.0f%% 的价差）—— 值得重做一次绝对尺度标定"
                   % (100.0 * sig["extra_r2"]))
    if bad:
        print("FAIL 标定守卫：")
        for b in bad:
            print("  - " + b)
        return 1
    print("OK 标定守卫：售价仍无信号（档位单独 R²=%.2f，属性只多解释 %.1f%%）；"
          "priors 里 %d 条 stat_weight_adj 的方向都与当前拟合一致（α 留出集降幅 %.0f%%）"
          % (sig["r2_tier"], 100.0 * sig["extra_r2"], len(adj), 100.0 * fitres["gain"]))
    return 0


def main():
    if "--check" in sys.argv:
        with open(os.path.join(ROOT, "mod", "data", "brotato_data.json"), encoding="utf-8") as f:
            return check(json.load(f), default_priors())
    quiet = "--quiet" in sys.argv
    force = "--force-table" in sys.argv
    with open(os.path.join(ROOT, "mod", "data", "brotato_data.json"), encoding="utf-8") as f:
        data = json.load(f)
    priors = default_priors()
    rows = rows_from_items(data)
    cols = features(rows)
    ys = [p for _v, p in rows]
    sst = sum((y - statistics.mean(ys)) ** 2 for y in ys)
    sig = signal_check(rows, cols, sst)

    if not quiet:
        print("== 第一问：能不能用售价反推属性价值？ ==")
        print("样本 %d 件（效果全为正、售价 >1 的普通物品）" % len(rows))
        print("只用档位拟合     R²=%.3f" % sig["r2_tier"])
        print("档位 + 全部属性点 R²=%.3f　→ 属性只额外解释 %.1f%% 的价差（门槛 %.0f%%）"
              % (sig["r2_full"], 100.0 * sig["extra_r2"], 100.0 * MIN_EXTRA_R2))
        print("同一档位内「属性总点数 vs 售价」的 Spearman：%s"
              % "  ".join("档%d %+.2f" % (t, r) for t, r in sorted(sig["rhos"].items())))
        print("门槛：档内需 ≥ %+.2f，实测最高 %+.2f" % (MIN_RHO, sig["best"]))
        if sig["usable"]:
            print("\n判决：价格表这次有信号，下面的逐属性比值可以当证据。")
        else:
            print("\n判决：Brotato 按档位定价、不按属性内容定价 —— 售价里没有可用来校验权重的价值信号，"
                  "不能拿它调 priors。（想看那张共线性噪声表：--force-table）")

    if sig["usable"] or force:
        beta = nnls(rows, cols)
        fit = dict(zip(cols, beta))
        cover = identifiability(rows, cols)
        util = our_utility(cols, priors)
        gpu = gold_per_utility(rows, util)
        if not quiet:
            print("\n折算率 1 效用 ≈ %.1f 金" % gpu)
            table(cols, fit, cover, util, gpu)

    if not quiet:
        report_tradeoff(data, priors)
    return 0


def table(cols, fit, cover, util, gpu):
    print("\n「件」= 有多少件物品带这个属性；「单独」= 其中多少件除了档位没别的属性。"
          "\n单独数太小 = β 是从共线性里分摊出来的。")
    print("\n属性               游戏金/点   我们效用/点   我们的金/点   比值   件/单独")
    for c in cols:
        if c.startswith("tier_"):
            print("档位底价 %-10s %8.1f" % (c[5:], fit[c]))
            continue
        b = fit[c]
        u = util.get(c, 0.0)
        ours_gold = u * gpu
        n, solo = cover.get(c, (0, 0))
        ident = solo >= 2
        ratio = (ours_gold / b) if (b > 0.05 and ident) else float("nan")
        flag = ""
        if not ident:
            flag = "样本不足（单独 %d 件）" % solo
        elif b <= 0.05:
            flag = "游戏没给价（被别的属性分摊了）"
        elif ratio >= 1.6:
            flag = "← 我们高估"
        elif ratio <= 0.62:
            flag = "← 我们低估"
        print("%-18s %10.2f %12.2f %12.1f %6.2f  %3d/%-3d %s"
              % (c.replace("stat_", ""), b, u, ours_gold, ratio, n, solo, flag))


if __name__ == "__main__":
    sys.exit(main())
