# 来源、归属与运行入口

## 单一维护位置

`video-title-and-cover` 是可单独发现、使用的 skill，不需要先加载完整视频制作流程。源文件位于 `lizheng-video-production` 实现仓库的 `skills/video-title-and-cover/`，用户 skill registry 与运行时链接到这里。

- `SKILL.md`：任务边界、封标核心判断、模式选择与交付。
- `references/editorial-judgment.md`：详细编辑判断；也是标题与高光脚本实际读取的规范正文。
- `references/cover-production.md`：人物选帧、原图与修饰、16:9／3:4 排版和成图检查。
- `references/cases.md`：本对话与 Mandy 档案的反馈机制、适用条件和未定稿状态。
- `references/research.md`：外部方法的来源、归因和证据边界；不要求每次生成加载。

## 与原视频工作流兼容

从本 skill 目录向上两级是实现根目录。以下旧路径使用**仓内相对软链接**，不维护第二份正文，fresh clone 也不依赖个人机器路径：

| 原入口 | 当前 owner |
|---|---|
| `data/guideline_kedaibiao.md` | `skills/video-title-and-cover/references/editorial-judgment.md` |
| `skill/references/cover-style-guide.md` | `skills/video-title-and-cover/references/cover-production.md` |
| `skill/references/title-packaging-research.md` | `skills/video-title-and-cover/references/research.md` |

`tools/generate_titles.py` 与 `tools/generate_highlights.py` 继续读取 `data/guideline_kedaibiao.md`，读到的是完整规范正文，不是导航页。CLI、输出格式及 `process_video.py` 调用契约不变。多轮标题流程只是可用实现，不是每次人工精修都要运行的前置条件。只拷贝旧脚本或 `data/` 不构成完整安装，应保留仓库及相对链接目标。

字幕、description、通用高光与整套后期由 `lizheng-video-editing` 编排；本 skill 负责封标判断、成图与相应开头的承诺连续性。录制前的 working package 可由本 skill 提供给 `idea-to-camera-ready-talk`；口播稿、录制卡仍归后者。`xhs-cover-title` 是可选的平台语感与历史案例补充，当前任务不机械继承其“必须不重复／必须留悬念”等较窄偏好。

## 来源

- 原 `data/guideline_kedaibiao.md`、封面视觉规范、外部包装研究；本次迁移保留原有自然人物选帧与修饰校准。
- 用户提供的 [Mandy × 课代表立正封面标题档案](https://wcnn9ad2kr0b.feishu.cn/wiki/RX7dwLUUJiyVEekXvWBcNZZQncb?from=from_copylink)，2026-09-13 读取；具体机制与边界在 `cases.md`。
- 一周年、晶晶会员访谈、fake work 等本对话反馈。成图、字幕、当期候选和精确数据保留在视频所属目录，不复制进 skill。
- 原仓 `data/top_titles.txt` 是频道历史样本，仍留在实现根目录；不能代替外部优秀创作者的实际封面与标题研究。

新增学习先区分可复用判断、当期事实与未验证假设。更新 skill 不会自动改线上标题或封面；公开发布仍按当前会话授权处理。
