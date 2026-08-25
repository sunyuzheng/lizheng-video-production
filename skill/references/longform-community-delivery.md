# 长视频、独立录音与社区回放交付

仅在用户提供独立录音、视频超过 60 分钟，或要求 Circle/社区活动回放时读取。

## 1. 双录音同步

1. 用机内声只做同步参考，不做最终节目音轨。
2. 每支 recorder 至少在前、中、后各取一个清晰的语音/瞬态锚点，分别测量它与 camera timeline 的对应时间。
3. 对每支录音独立拟合 `external_time = offset + rate × camera_time`：
   - `offset` 解决录音起点差；
   - `rate - 1` 是时钟漂移，报告为 ppm；
   - 拟合后复测全部锚点，单点残差目标 <20ms，平均残差目标 <10ms。
4. 用高质量重采样校正 rate，再按 offset 截取/补静音到 camera timeline。不要用单一 `adelay` 假设 90 分钟内完全无漂移。
5. 先通过单人说话片段判断哪支麦属于主持人/嘉宾，再决定左右声道。轨道命名要用人物名或角色，不用含糊的 A/B 交付。

## 2. 音频 QC

- 扫描 true peak、integrated loudness、掉线、长静音、爆音和饱和削波。交付参考为约 -16 LUFS、true peak 不高于 -1 dBTP；访谈以清晰和两人音色一致为优先。
- 如果一支麦在局部严重削波/噪声，可在有交叉收音的另一支 WAV 上平滑接管；交叉淡化要覆盖异常边缘，并在制作说明写明时间段。
- 用户明确要求“用 WAV 音轨”时，最终音频禁止混入机内声。机内声仅允许参与对齐分析。
- 导出后检查声道数、采样率、响度、峰值、时长差，并完整解码一次。

## 3. 开头剪辑与时间基准

- 语义起点是第一句正式开场，不是波形第一次有声音。
- 如需 stream-copy/keyframe 切视频，选择不吞掉首字的前一个干净关键帧；音频与字幕再精确落到该成片零点。
- ASR、SRT、VTT、章节和文章跳转时间必须全部基于剪后 master。禁止文章沿用剪前时间戳。

## 4. 字幕两轮精校

第一轮做全文实体、同音字、数字和术语一致性；60–120 分钟视频给 Codex 至少 900 秒，失败就停止，不落假 corrected 文件。

第二轮按相邻 cue 范围分段人工审校：

- 修改 JSON/清单中的每条 replacement 必须只包含目标 cue 的完整新文本；
- 每完成一段，逐条对照原始 cue 的前后各一条，检查跨 cue 搬词、重复、删词；
- 合并所有审校后再断句，不让不同审校段各自产生独立时间轴；
- 拉丁词、品牌、人名不能从中间拆分；SRT 和 VTT 从同一最终 cue 列表生成。

最终硬门：non-positive=0、overlap=0、visible chars >20=0、duration <0.2s=0、reading speed >25 chars/s=0。用：

```bash
cd <implementation_root>
venv/bin/python tools/subtitle_qc.py /path/to/video.final.srt \
  --write-vtt /path/to/video.final.vtt \
  --report /path/to/video_process/video.subtitle_qc.md
```

## 5. Circle 4GB 社区版

- 把 4GB 当硬上限，不当目标；默认做约 3.8GB，给容器 metadata 和平台计量差异留余量。
- 兼容性优先：MP4、H.264 High、yuv420p、1080p、AAC-LC 48kHz。按成片秒数倒推 video bitrate，先扣除音频和约 1–2% mux 余量。
- 完成后记录 exact bytes、时长、分辨率、帧率、codec、声道、首帧/首音频时间和音视频时长差；再完整解码到 null，任何 decode error 都要重做。
- thumbnail 用 16:9、清晰人物、缩略图可读的大字。生成模型可净化背景，但人物身份不变量；中文标题用确定性排版。

## 6. 帖子草稿

本地 Markdown 草稿至少包含：

1. 帖子标题；
2. 谁适合看、为什么值得看；
3. 嘉宾/主持人；
4. 视频、字幕和时长说明；
5. 带真实时间戳的观看地图；
6. 伴读文章正文；
7. 2–3 个讨论问题。

在 Circle 中选择明确的“活动回放”空间，只保存 draft，不发布。视频上传完成后再关联 VTT 和 thumbnail；如平台仍在处理，保持页面并等待状态完成，不能把本地文件名当作已上传成功的证据。
