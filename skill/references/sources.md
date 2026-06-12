# Sources

## Layout（2026-06-12 起，单仓库）

Skill 与实现合并在同一个仓库：

- `skill/SKILL.md` — 工作流权威定义（判型、三区、流水线、交付物、验收、持续校准）
- `tools/` — 全部实现脚本（转写、精校、断句、高光、文章、标题）
- `data/` — 资料区：`guideline_kedaibiao.md` 编辑标准、`top_titles.txt` 高播标题基准、`channel_vocab.json` 频道术语库
- `README.md` — 仓库总览与运行方式

加载方式：`~/.claude/skills/kdb-video-post-production` → `/Users/sunyuzheng/Desktop/AI/skills/kdb-video-post-production` → 本仓库 `skill/`（两级符号链接）。编辑 skill 只改本仓库的 `skill/SKILL.md`。

历史：skill 曾独立成仓（github.com/sunyuzheng/kdb-video-post-production），因与实现总是同步演化、双仓库造成漂移，2026-06-12 合并入本仓库并删除旧仓。

## Supporting

- `/Users/sunyuzheng/Desktop/AI/context-infrastructure/rules/skills/workflow_kdb_video_brief.md`
  - Useful when post-production feeds into topic or brief generation
- `/Users/sunyuzheng/Desktop/AI/skills/xhs-cover-title/`
  - 小红书封面+标题路线（路线 B）的手艺来源，独立 skill，本流水线调用它
