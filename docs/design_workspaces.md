# 设计：三个工作区（RLC 提取 / 走线模型 / 文件对比）与模式合并

> 状态：**草稿，待 owner 评审**（2026-10-02）。评审通过前不写代码。
> 本文是计划；落地后每条规则搬进 `docs/conventions/` 对应的档案，本文留作"为什么"。

## 0. 起因（owner 原话与实测）

- "我想算算某个 snp 文件中某个 PN 信号的 loading，我打开菜单点了 trace model，
  结果给我弹出一个 warning 我完全摸不着头脑"；"当我在 trace panel 里 select 了
  trace 后想看 trace model，可能也根本不会想到要用 analyze 里面的工具"。
- 实测（真实 GUI 走查，2026-10-02）：走线模型要 5 步、其中 3 步界面无提示；
  拒绝文案说 "Mode 6"，界面上没有任何地方写 Mode 6；切到 Mode 6 填好端口、只差
  Calculate 时弹的仍是同一句；主图画的是开路自阻抗（R 到 −10 kΩ），像算错了。
- 模式：五个模式最终都变成同一个 `TerminationSet` 交给同一个求解器，1/2/3/6 都是
  Custom 的子集；切模式藏数据；长得一样的表规则不同（Mode 6 拒绝"探针口又是 GND"，
  Mode 5 让 GND 赢）；编号漏到界面。
- 对比窗口：默认端口设置 "port 1 to ground" 不是用户选的，却决定了大字结论；
  端口设置从 trace 列表借；marker 只能回主窗口改；`1e+02 %` / 数字粘列 / Q ±100%
  方波；只能比两个文件。

**原则**：按**任务**分工作区；任务内只有**一套**端口描述；不自动起名、不自动识别；
错误在格子里当场标，不弹窗；不替用户选默认设置。

## 1. 共通：工作区切换

### 1.1 形态（方案 A，owner 已同意"主窗口切换"，A/B 评审时可改）

- 菜单栏下方一条全宽切换条，三个 `ttk.Radiobutton`（toolbutton 样式）：
  `RLC extraction` · `Trace model` · `Compare files`。**GUI 文字保持英文**（红区 X11
  字体不保证有 CJK，同 `compare_files.md` 的理由）。
- **Loaded Files 面板三个工作区共用**，位置不变；其余左侧内容与整个右侧按工作区换。
- 结构：`outer` PanedWindow 的左栏拆成"共享 Files"+"可换区"；右栏整体可换。
  RLC 工作区的现有 widget 树原封不动（tests 走 `results_text → TPanedwindow → master`
  的链与 460/431 宽度断言要保持；若必须变，同一提交重测并改断言）。

### 1.2 与 `rejected_ui.md` / `trace_model.md` 的冲突，以及怎么处理

两份档案写着"原理图必须是 Toplevel 里的 Canvas，绝不做成 tab"，理由是两条实测代价：

| 代价 | 本设计的处理 |
|---|---|
| tab 条占 26 px 绘图高度（minsize 下绘图 400 px） | 切换条实测高度写进档案；minsize 下绘图区重测（预计 ~374 px），作为已知代价接受 |
| 切 tab 抢焦点，M / V / Delete 失效 | 切回 RLC 时显式 `canvas.focus_set()`；新增测试：切出再切回后 M / V / Delete 仍生效 |

被否的那条是"RLC 图**旁边**多一个原理图 tab"；这里是**整窗按任务切换**，原理图在自己的
工作区里占满右侧。落地提交里同时改 `rejected_ui.md`（加 "Superseded 2026-10-xx" 注）
与 `trace_model.md`（删 "must not become a tab"，换成本节理由）。

### 1.3 session

- 新增顶层键 `workspaces`（仿 `attribution` 块：自带内层版本号，空则不写，读取失败只丢
  自己）：`{"version": 1, "active": "rlc"|"trace"|"compare", "trace": {...}, "compare": {...}}`。
- `SESSION_VERSION` 不动（旧读者忽略未知顶层键——已核实 `session_from_dict` 只读已知键）。

## 2. 阶段一：走线模型工作区（并删除旧 Trace model 窗口）

### 2.1 布局

```
左（可换区）                                   右
┌ Nets ──────────────────────────────────┐ ┌ Summary（monospace tk.Text，可排序）─────────┐
│ File [pkg_top.s24p ▾]                   │ │ Net   R_ser  L_ser  C_in  C_out  f_3dB  lumped│
│ Name  IN+    IN−    OUT+   OUT−          │ │ DQ0   ...                                     │
│ DQ0  [3 ▾]  [4 ▾]  [27 ▾] [28 ▾]   ⚠…   │ │▶CLK   ...                                     │
│ [+ Add net] [Duplicate row] [Remove]     │ ├ Schematic（tk.Canvas，pi_canvas_items）──────┤
├ Other ports ────────────────────────────┤ │                                              │
│ GND [ 15:22 ]  everything else: OPEN     │ ├ Response（tk.Canvas，response_canvas_items）─┤
├ Conditions ─────────────────────────────┤ │  勾选多个 net 叠画；marker 可拖               │
│ Freq [0.1] GHz  Source [1] Ω  Load [ ]fF │ ├ ▸ Details（pi_report_lines + bandwidth_lines）┤
│ [ Calculate all ]        [ Export CSV ]  │ └──────────────────────────────────────────────┘
└─────────────────────────────────────────┘
```

### 2.2 Net 表（`RowTable`，不是 Treeview——`editor_and_tables.md` 规则）

- 列：`Name | IN+ | IN− | OUT+ | OUT−`。端口格是下拉 + 可手输；下拉项显示
  `端口号  端口名`（文件里有名字时），**选什么完全由用户定**。
- **Name 必填**，不给默认名；新行 Name 为空且标红"name required"。
- 单端：两个 − 留空。差分：两个 − 都要填；只填一个 → 该格标红。
- 当场校验（格子标红 + 行尾一句原因，**不弹窗**），全部复用 `build_terminations_coupling`
  已有的拒绝理由，另加表级规则：
  - 名字重复 / 名字是 `A`、`B`（`LEGACY_GROUP_NAMES`）
  - 同一行内端口重复；一个端口被两行占用
  - 端口超出文件端口数
  - 端口同时在 GND 里
- 文件切换：表不清空；不在新文件端口范围内的格子标红。

### 2.3 计算（新 L2 模块 `pkg_rlc/services/tracenets.py`，无 Tk）

- 每个 net **单独**一次 `build_terminations_coupling([(IN), (OUT)], gnd, nports=)` +
  `compute_z_matrix(fe.Y, freqs, term)`；一行坏不拖累别行。
- **按返回的 `port_names` 取 IN/OUT**，不按位置——`resolve_meas_ports` 按最小端口号排序
  （CLI `_run_trace_model` 已是这样）。
- 差分时做一次单频 4 口 imbalance 检查；**带上 GND**（旧窗口没带、CLI 带了——以 CLI 为准，
  一并消除这处 GUI/CLI 分歧）。
- 输出每 net 一个结果对象（纯数组 + `PiModel` + bandwidth 表），不持有 `TraceConfig`。
- L0 `tracemodel.py`、L3 `tracemodel_report.py` 原样复用；L3 新增 `summary_table_lines(results, sort_key)`。

### 2.4 结果区

- Summary：每 net 一行，列 `R_ser L_ser C_in C_out f_3dB(open) lumped✓/⚠`；点表头排序；
  点行 → 下面画该 net。未算 / 已过期 / 出错 的行写明状态，不留空白。
- 过期：改某行或 GND/文件 → 只有受影响的行标 `stale`；`Calculate all` 只重算 stale 行。
  （与旧"冻结 + Recompute"规则一致：结果不会在眼皮底下悄悄变。）
- Source / Load 改动只重画 bandwidth，不重解（沿用旧窗口做法）。
- Export CSV：Summary 全精度 + 每 net 的分支值。

### 2.5 删除旧窗口（清单来自 2026-10-02 摸底）

- `pkg_rlc/panels/tracemodel_gui.py` 整个删；纯函数（`_terminations` 解析、`_draw*` 喂
  Canvas 的部分、bandwidth 重算）搬进新面板。
- 路由：`app.py` 的 import / Analyze 菜单项 / `_on_trace_model` / 3 处 refresh；
  `panels_traces.py` 右键项与 refresh；`panels_files.py`、`panels_editor.py`、
  `panels_results.py` 的 refresh；结果区指针行（`panels_results.py` `_footer_segments`
  491-496，无测试钉住）。
- 测试：删 `tests/test_tracemodel_window.py`；保留 `tests/test_tracemodel.py`。
- 文档：README（Analyze 行、"In the GUI" 段）、`docs/help/mode6.md` 窗口段、
  `trace_model.md` 窗口段、`test_suite_map.md` 行、`CLAUDE.md` 模块表。
- 结果区里 Mode 6 的指针行（Trace model 那句）删；Attribution 那句保留。

### 2.6 模块落点

| 层 | 文件 | 内容 |
|---|---|---|
| L2 | `pkg_rlc/services/tracenets.py`（新） | NetRow 校验 + 每 net 求解 → 结果 |
| L3 | `pkg_rlc/present/tracemodel_report.py` | + `summary_table_lines` |
| L5 | `pkg_rlc/panels/ws_tracemodel.py`（新） | 工作区面板；不 import app |
| L5 | `pkg_rlc/panels/workspaces.py`（新） | 切换条 + 显隐 + 焦点交还 |
| L6 | `pkg_rlc/frontend/app.py` | 接线、session 块 |

## 3. 阶段二：模式合并

这就是 `docs/design_connection_table.md` 第 4 阶段（"modes reframed as presets that fill
the table"）的落地；那份笔记 §4 描述的优先级陷阱在这里正面解决。

### 3.1 统一后的模型：就是 Mode 5 的存储，不发明新字段

- 每条 trace 只有：测量端口表 `mports` + 连接表 `conn_rows`（GND、短接、集总元件都是行）
  + `extra_lines`（DSL 逃生口，保留）。
- **没有单独的 GND 输入框**：GND 是连接表里的一行。一个概念一个视图——若既有 GND 框又有
  ground 行，两处会说不同的话。连接表**始终显示**，表下一行固定文字
  `Ports not listed anywhere are OPEN.`。
- `TraceConfig.mode` 字段保留，**一律写 5**：旧版程序读新 session 时当作 Custom 正确打开
  （摸底 F9：缺省会被当成 mode=1 算成 port_a→GND）。`MODE_NAMES` 只剩迁移用。

### 3.2 编辑器

```
File      [pkg_top.s24p ▾]
Start from template [ choose… ▾ ]   Port to GND / Between two ports /
                                    Loop with shorted far end / Several nets (coupling)
Measurement ports   Name | + ports | − ports            [+ Add]
Connections         Kind | Ports | To | Value            [+ Add]
                    Ports not listed anywhere are OPEN.
Plot  ☑ this trace  ☑ self  ☑ mutual（≥2 个测量端口才显示后两个）
Label / Style
```

- 模板 = 往两张表里填行，填完看到的就是表本身。表非空时套模板先确认（会覆盖）。
- Mode 单选、Port A / Port B / Short Pairs / GND 四个框、`MODE_PLACEHOLDERS`、
  `_update_mode_visibility` 的分支全部删除。
- 路由按**测量端口个数**：1 个 → 自阻抗路径（可拟合、普通结果块）；≥2 个 → 耦合结果块。
  数值不变（`compute_z` 本就是 `Zmat[:,0,0]`）；变化的只有原 Mode 6 里只填一个端口的
  trace：它从此能拟合、显示普通结果块（摸底 F5）。

### 3.3 统一的冲突规则（按物理含义分 + 侧 / − 侧，2026-10-02 实测后修订）

今天两套规则：Mode 6（`build_terminations_coupling`）拒绝；Mode 1/2/3 与 Mode 5 让 GND
悄悄赢——即把重叠端口从探针侧删掉（摸底 F3/F4）。实测这两种都不对：

一个探针侧的端口**在求解里是连在一起的**（Mode 6 自己的报错文案就这么说），所以接地其中
一个就等于接地整侧。实测（1 GHz，`+1 −3,4`）：

| 写法 | diff_pair | decap | coupled_float |
|---|---|---|---|
| 参考：`+1`，ground `3,4` | 5.001 nH | −12642 nH | 0 nH |
| `+1 −3,4`，ground `3,4`（− 侧全接地） | 与参考**逐位相同** | 逐位相同 | 逐位相同 |
| `+1 −3,4`，ground `3`（**只接地一部分**），今天的 Mode 5 | **−12635 nH** | −12667 nH | **NaN** |
| 物理上它的含义：`+1`，ground `3`，short `3–4` | 5.001 nH | −12642 nH | 0 nH |

"GND 悄悄赢"在部分接地时会把 `−3,4` 变成 `−4`，**算出一个物理上错误的数**，而且不报错。
所以统一规则是：

- **− 侧有端口在 ground 行里**：整个 − 侧都在地上，这就是"+ 侧对地"的测量。**接受并按物理
  含义计算**——把整个 − 侧并进地，再求解（全接地时与今天逐位相同）。行尾给黄色提示（不是红）：
  `− side is grounded (port 3), so this measures P1 to GND; ports 3,4 are all at GND.`
- **+ 侧有端口在 ground 行里**：测一个已接地的节点没有意义 → **拒绝**，标红。
- 其余 Mode 6 拒绝理由搬到统一路径：名字为 A/B、名字重复、同一端口同时在 + 和 − 两侧、
  一个端口被两个测量端口占用、端口超出文件端口数。
- 全部在格子里标红/标黄 + 一句原因，不弹窗；被拒绝的 trace 不参与 Calculate，Log 写明原因。
- 上表固化为测试。

### 3.4 迁移：数值逐位不变，隐藏行为摊到明面

- **何时**：session 加载时、新建 trace 时一次性迁移全部 trace（今天是"选中才迁移"，未选中
  的 trace 永远带着旧字段——摸底 §2）。每条被迁移的 trace 在 Log 写一行做了什么。
- **只迁"活"字段**（摸底 F1）：按旧 `mode` 决定哪些字段生效，其余清空。例：每条 Mode 5/6
  trace 都带着默认 `port_a="1"`，若一并折进来会多出一个探针，数值就变了。

| 旧模式 | 迁成 |
|---|---|
| 1 | 一个测量端口 `P1`：+ = port_a；GND → 一条 ground 行 |
| 2 | 一个测量端口：+ = port_a，− = port_b；GND → ground 行 |
| 3 | 同 2，Short Pairs 每对 → 一条两栏 short 行（`ports=a, to=b`，同 `_trace_role_rows`；绝不经 `"a,b short"`，摸底 F6） |
| 4 | 已有：先并成 2，再按 2 迁 |
| 6 | 测量端口表原样；GND → ground 行 |
| 5 | 原样；只清掉隐藏的 `gnd_ports` / `port_a` 等 |

- **端口串规整**（摸底 F2）：去空格（`"1, 2"` → `1,2`，`"6 - 14"` → `6-14`）；
  `parse_port_range` 逐 token strip 去重保序，所以语义不变。
- **旧的"GND 悄悄赢"**（按 §3.3 分情况）：
  - + 侧端口也在 GND（旧版把它从探针删掉）：迁移照做，数值不变，Log 写明；删完 + 侧为空的
    （旧版本就报错）→ 标红待用户处理。
  - − 侧**全部**在 GND：新规则与旧版逐位相同，无需改动，Log 提示一句。
  - − 侧**部分**在 GND：旧版算的是错数（§3.3 表）。新版给物理上正确的数，**数值会变**；
    Log 写明 "port 3 is grounded and is on the − side with port 4, so port 4 is at GND too;
    earlier versions dropped port 3 from the probe instead and reported a different number"。
    这是合并中**唯一**有意改变数值的地方，作为 bug 修复列进档案。
- **旧 Mode 5 依赖"静默合并"的配置**（重名、跨行同端口）无法通用地自动修，迁移后标红并说明——
  罕见，作为已知行为变化写进档案。
- 组合（多文件）trace：Short Pairs 迁成行后会走端口作用域（今天是 `_check_bare_ports`），
  合法写法数值不变，只是带文件前缀的写法变得合法（摸底 F8）。

### 3.5 证明"数值逐位不变"

`golden_legacy.npz` 只覆盖"直接调 builder"的 20 个 case，**不覆盖任何 `TraceConfig` 路径，
也没有 Mode 6**；`TestRowsReproduceNamedModes` 用的是 `< 1e-15` 而不是逐位（摸底 F10）。所以：

1. **动代码之前**，用当前代码对一个语料批量跑 `TraceConfig → _build_termination →
   compute_z_matrix`，存成新 fixture `tests/fixtures/golden_trace_paths.npz`。语料覆盖：
   每个模式 × 每个 fixture 文件，含重叠端口、带空格端口串、Short Pairs、隐藏陈旧字段、
   Mode 6 单端口、Mode 4 遗留、组合 trace。
2. 合并后：每个语料 trace 迁移 → 新路径求解 → `np.array_equal`（NaN 位置一致）。
3. `golden_legacy.npz` 原样保留，`test_golden_regression` 照跑；CLI 不经 `TraceConfig`，
   153 个 cli_reference fixture 不应有任何变化——作为副证。
4. 迁移语料里"旧版拒绝 → 新版也拒绝"、"旧版 GND 赢 → 新版数值相同 + Log 一行"各有专门断言。
5. **差分专项**（2026-10-02 预测量）：`A ↔ B`（Mode 2）、统一后的行（`+1 −2`）、`+/-` 耦合
   （`P1: +1 −2`）三条路径在 `diff_pair_4port` / `decap_4port`（GND 3,4）/
   `coupled_4port_float` 上 **`array_equal` 为真**——合并不改差分结果。对照组"两个单端探针
   再手算 `Z11+Z22−Z12−Z21`"数学上相同，但 `decap_4port` 上相对误差 **1.5e-8**（两个
   ~12.6 µH 量级的开路容抗相减得 1 nH，相消误差），所以差分**一律用 − 侧探针直接解**，
   不提供"单端算完再组合"的路径。单端（只填 +）与差分（+ 与 − 都填）在新表里是两种不同
   的行，不会混淆：同一文件上 port 1 单端 = −12642 nH（另一端开路，容性），1↔2 差分 = 1.000 nH。
   这组对照固化为测试。

### 3.6 界面上不再出现模式编号

- 结果区描述符 `M6: in:1/2 out:3/4 G:[]` → `in:1/2 out:3/4 GND:[]`（`_port_descriptor`）；
  `test_mode5_editor.py:294`、`test_run_snapshot.py:215` 随之改。
- Traces 列表 `info_str` 的 `MODE_NAMES` → 测量端口名摘要（如 `in, out`）。
- GUI CSV 头 `Mode: Custom` → `Setup: <描述符>`（无测试钉住）。CLI CSV 头不动（CLI 不变）。
- `report.py` "Help → Mode 6"、`attrib_gui` / `solve.py` 里给用户看的 "Mode 5/6" 文字改成任务名。
- run-diff 的 `mode` 字段（`_SIGNATURE_FIELDS`）：迁移后恒为 5，从签名里拿掉，
  `test_run_history` 相应改。

### 3.7 Help 按任务重排（总数不超过 10，不碰"第十一个标签页"那条否决）

`Mode 1/2/3/5/6` 五个页 → `Setting up a measurement`（对地 / 两点间 / 短接 / 元件，全在一张
表里讲）· `Coupling` · `Trace model` · `Compare files`。合计：Overview · Reading files ·
Save/Load · Setting up a measurement · Coupling · Trace model · Compare files · Input syntax ·
Worked examples = **9 页**。标签条宽度重测（`tests/test_session.py:879-904`）。
backlog TASK-017（Help 没提 Compare）随之关闭。

### 3.8 不动的

- CLI `--mode gnd|p2p|coupling` 及其 153 个参考输出：CLI 不经 `TraceConfig`，原样保留。
- `build_terminations_mode1/2/3/coupling` 这些 L0 builder 保留（CLI 和 golden 在用）。
- Attribution 窗口：它经 `app._build_termination` 建 spec，合并后自动走统一路径。

### 3.9 测试改动量（摸底计数）

约 21 个测试文件、~130 行引用 `mode=` / `ed_mode_var` / `_ed_mode_buttons`；大头是
`test_row_table.py`(24)、`test_port_roles.py`(15)、`test_mode5_editor.py`(12)。
`test_mode5_editor.py:849` 的"无表模式要塞进 431 px"测试整体作废（不再有无表模式）。
`test_core.py::TestTerminationPrecedence` 里 `test_named_modes_and_probe_model_disagree_on_purpose`
钉的正是要统一掉的分歧——改成钉"统一后拒绝"。

## 4. 阶段三：文件对比工作区（并删除旧 Compare 窗口）

### 4.1 布局

```
左（可换区）                                    右
┌ Files to compare ─────────────────────┐ ┌ Verdict strip：每个对比文件一行 ✓/✗ + 一句话 ┐
│ Reference A [pkg_30G.s4p ▾]            │ ├ ΔS (%)  每个对比文件一条曲线 + 限值线        ┤
│ Compare     ☑ pkg_50G  ☑ pkg_80G       │ ├ ΔL (%)  不可判定区段画灰                     ┤
├ What to compare ───────────────────────┤ ├ ΔQ (%)                                        ┤
│ ○ Raw S-parameters only                │ ├ ΔR (%)  （参考，无限值）                     ┤
│ ● Extracted L/Q/R under this setup:    │ │  marker 在图上直接拖                          │
│   [端口设置组件——阶段二产物]          │ ├ ▸ Details（默认折叠）                        ┤
│   [Copy setup from trace… ▾]           │ └───────────────────────────────────────────────┘
├ Limits ────────────────────────────────┤
│ S [1]%  L [1]%  Q [5]%   [ Compare ]   │
└────────────────────────────────────────┘
```

### 4.2 规则

- **一个参考对 N 个**：每个 (A, Bi) 独立走现有 `compare_s` / `compare_z`（已核实全是两两
  函数，无全局状态）；各对自有重叠频段与网格，曲线各画各的，Verdict 每对一行。
- **没有默认端口设置**。未定义时"提取"部分显示 "No setup defined"，只出 S 结论。
  `Copy setup from trace…` 是用户手动点的一次性复制，之后与 trace 无关联。
- 参考默认取最低顶频的文件（沿用现规则）；用户可改。
- marker 归本工作区所有，在图上拖；不再读主窗口 `rlc_freq_var`。
- **不可判定区段**（新增绝对判据，修 Q/R 方波）：`|Re Z| < ε·|Z|` 处 Q 与 R 的相对差
  不计算、画灰、不进结论；ε 默认值实测后定（候选 1e-3），写进 `similarity.py` 常量并在
  Details 里报被排除的点数。原有中值近零规则与 SRF 85% 规则保留。
- 修格式 bug：`_num` 在 99.5–100 出 `1e+02`；S 矩阵 :424 同病；≥100% 超长数字改为倍数写法
  （同 `report._delta_cell` 过 ×10 的做法）；表格列宽按内容算，不再固定 24。
- 修 `focus_set` 缺失（CLAUDE.md 不变式）。

### 4.3 删除

- `pkg_rlc/panels/compare_gui.py` 整个删（纯函数 `compare_summary_lines` 等搬到 L3
  `pkg_rlc/present/compare_report.py`）；Analyze 菜单项、Files 右键项、3 处 refresh。
  旧窗口的两个 bug（删文件后 Refresh 仍用旧数组；Clear All / 加载 session 不通知）随之消失。
- `tests/test_compare_files.py` 的纯测试迁到新模块；Tk 测试重写。
- 文档：`compare_files.md` 重写为工作区规则；backlog TASK-017 改指向新工作区的 Help。

## 5. 菜单的去留

三个工作区落地后 Analyze 菜单只剩 `Attribution…` 与 `Files in this trace…`（两者都是
"针对选中 trace"）。本计划**不动**它们，只记一笔：它们有与 trace model 同样的可发现性
问题，留作后续讨论。

## 6. 验收

每个阶段：
1. 全量 `python tests/run_parallel.py` 退出码 0；新增测试先证明会失败（mutation）。
2. **真实启动 GUI 走查截图**（DPI 校正过的截图脚本），按 owner 的原始场景走：
   - 阶段一：加载 4 口差分文件 → 切到 Trace model → 填 2 个 net → Calculate all →
     Summary 出两行 → 点行看图 → 故意填错一格看红字 → 切回 RLC 按 M/V/Delete 仍生效。
   - 阶段二：加载旧 session（含 mode 1/2/3/6 trace）→ 数值与旧版逐位一致 → 用模板建 trace。
   - 阶段三：加载 30/50/80 GHz 三个文件 → 只比 S → 定义端口设置比 L/Q → 拖 marker。
3. 每阶段一个（或数个）提交，直接 commit + push main，按路径 stage。

## 7. Workflow 编排（评审通过后）

- 阶段顺序：一 → 二 → 三（三依赖二的端口设置组件和一的切换框架）。
- 每阶段内按文件范围并行：L2/L3 纯逻辑 + 测试一组；L5 面板一组；L6 接线 + session 一组；
  文档一组；最后一个验证 agent 跑全量测试 + GUI 走查截图。
- 每阶段结束停下来给 owner 看截图，再进下一阶段。

## 8. 待 owner 定的

1. 切换条 A（顶部一条，~26 px）还是 B（菜单，0 px）——草稿按 A 写。
2. 走线模型的"其余端口"：阶段一只有 GND 框 / 开路；阶段三把它换成阶段二的连接表
   （GND、短接、集总元件都是行），与 RLC 工作区、对比工作区同一个组件——草稿按"接"写。
3. 迁移时"旧 Mode 5 依赖静默合并"的配置标红待修（§3.4），而不是尝试自动修——草稿按此写。
3b. "− 侧部分接地"的旧 Mode 5 trace：迁移后改给物理正确的数（§3.3/§3.4），而不是保留旧的错数
   ——草稿按"修正 + Log 说明"写。
4. 走线模型是否需要**跨文件拼接**（封装 + PCB 两个文件组成一条走线）——建议本计划不做，
   有真实需求再加。
5. 对比工作区 ε（Q/R 不可判定阈值）先按 1e-3，实测后报给你再定。
