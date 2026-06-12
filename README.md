# 课代表立正 · 视频内容生产工具 v4

给定一个视频文件，自动完成六步流程，最终产出可用的字幕、文章、高光分析和标题候选。

> **Skill 入口**：工作流的权威定义在 [`skill/SKILL.md`](skill/SKILL.md)（判型、三区、双路线标题、验收、持续校准），通过符号链接加载为 Claude Code 的 `kdb-video-post-production` skill。本 README 只讲仓库代码怎么跑。

| 步骤 | 内容 | 输出文件 | 依赖 |
|------|------|---------|------|
| 1. 转录 | Qwen3-ASR 本地转录 | `.qwen.srt` | 本地模型（完全离线） |
| 2. 字幕校对 | ASR 纠错专有名词/同音字 | `.corrected.srt` | Codex CLI |
| 3. 断句 | 每条 ≤20 字重新断句 | `.final.srt` | 本地规则（无需 API） |
| 4. 提取高光 | 识别视频开头高光片段，分析叙事弧 | `.highlights.md` | Claude Code CLI |
| 5. 生成文章 | 提炼频道风格文章 | `.article.md` | Claude Code CLI |
| 6. 生成标题 | 三轮 Fable 5 工作流，高光驱动 | `.titles.md` | Claude Code CLI |

步骤 1、3 完全本地运行，无需任何 API Key。步骤 2 通过 Codex CLI（`codex` 命令）以文件响应模式调用；步骤 4、5、6 通过 Claude Code CLI（`claude` 命令）以文件响应模式调用，默认使用 `claude-fable-5`。

---

## 使用前提

| 要求 | 说明 |
|------|------|
| **电脑** | Apple Silicon Mac（M1 / M2 / M3 / M4） |
| **Python** | 3.10 或更高版本 |
| **Codex CLI** | 字幕校对需要，`which codex` 确认已安装并登录 |
| **Claude Code CLI** | 高光、文章、标题生成需要，`which claude` 确认已安装并登录 |

> Windows / Intel Mac 暂不支持（转录模型 mlx-qwen3-asr 只支持 Apple Silicon）。  
> 不再需要配置 ANTHROPIC_API_KEY 或其他 API Key，AI 调用通过已登录的 Codex CLI 和 Claude Code CLI 完成。

---

## 一次性安装

```bash
git clone https://github.com/sunyuzheng/kdb-post-production.git
cd kdb-post-production
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

安装时间约 3-10 分钟，mlx-qwen3-asr 首次安装后第一次运行时还会自动下载模型（约 1.5 GB）。

确认 Codex CLI 和 Claude Code CLI 已安装并登录：

```bash
which codex         # 应输出路径
codex --version    # 应输出版本号
which claude        # 应输出路径
claude --version    # 应输出版本号
```

---

## 使用

### 最常用：全链路处理

```bash
venv/bin/python tools/process_video.py /path/to/视频.mp4 --no-seeds
```

有嘉宾名或专有名词时注入（提高转录准确率）：

```bash
venv/bin/python tools/process_video.py 视频.mp4 --seeds 嘉宾名 "公司名"
```

**防止 Mac 休眠（跑长视频时）：**

```bash
caffeinate -i venv/bin/python tools/process_video.py 视频.mp4 --no-seeds
```

### 情况 B：已有 SRT，只补高光 + 文章 + 标题

```bash
venv/bin/python tools/generate_highlights.py /path/to/视频名.final.srt -o /path/to/video-dir
venv/bin/python tools/generate_article.py /path/to/视频名.final.srt -o /path/to/video-dir
venv/bin/python tools/generate_titles.py /path/to/视频名.article.md -o /path/to/video-dir --workspace-dir /path/to/视频名_process
```

### 情况 C：只需标题（已有 highlights）

```bash
venv/bin/python tools/generate_titles.py /path/to/视频名.article.md -o /path/to/video-dir --workspace-dir /path/to/视频名_process
```

---

## 所有参数

| 参数 | 说明 |
|------|------|
| `--seeds 名字 术语` | 注入专有名词，提高 ASR 准确率（多个用空格分隔） |
| `--no-seeds` | 跳过术语输入，直接开始 |
| `--skip-transcribe` | 跳过转录（已有 `视频名_process/视频名.qwen.srt`） |
| `--skip-correct` | 跳过字幕校对 |
| `--skip-article` | 跳过文章生成 |
| `--skip-highlights` | 跳过高光提取（已有 `.highlights.md` 或不需要标题） |
| `--skip-titles` | 跳过标题生成 |
| `--process-dir DIR` | 指定过程文件目录（默认 `视频名_process/`） |
| `--max-chars N` | 每条字幕最大字数（默认 20） |

---

## 输出文件

交付文件生成在**视频同目录**：

| 文件 | 说明 | 用途 |
|------|------|------|
| `视频名.final.srt` | 最终字幕，≤20字/条 | **导入剪辑软件用这个** |
| `视频名.article.md` | 频道风格文章 | 内容归档；也是标题生成的输入 |
| `视频名.highlights.md` | 高光分析（中心命题+受众+叙事弧） | 标题锚点 |
| `视频名.titles.md` | 最终标题候选 + 封面建议 | **取标题用这个** |

过程文件生成在**视频同目录的 `视频名_process/`**：

| 文件 | 说明 |
|------|------|
| `视频名.qwen.srt` | Qwen 原始转录，未校对 |
| `视频名.corrected.srt` | Claude 校对后字幕 |
| `视频名_title_ws/` | 标题中间文件（round0/round1） |

---

## 关键设计

### 文章生成口吻

`generate_article.py` 的目标是把口播整理成更清楚的本人叙述，而不是 AI 观点包装。文章应像主播自己讲故事：具体、直接、有推理过程，只是更结构化。

重点约束：
- 少用 `不是……而是……`，全文最多 1 次
- 不写冒犯观众的话，例如 `不是因为你蠢`
- 不硬造 `XX法则`、`XX障碍`、`XX之墙` 等玄虚概念
- 优先保留具体案例、第一人称判断和推理来路

### 文件响应模式（v4 核心改动）

所有 AI 步骤使用文件响应模式：

1. 任务描述 + 内容写入临时文件
2. 告知 Codex 或 Claude：读取任务文件，将完整输出写入目标文件
3. Python 读取目标文件

相比 pipe 模式（`claude -p <内联 prompt>`）的优势：
- **不截断**：CLI 的心理模型是"完成工作并保存"，而非"对话回答"
- **无参数长度限制**：大内容通过文件传递
- **全文上下文**：字幕校对不再分批，Codex 可见完整内容

### 高光检测逻辑（`generate_highlights.py`）

优先检测 SRT 末尾编辑者手动追加的高光字幕（时间戳重置为 `00:00:xx`），有则用亲选片段分析，无则扫全文。

**手动追加高光**：把高光片段的 SRT 字幕行复制到 `.final.srt` 末尾即可。

### 三轮标题工作流（`generate_titles.py`）

| 轮次 | 角色 | 做什么 |
|------|------|--------|
| Round 0 | 资深编辑 | 理解高光 + 内容，发散生成候选 |
| Round 1 | 独立评审 | 对比 `data/top_titles.txt`（频道 Top 25 真实高播标题）找盲区 |
| Round 2 | 终审编辑 | 按 Round 1 指令补强，选出最终 6-10 个 + 封面建议 |

---

## 时间参考

| 视频时长 | 转录 | 校对 | 断句 | 文章 | 高光 | 标题 | **合计** |
|---------|------|------|------|------|------|------|---------|
| 10 分钟 | 2-3 分钟 | 1-2 分钟 | <1 分钟 | 1-2 分钟 | 2-3 分钟 | 10-15 分钟 | **~18-26 分钟** |
| 30 分钟 | 6-10 分钟 | 2-3 分钟 | <1 分钟 | 3-5 分钟 | 3-5 分钟 | 10-15 分钟 | **~25-40 分钟** |
| 60 分钟 | 15-20 分钟 | 3-5 分钟 | <1 分钟 | 4-6 分钟 | 3-5 分钟 | 10-15 分钟 | **~38-53 分钟** |

步骤 1 用本地模型（离线，不收费）；步骤 2 通过 Codex CLI；步骤 4-6 通过 Claude Code CLI 调用 Fable 5。

---

## 数据目录（`data/`）

| 文件 | 说明 |
|------|------|
| `guideline_kedaibiao.md` | 频道 Guideline：受众定位、标题策略、高光选取原则、封面设计逻辑 |
| `top_titles.txt` | 频道 Top 25 真实高播标题，Round 1 评审的外部基准 |
| `channel_vocab.json` | 频道词汇表（人名、品牌名、技术术语），注入 ASR 热词 |
| `correction_candidates.json` | 高置信度替换规则（数字格式等），规则层直接执行 |

---

## 工具说明

| 脚本 | 功能 |
|------|------|
| `tools/process_video.py` | 主入口，6步流程一体化 |
| `tools/codex_cli.py` | 文件响应模式 Codex CLI 工具（字幕校对） |
| `tools/claude_cli.py` | 文件响应模式 Claude CLI 工具（高光、文章、标题） |
| `tools/correct/correct_srt.py` | 字幕校对引擎（可单独调用） |
| `tools/resplit_srt.py` | 断句工具（可单独调用） |
| `tools/generate_article.py` | 文章生成（可单独调用） |
| `tools/generate_highlights.py` | 高光提取（可单独调用） |
| `tools/generate_titles.py` | 标题三轮工作流（可单独调用） |

---

## 常见问题

**Q：运行时报 `未安装 mlx-qwen3-asr`？**  
A：确认用的是 `venv/bin/python`，而不是系统自带的 `python3`。如果确认无误还是报错，手动安装：`venv/bin/pip install mlx-qwen3-asr`。

**Q：AI 步骤报错 `codex: command not found` 或 `claude: command not found`？**  
A：对应 CLI 未安装或未在 PATH 中。运行 `which codex` / `which claude` 确认。

**Q：高光分析选错了角度？**  
A：在 SRT 文件末尾手动追加高光字幕（见上方「高光检测逻辑」），系统会优先使用编辑者亲选的片段。

**Q：嘉宾名转录还是错了？**  
A：`--seeds` 里的名字必须是书面正确写法（如「刘嘉」而非「刘佳」）。校对阶段会在全文中查找并报告该名字的出现次数，如果提示「未找到」，说明 Qwen 转录用了另一个写法，需手动查找修改。

**Q：`.final.srt` 断句位置不对？**  
A：调整 `--max-chars` 参数（默认 20 字），或在剪辑软件里手动微调。

**Q：标题生成卡住不动？**  
A：步骤 5-6 调用 Claude Code CLI，需已登录。运行 `claude -p "test"` 确认可正常调用。
