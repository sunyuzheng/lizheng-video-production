---
name: kdb-post-production
description: 给定视频文件或已有 SRT，自动完成转录→校对→断句→高光→文章→标题六步，产出可用字幕、高光分析、文章和标题候选。过程文件收纳进独立文件夹，精校后的高光/文章/标题用 Claude Code CLI Opus 4.6。
type: Workflow
---

# 课代表立正 · 视频内容生产 Skill

## 目标输出

| 文件 | 用途 |
|------|------|
| `视频名.final.srt` | 导入剪辑软件的断句字幕 |
| `视频名.highlights.md` | 高光分析（中心命题 + 受众 + 叙事弧） |
| `视频名.article.md` | 文章稿 |
| `视频名.titles.md` | 6-10 个标题候选 + 封面建议 |
| `视频名.cover.png` | 播客/访谈风格封面图 |

交付文件生成在**视频同目录**。过程文件生成在**视频同目录的 `视频名_process/`**，包括 `.qwen.srt`、`.corrected.srt`、标题三轮 workspace 等。

## 代码库

`/Users/sunyuzheng/Desktop/AI/content/kedaibiao-srt-v2/`

所有命令在此目录下运行，使用 `venv/bin/python`。

## 本地 ASR 规则

- 转写优先使用 `/opt/homebrew/bin/mlx-qwen3-asr` CLI，不优先使用静默 Python API。
- 模型固定为 `Qwen/Qwen3-ASR-1.7B`。
- CLI 必须带 `--verbose`，让长视频能看到实时进度。
- CLI 输出的 SRT 统一收进过程目录，并重命名为 `视频名.qwen.srt`。
- 只有当 `mlx-qwen3-asr` CLI 不可用或失败时，才考虑降级方案，并明确说明。

## 运行方式

### 情况 A：从视频开始（全链路）

```bash
# 有嘉宾名 / 专有名词时注入（提高转录准确率）
venv/bin/python tools/process_video.py /path/to/视频.mp4 --seeds 嘉宾名

# 单口视频或不需要注入时
venv/bin/python tools/process_video.py /path/to/视频.mp4 --no-seeds
```

防止 Mac 休眠（长视频建议加）：

```bash
caffeinate -i venv/bin/python tools/process_video.py 视频.mp4 --no-seeds
```

只做字幕和精校，不生成高光、文章和标题：

```bash
caffeinate -i venv/bin/python tools/process_video.py 视频.mp4 --seeds 术语1 术语2 --skip-highlights --skip-article --skip-titles
```

课程/培训类素材如果用户要“课程内容介绍”，先用上面的字幕精校流程生成 `final.srt`，再基于最终精校字幕单独生成 `视频名.course-intro.md`；不要为了流程完整强行生成标题。

默认 Claude Code CLI 模型为 `claude-opus-4-6`。如需指定过程文件目录：

```bash
venv/bin/python tools/process_video.py 视频.mp4 --no-seeds --process-dir /path/to/process
```

### 情况 B：已有 SRT，只需高光 + 文章 + 标题

```bash
venv/bin/python tools/generate_highlights.py /path/to/视频名.final.srt -o /path/to/video-dir
venv/bin/python tools/generate_article.py /path/to/视频名.final.srt -o /path/to/video-dir
venv/bin/python tools/generate_titles.py /path/to/视频名.article.md -o /path/to/video-dir --workspace-dir /path/to/视频名_process
```

### 情况 C：只需标题（已有 highlights）

```bash
venv/bin/python tools/generate_titles.py /path/to/视频名.article.md
```

## 验收标准

- `titles.md` 存在，包含 ≥6 个标题候选，每个有一句话说明
- `titles.md` 包含封面建议（访谈：3句金句；单口：3-10字冲击文字）
- `highlights.md` 存在，包含中心命题、受众分析、≥3 段高光（每段有原话引用 + 叙事位置 + 好奇钩子）
- `article.md` 存在，使用精校后字幕和 `highlights.md`，不使用 raw ASR
- 访谈文章要像视频伴读/解读稿：开头说明整期真正讨论的问题；前半部分给 8-12 条观看地图；正文保留时间戳、高光原话、片段价值解释和综合判断；不要把长访谈压扁成一篇普通观点文
- 访谈高光要带时间戳、原话、vantage point / cognitive gap、为什么值得跳转观看，以及它在整期主线中的位置
- 访谈封面图用 `imagegen` 生成，保存在视频同目录，16:9 构图，双人对谈清晰，保留可加标题的留白，不生成乱码文字、水印或未经确认的标题
- 单口文章要像主播本人状态很好时写出来的版本：更结构、精确、条理清楚，同时保留观点、人格、风格和独特判断
- 文章避免 AI 腔：不滥用「不是……而是……」，不写「不是因为你蠢」这类冒犯观众的话，不硬造「XX法则/XX之墙」等玄虚概念；否定时优先否定结构和机制，不贴脸否定案例人物
- 标题无 AI 公文感，无空洞承诺，每条有具体内容锚点
- 视频同目录只保留交付文件；过程文件进入 `视频名_process/`

## 已知陷阱

| 问题 | 处理方式 |
|------|---------|
| 后台运行时交互式输入卡住 | 始终用 `--seeds` 或 `--no-seeds`，禁止用交互模式 |
| 高光选了戏剧性时刻而非叙事核心 | 在 `.final.srt` 末尾手动追加高光字幕，系统自动优先使用亲选片段 |
| 标题生成步骤报错 `claude not found` | 需提前安装并登录 Claude Code CLI：`which claude` 确认 |
| 嘉宾名仍转录出错 | `--seeds` 用书面正确写法；校对阶段会报告名字出现次数，未找到则需手动查找 |

## 关键设计

**精校后三件事**：先挖掘高光时刻，再生成文章，最后思考标题。三步都通过 Claude Code CLI 文件响应模式调用，默认 `claude-opus-4-6`。

**文章口吻**：文章不是营销号改写，也不是 AI 观点包装；目标是把口播整理成作者本人状态很好时写出来的版本。生成文章时融合 `substance-writing-review` skill：先识别源材料里真正有力量的 insight、独特判断、讲述者性格和表达方式，再放大它们，最后清理重复、绕弯和无信息过渡。必要边界是不要 AI 腔、不要故弄玄虚、不要冒犯观众、不要替作者编内心戏。输出前做中文自然度检查，重点清除翻译腔，但不要误伤作者原本的口语和英文技术表达。

**访谈文章形态**：长访谈的 `article.md` 默认是视频伴读/解读稿，不是摘要。它要帮助观众理解整期视频的主线、精彩片段、可跳转高光、嘉宾原话和这些观点的重要性。结构通常包括：开头说明这期真正讨论的问题和为什么值得看；给出 8-12 条观看地图；分 6-10 个高光段落，每段有时间戳、发生了什么、原话引用、为什么值得看、观众可以如何理解；结尾回到整期的核心价值。写作上用第一人称客观陈述对话，明确区分主持人、嘉宾和其他发言者，不把别人的判断写成主播自己的判断。总结可以长，优先保证内容全、重点突出、引用准确和 synthesis 到位。

**单口文章形态**：单口视频可以更接近完整文章。它不需要机械保留每个时间戳，重点是把核心判断、推理来路、具体故事和可操作启发讲清楚。小标题要自然具体，框架从内容里长出来，不为形式整齐牺牲表达。

**高光驱动标题**：`generate_titles.py` 先读 `highlights.md`，标题描述高光背后更大的问题，而非高光本身——高光让观众感觉「这期有料」，标题创造点击动机。

**访谈封面图**：访谈发布链路最后使用 `imagegen` skill 生成 `<video>.cover.png`。优先使用用户提供的现场照、视频截图或关键帧作为 reference image，生成播客/访谈风格 16:9 封面：真实自然、双人对谈明确、封面级光影和构图、上方或侧边留出可加标题区域。默认不把标题文字直接生成进图片，避免乱码；文字和 logo 后续由剪辑/设计工具添加。生成后把最终图复制到视频同目录，不只留在 `$CODEX_HOME/generated_images/`。

**三轮标题工作流**：Round 0 资深编辑发散生成 → Round 1 独立评审对比频道 Top 25 真实高播标题（`data/top_titles.txt`）找盲区 → Round 2 终审按指令补强并选定。

**时间参考**：8 分钟视频全链路约 15-20 分钟；30 分钟视频约 25-40 分钟。转录和断句本地完成；校对、高光、文章、标题调用 Claude Code CLI（Opus）。
