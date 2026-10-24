# prompts/：实际使用的提示词与任务说明

Decisions API 请求通过 `questions` 中的 `instructions`、`criteria` 和 `state` 中的上下文传递判据。
分章修订任务说明与绑定 brief 另存于下表。

| 文件 | 它是什么 | 谁读取 |
| --- | --- | --- |
| `revision-task.md` | 下发给改稿子代理的分章任务说明（据实际任务整理的参数化模板：路径归一化，lane 名/章号/packet 数/预算/优先 ID 为占位符） | 改稿子代理（人/agent） |
| `revision-brief.md` | lane 级绑定 brief：范围、预算、`<0.3` 采纳门槛、schema | 改稿子代理 |
| `svg-metadata-task.md` | 第 27 章隐藏 `<title>`/`<desc>` 的额外指令（据实际任务整理，路径归一化，lane 名为占位符） | 改稿子代理 |
| `svg-metadata-targets.json` | 该次元数据复评实际提交的 3 个 target / 5 段字串 | `workflow/recheck_svg_meta.py --targets` |

## 译图状态（准备阶段判一次，字体与出图共用同一结果）

顺序是固定的，不要提前：`prepare_inputs.py` 先把原图与译图**两资产原样字节复制**（此步不碰任何字体），
再让该章已采纳修订（含隐藏 `<title>`/`<desc>` 元数据）全部落地，然后**只判一次**：按图位的最终实际文字
得出 `has_translation`——可见 `<text>`/`<tspan>` 与隐藏 `<title>`/`<desc>` 有文字变化即为 `True`；
只差 `font-family`、序列化或数字/杰戈语原样保留都不算译文。同一次结果派两处消费：

1. **字体**：`True` 时按同一份节点级计划把未翻译 `<text>` 与共享 `<g>` 恢复原字体，只有真正翻译过的
   `<text>` 保留既有中文字体栈；几何、字号、颜色、动画、defs、id 都不动。`False` 时不处理字体，直接写回
   原图原始字节。
2. **出图**：同一个布尔写进 `source.json` 的两处——`figures[]` 条目与 `segments[].figure`——值恒等；
   `render.py` 的 `render_figure` 只读它，不读文件、不比较字节、不重复判定，因此不会出现重复图。

- `False`：`render_figure` 只输出英文原图一张 `<img>`。
- `True`：保留既有译文与已采纳元数据（额外改动仅字体），原、译各一张。
- 本轮之前准备的输入没有该字段，沿用既有两图行为；准备器对每个图位必写布尔。

图外题字 `cap_en`/`cap_zh` 两份独立。没有模型字段，`edits.json` 仍只有 `changes` 与
`chapter_state`，翻译流程不变。

## 线缆提示词（在代码里，不在这里复制）

`request` 字段就是实际发送的 HTTP body，因此提示词文本作为常量存在于构造它的模块中，避免
出现第二份会漂移的副本：

- 正文/可见图标签判断：`workflow/judge.py` 的 `INSTRUCTIONS`、`FIGURE_INSTRUCTIONS`、
  `CRITERIA`，状态构造见 `build_payload`（+/-2 窗口、`glossary_for`、`notes_for`）。
- 隐藏 SVG 元数据判断：`workflow/recheck_svg_meta.py` 的 `META_INSTRUCTIONS`、
  `META_CRITERIA`，状态构造见其 `build_payload`。
- 改稿包内的规约文字（"概率只是路由信号"等）：`workflow/packet.py` 的 `rules`。

`tests/test_workflow_judge.py` 断言这三处文本与状态构造确实进入 `request`，并断言同一 target
在三个模型下的 `state`+`questions` 指纹相同（只有 `model`/`provider` 不同）。

## 蒸馏与保留原则

- 保留：判据文句、目标/上下文构造、`<0.3` 采纳门槛、单轮复评、`pending` 保留原译、
  概率不得直接等于采纳、"不要为降低分数重写"。
- 删除：本机绝对路径（已替换为仓库相对名）、lane 临时目录、Hermes 运行时/账户/密钥说明、
  会话系统提示与用户记忆、与本书校订无关的会话内容。
- 数字（各章疑点数、USD0.12/0.02 等）是当次运行的真实约束。重跑时须自行确认后再显式传入
  命令行（`--budget-usd`、`--allowance-usd`），不要把历史金额当成默认许可。

## 来源与可核性

来源为本次制作时实际派发的 `delegate_task.goal/context` 与绑定的 `revision-brief.md`。
各文件说明了原始指令的保留范围，以及路径和运行参数的替换方式。
