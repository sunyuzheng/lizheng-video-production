# Lizheng Video Production · 技术决策

这份文档记录实现层的长期判断。任务路由在 `skill/SKILL.md`，运行方式在 `README.md`；这里解释为什么系统这样设计。

## 1. 确定性和编辑判断分层

时间码、断句、格式转换、文件布局、退出码和 QC 应尽量由可测试的代码决定。高光、文章、标题与封面仍需要编辑判断；脚本负责提供上下文、候选和可追溯文件，不把 prompt 输出冒充确定性结果。

这条边界避免两个相反错误：用人工反复做机器能稳定完成的事，或把本来需要理解材料的工作伪装成自动规则。

## 2. 文件是运行状态

源媒体不覆盖，raw ASR、corrected 和 final 保持不同文件角色。默认重跑 ASR 时，输出先落在本次隔离目录；只有结构有效的当前产物才刷新 `.qwen.srt`，失败则保留上次版本。每一步读明确文件、写明确文件；工作区保存模型快照、brief、context、QC 和轮次草稿。下游不依赖一次会话里“模型还记得什么”。

文章阶段保存实际注入的 writing skill 主文件、来源与 SHA-256，因此能核对或固定当时的主责写作契约。它不等于整次运行的精确复现：还需要相同代码版本、本期素材和显式输入；主文件按需引用的外部 reference 不会被无工具模型自动读取。

## 3. 在信息缺失发生的位置补信息

新嘉宾名和产品名如果在 ASR 阶段被听成同音字，下游很难凭空恢复。`--seeds` 在转写前提供当期实体，精校阶段再检查全文一致性。单期实体留在当期；只有多期复用且经过确认的术语才进入频道词库。

词汇表与规则修正只处理有可靠证据的模式；开放式全文精校用于语境和实体一致性，但必须保留原始稿并接受 QC，而不是静默覆盖。

## 4. 字幕 QC 是依赖门

`.final.srt` 只有在 SRT 结构、正时长、重叠、长度、最小时长与阅读速度检查通过后，才导出 `.final.vtt` 并允许下游内容生成。失败时保留诊断稿和报告，但流程 fail-fast。

“文件存在”不是成功条件：旧产物可能来自此前运行，主流程必须依靠本次状态和退出码报告成功。

SRT/VTT、article/brief/context/writing-skill 快照、以及清理版视频/重映射字幕都是多文件契约。它们先全部准备好，再成组提交；中途 I/O 失败时恢复上一组，不留新旧混合交付。

## 5. 模型调用使用最小权限

文本生成不需要让模型读取任意工作区或调用 shell。Claude 以无工具、安全、无持久会话模式接收 prompt，Python 捕获 stdout 并写入目标文件。Codex fallback 使用临时空工作区、只读 sandbox、ephemeral session，并忽略用户工具配置；不让内容生成步骤获得不必要的项目读取或外部写权限。

具体模型名称属于运行配置而不是文档事实。脚本允许环境变量或 CLI 显式覆盖，并尽量使用已登录 CLI 的当前默认，避免 README 因模型版本变化而漂移。

## 6. 文章只有一个编辑 owner

访谈由 `expert-interview-article` 主责，单口由 `substance-writing-review` 主责。视频流水线组织字幕、高光、嘉宾资料、采访者观察与发布 surface，但不再叠加第二套缩水的文章规范。

`article`、`community`、`companion`、`release` 是不同产物契约，而不是文风标签；只有 companion 默认把时间戳变成观看导航。

## 7. 历史样本扩大判断，不构成发布 gate

三轮标题流程把发散、外部样本评审与收敛拆开，适合系统性扩大候选。编辑也可以直接提出标题，特别是快速 brainstorm、新平台或当期出现历史样本未覆盖的强角度时。

高光、标题和封面共同服务观看路径，但没有永久的“不重复”规则。历史高播放标题与频道 guideline 是先验；最终选择仍由材料、平台、受众和诚实兑现共同决定。

## 8. 自动化能力与 agent recipe 分开

`process_video.py` 只承诺仓库中真正实现并测试的步骤。双 WAV 漂移校正、剪前导、社区版压制、封面设计、Google Doc 与平台草稿属于条件能力：有脚本的用独立脚本，没有脚本的明确标成 agent/ffmpeg/connector recipe。

这样 README 可以回答“fresh clone 后到底能跑什么”，skill 则负责在更完整的真实工作中编排外部能力。

## 9. 数据资产

| 文件 | 作用 | 维护原则 |
|---|---|---|
| `data/guideline_kedaibiao.md` | 频道标题、高光与包装判断 | 只收跨期可复用的机制与条件 |
| `data/top_titles.txt` | 真实高播放标题样本 | 按实际数据更新，不当模板 |
| `data/verified_hotwords.txt` | 人工确认的跨期 ASR 热词 | 一行一词；单期实体不进入 |
| `data/verified_corrections.json` | 人工确认的纠错候选 | 有证据且需语境判断，不做盲目全局替换 |
| `data/channel_vocab.json` | 由以上两份生成的最小 runtime schema | 只保留脚本实际消费的字段 |
| `data/writing-skills/*.md` | fresh clone 的自包含 writing-skill fallback | 同步 canonical skill 的核心契约，不依赖其本机 reference 路径 |

每次修改先找准确 owner。复制同一原则到 README、skill、prompt 和 data 会重新制造漂移。
