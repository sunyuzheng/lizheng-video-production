# 课代表立正 · 视频后期生产（kdb-video-post-production）

一段原始录制 → 五类内容资产：**精校字幕、高光、文章、标题、YouTube description**。本仓库同时包含 **skill**（工作流定义）和**实现**（流水线代码），2026-06-12 起合并为单一仓库，skill 和代码在同一个 commit 里同步演化。

```
.
├── skill/SKILL.md        ← 工作流权威定义：判型、三区、流水线、交付物、验收、持续校准
├── skill/references/     ← 来源与布局说明
├── tools/                ← 流水线脚本（转写、精校、断句、高光、文章、标题）
├── data/                 ← 资料区：频道 guideline、高播标题基准、术语库
└── README.md             ← 本文件：仓库代码怎么跑
```

**Skill 加载方式**：`~/.claude/skills/kdb-video-post-production` → `/Users/sunyuzheng/Desktop/AI/skills/kdb-video-post-production` → 本仓库 `skill/`（两级符号链接）。改 skill 只改 `skill/SKILL.md`，立即生效。

---

## 工作流总览

第 0 步先**判型**：单口（口播）还是访谈，决定后面所有分流——文章形态（外发独立稿 vs 视频伴读稿）、高光数量（3-4 段 vs 6-8 段）、封面图（默认不做 vs 默认做）。判型由脚本从字幕内容自动完成。

| 步骤 | 内容 | 输出文件 | 引擎 |
|------|------|---------|------|
| 1. 转录 | Qwen3-ASR 本地转录（注入频道热词 + seeds） | `.qwen.srt` | 本地模型，完全离线 |
| 2. 精校 | 同音字、专有名词、**实体全文一致性**（如公司名被听成两种写法） | `.corrected.srt` | Codex CLI |
| 3. 断句 | **先合并再重切**：跨越 ASR 坏 cue 边界，按标点/jieba 词边界切 ≤20 字 | `.final.srt` | 本地规则，无需 API |
| 4. 高光 | 独立的观众感模块：时间戳 + 原话 + cognitive gap + 叙事弧 + 剪辑组合 | `.highlights.md` | Claude Code CLI |
| 5. 文章 | 按判型分流；自动读取高光做跳转地图；禁元叙述和空转总结 | `.article.md` | Claude Code CLI |
| 6. 标题 | 三轮工作流，以频道真实高播标题为外部基准；终审置顶、无内部代号 | `.titles.md` | Claude Code CLI |
| 7. YouTube description | 平实介绍 + 可复制的 mm:ss 章节，从 00:00 开始 | `.youtube-description.txt` | Claude Code CLI |

**标题有两条路线**：上表第 6 步是路线 A（B站/YouTube 频道长标题）；路线 B 是小红书封面+标题，由 Claude Code 调用独立的 [`xhs-cover-title`](https://github.com/sunyuzheng) skill 产出 `.xhs.md`（封面文案 + ≤20 字标题，喂料用 seeds 身份信息和高光原话），不在本仓库脚本内。

封面图三种比例（16:9、3:4、4:3）是发布设计步骤，由 agent 根据 `.xhs.md` / `.titles.md` 的封面建议和视频截图生成，不在 `process_video.py` 自动生成。生成前先读 `skill/references/cover-style-guide.md`，按案例风格做黄白黑巨字短视频封面，不做干净海报或玻璃卡片。

**引擎降级**：步骤 4-7 默认 `claude-fable-5`；CLI 不可用、超时或没写出文件时，`tools/claude_cli.py` 自动降级到 Codex `gpt-5.5`，产物文件约定不变，降级在输出里明示 ⚠。

---

## 使用前提

| 要求 | 说明 |
|------|------|
| **电脑** | Apple Silicon Mac（M1 / M2 / M3 / M4） |
| **Python** | 3.10 或更高版本 |
| **Codex CLI** | 字幕精校 + 降级引擎，`which codex` 确认已安装并登录 |
| **Claude Code CLI** | 高光、文章、标题生成，`which claude` 确认已安装并登录 |

> Windows / Intel Mac 暂不支持（mlx-qwen3-asr 只支持 Apple Silicon）。
> 无需配置任何 API Key，AI 调用全部通过已登录的 CLI 完成。

## 一次性安装

```bash
git clone https://github.com/sunyuzheng/kdb-video-post-production.git
cd kdb-video-post-production
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

安装约 3-10 分钟；首次运行会自动下载转录模型（约 1.5 GB）。依赖里的 `jieba` 用于断句的词边界切分。

---

## 使用

### 最常用：全链路处理

```bash
caffeinate -i venv/bin/python tools/process_video.py /path/to/视频.mp4 --seeds 嘉宾名 公司名 产品名
```

`caffeinate -i` 防止长视频跑到一半 Mac 休眠。没有专有名词时用 `--no-seeds`。

**seeds 的双重作用**：注入 ASR 提高专有名词准确率；同时是小红书路线的「主人公背景」原料（身份词要带具体数字）。**单期实体（嘉宾公司名、产品名）只走 seeds，不进 `data/channel_vocab.json`**——词库只收频道级多期复用的术语。

### 已有 SRT，只补内容

```bash
venv/bin/python tools/generate_highlights.py /path/to/视频名.final.srt -o /path/to/video-dir
venv/bin/python tools/generate_article.py    /path/to/视频名.final.srt -o /path/to/video-dir
venv/bin/python tools/generate_titles.py     /path/to/视频名.article.md -o /path/to/video-dir --workspace-dir /path/to/视频名_process
venv/bin/python tools/generate_youtube_description.py /path/to/视频名.final.srt -o /path/to/video-dir
```

顺序必须**先高光后文章**——`generate_article.py` 会自动读取同目录的 `highlights.md` 做选题、时间戳和原话线索。

### 所有参数

| 参数 | 说明 |
|------|------|
| `--seeds 名字 术语` | 注入专有名词（多个用空格分隔；书面正确写法） |
| `--no-seeds` | 跳过术语输入 |
| `--skip-transcribe` | 跳过转录（已有 `视频名_process/视频名.qwen.srt`） |
| `--skip-correct` | 跳过字幕精校 |
| `--skip-highlights` | 跳过高光提取 |
| `--skip-article` | 跳过文章生成 |
| `--skip-titles` | 跳过标题生成 |
| `--skip-youtube-description` | 跳过 YouTube description 生成 |
| `--process-dir DIR` | 指定过程文件目录（默认 `视频名_process/`） |
| `--max-chars N` | 每条字幕最大字数（默认 20） |

---

## 输出文件（三区约定）

**交付区** = 视频同目录：

| 文件 | 说明 | 用途 |
|------|------|------|
| `视频名.final.srt` | 最终字幕，≤20字/条，词不切开 | **导入剪辑软件用这个** |
| `视频名.highlights.md` | 高光候选 + 叙事弧 + 推荐剪辑组合 | 剪辑跳转 + 标题原料 |
| `视频名.article.md` | 单口外发稿 / 访谈伴读稿（按判型） | 发布 / 归档 |
| `视频名.titles.md` | 终审标题（标字数）+ 封面建议 + 备选 | **取标题用这个** |
| `视频名.youtube-description.txt` | YouTube 介绍 + 简洁章节（mm:ss，从 00:00 开始） | 复制到 YouTube description |
| `视频名.xhs.md` | 小红书封面+标题方案（发小红书时生成） | 小红书发布 |
| `视频名.cover-16x9.png` / `视频名.cover.png` | 16:9 带字封面图 | YouTube / 旧流程主封面 |
| `视频名.cover-3x4.png` | 3:4 带字封面图，推荐两张截图上下叠放，中间放字 | 小红书 |
| `视频名.cover-4x3.png` | 4:3 带字封面图 | B站 / 抖音 / 视频号 |

**工作区** = `视频名_process/`：`*.qwen.srt`（原始转录，永不覆盖）、`*.corrected.srt`（精校稿）、`*_title_ws/`（标题轮次中间文件）。过程文件不当交付。

**资料区** = 本仓库 `data/` + `xhs-cover-title` skill 的词库样本库。只读引用，更新走「持续校准」（见 `skill/SKILL.md`）。

---

## 关键设计

### 文件响应模式

所有 AI 步骤：任务写临时文件 → CLI 把完整输出写目标文件 → Python 读回。相比 pipe 模式：不截断、无参数长度限制、精校可见全文上下文。

### 断句：先合并再重切（`resplit_srt.py`）

ASR 的原始 cue 边界经常落在词中间（「…这是你的事 / 情当然…」）。脚本先把停顿 ≤0.6s 的相邻 cue 合并成窗口（每字符按时长插值，时间锚点保留），再按「句末标点 → 子句标点 → jieba 词边界 → 强制截断」四级断句。下游高光/文章引用的时间戳在重切后仍然有效。

### 文章口吻（`generate_article.py`）

像主播本人状态最好时写出的版本，不是 AI 观点包装。硬约束：

- **禁元叙述**：不写「核心命题可以压成一句话」「这段值得先看」「注意这里的证据等级」——直接给内容，让事实自己产生分量
- **禁空转总结**：不写「每一个都被重新定义过」——直接说定义是什么、意义是什么
- 少用「不是……而是……」；不硬造「XX法则」「XX之墙」；保留具体案例、第一人称判断和推理来路

### 高光检测（`generate_highlights.py`）

优先用 SRT 末尾编辑者手动追加的高光字幕（时间戳重置为 `00:00:xx` 的段落）；没有才全文扫描。手动追加方法：把选定片段的 SRT 行复制到 `.final.srt` 末尾。

### 三轮标题（`generate_titles.py`）

| 轮次 | 角色 | 做什么 |
|------|------|--------|
| Round 0 | 资深编辑 | 理解高光 + 内容，发散生成候选 |
| Round 1 | 独立评审 | 对比 `data/top_titles.txt`（频道 Top 25 真实高播标题）找盲区 |
| Round 2 | 终审编辑 | 补强 + 终审。交付文件固定结构：最终标题置顶、零轮次代号，推理过程留在 `_title_ws/` |

不要徒手写频道标题——徒手写绕过了高播标题的外部基准。

---

## 数据目录（`data/`）

| 文件 | 说明 |
|------|------|
| `guideline_kedaibiao.md` | 频道 Guideline：受众定位、标题策略、高光选取原则 |
| `top_titles.txt` | 频道 Top 25 真实高播标题，标题评审的外部基准（发布后高播标题持续回流） |
| `channel_vocab.json` | 频道词汇表，注入 ASR 热词。**只收多期复用的术语**，单期实体走 `--seeds` |
| `correction_candidates.json` | 高置信度替换规则，规则层直接执行 |

## 工具说明

| 脚本 | 功能 |
|------|------|
| `tools/process_video.py` | 主入口，七步一体 |
| `tools/claude_cli.py` | Claude CLI 文件响应封装，内置 → Codex gpt-5.5 降级 |
| `tools/codex_cli.py` | Codex CLI 文件响应封装 |
| `tools/correct/correct_srt.py` | 精校引擎：同音字 + 实体一致性 + 全文覆盖（可单独调用） |
| `tools/resplit_srt.py` | 断句：合并窗口 + jieba 词边界（可单独调用） |
| `tools/generate_highlights.py` | 高光提取，自动判型单口/访谈（可单独调用） |
| `tools/generate_article.py` | 文章生成，按判型分流、自动吃高光（可单独调用） |
| `tools/generate_titles.py` | 标题三轮工作流（可单独调用） |
| `tools/generate_youtube_description.py` | YouTube description + 章节生成（可单独调用） |

## 时间参考

| 视频时长 | 转录 | 精校 | 断句 | 高光 | 文章 | 标题 | YouTube description | **合计** |
|---------|------|------|------|------|------|------|---------------------|---------|
| 10 分钟 | 2-3 分钟 | 1-2 分钟 | <1 分钟 | 2-3 分钟 | 1-2 分钟 | 8-15 分钟 | 1-2 分钟 | **~16-27 分钟** |
| 30 分钟 | 6-10 分钟 | 2-3 分钟 | <1 分钟 | 3-5 分钟 | 3-5 分钟 | 8-15 分钟 | 1-2 分钟 | **~26-42 分钟** |
| 110 分钟 | ~30 分钟 | 3-5 分钟 | <1 分钟 | 2-4 分钟 | 3-5 分钟 | 8-15 分钟 | 1-2 分钟 | **~51-62 分钟** |

（110 分钟一行是 2026-06-11 徐老师访谈的实测：转录 1768s、精校 187s、高光 151s、文章 196s、标题 474s。）

## 常见问题

**Q：运行时报 `未安装 mlx-qwen3-asr`？**
A：确认用的是 `venv/bin/python`，不是系统 `python3`。仍报错则 `venv/bin/pip install mlx-qwen3-asr`。

**Q：AI 步骤报 `codex: command not found` / `claude: command not found`？**
A：对应 CLI 未安装或不在 PATH。`which codex` / `which claude` 确认。

**Q：高光选错了角度？**
A：在 `.final.srt` 末尾手动追加亲选高光字幕（见「高光检测」），脚本会优先采用。

**Q：嘉宾名转录还是错了？**
A：`--seeds` 用书面正确写法。精校阶段会报告每个 seed 的全文出现次数，提示「未找到」说明 ASR 用了别的写法——精校的实体一致性扫描会尝试统一，仍漏的手动改 `.corrected.srt` 后重跑断句。

**Q：`.final.srt` 断句不对？**
A：调 `--max-chars`（默认 20），或在剪辑软件里微调。

**Q：标题生成卡住？**
A：确认 Claude Code CLI 已登录（`claude -p "test"`）。Fable 5 不可用时会自动降级 Codex gpt-5.5，输出里有 ⚠ 提示。
