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

用户说“起标题”时，默认每个方案同时交付视频标题和封面上的标题文案；最终回复也要成对直接列出，不能只把封面文案藏在文件里。封面文案属于标题交付，实际设计或渲染封面按需进行。用户明确只要视频标题时按其要求缩小范围。只要 description 就只完成 description，不为了“完整”生成其余文件。已有字幕时从字幕开始，不重复转写。

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

字幕链路是：原始转写 → 全文精校 → 先合并再重切成 candidate → 正文字符流与机械边界风险检查 → 语义断句复核 → 时间／长度 QC → 从同一 cue 列表晋升 SRT 与 VTT。字数上限只是显示约束，不是断句目标；规则脚本产出的等长短句即使结构、时长和阅读速度都合格，也不因此成为可交付字幕。

主流程会拦住连续“接近字数上限、又没有语义标点”的机械装箱形态。触发时保留 candidate 和报告，停止所有下游；按 `references/subtitle-delivery.md` 重建语义边界、校验没有增删改正文并人工通读后，再单独晋升。未触发只代表没有命中这一类风险，不替代人工语义验收。

长视频、拉丁词边界、时间码和字幕验收见 `references/subtitle-delivery.md`。

## 内容资产

### 高光

`tools/generate_highlights.py` 输出带时间戳的候选片段。高光的原话负责让剪辑师定位；它不要求标题、封面和文章逐字照抄。

选择时先找材料真正稀缺的部分：第一手经历、具体机制、重要定义、决策与代价、现场修正，以及只有这个人处在这个位置才看得到的东西。悬念和戏剧性有用，但不能替代 substance。

一般高光是内容发现，不自动等于视频开头。标题与封面定下后，再按当前包装选 cold open：片段应让观众确认自己点对了、让同一个问题更值得追，或直接进入答案；否则即使很精彩，也留在正文。若访谈的最强 premise 分散在多处、没有干净原片能承担这件事，优先给主持人写一段可补录的 narrative intro。

### 文章

视频 skill 只负责准备上下文与文件契约，正文只交给一个主责 writing skill：

| 类型 | 主责 writing skill | `surface=auto` |
|---|---|---|
| 访谈 | `expert-interview-article` | `companion` |
| 单口 | `substance-writing-review` | `article` |

`article`、`community`、`companion`、`release` 分别是独立文章、独立社区帖、视频伴读／活动回放和短发布介绍。采访者回看后的观察写入 `<video>_process/<video>.editorial-notes.md`；把它当待验证判断，用逐字稿、反证和人物回应校准，不直接当事实。

运行时优先读取本机当前 writing skill，fresh clone 使用 `data/writing-skills/` 的版本化 fallback。实际注入的 `SKILL.md` 主文件、来源和 hash 都写入本期快照与 article context；无工具模型不会自行读取主文件引用的外部 reference，因此主文件必须自包含。

### 标题

需要完整包装时运行标题流程：先找观众原有观看动机与本期独有证据的交点，再把标题、封面与视频兑现位置一起生成。另一路 challenger 不看 brief 和首轮答案，先重新选题，再把两组候选放在一起冷启动检验：

先保住原材料中的具体矛盾、动作和对话转折，再提炼观点；已有完整字幕时，摘要或文章不能替代它。候选应显出观众尚不知道或仍想看的东西，警惕把现场问题压成“建立信任／长期主义”等观众以为早懂的道理。先比较少量不同的观看承诺，再磨语言；模型冷读与排序只用于编辑判断，不代表真实观众测试。

```bash
venv/bin/python tools/generate_titles.py /path/to/video.article.md \
  --output-dir /path/to/delivery \
  --workspace-dir /path/to/video_process \
  --source-srt /path/to/video.final.srt
```

主流水线会自动把 final SRT 作为开头定位材料，并在存在时采用与之匹配的 speaker sidecar；单独以文章运行时也应传入 final SRT，必要时可显式加 `--speaker-srt`。没有带时间逐字稿时，首选仍可设计主持人补录 intro，但不能声称原片 cold open 已经可执行。没有通过校验的 speaker sidecar 时，不替原片声音强行标注主持人或嘉宾。

用户要求快速 brainstorm 时可以直接提出成套的标题与封面方向。标题不是全文概括；一个由视频充分兑现的强段落，可以比覆盖整期的平庸摘要更适合作主标题。历史标题是扩展判断的样本，不是只能照着走的模板。

频道核心发现受众关心科技、进步、AI 与个人成长，很多人在科技公司工作；高管、学生和创业者是自然延伸。垂直话题仍先找这些人会停下来的入口。嘉宾是 VC，不意味着主标题要服务 VC；人物身份、公司与数字可以成为包装中心，但需要它们本身已有观众看得懂的意义，而不是靠编辑事后迁移。

标题、封面与开头从同一个 premise 一起判断：组合后能否迅速显出值得花时间的回报，第一段能否确认并开始兑现这份期待。实用价值、向往、人物兴趣和情感体验都可以驱动观看，不必统一制造焦虑或悬念；可以透露结论，只要过程、证据、反例或体验仍值得看。`<video>.titles.md` 的 `开头衔接` 应给出通过 QC 的 cue-level 原片 cold open、主持人补录 intro 或有理由的 hybrid，而不是泛泛说“用高光开场”。正文缺少关键兑现时，明确指出需要补的答案、案例或推演，或改用可兑现的承诺；开头不能替代缺失的正文。若观看动机需要标题之外的一段编辑解释，这个 premise 通常还不适合做主包装。具体判断见 `../data/guideline_kedaibiao.md`；实际制作封面再读 `references/cover-style-guide.md`。

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
| `<video>.titles.md` | 首选与备选标题 × 封面组合、兑现位置和开头衔接 |
| `<video>.youtube-description.txt` | description 与章节 |
| `<video>.cover-16x9.png` / `.cover-3x4.png` | 两个平台的独立封面 |
| `<video>.clean.mp4` | 可选非破坏性清理版；同步字幕需先以 candidate 复核并通过 QC |

过程文件不冒充交付文件。程序失败后即使目录里存在旧文件，也要说明它们是否来自本次运行。
口头禅剪辑重映射出的 `<video>.clean.candidate.srt` 留在工作区；修正部分相交 cue 的文字并通过 QC 后，才晋升为 `.clean.final.srt/.vtt`。

## 完成前看五件事

1. 专有名词、数字、日期和人物归因是否可追溯且全链路一致。
2. 字幕是否按意思而不是按字数断开；机器门与人工语义复核是否都通过，SRT/VTT 是否来自同一 cue 列表。
3. 文章、标题与 description 是否抓住了本期独特内容，而不是用熟悉的空泛概念代替理解。
4. 封面人物身份、表情、文字和品牌资产是否正确，缩略图尺寸下是否仍清楚。
5. 用户只要求草稿时是否始终停在草稿；发布前是否展示最终 payload、目的地与受众并取得批准。

## 持续校准

把反馈更新到最准确的 owner：字幕机制进脚本与测试，频道标题经验进 `data/guideline_kedaibiao.md`／`top_titles.txt`，文章判断进对应 writing skill，品牌规则进 `superlinear-brand-usage`。记录原理、适用条件和失败模式，不把一次修改追加成永久禁句表。

只有在维护标题系统或核对外部方法归因时，才读 `references/title-packaging-research.md`；普通标题生成不需要加载它。
