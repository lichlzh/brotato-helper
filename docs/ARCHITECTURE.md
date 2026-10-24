# 技术文档：ShopAdvisor 的实现结构

面向「要改代码的人」。决策逻辑为什么这样设计见 `DESIGN.md`，安装/调参/排错见 `../README.md`。

---

## 1. 运行时拓扑

```
游戏进程
 └─ /root/ModLoader/BrotatoHelper-ShopAdvisor        mod/mod_main.gd（loader 实例化）
      └─ ShopAdvisor                                  mod/scripts/shop_advisor.gd（数据枢纽，Node）
           ├─ advisor.gd     纯计算引擎（Reference，不碰任何游戏类）
           └─ state_collector.gd  游戏对象 → 纯字典（Reference）

 脚本扩展（ModLoaderMod.install_script_extension，镜像游戏脚本路径）
 ├─ ui/menus/shop/shop_items_container.gd   ← 唯一驱动点
 └─ ui/menus/shop/shop_item.gd              ← 只做角标显示
```

一次重算的调用链：

```
Timer(0.6s) 或 游戏调 update_buttons_color()
  → shop_items_container._adv_on_tick / update_advisor
      1. 遍历 _shop_items，收集 ShopItem 节点 + 拼局面签名
      2. 签名没变 → 直接 return（组合选择要穷举子集，白算会掉帧）
      3. hub.advise(RunData, player_index, offers, reroll_cost, tier)
           ├─ collector.collect_player(...)  → player 字典（含 probes 记录）
           ├─ collector.collect_offer(...)   → 每个栏位的 offer 字典
           ├─ advisor.advise_shop(player, shop, priors, index) → 结果字典
           └─ debug_dump 打开时 → 写 user://shopadvisor_debug_w<波次>.json
      4. 逐栏位 node.shop_advisor_set_result(items[i])，空栏传 {} 清角标
      5. _adv_update_summary(result) 在 RerollButton 上方写汇总行
```

只有一个驱动点的理由：整店必须一起算（组合选择、预算、刷新期望都跨栏位），
逐件 hook 会产生「四条独立建议 + 一个互相矛盾的购买组合」。
汇总行原本打算挂在 `shop.gd`，改挂在容器里可以少一个 hook 点，也少一处随版本失效的地方。

## 2. 文件职责

| 文件 | 职责 | 不该在这里做的事 |
| --- | --- | --- |
| `mod/mod_main.gd` | loader 入口：定位解压目录、实例化枢纽、注册两个 script extension | 任何计算 |
| `mod/scripts/shop_advisor.gd` | 加载 `priors.json` / `brotato_data.json`、建 id 索引、缓存 `last_result`、落盘诊断、收 probe 记录 | 决策公式 |
| `mod/scripts/state_collector.gd` | 从 `RunData` / `ItemService` / `ShopItem` 抽纯数据，全部防御式读取并记录「靠哪条途径读到」 | 打分、比较 |
| `mod/scripts/advisor.gd` | 决策引擎（效用曲线、DPS、套装、组合穷举、刷新期望、阈值判定） | 引用任何游戏类或节点 |
| `mod/extensions/.../shop_items_container.gd` | 驱动：时机、签名缓存、刷价/档位/金钱的读取、汇总行 | 打分公式 |
| `mod/extensions/.../shop_item.gd` | 惰性创建/更新角标 Label | 自己算分 |
| `tools/advisor.py` | 上面 `advisor.gd` 的**同构参考实现**，本机唯一可运行的核心 | — |
| `tools/*.py` | 数据抓取、回归、静态检查、标定、诊断摄取 | 运行时依赖（mod 不读 tools/） |
| `tests/cases.json` | 16 个典型局面 + 期望标签 + 阈值距离 | — |

`advisor.gd` 保持「纯 Dictionary/Array」是整套可测性的前提：它没有一处 `RunData`、`Node`、`get_node`，
所以 Python 侧可以逐行同构复刻，静态检查才能比对两边。

## 3. 双实现策略与防漂移

本机没有 Godot 3，GDScript 无法编译，更无法运行。核心逻辑因此写两遍：

- `tools/advisor.py` —— 用来跑回归、标定、`ingest_debug` 生成候选用例；
- `mod/scripts/advisor.gd` —— 游戏里真正执行的那份。

两份是**手工同构**（函数名、分支顺序、常数都对应），靠三道静态检查拦漂移，都在 `build.sh` 里：

| 检查 | 比什么 | 拦住的事故 |
| --- | --- | --- |
| `check_parity.py` | 两边出现的 `stat_*` 键集合；`priors.get("…")` 键集合；顶层函数名集合（有单边白名单 `_PY_ONLY` / `_GD_ONLY` / `_RENAMED`）；`advise_shop` 返回值与每栏位结果的字典字面量键集合 | 改了 Python 忘了 GDScript；改了一边的输出字段（UI 会静默读到默认值） |
| `check_gd.py` | tab 缩进、括号配对、`func` 重名、同函数内 `var` 重声明、局部变量遮蔽脚本级成员、Godot 4 专属写法、`TYPE_DICT` | 写错直接编译失败的语法 |
| `check_inputs.py` | `advisor.py` 读的字段 == `advisor.gd` 读的字段，且这些字段被 `state_collector.gd` / `shop_items_container.gd` 真的产出 | 引擎新读一个字段、用例里手写了、游戏里根本取不到（线上只会静默退回默认值，用例永远发现不了） |

第四道是行为回归：`evaluate.py`（16 个用例 + 不变量 + 套装数学对拍 + 期望公式对拍）。

## 4. Godot 3 / GDScript 3 的硬约束（都踩过或是真实编译错误）

Brotato 是 **Godot 3.x** 游戏，Mod 只能写 GDScript 3。下面这些是本仓库必须遵守的写法：

| 约束 | 原因 |
| --- | --- |
| 缩进必须全 tab | GDScript 混用空格直接报错 |
| 一个函数只有一个作用域 | `if`/`for` 里再声明同名 `var` 是编译错误（写 `advisor.gd` 时真踩过） |
| 没有 `TYPE_DICT` | 枚举名是 `TYPE_DICTIONARY`，写成 `TYPE_DICT` 是未定义标识符 |
| 全局 `max/min` 只接两参 | 不像 Godot 4 可变参数 |
| 字符串 join 用 `PoolStringArray(...).join(sep)` | Godot 3 的 `Array` 没有 `join()` |
| `connect("timeout", self, "_adv_on_tick")` | Godot 3 的 信号连接签名，Godot 4 是 `signal.connect(callable)` |
| `onready` / `instance()` / `File.new()` / `JSON.new()` + `parse()` | 而不是 Godot 4 的 `@onready` / `instantiate()` / `FileAccess` / `JSON.parse_string` |
| 三元写作 `a if cond else b` | 没有 `? :` |
| `user://` 只能扁平文件名 | 子目录需要先 `DirAccess.make_dir_recursive`，不值得为一个诊断文件引入 |
| 不给 `_adv_hub()` 标返回类型 | 标成 `Node` 会让 Godot 3 把 `hub.advise()` 判成「Node 上找不到该方法」的编译错误；不标注则按动态派发通过 |
| 不引用可能不存在的自动加载标识符 | 引用未知 autoload 名是编译错误，所以单例统一走 `/root/ItemService` 这类路径查找 |
| 新增成员一律 `_adv_` / `ADV_` 前缀 | 脚本扩展与父脚本**共用一个命名空间**，撞名会静默覆盖游戏自己的实现 |
| `super` 调用靠 `_adv_busy` 防递归 | 父脚本内部再调 `update_buttons_color()` 会派发到我们的版本，形成无限递归 |
| 不覆盖 `_ready` | 父脚本有没有 `_ready` 不确定，覆盖会打断游戏自身的初始化；角标改成第一次收到建议时惰性创建 |
| `:=` 不要用在 Variant 表达式上 | 类型推断不出来会警告，游戏若开了 warnings-as-errors 就崩 |

`check_gd.py` 把其中可静态检出的部分变成打包前置条件。

## 5. 防御式读取与探针（probes）

所有游戏字段读取都走「多条途径依次试 + 全部失败退回默认值」，并把**命中的那条**记进 `probes`。
例如 `state_collector.read_stat` 依次尝试 `RunData.get_stat` / `get_stat_value` / `get_effect_value`
→ 直接属性 → `RunData.effects` 字典 → `RunData.player_run_data.stats`；
`shop_items_container._adv_gold` 优先 `RunData.get_player_gold(i)`（合作模式下 `RunData.gold` 不是本玩家的钱），
退回 `RunData.gold` / `money`；`_adv_shop_tier` 优先 `ItemService.get_tier_from_wave(wave)`，
退回内置「波次 → 档位」表。

设计意图：游戏大版本更新改了内部结构时，**最坏结果是建议变粗糙或不显示，不会崩游戏、不会碰存档**。
代价是「猜错的途径」在本机不可见 —— 所以 probes 会随诊断文件落盘，只有 `probes` 里出现「没找到」「兜底」
才说明需要改代码，其余情况落盘的作用是给调参提供真实局面。

## 6. 诊断落盘与字段清单

`priors.json` 的 `debug_dump: true` 打开后，每次整店重算覆写 `user://shopadvisor_debug_w<波次>.json`，
payload：

```
engine / game_version / mod_dir / index_size   环境
probes                                          每个字段命中的读取途径
inventory                                       /root 下单例与关键对象的属性/方法/信号名清单
player / shop                                   引擎看到的原始输入
result                                          打分与建议
```

`inventory` 由 UI 侧第一次重算时调 `collector.scan_inventory(root, RunData, 一个 ShopItem)` 生成，
**只落盘、不参与任何建议**：上游参考库里没有怪物/天赋/ban 的数据，这些字段名本机猜不出来，
有了清单就不用猜（待办 #8 的入口）。落盘只跑一次靠 `hub.has_inventory()` 把关。

`tools/ingest_debug.py game_dumps/*.json` 把这些文件转成：
`tests/cases_from_game.json`（候选用例，期望标签是当前引擎算出来的，属于「先中靶再调靶」，要人核对才值得并进 `cases.json`）
和 `tests/inventory_from_game.json`（合并字段清单，并按 talent / monster / wave / ban / crystal / reroll / character
等关键字挑出可能的 #8 入口）。终端列出 probes 中「没找到 / 兜底」的字段。`--selftest` 自检管线本身，在 `build.sh` 里跑。

## 7. 输入契约

引擎（两份实现）只吃这两个字典，形状由 `state_collector.gd` 保证、由 `tests/cases.json` 复刻。
`check_inputs.py` 提取的就是下面这些名字（py/gd 必须一致，且 collector 必须真的产出）：

```
player = {wave, gold, danger,
          stats{stat_*: float},          # 键集合由 collector 的 STAT_KEYS 提供
          weapon_ids[], total_dps, melee_share}
shop   = {tier, reroll_cost,
          offers[{id, name, kind, price, cursed, effects[{key,value}],
                  base{damage, cooldown, scaling, type}}]}   # base 可由参考表兜底
```

用例的 `player` 里还写了 `character_id`，但引擎目前不读它（角色只以汇总后的属性出现）——
它是待办 #8 的占位，加了读取才生效。

`total_dps` / `melee_share` 不是独立输入：`collector.derive_dps`（用例侧是 `evaluate.enrich_player`）
从 `weapon_ids` + 参考表推导，所以武器改了 DPS 会自动影响后续打分。
引擎的返回形状（`check_parity` 比对字面量键）：

```
{items:[{name,score,utility,dps_pct,price,recommendation,reasons}],
 reroll, lock, reroll_expected, reroll_margin, buy_set, spend, budget, left,
 thresholds:{buy, maybe}}
```

## 8. 工具链与打包门

`build.sh` 的顺序就是它的 gate 链，任何一步失败都不出包：

```
check_parity.py     py/gd 同构（stats / priors 键 / 函数名集合 / 输出字段）
check_gd.py         GDScript 结构体检
check_inputs.py     输入契约（读的字段 == 产出的字段）
calibrate.py --check 标定守卫：已采用的 stat_weight_adj 方向是否仍与当前数据拟合一致
evaluate.py         16 用例 + 不变量 + 套装数学 + 期望公式对拍
ingest_debug.py --selftest
JSON 校验 + 必备文件清单
zip → dist/BrotatoHelper-ShopAdvisor.zip（根目录直接是 manifest.json / mod_main.gd）
```

`evaluate.py` 除了标签断言还校验**不变量**（`check_invariants`，不写进用例也不该被破坏）：
不超预算；标 `buy`/`maybe` 的件都在 `buy_set` 里；`lock` 不指向推荐购买的那件；`buy_set` 里没有单件超过总预算的。

### 阈值距离断言

离散标签是擦边的：某栏位现在高出买入门槛 3.0 分，改一下 priors 掉到 −0.4 标签就翻了；
但如果它只是从 +30 挪到 +31，标签断言永远不会报警 —— 而那可能正是「价格标定悄悄跑偏」。
所以每条 item 期望带 `margin` = 这件商品的分数离**决定它这个标签的那条线**还差多少
（`buy`/`lock` 参照 `buy_thr`，`maybe`/`skip` 参照 `maybe_thr`，刷新判定参照
`headroom − 刷价 × reroll_gain_per_cost`）。默认容差 `margin_tol: 2.0`，
变化超过 2 分即 FAIL。调完 priors、人工确认各局面建议仍然合理之后，用 `record_margins.py` 重新记一遍数字。
`|margin| < 1.5` 会被标 NOTE：那说明局面本身设在刀口上，该改局面而不是记数字。

## 9. 数据管线

`tools/fetch_data.py` 从 mojimoon/brotato 抓 `brotato_data.en.json` + `.zh.json`，裁剪成
`mod/data/brotato_data.json`，保留 `weapons` / `items` / `characters` / `sets` / `stat_names`
（id、中英文名、tier、price、dlc、类型、武器 base 伤害与 scaling、`stat_*` 效果）。
枢纽建索引时把 `sets` 挂到 `index["_sets"]`、中文名挂到 `index["_stat_names"]`（角标理由行用它显示中文属性名）。
游戏更新数值 → 重跑 `fetch_data.py` → 跑 `build.sh`（`calibrate.py --check` 会顺带检查权重结论有没有被新数据推翻）。

## 10. 性能

- 局面签名（玩家序号 + 每件 id@价格 + 刷价 + 档位 + 金钱）没变就不重算；
- 0.6s 自建 Timer，且只在 `is_visible_in_tree()` 时跑，不覆盖游戏的 `_process`；
- 档位池期望按 `tier|kind` 建倒排并缓存，缓存键含 `player_fingerprint`（影响打分的玩家字段），局面变了自动失效；
- 组合穷举限制 n ≤ 8（8 件已是 256×8 次打分，GDScript 里会卡），商店正常 4 栏；
- 角标惰性创建，`mouse_filter = MOUSE_FILTER_IGNORE` + `z_index 4090`，不挡点击也不参与布局。

## 11. 贡献者指南

**加一个属性 / 新效果**（必须两边同步，静态检查会拦）：

1. `tools/advisor.py::_utility_stat_gain`（或 `special_keys`）加曲线；
2. `mod/scripts/advisor.gd` 对应函数加同一条曲线，注意同函数内不要重复声明 `var`；
3. 若引入新常数 → 加进 `mod/data/priors.json`（`check_parity` 要求 priors 覆盖两边引用的所有键）；
4. `state_collector.gd` 的 `STAT_KEYS` 加上这个键（否则运行时不产出 → `check_inputs` FAIL）；
5. `tests/cases.json` 补一个能体现它的局面；
6. `./build.sh`（必要时 `record_margins.py` 写回阈值距离）。

**调权重**：改 `priors.json` → `evaluate.py -v` 看每个局面的分数/阈值距离/买组合/E[重掷最好件]
→ 人工确认建议仍合理 → `record_margins.py` → `build.sh`。
若打算按某个属性的「金/点」比值改权重，先读 `DESIGN.md` 第 7 节：售价那条路已经证伪，
`calibrate.py --check` 也会拦下方向被推翻的采用项。

**改 hook 点**：`extensions/` 下的路径必须镜像游戏脚本路径，首行 `extends "res://…"`；
新增成员一律带 `_adv_` 前缀；任何字段读取都要有兜底并记 probe。

## 12. 已知技术债

- 两份实现的同步靠静态检查，不是单源生成。改成 codegen 的成本高于目前收益（曲线形状多、可读性优先）。
- `expected_reroll_factor` 兜底路径与档位池期望并存，前者只在参考数据加载失败时生效。
- 组合值是「按独立分降序固定累加边际」的近似，不是全排列最优。
- 免费件（price 0）恒标 `buy` 且不进预算选择器 —— 若以后出现「免费但有害」的来源，需要在这里加判断。
- 待办 #8（怪物构成 / 天赋 / ban 列表参与评分）阻塞在真实字段清单上，机制已就绪（`inventory` 落盘 + `ingest_debug`）。
