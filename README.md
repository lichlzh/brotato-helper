# Brotato ShopAdvisor（商店决策助手）

进商店时给每个栏位打一个建议角标，并在刷新按钮上方提示「该刷新 / 该锁定」。

安装见第 1 节，调参见第 3 节；决策模型为什么这样设计见 [`docs/DESIGN.md`](docs/DESIGN.md)，
代码结构与工程约束见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

```
买 103/90     锁定 90/80     可考虑 21/45     跳过 -3/20
```

- **买**：进了本次推荐购买组合，且分数 ≥ 本档位标准件
- **锁定**：分数够高但这次不买 —— 要么买不起（差多少金写在提示里），要么预算让给更值的组合（锁定免费，留到下一波）
- **可考虑**：进了组合但没到买入门槛，值一点钱但不值得放弃刷新/升级
- **跳过**：分数低于档位线
- 刷新按钮上方：`[买这套] 小盾甲+血条　花90 剩10` / `[刷新] 这里重掷更划算` / `[锁定] 金属板 值得留下`
- 鼠标停在角标上：给出关键理由（如 `护甲+8、最大生命-10`、`非本流派武器`、`负重已满 -12`）

建议随局面变化：波次、难度、当前属性、已有武器构成（近战/远程占比）、金钱、负重上限都会改变评分。

不是逐件独立判「买得起就买」：四件栏位会在剩余金币预算下穷举所有子集（≤10 件），比较**组合价值**后给出
「买这几件」。组合里每件按已经买下的东西重新估边际价值 —— 所以会出现「分数最高的那件反而建议锁定，
因为两件便宜的加起来更值」「第二件同类护甲边际递减所以不叠」这类单件比较给不出的结论。

刷新建议不是拍脑袋的阈值：它把当前档位物品池里的每一件都按你这个局面打一遍分，
用「抽 4 件不重复时最好那件」的解析期望（order statistic）跟现状比，所以「负重满了刷武器也没用」
这类局面会自动倾向于不刷。刷不刷用的是**买完推荐组合后剩下的钱**判断，不会出现「买这三件 + 去刷新」的矛盾建议。
所有数值权重都在 `mod/data/priors.json` 里，改参数不用改代码。

---

## 1. 安装

Brotato 已内置 godot-mod-loader，不需要额外装任何东西。

1. 运行 `./build.sh`，得到 `dist/BrotatoHelper-ShopAdvisor.zip`
2. 把 zip 放进游戏的 `mods/` 目录（没有就自己建）：

   | 平台 | 路径 |
   | --- | --- |
   | Linux | `~/.local/share/Steam/steamapps/common/Brotato/mods/` |
   | Windows | `C:\Program Files (x86)\Steam\steamapps\common\Brotato\mods\` |
   | macOS | `~/Library/Application Support/Steam/steamapps/common/Brotato/Brotato.app/Contents/Resources/mods/` |

3. 启动游戏 → 主菜单 **Mods** → 列表里找到 `BrotatoHelper / ShopAdvisor` → 勾选启用 → 按提示重启游戏
4. 开一局进商店，栏位左上角应出现彩色角标

**卸载**：删掉 `mods/` 里的 zip，以及 `mods-unpacked/BrotatoHelper-ShopAdvisor/` 目录。

## 2. 版本不匹配怎么办

`manifest.json` 里 `compatible_game_version` 写的是 `["*"]`，正常不会被拦。若 Mods 菜单提示不兼容或报错：

1. 在主菜单 Mods 设置里打开 **Force Load / 强制加载**
2. 或把 `manifest.json` 的 `extra.godot.compatible_game_version` 改成你的游戏版本（主菜单左下角能看到版本号）后重新打包

游戏大版本更新后 `ui/menus/shop/*` 的内部结构可能变化。这个 mod 的 hook 全部是防御式写的：字段读不到就退回默认值，hook 失败最多是「不显示建议」，不会让游戏崩溃或影响存档。

日志位置（看不到角标时先看这里，搜 `ShopAdvisor`）：

- Linux/macOS：`~/.local/share/godot/app_userdata/Brotato/logs/`
- Windows：`%APPDATA%\Godot\app_userdata\Brotato\logs\`

也可以带命令行参数启动看控制台输出：`brotato --verbose`（或 `--mod-loader-verbose`，取决于 loader 版本）。

### 第一次进游戏：建议先开诊断落盘

这个 mod 是在没有游戏的环境下写的，`state_collector.gd` 里所有字段读取途径（属性怎么取、金钱怎么取、
已刷新次数存在哪、档位怎么算）都是按常见写法**猜**的，全部做了兜底。开了诊断落盘后，游戏里每次进商店
都会把「实际命中了哪条途径 + 完整的输入输出」按波次写成 JSON，发回来即可核对字段名、把真实局面补成回归用例。

1. 编辑 `mods-unpacked/BrotatoHelper-ShopAdvisor/data/priors.json`，把 `"debug_dump": false` 改成 `true`
2. 进一局游戏、打开商店（多逛几波更好，每波一个文件）
3. 退出游戏，取 `shopadvisor_debug_w*.json`（`w3.json` = 第 3 波的商店）：

   | 平台 | 目录 |
   | --- | --- |
   | Linux | `~/.local/share/godot/app_userdata/Brotato/` |
   | Windows | `%APPDATA%\Godot\app_userdata\Brotato\` |
   | macOS | `~/Library/Application Support/Godot/app_userdata/Brotato/` |

4. 把这些文件发回来后，开发机上一条命令转成候选用例：`python3 tools/ingest_debug.py game_dumps/*.json`
   —— 它会同时打印每个局面里哪些读取途径没命中（需要改代码），其余只是给调参提供真实数据。

文件里有 `probes`（每个字段命中的读取途径，例如 `stat_read: get_effect_value`、`tier: 内置波次→档位表兜底`）、
`player`/`shop`（引擎看到的原始输入）、`result`（打分与建议），还有一份 `inventory`：**`/root` 下有哪些单例节点，
以及 `RunData` / `ItemService` / 角色节点 / `ShopItem` 与其 `item_data` 各自声明的属性、方法、信号名**
（第一次重算时扫一次，只落盘、不参与任何建议）。上游参考库里没有怪物/天赋/ban 的数据，这些名字本机也猜不出来，
有了清单就不用猜。**只有 `probes` 里出现「没找到」「兜底」时才需要改动代码**，
其余情况这些文件的作用是给调参提供真实局面。用完记得把开关改回 `false`（同波内每次重算会覆写那个波的文件，代价不大但不必要）。

`ingest_debug.py` 会把清单合并成 `tests/inventory_from_game.json`，并按关键字（talent / monster / wave / ban /
crystal / reroll / character …）挑出可能是 #8 入口的字段名。

## 3. 调参

改 `mod/data/priors.json` 后 `./build.sh` 重新打包。想在游戏中直接调：编辑
`mods-unpacked/BrotatoHelper-ShopAdvisor/data/priors.json` 并重启游戏（zip 内容没变时 loader 不会覆盖它）。

常用的几个旋钮：

| 键 | 作用 |
| --- | --- |
| `tier_anchors` | 各档位「标准件」分数线，整体调高/调低推荐严格程度 |
| `buy_wave_scale` | 买入门槛相对档位线的倍数：早期 1.2 倍、随波次降到 1.0 倍（`max(anchor, …)` 兜底，所以不会低于档位线） |
| `maybe_factor` | 「可考虑」相对档位线的比例 |
| `weapon_dps_anchor` | 武器 DPS 增益折算成分数（100% DPS ≈ +100 分） |
| `off_type_factor` | 异流派武器折扣（近战号拿远程枪） |
| `loadout_penalty_start` / `_per_slot` | 负重超过 N 件后每多一件的扣分 |
| `set_first_count` | 同职业武器凑到第几把才开始吃套装加成（**待游戏内确认**，见第 4 节） |
| `set_bonus_weight` | 套装增量按普通属性效用的几倍计分 |
| `armor_per_point_*`、`dodge_per_point_*` | 每点护甲/闪避的价值随波次曲线 |
| `stat_weight_adj` | 各属性效用的相对权重修正（由物品内部兑换比拟合，见第 4 节） |
| `reroll_gain_per_cost` | 刷新期望提升要覆盖刷价的比例门槛（越大越不爱刷） |
| `reroll_expected_factor` | 只在参考数据加载失败时兜底用（正常走档位池期望） |
| `special_keys` | 非属性类效果（如商店价格、范围、陷阱）的单项权重 |

## 4. 开发说明

决策模型为什么这样设计见 [`docs/DESIGN.md`](docs/DESIGN.md)，运行时结构、Godot 3 的坑、
工具链与贡献者指南见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

```
mod/                Mod 源码（zip 内容）
  mod_main.gd         入口：装 hook、挂数据枢纽
  scripts/
    advisor.gd        决策引擎（纯 Dictionary/Array，不碰游戏类）
    state_collector.gd 从 RunData / ShopItem 抽纯数据，全部防御式读取
    shop_advisor.gd   枢纽：加载 JSON、建索引、缓存最近一次结果
  extensions/         镜像游戏脚本路径的 script extension
  data/               brotato_data.json（参考数据）+ priors.json（权重）
docs/
  DESIGN.md           设计说明：价值模型、三类建议的推导、权重标定与局限
  ARCHITECTURE.md     技术文档：运行时拓扑、双实现防漂移、Godot 3 约束、工具链
tools/
  fetch_data.py       从 mojimoon/brotato 抓反编译数据 → mod/data/brotato_data.json
  advisor.py          同一套逻辑的 Python 参考实现（本机唯一可运行的核心）
  evaluate.py         跑 tests/cases.json 用例（期望公式穷举对拍 + 买组合不变量 + 阈值距离断言）
  record_margins.py   确认建议仍合理后，把当前阈值距离写回用例（调完 priors 再跑一次）
  check_parity.py     静态检查 advisor.py 与 advisor.gd 没有跑偏（stat 键 / priors 键 / 函数名集合 / 输出字段）
  check_gd.py         GDScript 结构体检：tab 缩进、括号配对、函数/局部变量重名、Godot 4 写法
  check_inputs.py     输入契约：引擎读的字段必须 py/gd 一致，且运行时真的产出（用例是手写的，盖不住这类缺口）
  calibrate.py        权重标定审计：本机数据到底能不能校验属性权重（结论：售价不能，物品内部兑换比能）
  ingest_debug.py     游戏诊断文件 → 候选回归用例 + 合并字段清单（tests/inventory_from_game.json）
tests/cases.json      16 个典型局面 + 期望建议 + 每个断言栏位离阈值的距离
build.sh              回归 → 打包 zip（根目录直接是 manifest.json）
```

数据源用 [mojimoon/brotato](https://github.com/mojimoon/brotato) 仓库里由游戏 `.tres` 反编译生成的
`brotato_data.en.json`（跟随 DLC 更新），中文名来自同仓库 `brotato_data.zh.json`。fandom / BWIKI 的表停在 0.8.x，不用。

```bash
python3 tools/fetch_data.py     # 更新参考数据（当前：257 武器 / 247 物品 / 64 角色）
python3 tools/evaluate.py -v    # 逐用例看每个栏位的打分、阈值距离、买组合、E[重掷最好件]
python3 tools/check_parity.py   # py/gd 同构检查
python3 tools/check_gd.py       # GDScript 结构体检（本机没有 Godot 3 编译器）
python3 tools/check_inputs.py   # 输入契约检查（py/gd 读同一批字段，且运行时都产出）
python3 tools/calibrate.py      # 权重标定审计（售价为什么不能用 + 兑换比 α 表）
python3 tools/calibrate.py --check   # 守卫：已采用的修正方向是否仍与当前数据拟合一致（build.sh 会跑）
python3 tools/record_margins.py # 调完 priors、人工确认建议仍合理后写回阈值距离
python3 tools/ingest_debug.py --selftest          # 自检导出管线
python3 tools/ingest_debug.py game_dumps/*.json   # 游戏诊断文件 → 候选用例
./build.sh
```

改决策逻辑时必须 Python 与 GDScript 两边同步改，`check_parity.py` 会在函数名集合出现单边漂移时拦下来；
`evaluate.py` 除了用例期望，还对每个局面校验不变量：不超预算、标 buy/maybe 的件都在 `buy_set` 里、
`lock` 不指向推荐购买的那件。

### 属性权重怎么来的：一次证伪 + 一次可用的标定

`utility_from_stat_gain` 里那些系数最初全是拍的。本机能不能校验它们？`tools/calibrate.py` 两种办法都试了，
它会把判决一起打印出来，就是为了避免以后有人拿同一批数据再拟合一遍就改权重。

**售价反推 —— 证伪。** 想法是拟合 `price ≈ Σ β·每点属性 + 档位底价`，读出的 β 就是「游戏认为 1 点值多少金」。
实测：档位哑变量单独就解释 R²=0.88，加上全部属性点只多解释 2.3% 的价差；同一档位内
「属性总点数 vs 售价」的 Spearman 只有 −0.22 ~ +0.29。**Brotato 按档位定价，不按内容定价**，
所以逐属性 β 是共线性分摊出来的噪声，拿来调 priors 等于拿噪声调参。（`identifiability()` 会标出
哪些属性从来没单独出现过，那种 β 连形状都不是。）

**物品内部的兑换比 —— 可用。** 参考表 247 件物品里有 115 件同时带正效果和负效果，
这就是设计者亲手写下的「用什么换什么」。若一件物品给的好处和要的代价在它的价值度量里互相抵消，
那么用我们自己的效用函数算 `正边效用 / 负边效用` 应当只是个常数；各件对这个常数的偏离就是权重误差。
把偏离对「属性出现位置」做一次岭回归（每属性一个 α，正边取正号、负边取负号），α 就是
「我们的权重相对设计者该乘 e^α」。这层信息是售价给不了的，因为它比的是同一件物品内部的两边。

两个必须知道的限定，都写在脚本的打印里：

- **泛化检验**：α 只有在留出集（没参与拟合的物品）上也能压低残差才算数。控住档位后残差从 0.905 降到 0.606
  （−33%），不控时 40% —— 差的那部分属于「高档位物品两边都给得多」，是档位效应，不是权重误差，
  所以表读**控档位**那一版。
- **只采用了一半**：符号在岭强度跨 3 个数量级（λ=0.05→30）时全部稳定，但幅度随 λ 线性衰减，
  说明幅度是正则化决定的。因此按 e^(α/2) 半强度采用，且**只采用 α<0 的方向**
  （elemental 0.43、lifesteal 0.59、engineering 0.64、speed 0.72、hp_regeneration 0.74、ranged 0.82）——
  下调高估的属性是把误推的杂货推到线下，方向安全。拟合同时说 armor / dodge / percent_damage / crit
  被我们低估 1.4~1.8 倍，**这条没有采用**：我们的护甲分数本来就顶在买入门槛两倍以上的
  （+8 护甲 ≈ 103 分，档位门槛 ≈ 45 分），再抬只会让预算选择器退化成「无脑挑护甲」。
  这种跨属性单位不统一是旧伤，而兑换比审计只量物品内部两边的比值、量不到绝对尺度，所以它看不见这个后果。

结论写进 `priors.json` 的 `_stat_weight_adj_help`（含全强度 α 备查）。**绝对尺度只能靠游戏内实跑定标**：
开第 2 节的诊断落盘打一把，把 `shopadvisor_debug_w*.json` 拿回来跑 `ingest_debug.py`，
对着真实局面看建议有没有「明显不该买却买了」，那才是能动的下一个旋钮。

### 武器职业套装：只算增量

参考表里每把武器带 `sets`（职业名，如 `WEAPON_CLASS_BLADE`），共 17 个职业、每职业 5 档效果；
第 i 档是**持有 i+1 把该职业武器时的累计加成**（徒手套：+3/+6/…/+15 闪避；刀刃套：+1/+2/… 近战伤害与吸血）。
所以「再拿一把同职业武器」在商店里是笔真实的账，引擎现在会算它。

两处刻意的设计：

- **只用差值**：`set_gain(现有武器, 新武器) = 套装累计(买后) − 套装累计(买前)`。游戏是否把套装加成
  已经并进 `RunData` 的属性，本机确认不了；只取增量就不会和已含的属性重复计数，无论哪种情况都只是略微保守。
- **`set_first_count` 待游戏内确认**：数据里有 5 档，但「第 1 把就算一套」和「要 2 把才算」两种读法都说得通
  （1..5 或 2..6）。默认 2 是保守选择——不会怂恿你把第一把同类武器当套装件收。
  确认方法：进游戏拿 2 把同职业武器，看属性面板/套装提示有没有在 1 把时就给加成；
  若 1 把就给，把 `set_first_count` 改成 `1` 即可，`evaluate.py` 的 `self_test_sets` 会跟着提示改哪里。

套装数学用 `tools/evaluate.py::self_test_sets` 逐条对拍参考表（门槛、跨档、双职业同时跨档、
多拿几件、档位封顶），因为整店分数会同时受 DPS 锚点、负重、流派占比影响，把「套装加了几分」淹没在噪声里，
用例断不出来。用例侧只留 `c16` 一根**接线销**：把 `score_item` 里的套装计分拆掉，它的阈值距离立刻掉下去。

### 阈值距离断言（为什么用例不只断言「买/跳过」）

离散标签是擦边的：某个栏位现在高出买入门槛 0.6 分，改一下 priors 掉到 -0.4，标签就翻了，
而如果它只是从 +30 挪到 +31，标签断言永远发现不了这种「价格标定悄悄跑偏」。
所以每条 item 期望都带一个 `margin` —— 这件商品的分数离**决定它这个标签的那条线**还差多少：

| 标签 | 参照线 | margin 含义 |
| --- | --- | --- |
| `buy` / `lock` | `buy_thr`（买入门槛） | 正数 = 高出门槛多少 |
| `maybe` / `skip` | `maybe_thr`（可考虑线） | 正数 = 在线上方，负数 = 差多少才值得考虑 |
| `reroll_margin` | `headroom - 刷价×reroll_gain_per_cost` | 正数 = 该刷，负数 = 不该刷 |

用例默认容差 `margin_tol: 2.0`。调 priors 后跑 `evaluate.py`，凡是让某个局面离阈值远近变化超过 2 分的
改动都会 FAIL —— 这时先 `-v` 看那份局面还合不合理，合理就 `record_margins.py` 重新记一遍数字，
不合理就是调坏了。（`|margin| < 1.5` 的用例会被标 NOTE：那说明局面本身设在刀口上，优先改局面而不是记数字。）

## 5. 已知限制

- **未在游戏中实测**。开发机上没有 Steam 和 Godot 3，GDScript 无法本地编译，只能靠
  「核心逻辑纯函数化 + Python 同构实现 + 用例回归」保证逻辑正确；UI hook 部分按
  [Bantato](https://github.com/lilongfei2000/Bantato) 等现成 shop hook 的真实字段写，仍需首次进游戏验证。
- 效用模型是人工先验，不是精确的战斗模拟：它判断「这件相对本档位标准件值不值」，不保证最优解。
- 非属性类效果（`+1 商店栏位`、`陷阱`、`队友相关`）只有粗略权重，缺协同判断。
- 刷新期望会把「刷出来也买不起」的高分件算进去：因为锁定免费，刷到好件锁住、下波再买是本作的常规打法。
  若你更想「买不起就别刷」，在 `expected_reroll_score` 里按 `price > gold` 过滤即可（Python 与 GDScript 同步改）。
- 角色天赋/被动没参与评分，只用了汇总后的当前属性。
- 组合选择穷举所有子集（参与抢预算的件数上限 8，商店正常只有 4 栏），但每个子集的价值是「按独立分从高到低逐件重估边际」
  累加出来的 —— 累加顺序固定、不是全排列取最优，所以组合值是近似。四件商店栏位下 n≤4，误差可以忽略。
  另外 `buy_set` 只算花钱的件，免费件（价格 0）永远单独标 buy，不占预算。
- 建议语言为中文；角标只读，不会替你点击购买。
