# 口头禅、重复与假启动的非破坏性剪辑

仅在用户明确要求剪掉口头禅、犹豫、重复或假启动，并需要输出新视频时读取。

这不是词表删除。中文里的“这个”“就是”“然后”可能是噪音，也可能承担指代、判断、节奏或强调。先判断语义，再定位声画，最后执行可回退的 edit plan。

## 三层判断

1. **语义**：删除后句子是否仍完整，指代、否定、因果和强调是否不变。“这个问题”“就是 Codex 写的”通常不能按口头禅删。
2. **修正**：重复词、未完成后完整重说、长犹豫音和明显假启动更适合候选；优先删前一遍，保留信息更完整的一遍。
3. **声画**：切点是否伤到相邻音节，屏幕录制的鼠标、窗口或人物动作是否会突跳。高风险位置只记候选，不自动 cut。

## Edit plan

用 word-level timestamps 辅助定位，但由人或 agent 逐项审过。计划写入 `<video>_process/<video>.filler-cuts.json`：

```json
{
  "cuts": [
    {
      "start": 12.34,
      "end": 12.82,
      "label": "呃",
      "reason": "未完成假启动前的独立犹豫音",
      "decision": "cut"
    }
  ]
}
```

只有 `decision: "cut"` 的区间会执行。时间使用源视频秒数；重叠或非法区间应让脚本失败，不猜测修复。

## 先 dry-run，再渲染

```bash
cd <implementation_root>

venv/bin/python tools/render_filler_cuts.py /path/to/video.mp4 \
  /path/to/video_process/video.filler-cuts.json \
  --output /path/to/video.clean.mp4 \
  --srt-in /path/to/video.final.srt \
  --srt-out /path/to/video_process/video.clean.candidate.srt \
  --dry-run

# 审核区间与预计删减时长后，去掉 --dry-run 真正渲染
```

永远写新文件，不覆盖源视频。字幕需要用同一 edit plan 重映射；不要分别手工改视频和字幕时间码。

脚本只重映射 cue 时间：整条落在 cut 内会删除，部分相交的 cue 会缩短或平移，但脚本不会根据 `label` 猜测性地删除 cue 内某几个字。因此输出只能叫 `.clean.candidate.srt`。凡是 cut 穿过一个仍保留的 cue，字幕文本都要回看成片复核并按实际语音修正。

复核修字后再晋升：

```bash
venv/bin/python tools/subtitle_qc.py \
  /path/to/video_process/video.clean.candidate.srt \
  --promote-srt /path/to/video.clean.final.srt \
  --write-vtt /path/to/video.clean.final.vtt \
  --report /path/to/video_process/video.clean.subtitle_qc.md
```

命令退出非零时不晋升 SRT；不要手工把 candidate 改名成 final 绕过质量门。

## 渲染后 QC

- 成片时长与“源时长 − 计划删减”差异不超过 0.2 秒；
- 音视频同步，切点前后没有吞字、爆音或意外长静音；
- 重点复听无自然停顿的相邻词、长犹豫音和多段连续修正；
- 屏幕录制的视觉跳变仍可接受；
- `.clean.final.srt/.vtt` 来自同一份已复核 candidate，cue 单调递增、无重叠，并重新通过字幕 QC。
- 与 cut 部分相交的 cue 文本已经按实际成片复核，不残留被剪掉的词。

如果一处候选在语义或声画上拿不准，保留比强删更好。这个流程优化的是观看流畅度，不是把人的说话方式磨成合成语音。
