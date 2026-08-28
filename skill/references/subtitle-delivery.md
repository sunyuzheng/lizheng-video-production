# 字幕精校与交付

仅在任务包含 KDB 字幕、VTT、长视频精校或字幕 QC 时读取。

## 一条时间轴

`.qwen.srt` 是原始记录，`.corrected.srt` 是全文精校稿，`.final.candidate.srt` 是重新断句后的诊断候选；只有通过 QC 才晋升为交付区的 `.final.srt` 和 `.final.vtt`。不要在 VTT 里单独改字；SRT 与 VTT 必须从同一 cue 列表生成。

精校优先处理会改变理解或传播的错误：人物、公司、产品、技术术语、数字、日期、否定词和同一实体的多种写法。屏幕录制可从可见 UI 补充 seeds，但不要只按文件名猜实体。

## 长视频

60 分钟以上默认给全文校对至少 900 秒。超时或模型失败时停止，不把 raw ASR 复制成 corrected 文件。字幕精校保留原话中的语气词、重复和不完整推理；如果任务确实要清理口头禅或 false start，转到口头禅剪辑流程，先修改成片，再按同一剪辑计划重映射字幕。

对较长材料做人工语境抽查：

- 每一处替换同时看前后 cue，避免搬词、重复和漏词；
- 全片抽查高频专有名词和数字，而不是只看模型报告的修改；
- `corrections=0` 不是质量证明；若画面或语境能确认错误，仍要修正；
- 拉丁词、人名和产品名不能从词中间断开。

## 断句与时间码

先把 ASR 的坏 cue 边界合并成时间窗口，再按句末标点、子句标点和词边界重切。默认每条可见字符不超过 20；极长的不可拆拉丁词可保留并交给 QC 报告提示人工选择。

剪掉前导或清理口头禅以后，字幕、章节和文章跳转时间都要映射到最终成片的 `00:00`，不能混用剪前时间。

## QC 硬门

默认交付门：

- 无法解析的 SRT block：0
- 非正时长：0
- cue 重叠：0
- 可见字符超过上限：0
- 时长短于 0.2 秒：0
- 阅读速度超过 25 字符／秒：0

```bash
cd <implementation_root>
venv/bin/python tools/subtitle_qc.py /path/to/video.final.candidate.srt \
  --promote-srt /path/to/video.final.srt \
  --write-vtt /path/to/video.final.vtt \
  --report /path/to/video_process/video.subtitle_qc.md
```

报告可以在失败时生成；`.final.vtt` 只有 QC 通过后才晋升为交付。主流程遇到 QC 失败应停止所有依赖最终字幕的内容步骤。

## 交付检查

1. `.qwen.srt` 与 `.corrected.srt` 留在工作区，corrected/final 不反写 raw；ASR 重跑失败时保留上一份 `.qwen.srt`。
2. `.final.srt` 和 `.final.vtt` cue 数、时间码和文本一致。
3. QC 报告属于本次运行，不是旧文件。
4. 术语、数字、日期和人物名字在字幕、章节、高光与文章中一致。
5. 导入目标剪辑软件或社区平台后做一次实际预览。
