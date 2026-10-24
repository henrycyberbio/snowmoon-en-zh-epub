# Snowmoon bilingual EPUB

English     [简体中文](README.md)

Vitalik has asked for the production pipeline behind derivative editions of *Snowmoon* to be published openly. This repository does that for the bilingual edition. It holds the prompts, code and source material the edition was built from, so others can reproduce it, change it or carry the translation further.

The edition covers all 32 chapters, with English passages followed by their Chinese translations. It preserves the original figures, dialogue colors and device panels.

![Snipaste](Snipaste.png)

## Project structure

```text
snowmoon-en-zh-epub/
├── README.en.md
├── README.md
├── COPYING
├── pyproject.toml
├── uv.lock
├── inputs/                   # Fixed English snapshot, Chinese base text, SVGs and source list
├── edits.json                # Accepted revisions and the review record they need
├── release/                  # The finished edition and the review-opinions workbook
│   ├── snowmoon-bilingual-full-ch01-32.epub
│   └── Snowmoon-翻译修改意见.xlsx
├── prompts/                  # Decision prompts and revision task briefs
├── workflow/                 # Decision stage, revision packets, recheck and opinions export
└── book/
    ├── prepare_inputs.py     # Prepare chapters and apply revisions
    ├── build_full.py         # Assemble the whole book, convert and read back
    ├── render.py             # Render bilingual paragraphs, tables and panels
    ├── style_base.css
    └── checks/image_layout.py
```

## Production workflow

Install uv and Calibre, confirm `ebook-convert` is on PATH, then install the pinned Python dependencies. This run used Calibre 9.9.0 and Beautiful Soup 4.15.0.

```bash
uv sync --frozen
```

### 1. Prepare chapters

Align the fixed English snapshot with the Chinese base text at paragraph level, and bring together terminology, translator notes and the matching figures. The command also applies the body, note and SVG accessibility-text revisions already accepted in `edits.json`. Working files go to `work/` and the original inputs stay unchanged.

```bash
uv run python book/prepare_inputs.py
```

### 2. Revision

To rebuild the existing edition, skip this section and reuse the fixed revisions in the repository. A new review round uses the scripts in `workflow/` and the task briefs in `prompts/`. It has three steps.

1. **Decision stage**: the English text, the current Chinese, the neighboring paragraphs and terminology context go to three decision models over OpenRouter Decisions. Each judges whether this piece of Chinese contains a substantive semantic error that needs correcting. The models return probability judgments that locate suspected mistranslations, omissions, referent errors and number errors. A probability is a pointer to a suspect passage, not a quality score for the translation. Items where the models disagree also go to the check below.
2. **Proposed revisions**: DeepSeek checks each flagged point against its context and reads the relevant chapter where needed, separating differences between source versions from genuine mistranslation. It proposes the smallest necessary edit only where the evidence is sufficient, states its grounds and keeps the author's and the translator's voice. Points it cannot settle are left for review.
3. **Candidate recheck and adoption**: each concrete candidate goes back through the decision stage, which rechecks it once against the same question. Figure labels and the hidden SVG title/desc use their own corresponding criteria. The main agent adopts the edit, keeps the current Chinese or holds it for review, drawing on the semantic evidence and the recheck results.

Accepted revisions to body text, translator notes and figure accessibility text are written to `edits.json` together with their reasons. After an update, rerun the preparation command and then build the EPUB. A new review round needs OpenRouter credentials and a call budget, and `.env.example` shows the credential setup.

Review opinions from a new round go to `work/reviews/NN/opinions.json` and are exported as a workbook by `workflow/export_opinions.py`. Original SVG accessibility text comes from `inputs/upstream/assets`, mapped through `figures[].file` and `figures[].zh` in that chapter's `source.json`. The archive root defaults to `inputs/upstream`, and `--archive` points it at another directory.

### 3. Build the bilingual EPUB

The renderer lays out the Chinese and English paragraphs together with the figures. Calibre converts the result to EPUB, and the build reads it back to check chapter order, text, resource references and metadata. Output goes to `output/`.

```bash
uv run python book/build_full.py --chapters-root work/chapters --output output
```

## License and acknowledgments

The source work, the Chinese base text, and this repository's code and revisions are provided under GNU GPLv3. The full license text is in `COPYING`.[^1][^2]

### Source work and Chinese translation

Thanks to **Vitalik Buterin** for writing *Snowmoon* and releasing it openly. The author states that he wrote the text himself, with Kimi K3 and Qwen 3.8 Flash Next assisting with proofreading the original and building the website.[^1]

Thanks also to **XiaoHai67890/snowmoon-zh** for the complete Chinese translation, its figures and the editable source files, with the original translation and proofreading credited to **Codex (AI)**.[^2] This project revises that simplified-Chinese edition, v1.1, where the evidence calls for it and sets it bilingually. English comes from the website snapshot of 2026-10-23, and the Chinese is fixed at commit `55f9a20b383ee124eb3eeffd137844d4b45990d6`. Input sources and hashes are listed in `inputs/manifest.json`. The upstream source-package checksum list is provided separately.[^3]

### Models used in this project

| Model | Role |
| --- | --- |
| GPT-6.1 Sol | Planning, review, evidence adjudication and integration |
| DeepSeek V4.1 Flash | Subagent checking, revision and code implementation |
| TypeSafe Jev 1.14 | Decision stage: judging current Chinese and rechecking candidates |
| OpenAI GPT-6 Luna Decisions | Decision stage: judging current Chinese and rechecking candidates |
| Perplexity Decider Flash v1.2 | Decision stage: judging current Chinese and rechecking candidates |

### Tools

- **Hermes Agent, OpenRouter**: task orchestration and execution, model calls.
- **Python, uv, Beautiful Soup, Calibre**: running the scripts, dependency management, HTML processing and EPUB conversion.
- **PyMuPDF, openpyxl, a Chromium-based browser**: extracting the source package embedded in the PDF, exporting the review workbook, checking SVG layout.

### Sources

[^1]: https://vitalik.eth.limo/snowmoon

[^2]: https://github.com/XiaoHai67890/snowmoon-zh/

[^3]: https://raw.githubusercontent.com/XiaoHai67890/snowmoon-zh/55f9a20b383ee124eb3eeffd137844d4b45990d6/SHA256SUMS
