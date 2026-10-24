# 改稿 lane 绑定 brief（归一化）

来源：当时实际使用的 `revision-brief.md`（ladder 各 lane 读取的绑定文件）。
**归一化**：项目绝对路径替换为仓库相对名；lane 临时目录改为"仓库外自建"；删除与本机
环境相关的说明。任务要求、金额门槛、采纳条件、schema 与"诚实交付"条款逐字保留。
文中"注："为本次整理补充的边界说明。

文中运行状态（32 章已对齐、各章疑点数、pilot 三章已并入、29 章初筛 11463 个成功单元、
1221 个 routed item 等）是**该次运行的历史状态，不是当前仓库状态**：仓库不附带 `work/`，
重跑得到的是运行者自己的数字。金额同理，是当次门槛而非默认许可。

---

用户授权完成全部 32 章与完整中英 EPUB，以及恰好三列（原翻译 / 推荐翻译 / 原因）的
Excel 改动表。所有 workflow 数据保持 JSON；已明确拒绝按模型切换 TOON 的"整体省 20%"
方案。改稿子代理使用 DeepSeek V4.1 Flash（OpenRouter）。**不要**更改模型/provider 设置，
也不要引入另一个改写 API。

```
ROOT     = work/            （prepare_inputs.py 生成：chapters/、baseline/）
PACKETS  = work/packets/    （compact JSON，packet.py 产出）
SCREEN   = work/screening/  （judge.py 的真实调用证据）
REVIEWS  = work/reviews/NN/ （每章 opinions.json + review.json）
RECHECKS = work/rechecks/NN/
```

## 状态（历史运行状态，非当前仓库状态）

32 章输入已对齐，4310 个有序块，56 图 / 112 SVG。`baseline/NN/zh.json` 保留归档的
chapter/segments/notes/terms，但是带 provenance 的投影，不是归档 JSON 的逐字节副本。
归档原始数据在 `inputs/` 下不可变。除已验收的 pilot 修订外，当前中文与 baseline 一致。

pilot 三章已审阅并写入 `work/reviews/01|05|09/opinions.json`；不要重复这些审阅或付费调用。
其余 29 章初筛返回 11463 个成功唯一单元。所有模型可见目标都有完整 roster，除三个经明确
批准的构造语言保留例外（26-020/022/026）：OpenAI 对它们拒答，去引号标记后 EN/ZH 机械相同，
STYLE 要求保留构造语言原文。**不要**重问、修改或声称它们被模型判过。

29 个紧凑 JSON packet 共 1221 个 routed item。概率只用于路由，永不证明改动。要读你负责章节
的英文+中文与相关 terms/notes 以取得场景上下文，不要只从目标窗口判断。被否决的 pilot
"tea" 改动就是反例：后文明确支持它。

## 你有界的工作

只写你负责章的 `work/chapters/NN/zh.json`、`revision.json`、`work/reviews/NN/`、
`work/rechecks/NN/`。其他章、baseline、inputs、packets、screening、book、tools 只读。
不跑 Calibre、不截图、不 Git。有独立渲染负责人在修源文本遗漏/样式；不要改 HTML 渲染代码
或 SVG 资产（除非另行明确指派）。

对 compact packet 里**每一个** routed item 检查并记录处置与具体理由：
`false_positive` / `confirmed_error` / `pending` / `literal_preservation`。上下文有歧义时
读整段/整场景。完整的章审阅意味着所有 packet ID 在 `work/reviews/NN/review.json` 中恰好
出现一次且有证据，不只是你决定改的那些。读到场景时若发现此前未标记的具体错误，可一并
修正，并把这些 ID 明确列为 added-review 目标；不要为增加改动数而虚构风格改动。

聚焦实质语义错误、漏译/增译、actor/referent、否定/条件/因果、数字/单位/公式、明显错误的
技术术语。保留正确的原译与语气。不要仅为统一而统一无害措辞（巴士/自动巴士、终端/掌上终端）。
除有具体理由的改动外，保留所有 ID/顺序/notes/terms/markup/protected span。构造语言文本保持
源样；网站/源文错误不要悄悄修好。

`work/context/concise-review-notes.md` 与 `review-priorities.json` 提供整书证据。重要上游
变化：02-057 的六位数字→两位；04-054 的旧 17/33→当前 16/31；29-104 Telten→Thaldur，有
同章 29-030 佐证。这些是源版本对齐，**不是**原译者错误。02-031 含作者自相矛盾的
"right left"，保留上下文支持的原译而非猜方向。19-033 的罗马字母是有意的源文图注，当前
渲染器漏掉；由渲染负责人修，不归翻译 lane。27 章隐藏 SVG 元数据另有明确指令；不要把它当作
可见标签漏译，也不要现在改资产结构。

## 提出、复评一次、保守采纳

改稿前先保存提案，含 literal 原文/最终整段、英文证据与理由。仅对具体有据的改动，在你的
临时目录做候选章副本，保留 source/glossary/alignment/assets，只替换拟改中文。仅对实际改动的
target ID 用 `workflow/judge.py` 对候选跑复评，三个既有固定 OpenRouter 模型，原生 JSON。
所有付费证据存到 `work/rechecks/NN/` 下的新唯一文件名，绝不覆盖/重发成功响应。
读 `workflow/judge.py --help` 与用法说明，用真实 CLI（`--chapter-dir`、`--output`、
`--execute`、`--budget-usd`、`--target-ids`）。不要另造 chat 评分器。

整个 lane 的外部复评上限是 USD0.12（不是每章），拆成互不重叠的章上限，其和 ≤0.12。
复评独立于子代理推理费用。缺失 cost 记为 unknown/reserved。不要循环优化分数，不要对内容
拒答重问，只做有界传输重试。复评只针对实际改动，不是所有 false positive。

只有在证据支持、protected markup 得以保留、且全部三个模型的修正文本 substantive-error
概率均 <0.3 时，才写入实际工作段。该门槛是保守的 workflow 门禁，不是准确率证明。若候选
仍有分歧/未知/拒答，实际工作保留原文，并把具体候选记为 `pending`。绝不静默应用 pending
建议。每个 target 只做一轮改动/复评；不要为压低分数反复调多个备选。

## 意见/修订 schema

`work/reviews/NN/opinions.json`：

```json
{"chapter": N, "reviewed": true,
 "items": [{"id": "NN-SSS", "status": "adopted|pending",
            "zh_original": "literal BASELINE whole paragraph",
            "zh_recommended": "literal actual recommended paragraph",
            "reason": "specific Chinese rationale, explain source-version changes honestly",
            "evidence_en": "specific English quotation"}]}
```

每段最终只有一条正文意见；合并局部修正。不要相同 before/after 行，不要被否决的
false positive 进改动表。更细的被否决/pending 条目留在 `review.json`。部分完成时把
`reviewed` 设为 `false` 并报告确切缺失 ID，而不是标成整章完成。

`revision.json` 必须区分 `reviewed=true/false` 与 `revised=(adopted count>0)`、计数、
`edit_list`（exact before/after/evidence）、adopted/pending ID、复评文件引用与实际成本，
全部对应最终工作文本。不要声称每句都人工重译：审阅范围是完整初筛加上你对每个 routed item
的检查与场景核对。工作改动必须与 adopted 行对得上；默认保留原 notes/terms。

02-057 特例：若按当前两位数字源对齐该段，其旧译注描述旧笔误可能过期。把 exact old/new
note 文本存进 `opinions.json` 的 `note_changes` 数组，并在该段 reason 里解释注文对齐。
不要静默删注，也不要把它伪装成原译者错误；父代理会在最终表加入 literal note 行。其余
随手的注文清理不做。

用 `workflow/export_opinions.py --validate-only --chapters <你的章> ` 并显式给出
baseline/chapters/reviews 根目录，测试 adopted/pending 的 literal 不变量。不要为了让自己
的行通过而改导出器。保持 baseline 对象不可变；若复评使提案仍为 pending，该段的实际工作
文本必须仍等于 baseline。

## 期限与诚实交接

每 lane 约 20 分钟；看实际墙钟，不要编造时间戳。一次完成一章，写完该章真实
review/opinions/revision 再进入下一章。若预算用尽，返回已完成章与明确剩余 ID/章，作为
部分交付；不要伪造"全误报"清扫，也不要为显得详尽而输出过长结果。不要等其他子代理或整书
渲染。返回实际 reviewed ID 数、adopted/pending 候选、具体英文证据、复评身份/成本/原始文件
引用、导出校验退出码，以及确切剩余工作。父代理拥有最终批准、整书构建、总账与交付。

注：本仓库只提供脚本与提示词，不附带上述 `work/` 产物。其中只有 `chapters/`、`baseline/`
由 `prepare_inputs.py` 生成，`packets/`、`screening/` 由对应脚本产出，`reviews/`、`rechecks/`
来自审校与复评步骤，`work/context/` 需由运行者自行提供。金额数字是当次运行的实际门槛，
重新运行时应由运行者自行确认并显式传入。
