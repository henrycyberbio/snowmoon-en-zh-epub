# 分章改稿任务说明（实际发送给改稿子代理的指令）

本文件据本书校订过程中**实际下发给改稿子代理**的任务说明整理，用于复现当时的决策输入。
来源：本机 Hermes 会话中 `delegate_task` 调用参数里的 `goal` / `context` 字段（只读检索，
会话 ID 不公开），以及当时的绑定 brief `revision-brief.md`。

## 整理说明（必读）

本文件是**据实际任务整理的参数化模板**，不是整篇逐字复原：原指令中属于当次运行的具体值
已换成占位符，其余任务要求按原指令保留。

- **逐字保留**：审校范围、逐条 reason 要求、`<0.3` 采纳门槛、单轮复评、
  `pending` 保留原译、`literal` 原译取自 baseline 全段、`reviewed=false` 的诚实交付、
  金额上限与"不得刷分"约束、意见表三列与"误报不入表"规则。
- **参数化（非路径参数）**：原指令里这些位置是当次运行的具体值，本文件改用占位符：
  `{{LANE}}` 该 lane 的名字、`{{CHAPTERS}}` 该 lane 负责的章号、`{{PACKET_ITEMS}}` 该 lane
  的 packet item 数、`{{LANE_CAP_USD}}` 该 lane 的外部复评金额上限、`{{PRIORITY_IDS}}`
  最高优先的 target ID 列表。它们不是路径，也没有被归一化成路径。
- **路径归一化**：原指令中的本机绝对路径（项目根、临时目录、brief 路径）已替换为
  仓库相对名，见下表。路径替换与上面的参数占位都只改字面量，没有改写任务要求。
- **新增说明**：形如「注：」的句子是本次公开整理时补的边界说明，原文没有。
- **未收录**：会话系统提示、用户记忆、密钥/账户余额、工具调用流水、与本书校订无关的
  会话内容。运行参数的占位符见上文，保留的历史状态数字在对应说明中注明。

| 原文中的路径（运行期绝对路径） | 本仓库中的名字 |
| --- | --- |
| 项目运行根（整书工作根目录） | 仓库 `work/`（各子目录来源不同，见文末） |
| 章节目录 `<运行根>/chapters/NN/` | `work/chapters/NN/` |
| 改稿包目录 `<运行根>/packets/` | `work/packets/` |
| 证据目录 `<运行根>/reviews/NN/`、`rechecks/NN/`、`screening/` | 同名的 `work/` 子目录 |
| lane 绑定 brief（缓存目录中的绝对路径） | `prompts/revision-brief.md` |
| lane 临时目录（缓存目录中的绝对路径） | 运行者自选的仓库外临时目录 |
| 生产脚本 `workflow/judge.py`、`tools/export_opinions.py` | `workflow/judge.py`、`workflow/export_opinions.py` |

本表只改路径字面量；运行参数（lane 名、章号、packet 数、金额、优先 ID）见上一节的占位符。
表中不再保留原始绝对路径（其中含个人目录名）。

## 任务说明（路径已按上表替换，运行参数为占位符）

完整读取 `prompts/revision-brief.md`。你是 {{LANE}}，唯一负责章 {{CHAPTERS}}，共
{{PACKET_ITEMS}} 个 packet item；预算为整个 {{LANE}} 外部决策模型复评总计
{{LANE_CAP_USD}}（可均分每章，未用可在本 lane 内重分，但总和不超过该上限），不重跑初筛。
只写这些章的 `work/chapters/NN/zh.json`、`revision.json`、`work/reviews/NN/`、
`work/rechecks/NN/`；临时目录用仓库外自建路径。所有别章、原稿/基线/packet/正式
screening/book/tools 只读。另有若干非重叠改稿子代理和一个渲染子代理在并行，请不覆盖他们。

最高优先 {{PRIORITY_IDS}} 的当前官网上游数字修订，见 `work/context/review-priorities.json`
和原档案英文与官网快照；不是原译故障。若采纳 02-057，旧 note 曾说原英文笔误、保留六位数，
需同步为跟随当前官网；把 exact old/new note 另记 `opinions.json.note_changes`，正文 reason
解释，不能默默删注。02-031 官网本身 right left 矛盾，保留上下文自洽原译不猜转向；
03-060 终端/掌上终端仅文体缩略，不强制统一。原图 4-006 动画不归你，图型没有 text 时
structural_only 不算误译。

必须实际读每个 item、整章场景和必要跨章证据，逐条 reason 写 `review.json`，不套"全误报"
批量结论。没有具体错误不必改，等价译法不改。对确证候选只做一次三模型复评；每个模型
prob<.3 且语义证据成立才实际写工作稿，否则 pending 保留 baseline，绝不能为刷分二次润色。
意见表只收入具体 adopted/pending 候选，literal 原译来自 baseline 全段，非误报堆表。完成
一章写齐 3 个实文件，`workflow/export_opinions.py --validate-only` 显式章范围回读；不等其他
子代理，不跑 Calibre/截图/Git。到期若未完成所有项，交已完成章与准确剩余 ID，
`reviewed=false` 保留未完不冒称全数。返回每章 reviewed 个数、采纳/待决、具体证据/复评/
真实成本/校验退出码。

## 该说明依赖的输入（相对路径）与各自的来源

- `work/chapters/NN/{source.json,zh.json,glossary.json,alignment.json}`：对齐后的英文块与
  当前中译；由 `book/prepare_inputs.py` 生成，原始快照不可变。
- `work/baseline/NN/zh.json`：原译基线，意见表 `原翻译` 列的来源；也由
  `book/prepare_inputs.py` 生成（带 provenance 的投影）。
- `work/packets/chapter-NN.json`：由 `workflow/packet.py` 生成的紧凑 JSON 改稿包。
- `work/screening/chapter-NN.json` + 同名 `.records.jsonl`：由 `workflow/judge.py` 的真实调用产出。
- `work/reviews/NN/`、`work/rechecks/NN/`：**不由脚本自动生成**，须按本说明的审校与复评步骤
  由改稿 lane 逐步写入。
- `work/context/*`：整书层面的人工证据笔记（优先级、术语、已知源版本变化），
  由操作者/代理提供，仓库脚本不生成、也不附带。

注：本仓库不附带任何 `work/` 运行结果。`prepare_inputs.py` 只生成 `chapters/` 与
`baseline/`；`packets/`、`screening/` 来自对应脚本；`reviews/`、`rechecks/` 需要实际审校与
复评步骤；`work/context/` 是操作者/代理提供的上下文。首次运行不会自动得到整套 `work/`。
