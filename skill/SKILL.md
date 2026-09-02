---
name: lizheng-video-editing
description: 为课代表立正／KDB 视频从原始媒体或已有字幕开始完成转写、精校、断句、VTT/QC，并按需生成高光、文章、标题、YouTube description、封面及发布草稿。适用于用户给出视频、音频或 SRT 并要求一个或多个视频发布资产；纯转写优先用 transcribe，已有可读访谈材料且只写对外文章优先用 expert-interview-article。
---

# Lizheng Video Editing

这套 skill 负责把一期视频的原始材料组织成可靠的字幕和可发布的内容资产。它既不是“每次都跑全套”的固定流水线，也不替文章、品牌或复杂视频设计的专门 skill 做判断。

## 先路由，再动手

| 请求 | 主责 |
|---|---|
| KDB 视频／SRT → 字幕、高光、标题、description、封面或组合交付 | 本 skill |
| 普通音视频只转文字，不需要 KDB 字幕交付规格 | `transcribe` |
| 已有可读访谈材料，只写对外文章／社区帖 | `expert-interview-article` |
| 单人口播素材要真正剪成可发布短视频 | `kdb-talking-head-short-production` |
| 已有成片要做定时重构、动态图形或复杂视觉包装 | `talking-head-recut`／`hyperframes` |
| 选择 Superlinear logo、颜色和品牌资产 | `superlinear-brand-usage` |

用户只要标题或 description，就只完成该产物；不要为了“完整”生成其余文件。已有字幕时从字幕开始，不重复转写。

## 两个工作原则

- **确定性环节交给脚本**：转写、精校调用、断句、时间码、QC、格式导出和可验证的剪辑计划尽量可复现；agent 负责判型、编辑判断、设计和外部平台操作。
- **文件是状态，不靠会话记忆**：源媒体不覆盖；`.qwen.srt` 只由本次成功且结构有效的 ASR 刷新，不被校对稿或失败运行反写。本期观察、嘉宾资料、writing-skill 快照和中间草稿落进工作区，下游只读取明确文件。

字幕精校和内容资产默认使用 Codex。高光、文章、标题与 description 在 Codex 失败时可降级到无工具的 Claude；降级必须明示，两条路径都失败时不复用旧产物伪装成功。

## 找到实现目录

先以本 `SKILL.md` 为起点寻找 `tools/process_video.py`：检查当前目录，再检查上一级。找到的目录记为 `<implementation_root>`。不要依赖某台机器的 Desktop 绝对路径。

文件分区：

- **交付区**：源媒体所在目录，只放用户可直接使用的最终文件。
- **工作区**：默认 `<video>_process/`，保存 raw ASR、corrected 字幕、QC、brief、context、模型快照和轮次草稿。
- **资料区**：实现目录的 `data/` 与 `skill/references/`，只在对应任务需要时读取。

## 核心字幕链路

先收集本期人名、公司、产品和技术术语作为 `--seeds`。它们是当期上下文，不自动进入频道长期词库。

```bash
cd <implementation_root>

# 全链路
caffeinate -i venv/bin/python tools/process_video.py /path/to/video.mp4 \
  --seeds 嘉宾名 公司名 产品名

# 只做字幕、VTT 和 QC
caffeinate -i venv/bin/python tools/process_video.py /path/to/video.mp4 \
  --seeds 嘉宾名 公司名 产品名 \
  --skip-highlights --skip-article --skip-titles --skip-youtube-description

# 已有 corrected/final SRT，显式从该稿重跑 QC 与所需下游
caffeinate -i venv/bin/python tools/process_video.py /path/to/video.mp4 \
  --subtitle-source /path/to/video.final.srt --no-seeds
```

字幕链路是：原始转写 → 全文精校 → 先合并再重切 → QC → 从同一 cue 列表导出 VTT。QC 是下游硬门：失败时保留诊断稿和报告，但不把 VTT 当成交付，也不继续生成高光、文章、标题或 description。

长视频、拉丁词边界、时间码和字幕验收见 `references/subtitle-delivery.md`。

## 内容资产

### 高光

`tools/generate_highlights.py` 输出带时间戳的候选片段。高光的原话负责让剪辑师定位；它不要求标题、封面和文章逐字照抄。

选择时先找材料真正稀缺的部分：第一手经历、具体机制、重要定义、决策与代价、现场修正，以及只有这个人处在这个位置才看得到的东西。悬念和戏剧性有用，但不能替代 substance。

### 文章

视频 skill 只负责准备上下文与文件契约，正文只交给一个主责 writing skill：

| 类型 | 主责 writing skill | `surface=auto` |
|---|---|---|
| 访谈 | `expert-interview-article` | `companion` |
| 单口 | `substance-writing-review` | `article` |

`article`、`community`、`companion`、`release` 分别是独立文章、独立社区帖、视频伴读／活动回放和短发布介绍。采访者回看后的观察写入 `<video>_process/<video>.editorial-notes.md`；把它当待验证判断，用逐字稿、反证和人物回应校准，不直接当事实。

运行时优先读取本机当前 writing skill，fresh clone 使用 `data/writing-skills/` 的版本化 fallback。实际注入的 `SKILL.md` 主文件、来源和 hash 都写入本期快照与 article context；无工具模型不会自行读取主文件引用的外部 reference，因此主文件必须自包含。

### 标题

需要完整包装时运行标题流程：先建立观众认知转变 brief，再经过候选、独立 challenger 与终审；流程会参考 `data/top_titles.txt` 与频道 guideline：

```bash
venv/bin/python tools/generate_titles.py /path/to/video.article.md \
  --output-dir /path/to/delivery \
  --workspace-dir /path/to/video_process
```

用户要求快速 brainstorm 时可以直接提出标题；历史标题是扩展判断的样本，不是只能照着走的模板。标题要让目标观众迅速理解“为什么点开”，人物身份、数字、冲突、问题和结论都是可选手段，取决于当期真正有分量的内容。

写候选前，先说清一个具体的 `看前 → 看后`：哪类观众原本怎样理解或描述问题，视频提供了什么足以改变其判断的新信息。一个厉害人物、一家知名公司或一次早期判断通常先是答案可信度的证据；若拿掉这些名字后说不清观众为什么在意，标题仍停留在人物履历或内容摘要。遇到这种情况要回到材料重新找观众正在付出代价的困惑，不要只给旧标题换更刺激的词。

完整节目还要区分频道较广的发现受众与嘉宾同行／专业子群。用户没有指定垂直投放时，第一名优先选择能被更广受众迁移到自身重要选择、且由整期充分兑现的问题；专业人群很痛但覆盖较窄的角度可以明确作为备选或切片。广泛不等于泛化，不能把视频没有回答的问题硬说成人生道理。

频道标题与高光的判断基准见 `../data/guideline_kedaibiao.md`。

### YouTube description

入口：`tools/generate_youtube_description.py <video>.final.srt`。

开头直接给 substance：本期具体讨论什么、出现了哪些难得事实或问题、观众为什么可能关心。不要先写“本期适合谁”“最有价值的一条线”之类元叙述。章节使用真实时间戳，从 `00:00` 开始；通过 validator 后才称为可复制交付稿。

## 条件能力

- **说话人归因**：访谈需要严格区分主持人与嘉宾时，运行本地 diarization／speaker reference 流程；`UNKNOWN`、`MIXED` 不靠语义强行归人。安装与命令见 README。
- **口头禅、重复与假启动剪辑**：只在用户要求真实剪辑时做，生成可审查 edit plan，再非破坏性渲染。见 `references/filler-cut-editing.md`。
- **独立 WAV、剪前导、长视频社区版**：这是 agent/ffmpeg recipe，不是 `process_video.py` 的自动能力。见 `references/longform-community-delivery.md`。
- **封面**：默认交付 YouTube 16:9 与小红书 3:4 两套可直接替换模板；其他比例按发布平台需要。先读 `references/cover-style-guide.md`，品牌资产再路由 `superlinear-brand-usage`。
- **Google Doc、Circle／社区草稿**：属于外部 connector 或浏览器操作。先在本地准备完整稿与资产，只创建草稿；任何公开发布、通知或群发都需要对最终 payload 和目的地重新确认。

## 交付契约

| 产物 | 含义 |
|---|---|
| `<video>.final.srt` / `.final.vtt` | 通过 QC 的同文字幕交付 |
| `<video>_process/<video>.subtitle_qc.md` | 字幕质量报告 |
| `<video>.speaker_labeled.srt/.md` | 可选说话人归因稿 |
| `<video>.highlights.md` | 高光候选与剪辑定位 |
| `<video>.article.md` | 指定 surface 的文章 |
| `<video>.titles.md` | 标题候选与推荐 |
| `<video>.youtube-description.txt` | description 与章节 |
| `<video>.cover-16x9.png` / `.cover-3x4.png` | 两个平台的独立封面 |
| `<video>.clean.mp4` | 可选非破坏性清理版；同步字幕需先以 candidate 复核并通过 QC |

过程文件不冒充交付文件。程序失败后即使目录里存在旧文件，也要说明它们是否来自本次运行。
口头禅剪辑重映射出的 `<video>.clean.candidate.srt` 留在工作区；修正部分相交 cue 的文字并通过 QC 后，才晋升为 `.clean.final.srt/.vtt`。

## 完成前看五件事

1. 专有名词、数字、日期和人物归因是否可追溯且全链路一致。
2. 字幕 QC 是否真的通过，SRT/VTT 是否来自同一 cue 列表。
3. 文章、标题与 description 是否抓住了本期独特内容，而不是用熟悉的空泛概念代替理解。
4. 封面人物身份、表情、文字和品牌资产是否正确，缩略图尺寸下是否仍清楚。
5. 用户只要求草稿时是否始终停在草稿；发布前是否展示最终 payload、目的地与受众并取得批准。

## 持续校准

把反馈更新到最准确的 owner：字幕机制进脚本与测试，频道标题经验进 `data/guideline_kedaibiao.md`／`top_titles.txt`，文章判断进对应 writing skill，品牌规则进 `superlinear-brand-usage`。记录原理、适用条件和失败模式，不把一次修改追加成永久禁句表。
