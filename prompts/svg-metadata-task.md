# 图表无障碍元数据补审任务（实际额外指令）

本文件是第 27 章三幅图中 5 段未译 `<title>` / `<desc>` 的**实际额外任务指令**，随改稿 lane
一起下发。来源：本机 Hermes 会话的 `delegate_task` 参数（lane L6 的 `context`，标题为
「额外明确任务：27章SVG accessibility metadata」；会话 ID 不公开）。
**归一化**：项目绝对路径替换为仓库相对名（见下表），lane 名保留为占位符，其余要求逐字保留。
"注："为本次整理补的边界说明。

| 原文路径 | 本仓库中的名字 |
| --- | --- |
| `FULL/chapters/27/assets/ch27-fig0{1,2,3}-zh.svg` | `work/chapters/27/assets/ch27-fig0{1,2,3}-zh.svg` |
| `inputs/archive/Snowmoon-source/assets/ch27-fig0N.svg` | `inputs/upstream/assets/ch27-fig0N.svg` |
| `baseline/27/assets/*-zh.svg` | `work/baseline/27/assets/*-zh.svg` |
| `rechecks/27/svg-metadata-*` | `work/rechecks/27/svg-metadata-*` |
| `context/concise-review-notes.md` | `work/context/concise-review-notes.md` |

## 逐字指令（路径已替换）

【额外明确任务：27章SVG accessibility metadata】共有 5 个未译 title/desc：27-118 fig01
title+desc；27-120 fig02 desc（title 已译，不要再改）；27-125 fig03 title+desc。候选依据在
`work/context/concise-review-notes.md` 第 65 行起，但你必须读实际 SVG title/desc 英语与其
可见中英文标签/百分比，不能抄候选而不核数字。只替换这 5 个根 metadata text 原始 XML 字节
片段，不整树重序列化、不动任何可见 text/tspan/坐标/defs/样式/viewBox/root margin。中文 SVG
路径 `work/chapters/27/assets/ch27-fig0{1,2,3}-zh.svg`。原始 immutable baseline 以
`inputs/upstream/assets/ch27-fig0N.svg` 为准；另一个导出子代理会 ADD ONLY 对应
`work/baseline/27/assets/*-zh.svg` 原字节副本，你不能写 baseline 或等它。

普通 `judge.py` 的 figure 分支只读 text/tspan，不会看到 hidden metadata，因此不能对完全没含
title/desc 的 state 复评后声称验证了这 5 串！可把每幅图候选 title/desc 作为一个 metadata
target（最多 3 target×3 model=9 calls），复用 `judge.py` 实际 http、live_identity、
parse_answer、identity_ok 等 helper，原生 JSON state 包含 target_id、每个 element 的 English
与 candidateChinese、可见 label pairs/数字作术语背景；question 仍 typed substantive_error，
明确只判断该图给定无障碍中译的错漏/数值，三模型 same state/questions+pins，保存原始
request/raw response/cost 在 `work/rechecks/27/svg-metadata-*`。不是新 chat scorer，不泄漏
初评概率，也不对等价 correct 字串问第二轮。metadata 预算 ≤0.02 且全 lane ≤0.12。3 prob
均 <.3 且语义/数字无误才应用各图的候选 metadata；否则该图 meta 保持原文，意见 pending。
不可就此新增可见标签改译/改变图形。metadata 不需要把整幅图栅格化，也不做 reader/截图。

metadata opinions 每一个变化 element 一行，沿用现有 items 附加字段：

```json
{"id": "27-118", "scope": "svg_metadata", "asset": "assets/ch27-fig01-zh.svg",
 "element": "title|desc", "status": "adopted|pending",
 "zh_original": "原 metadata 完整字面值", "zh_recommended": "推荐字面值",
 "reason": "中文具体依据", "evidence_en": "actual English metadata"}
```

同 baseID 两个 element 不是重复，导出器会最小扩展该 scope；不改 tools，也不等工具，多做你
自己的严格值/数值/图形字节不变检查。原节点 id 属性必须保留。`review.json` 每个 packet ID 仍
只一项，其元数据变化可在该 figure 项描述，并在 `revision.json` 拆
`body_edits`/`metadata_edits`，final revised 是任意采纳变化 >0，不谎称正文改了 5 段。

注：该 lane 结束时的实际诉求是"3 次调用以内在 ≤0.02 美元内给出 5 段字串的处置"，工具实现在
`workflow/recheck_svg_meta.py`；本文件里的 `META_INSTRUCTIONS` / `META_CRITERIA` 即该次调用
`request.questions` 的真实逐字内容，不在本 md 中重复一份以免漂移。实际提交的 3 个 target
及其 5 个 element 字面值见 `prompts/svg-metadata-targets.json`。

## 与可见标签判断的分工

- 可见 `<text>`/`<tspan>` 标签：由 `judge.py` 的 figure 分支（`FIGURE_INSTRUCTIONS`）判断，
  状态里带 ordered label pairs。
- 隐藏 `<title>`/`<desc>`：只能由上面这条独立指令判断，状态里带 `elements` 与可见标签背景。
- 两者共享同一 `substantive_error` question id、同一 provider pin 规则、同一 `<0.3` 采纳门槛。
