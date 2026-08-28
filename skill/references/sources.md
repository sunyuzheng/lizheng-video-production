# Skill layout and ownership

`lizheng-video-production` 是实现与 skill 的 canonical 公共仓库：

- `skill/SKILL.md`：任务路由、文件契约与工作流编排；
- `skill/references/`：只在条件任务中读取的制作说明；
- `tools/`：可执行、可测试的自动化；
- `data/`：频道标题基准、术语和 writing-skill fallback；
- `README.md`：fresh clone 安装、CLI 与能力边界。

安装后的 canonical skill name 是 `lizheng-video-editing`。Codex 与 Claude 可以分别把仓库的 `skill/` 链接到：

- `~/.codex/skills/lizheng-video-editing`
- `~/.claude/skills/lizheng-video-editing`

旧名 `kdb-video-post-production` 只作为迁移背景，不再是 README 或 frontmatter 的当前名称。

## 下游 owner

- 访谈文章：运行时优先使用已安装的 `expert-interview-article`，仓内 `data/writing-skills/expert-interview-article.md` 是版本化 fallback。
- 单口文章：运行时优先使用已安装的 `substance-writing-review`；仓内同名文件同步自公开仓库 `https://github.com/sunyuzheng/substance-writing-review`，作为 fresh clone 的版本化 fallback。
- 品牌 logo、颜色和资产限制：`superlinear-brand-usage`。
- 小红书平台专用标题手艺：如果安装了独立的 `xhs-cover-title`，可以作为额外候选来源；主流水线不能依赖用户机器的绝对路径。

每次文章运行把实际注入的 writing-skill 主文件保存为本期快照，并在 article context 记录来源与 SHA-256。它让当时的主责契约可核对、可固定；完整复现还依赖相同代码、本期素材与显式输入。主文件引用的外部 reference 不会在无工具流水线里自动加载。

## 什么应当改在哪里

- 可确定执行的行为、退出码与格式：脚本和测试；
- 视频流程路由与交付边界：本 skill；
- 频道标题、高光经验：`data/guideline_kedaibiao.md` 与 `data/top_titles.txt`；
- 对外文章判断：对应 writing skill；
- 品牌事实：品牌 owner；
- 平台限制：运行时查证，不把易变数字写成永久事实。

同一原则不要复制进多个 owner。需要引用时描述路由和文件契约，不维护第二套缩水版规范。
