---
name: kdb-video-post-production
description: 给定视频、音频或已有字幕，完成本地转写、字幕精校、断句，并按视频类型（单口/访谈）分流产出：高光选取、文章（单口外发稿或访谈伴读稿）、标题（频道多轮标题，或经 xhs-cover-title 产出小红书封面+标题）、YouTube description、访谈封面图。转写用 mlx-qwen3-asr 1.7B，精校用 Codex CLI，内容生成用 Claude Code Fable 5，不可用时降级 Codex gpt-5.5。
---

# KDB 视频后期生产 v2

一段原始录制 → 五类资产：**①精校字幕 ②高光 ③文章 ④标题 ⑤YouTube description**（+ 访谈封面图一个可选件）。

两条铁律贯穿全程：
- **确定性优先**：有脚本的环节跑脚本，agent 只做判型、路由、把关和脚本兜不住的部分，不徒手重做脚本已覆盖的工作。
- **文件为介质**：每一步读文件、写文件，下游只依赖上游的产物文件，不依赖会话内记忆。

内容生成引擎：默认 Claude Code `claude-fable-5`（脚本内置）；Fable 5 不可用、超时或没写出文件时，`claude_cli.py` 自动降级到 Codex `gpt-5.5`，产物文件约定不变，降级会在脚本输出里明示。

## 第 0 步：判型

先判断这是**单口（口播）**还是**访谈**，这决定后面所有分流：

| | 单口 | 访谈 |
|---|---|---|
| 文章形态 | 外发独立文章，像主播状态最好时自己写的 | 视频伴读/观看地图，如实还原对话 |
| 文章去处 | Twitter / LinkedIn / Superlinear 站点 | 随视频发布，给观众当观看指引 |
| 高光 | 3-4 段核心论断 | 6-8 段，覆盖嘉宾不同侧面 |
| 封面图 | 默认不做 | 默认做三种带字比例 |

判型依据：发言人数量、对话结构。拿不准直接问用户。实现脚本内部已按此分流，skill 层判型是为了确定交付物组合、并和用户对齐预期。

## 三个区

- **交付区** = 视频同目录：只放最终交付文件（见「交付物」表）。
- **工作区** = `<video>_process/`：所有中间产物（raw ASR、corrected 字幕、轮次草稿）。过程文件不当交付，也不覆盖——原始转写稿永远保留。
- **资料区**（只读引用，更新只走「持续校准」）：
  - 实现仓库 `/Users/sunyuzheng/Desktop/AI/content/kdb-video-pipeline/`：全部脚本 + `data/`（`guideline_kedaibiao.md` 编辑标准、`top_titles.txt` 频道高播标题基准、`channel_vocab.json` 术语库）
  - `/Users/sunyuzheng/Desktop/AI/skills/xhs-cover-title/`：小红书封面+标题手艺（含 hot-words、examples）

## 第 1 步：精校字幕（质量地基）

后面一切内容都长在这份字幕上，钩子要捞「原文原话」、引用要准，全靠它。

- 本地转写必须优先用 `/opt/homebrew/bin/mlx-qwen3-asr` CLI，模型固定 `Qwen/Qwen3-ASR-1.7B`，带 `--verbose` 保留可见进度。不用静默 Python API（长视频没有进度，容易误判卡住）。CLI 不可用才降级并向用户说明。
- 转写前收集人名、品牌、产品、工具名、课程名，作为 `--seeds` 注入——这份名单同时是后面小红书路线的「主人公背景」原料，别丢。
- 字幕精校默认用 Codex CLI，重点修术语、数字、日期、实体和反复识别错误；之后重新断句，适合上屏和剪辑。

```bash
cd /Users/sunyuzheng/Desktop/AI/content/kdb-video-pipeline

# 完整链路（含高光/文章/标题）
caffeinate -i venv/bin/python tools/process_video.py /path/to/video.mp4 --seeds 术语1 术语2

# 只要字幕
caffeinate -i venv/bin/python tools/process_video.py /path/to/video.mp4 --seeds 术语1 术语2 --skip-highlights --skip-article --skip-titles
```

## 第 2 步：高光（独立模块，观众感）

**高光不是文章的选段。** 视频观众的注意力逻辑和文章读者不同——什么能让人停下、什么能让人跳转，要按观看行为判断，所以高光单独成模块，先于文章跑，产物供两个下游使用：文章的跳转地图 + 标题/小红书的金句原料。

- 入口：`venv/bin/python tools/generate_highlights.py /path/to/video.final.srt`。脚本会优先采用 SRT 末尾编辑者亲选的高光段（权威来源），没有才全文扫描。
- 每段高光必须有：可跳转时间戳、原话引用、cognitive gap、为什么值得跳转观看、在整期主线中的位置；访谈另加 vantage point。
- 不只选戏剧性片段，也要选支撑主线的关键解释、人物选择和反常识判断。

## 第 3 步：文章（按型分流）

入口：`venv/bin/python tools/generate_article.py /path/to/video.final.srt`。脚本内置了两种形态的写作规范和 `substance-writing-review` 的判断框架（识别有力量的 insight → 放大 → 清理），agent 不重写 prompt，只对产物把关。

**文章参考高光**：脚本会自动读取同目录的 `highlights.md`，作为选题、时间戳和原话线索——所以必须先跑高光再跑文章（`process_video.py` 已按此顺序；单独跑脚本时自己保证顺序）。

- **单口稿**：像主播本人状态最好时写出的版本——更清楚、更锋利，但不是另一个人写的。不套模板、不改成营销号/AI 总结/咨询报告。发布目标是 Twitter/LinkedIn/Superlinear；如需平台变体（thread 拆分、英文版），在文章定稿后按用户要求另做，不默认生成。
- **访谈稿**：视频伴读，不是摘要。观看地图 + 高光段落（时间戳、原话、解读、启发）+ 整期核心价值。第一人称但只客观陈述对话、不加内心戏；严格区分主持人和嘉宾的判断；优先完整段落，少并列换行，少「不是……而是……」AI 腔。

## 第 4 步：标题（钩子工程，两条路线）

两条路线目标相同：**给人一个点进来的理由**。机制是悬念链——开头种下一个悬念，解决它的同时抛出下一个，一环扣一环；观众撑过第一分钟基本就留下了，所以标题、封面、开头高光的排布值得花全流程里最多的脑筋。

**路线 A · 频道标题**（B站/YouTube/视频号长标题）：

```bash
venv/bin/python tools/generate_titles.py /path/to/video.article.md
```

三轮工作流（内部已用 Fable 5），以 `guideline_kedaibiao.md` + `top_titles.txt` 真实高播标题为基准。**agent 不徒手写频道标题**——徒手写绕过了外部基准。

**路线 B · 小红书封面+标题**：调用 `xhs-cover-title` skill，产出 `<video>.xhs.md`（文稿概述 + 金句清单 + 3-5 套封面+标题方案）。喂料方式：
- 内容原文 = `.final.srt`（必须精校稿——xhs 红线「钩子必须原文原话」依赖字幕准确）
- 主人公背景 = 第 1 步收集的 seeds 里的身份信息（xhs 要求身份词带具体数字）
- 金句原料 = `highlights.md` 里的原话引用（高光环节已经替它捞了一半金句）

**两条路线共用的分工原则**：标题不复述高光本身，标题描述高光背后更大的问题——标题让人想点进来，高光让人想看完。

## 第 5 步：封面图（访谈默认）

封面默认带字，文字优先来自 `.xhs.md` 或 `.titles.md` 里的封面建议，不临场硬编。优先用用户提供的现场照、视频截图或关键帧作 reference；如果从视频抽帧，必须检查人物没有被裁掉、表情清晰、文字没有压脸。

生成前必须先读 `skill/references/cover-style-guide.md`，并对照 `skill/references/封面案例/` 的实际图片。目标风格是高点击短视频封面：真实截图作底，黄白黑巨字直接压画面，黑描边/投影保证缩略图可读；只允许硬边黑条/黄条承载小信息。不要做半透明玻璃卡片、圆角海报卡、渐变装饰块或干净留白海报。

默认交付三种比例：
- `<video>.cover-16x9.png`：16:9，用于 YouTube；`<video>.cover.png` 可保持为同一张 16:9 主封面，方便旧流程取用。
- `<video>.cover-3x4.png`：3:4，用于小红书；推荐两张截图上下叠放，中间用黄白巨字穿插，不要只做小字信息带。
- `<video>.cover-4x3.png`：4:3，用于 B站、抖音、视频号。

封面文字分工：
- 主封面文案：短、狠、可一眼读完，优先用小红书路线 B 选定的封面文案。
- 副文案：一句内容承诺或原文金句，不要复述标题。
- 不额外加 logo 或水印；如果源视频截图已经烧进 logo，不要为了“去水印”粗暴涂抹到画面变脏，优先换帧，或用案例风格里的黑条/黄条自然覆盖。

如果用 **Codex CLI 侧的 `imagegen` skill** 做图（不是 Claude skill，产物落在 `$CODEX_HOME/generated_images/`），最终图仍必须拷贝到视频同目录，不能只留在 generated_images。

## 第 6 步：YouTube description（发布说明）

入口：`venv/bin/python tools/generate_youtube_description.py /path/to/video.final.srt`，产出 `<video>.youtube-description.txt`。

写法要求：
- 开宗明义：这期给什么观众带来什么新信息，为什么值得看。
- 直接、有条理、平实，不写营销号腔、公众号腔或夸张承诺。
- 章节必须适合 YouTube，时间戳用 `mm:ss`，从 `00:00` 开始。
- 章节数不要太多，宁可 8-10 个关键节点，也不要逐小节铺满。
- 时间戳必须根据 `.final.srt` 的真实时间判断，不能编。

## 交付物

| 文件 | 何时生成 | 由谁生成 |
|---|---|---|
| `<video>.final.srt` | 总是 | 脚本流水线 |
| `<video>_process/*.qwen.srt` `*.corrected.srt` | 总是（工作区） | 脚本流水线 |
| `<video>.highlights.md` | 视频/访谈发布 | generate_highlights.py |
| `<video>.article.md` | 发布文章时 | generate_article.py |
| `<video>.titles.md` | 频道发布 | generate_titles.py |
| `<video>.youtube-description.txt` | YouTube 发布 | generate_youtube_description.py |
| `<video>.xhs.md` | 小红书发布 | xhs-cover-title skill |
| `<video>.cover-16x9.png` / `<video>.cover.png` | 访谈发布默认 | 截图/设计或 imagegen |
| `<video>.cover-3x4.png` | 小红书发布默认 | 截图/设计或 imagegen |
| `<video>.cover-4x3.png` | B站/抖音/视频号发布默认 | 截图/设计或 imagegen |

不为形式完整强行生成用户没要的产物；用户明确说不要标题，就不生成 `.titles.md`。

## 验收标准

- 本地 ASR 输出里能看到 `mlx-qwen3-asr`、`Qwen/Qwen3-ASR-1.7B` 和进度信息。
- `.qwen.srt`、`.corrected.srt` 在工作区；`.final.srt` 和各交付物在交付区。
- 专有名词、数字、日期、工具名全链路一致；字幕断句适合上屏，无 ASR 原始长块。
- 所有下游内容（高光/文章/标题/小红书）都基于 `.final.srt`，不直接依赖 raw ASR。
- 文章形态与判型一致：单口=独立外发稿，访谈=伴读稿；嘉宾判断没有被写成主播判断。
- `.xhs.md` 通过 xhs-cover-title 自带的自检清单（≤20 字、零 emoji、封面标题不重复等）。
- `.youtube-description.txt` 可直接复制到 YouTube：介绍平实、有钩子；章节从 `00:00` 开始；时间戳为 `mm:ss` 且对应字幕真实段落。
- 封面图三比例齐全：16:9、3:4、4:3；默认带字；人物不被裁掉；文字不压脸、不出界；已保存到视频同目录。
- 不泄露 token、cookie 或私有数据。

## 持续校准

- 发布后的高播标题 → 追加进资料区 `top_titles.txt`，保持路线 A 的外部基准不过时。
- 小红书发布后实际采用的封面标题和数据 → 走 xhs-cover-title 自己的校准机制（examples.md 追加区）。
- 用户对高光/文章产物的修改 → 提炼成规则改进 `guideline_kedaibiao.md` 或脚本 prompt（资料区是规则的权威出处，本文件只描述流程，不复述规则全文）。
- 反复识别错误的术语：**频道级复用的**（多期会出现）才进 `channel_vocab.json`；单期实体（嘉宾公司名、产品名）走当期 `--seeds`，不进词库。
