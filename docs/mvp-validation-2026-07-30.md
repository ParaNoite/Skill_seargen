# MVP Validation - 2026-07-30

This document records the final MVP validation matrix for real public Bilibili videos.
Runtime media, model output, logs, frames, and generated skill packages remain local under
`runs/` and `skills/` and are not intended for Git.

## Environment

- Date: 2026-07-30 (Asia/Shanghai)
- Platform: Windows, PowerShell
- Python: repository `.venv`
- ASR: local faster-whisper backend
- Media tools: system `yt-dlp` and `ffmpeg`

## Execution Matrix

All rows used the same runtime configuration: `large-v3-turbo`, CUDA `float16`, 10-second
frame sampling, `--judge-difficulty off`, and the local `config.json` without a
`NEWAPI_API_KEY`. The missing key is intentional in this validation environment and makes
vision/OCR and distillation produce explicit skipped audit records.

| Video | Duration | Wall time | Media | Frames | ASR | Evidence | Final result |
| --- | ---: | ---: | --- | ---: | --- | ---: | --- |
| [BV1rpWjevEip](https://www.bilibili.com/video/BV1rpWjevEip/) | 2:43 | 78.259 s | failed: CDN connection refused after 10 retries | 0 | skipped | 2 metadata items | failure audit |
| [BV1Jgf6YvE8e](https://www.bilibili.com/video/BV1Jgf6YvE8e/) | 2:56 | 94.879 s | downloaded | 18 | 74 segments / 978 chars | 76 items | failure audit |
| [BV1wD4y1o7AS](https://www.bilibili.com/video/BV1wD4y1o7AS/) | 3:34 | 116.531 s | downloaded | 21 | 68 segments / 1,007 chars | 70 items | failure audit |
| [BV1qW4y1a7fU](https://www.bilibili.com/video/BV1qW4y1a7fU/) | 6:53 | 190.477 s | downloaded | 41 | 156 segments / 2,135 chars | 158 items | failure audit |

The four runs took 480.146 seconds of wall time. Three of four samples completed public
metadata, media download, FFmpeg audio/frame extraction, local GPU ASR, timeline merge, and
audit packaging. Across those successful local chains: 80 frames, 298 ASR segments, and
304 evidence items were produced. All four runs correctly retained a failure audit package;
the three downloaded samples stopped at distillation because no NewAPI key was available.

## Authenticated End-to-End Checks

The API key was supplied only through the test process environment. It was not written to
configuration, run artifacts, this report, or tracked files. These runs used the same local
ASR and media settings as the baseline, with no vision frame limit.

| Video / mode | Wall time | Frames / vision | ASR | Evidence | Distill | Score / result |
| --- | ---: | --- | --- | ---: | --- | --- |
| [BV1Jgf6YvE8e](https://www.bilibili.com/video/BV1Jgf6YvE8e/), `standard` | 371.345 s | 18 / 18 succeeded, 0 errors | 74 segments | 195 | succeeded | rule 90, judge 18, failed threshold |
| [BV1Fr4y1f73F](https://www.bilibili.com/video/BV1Fr4y1f73F/), `standard` | 1,066.043 s | 35 / 35 succeeded, 0 errors | 153 segments | 732 | succeeded | rule 90, judge 32, failed threshold |
| [BV1RZYSzaEnz](https://www.bilibili.com/video/BV1RZYSzaEnz/), `off` | 324.463 s | 9 / 9 succeeded, 0 errors | 56 segments | 208 | failed: invalid JSON | failure audit |

The three fresh end-to-end runs took 1,761.851 seconds (29 minutes 21.851 seconds).
They completed 62 successful vision calls with no per-frame API errors. The third run's
distillation was retried from the saved timeline without repeating media, ASR, or vision;
the retry took 21.668 seconds and returned `invalid_distillation_json` again.

The actionable Excel sample was then resumed from its successful real-video distillation
with `judge=off`. The score and package stages completed in 0.148 seconds and wrote the
real candidate package `skills/python-pandas-excel-e08610` with status `needs_review`,
rule/final score 90, and these files:

- `SKILL.md`
- `README.md`
- `metadata.json`
- `evidence_timeline.json`

Post-run verification passed: `score` read the candidate metadata, and `inspect` exited 0
for all three authenticated runs.

### Findings

- NewAPI vision, distillation, and judge integration is operational end to end.
- Standard judge correctly rejected the course-introduction sample and the narrow Excel
  recipe despite rule score 90; the judge cited redundant static-frame evidence, weak
  boundaries, low transferability, and an inaccurate explanation of `sheet_name=None`.
- Distillation has no automatic retry or raw-response audit for model output that violates
  the JSON contract. One short troubleshooting sample failed twice with
  `invalid_distillation_json`.
- Full-frame vision is the main latency cost: 236.373 seconds for 18 frames and 966.934
  seconds for 35 frames in these runs.
- The `video` command returned process exit code 0 for runs whose final status was `failed`.
  Automation must currently inspect the emitted JSON or `run_state.json` instead of relying
  on the process exit code.

## Offline Checks

- Cleanup before testing: emptied `runs/`, `skills/`, and Python cache directories; retained
  `.hf-cache` to avoid deleting model weights.
- Unit tests: 91 tests passed in 13.161 s.
- CLI help: exit 0.
- `mvp-check`: passed both candidate and failure-audit branches in 0.276 s.
- ASR model load: `large-v3-turbo` on CUDA `float16` succeeded in 8.932 s.
- Post-authentication regression: 91 tests passed in 4.238 s; `mvp-check` passed in 0.208 s.
- Secret scan: no API-key-shaped value was found in the tracked workspace area after tests.

## Reproduction

```powershell
$env:SKILL_GATHER_FASTER_WHISPER_DEVICE="cuda"
$env:SKILL_GATHER_FASTER_WHISPER_COMPUTE_TYPE="float16"
.\.venv\Scripts\python.exe -m skill_gather video `
  "https://www.bilibili.com/video/BV1wD4y1o7AS/" `
  --config config.json --out .\skills --runs .\runs --judge-difficulty off
```

Runtime media, frames, audio, model output, and generated packages remain local under
`runs/` and `skills/`; the failed CDN attempt's partial media files were removed after
verification, while its JSON/Markdown audit records were retained.
