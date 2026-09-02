# 课代表立正 · 视频后期生产

`lizheng-video-production` 把视频或已有字幕整理成可靠的字幕交付和按需发布资产：高光、文章、标题与 YouTube description。仓库同时包含可执行工具和 Codex／Claude skill；自动化边界、外部能力和草稿发布被明确分开。

## 它真正完成什么

| 能力 | 自动化等级 | 入口／依赖 |
|---|---|---|
| 本地 ASR、全文精校、重新断句、字幕 QC、VTT | 主流程自动 | `tools/process_video.py` |
| 高光、文章、标题、YouTube description | 主流程按需生成；标题与 description 有格式门，高光与文章需编辑验收 | Codex CLI，失败时 Claude CLI fallback |
| 说话人区分与可选声纹映射 | 独立脚本 | pyannote + `ffmpeg` |
| 口头禅／重复／假启动的非破坏性剪辑 | 独立脚本，edit plan 需先审核 | `tools/render_filler_cuts.py` + `ffmpeg` |
| 双 WAV 漂移对齐、剪前导、社区版压制 | agent 制作 recipe，不是主脚本自动能力 | `ffmpeg`／`ffprobe` |
| 16:9、3:4 封面 | 设计／外部 skill | Canva、图像工具、`superlinear-brand-usage` |
| Google Doc、社区／Circle 草稿 | 外部 connector 或浏览器操作 | 只创建草稿；发布另需批准 |

目录：

```text
skill/       任务路由、交付契约和条件制作说明
tools/       可执行脚本
tests/       确定性行为和失败语义测试
data/        频道基准、术语、writing-skill fallback
DESIGN.md    长期技术决策
```

## Fresh clone

### 前提

- Apple Silicon Mac；主 ASR 基于 MLX。
- Python 3.10 或更高。说话人标注建议使用单独的 Python 3.11+ 环境。
- `ffmpeg` 与 `ffprobe`。
- 已安装并登录 Codex CLI。Claude Code CLI 是内容生成的可选 fallback，需要降级能力时再安装并登录。

AI 文本步骤使用已登录 CLI，不直接读取云 API key。可选的 pyannote 模型需要 Hugging Face 账号授权。

### 安装代码

```bash
git clone https://github.com/sunyuzheng/lizheng-video-production.git
cd lizheng-video-production

python3 --version
python3 -m venv venv
venv/bin/pip install -r requirements.txt

ffmpeg -version
codex --version
claude --version  # 可选 fallback
```

如果系统 `python3` 低于 3.10，用已安装的具体版本创建 venv，例如 `/opt/homebrew/bin/python3.12 -m venv venv`。首次转写会下载 Qwen3-ASR 模型；下载大小与模型 revision、缓存状态有关，请预留充足磁盘空间。

### 安装 skill

Canonical skill name 是 `lizheng-video-editing`。在仓库根目录执行：

```bash
mkdir -p ~/.codex/skills
ln -s "$(pwd)/skill" ~/.codex/skills/lizheng-video-editing
test -f ~/.codex/skills/lizheng-video-editing/SKILL.md
```

如果同时使用 Claude skill：

```bash
mkdir -p ~/.claude/skills
ln -s "$(pwd)/skill" ~/.claude/skills/lizheng-video-editing
test -f ~/.claude/skills/lizheng-video-editing/SKILL.md
```

命令故意不带强制覆盖参数；目标已存在时先检查它指向哪里，再决定是否迁移。旧名 `kdb-video-post-production` 已弃用，不再是 frontmatter 或安装文档的当前名称。

## 三个常用 recipe

### 1. 全链路

```bash
caffeinate -i venv/bin/python tools/process_video.py /path/to/video.mp4 \
  --seeds 嘉宾名 公司名 产品名
```

访谈若要独立社区帖，而不是随视频伴读：

```bash
caffeinate -i venv/bin/python tools/process_video.py /path/to/video.mp4 \
  --article-type interview \
  --article-surface community \
  --seeds 嘉宾名 公司名
```

没有当期专有名词时用 `--no-seeds`。单期实体只走 seeds；只有跨期复用且确认过的术语才进入 `data/channel_vocab.json`。
不传 `--skip-transcribe` 时，即使工作区已有 `.qwen.srt` 也会在隔离目录重跑 ASR；只有当前运行产出唯一且结构有效的 SRT 才刷新 raw，失败时保留上一版。

### 2. 只做字幕、VTT 和 QC

```bash
caffeinate -i venv/bin/python tools/process_video.py /path/to/video.mp4 \
  --seeds 嘉宾名 公司名 产品名 \
  --skip-highlights --skip-article --skip-titles --skip-youtube-description
```

字幕 QC 是依赖门。失败时保存诊断用 SRT 和 QC 报告，退出非零；不会把失败 VTT 当交付，也不会继续生成下游内容。

### 3. 已有 SRT，只补内容

```bash
venv/bin/python tools/generate_highlights.py /path/to/video.final.srt \
  -o /path/to/delivery

venv/bin/python tools/generate_article.py /path/to/video.final.srt \
  -o /path/to/delivery \
  --workspace-dir /path/to/video_process \
  --highlights /path/to/delivery/video.highlights.md \
  --article-type interview --surface companion

venv/bin/python tools/generate_titles.py /path/to/delivery/video.article.md \
  -o /path/to/delivery \
  --workspace-dir /path/to/video_process

venv/bin/python tools/generate_youtube_description.py /path/to/video.final.srt \
  -o /path/to/delivery
```

文章按类型只加载一个主责 writing skill：访谈使用 `expert-interview-article`，单口使用 `substance-writing-review`。本机没有当前 skill 时使用 `data/writing-skills/` fallback；其中 `substance-writing-review.md` 同步自公开仓库 `https://github.com/sunyuzheng/substance-writing-review` 的自包含主文件。实际注入的文件、来源和 hash 会保存到本期工作区。自动流水线不会自行读取其中按需引用的外部 reference，因此 fallback 主文件必须能独立承担写作契约。

标题流程会先读取完整文章或带时间线的完整 SRT，保存一份 `title_brief.md`，明确观众看前与看后的判断变化；候选出来后，challenger 会重新读取源材料，允许推翻第一轮，而不是只做措辞润色。最终稿仍需要编辑判断，脚本的多轮输出不等于自动选中了可发布标题。

`surface` 含义：

| 值 | 产物 |
|---|---|
| `article` | 不依赖视频的独立文章 |
| `community` | 不依赖视频的社区帖 |
| `companion` | 带观看导航的视频伴读／活动回放 |
| `release` | 较短发布介绍 |
| `auto` | 访谈默认 companion，单口默认 article |

## 主入口参数

以 `python3 tools/process_video.py --help` 为最终真值。常用参数：

| 参数 | 说明 |
|---|---|
| `--skip-transcribe` / `--skip-correct` | 跳过对应步骤；后续使用的 raw SRT 会明示打印，不自动挑选旧 corrected/final |
| `--subtitle-source PATH` | 显式使用已有 corrected/final SRT，并跳过 ASR 与校对 |
| `--skip-highlights` / `--skip-article` / `--skip-titles` / `--skip-youtube-description` | 只是不生成该资产；不会把同目录旧文件注入本次下游 |
| `--article-type auto\|interview\|monologue` | 文章素材类型；信号不足时 auto 不凭主题猜 |
| `--article-surface auto\|article\|community\|companion\|release` | 文章发布契约 |
| `--article-writing-skill PATH` | 固定或重新使用此前保存的 writing-skill 主文件；完整复现还需相同代码与本期素材 |
| `--seeds ...` / `--no-seeds` | 当期实体与 ASR 上下文 |
| `--model MODEL` | 显式覆盖 Codex 字幕精校模型；默认使用 CLI 配置 |
| `--correction-timeout SECONDS` | 全文精校超时，默认 900 秒 |
| `--process-dir PATH` | 自定义工作区 |
| `--max-chars N` | 每条字幕最大可见字符，默认 20 |

### 维护频道词汇

`data/channel_vocab.json` 是运行时文件，只保留 `schema_version`、`verified_candidates` 和 `hotwords_context`。人工确认的源分别是 `data/verified_corrections.json` 与 `data/verified_hotwords.txt`：

```bash
python3 tools/extract_channel_vocab.py \
  --channel-root /path/to/kedaibiao-channel \
  --candidates data/verified_corrections.json \
  --hotwords-file data/verified_hotwords.txt \
  --output data/channel_vocab.json
```

历史字幕里自动推断出的专有词和混淆对只在显式 `--audit-output` 时另存审计文件，不直接进入 runtime。这样不会把“出现频率高”误当成“可以自动改”。

## 产物与失败语义

**交付区**是源媒体目录，只放可使用的最终资产：

| 文件 | 用途 |
|---|---|
| `<video>.final.srt` / `.final.vtt` | 通过 QC 的同文字幕 |
| `<video>.speaker_labeled.srt/.md` | 可选说话人归因稿 |
| `<video>.highlights.md` | 高光、时间戳与剪辑定位 |
| `<video>.article.md` | 指定 surface 的文章 |
| `<video>.titles.md` | 标题候选与推荐 |
| `<video>.youtube-description.txt` | 已验证的 description 与章节 |
| `<video>.clean.mp4` | 可选非破坏性清理版；重映射字幕在通过复核与 QC 前仍是 candidate |

**工作区**默认是 `<video>_process/`，包含 raw ASR、corrected 字幕、字幕 QC、article brief/context、writing-skill 快照、editorial notes、diarization 数据和标题轮次草稿。

主流程失败会退出非零。诊断文件可能仍然存在，目录里也可能有此前运行的旧产物；以本次退出码、终端摘要和 QC 报告为准，不用“看见文件”代替成功判断。

## 可选：说话人归因

安装独立环境：

```bash
/opt/homebrew/bin/python3.11 -m venv venv-diarization
venv-diarization/bin/pip install -r requirements-diarization.txt
venv-diarization/bin/hf auth login
```

按 Hugging Face 页面提示接受 pyannote 模型条款。最轻量的 diarization：

```bash
venv-diarization/bin/python tools/speaker_attribution.py /path/to/video.mp4 \
  --srt /path/to/video.final.srt --num-speakers 2
```

有单人参考音频时：

```bash
venv-diarization/bin/python tools/build_speaker_refs.py /path/to/solo.m4a \
  --speaker host --out-dir data/speakers/host/refs --count 3 --clip-seconds 10

venv-diarization/bin/python tools/speaker_attribution.py /path/to/video.mp4 \
  --srt /path/to/video.final.srt \
  --speaker-ref host=data/speakers/host/refs/host_ref_01_000120s.wav \
  --assign-remaining guest --num-speakers 2
```

参考音频文件名末尾的秒数由 `build_speaker_refs.py` 自动生成；上面的 `000120s` 只是示例，实际命令使用脚本刚输出的路径。

声纹是生物识别材料，不提交 GitHub。ASR 负责“说了什么”，diarization 只负责“谁在说”；低置信位置保留 `UNKNOWN`／`MIXED`。
高光和文章只会自动采用与当前 final SRT 的 cue 时间轴和去除 speaker 前缀后文字一致的 `.speaker_labeled.srt`；无法校验或已过期的 sidecar 不会替换本次逐字稿。

## 可选：口头禅与假启动剪辑

先建立并审核 `<video>_process/<video>.filler-cuts.json`，然后 dry-run：

```bash
venv/bin/python tools/render_filler_cuts.py /path/to/video.mp4 \
  /path/to/video_process/video.filler-cuts.json \
  --output /path/to/video.clean.mp4 \
  --srt-in /path/to/video.final.srt \
  --srt-out /path/to/video_process/video.clean.candidate.srt \
  --dry-run
```

确认区间和预计删减时长后去掉 `--dry-run`。脚本只执行 `decision: "cut"` 的区间，永不覆盖源视频。字幕会按同一计划重映射时间，但先命名为 `.clean.candidate.srt`；部分 cue 内的文字不会被猜测性改写，必须对照成片修字，再通过 `subtitle_qc.py --promote-srt ... --write-vtt ...` 晋升为 `.clean.final.srt/.vtt`。详细判断与命令见 `skill/references/filler-cut-editing.md`。

## 外部制作与发布

- 封面默认做独立的 YouTube 16:9 和小红书 3:4 可替换模板；4:3 按需。使用真实人物、`#238343` 品牌识别和正确 LOGO-006，具体见 `skill/references/cover-style-guide.md`。
- 双 WAV、剪前导和社区版压制见 `skill/references/longform-community-delivery.md`。这些是 recipe，不是主脚本承诺。
- Google Doc、Canva 与平台草稿依赖已安装 connector／浏览器能力。仓库不会自动安装或检测这些外部服务。
- 本地稿与平台 draft 可以直接创建；公开发布、通知、群发或覆盖线上内容前必须展示最终 payload、目的地和受众并取得批准。

## 开发与验证

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m compileall -q tools tests
python3 tools/process_video.py --help
```

单元测试覆盖文件契约、失败语义、字幕重切/QC、模型 CLI 封装与文章 context。它们不替代真实的 ASR 下载、Codex／Claude 登录、pyannote、ffmpeg 全片解码或平台上传测试。

## 常见问题

**ASR 看起来没动？** 应使用当前 venv 同目录或 PATH 中的 `mlx-qwen3-asr` CLI，并保留可见进度。第一次运行还可能在下载模型。

**校对显示 0 个修改？** 这不是质量证明。结合画面、seeds 和语境抽查专有名词、数字与否定词。

**为什么没有 VTT？** 查看 `<video>_process/<video>.subtitle_qc.md`；QC 未通过时不会晋升 VTT。

**Codex 不可用？** 内容步骤会显式报告并尝试 Claude fallback；两者都不可用时失败，不把旧文件冒充本次结果。Codex 内容模型可用 `LIZHENG_CODEX_CONTENT_MODEL`（或通用的 `LIZHENG_CODEX_MODEL`）覆盖，Claude fallback 可用 `LIZHENG_CLAUDE_FALLBACK_MODEL`（或 `LIZHENG_CLAUDE_MODEL`）覆盖；不设置时使用各自 CLI 当前默认。

**为什么 fresh clone 没有小红书专用 skill 或 Canva？** 它们是可选外部能力。核心脚本自包含；外部能力可用时由 `lizheng-video-editing` 编排，不把个人机器路径写成仓库依赖。
