# Lip Sync verification findings — 2026-11-04

Technical tasks completed. Workboard lifecycle could not be confirmed: native create and claim requests timed out; no new card exists. Checkpoints and completion are requested once against existing splice card 10bfa55e-1fad-4c1a-9c5e-93ec30c78044. No reliance on any other worker.

## Root cause and changes
There are two project directories. The requested workspace/project-lip-sync copy lacked the dotenv and Claude HTTP 401 fix; the existing splice runner lives in workspace/projects/lip-sync and imports its sibling translator, which already had the fix. The requested copy had a partial sacred-term layer with case-insensitive AS matching ordinary “as”, ineffective symbol boundaries, and overlapping abbreviation regexes.

Added dotenv discovery and direct Claude fallback on batch HTTP 401 to the requested translator. Repaired the sacred-term regex layer in both copies: unique per-occurrence placeholders; uppercase bare RA/AS/SAW/PBUH/RH; wrapped abbreviations; ﷺ and ﷻ; Arabic honorifics; canonical Bangla gazetteer including Allah/SWT, Subhanallah, Insha’Allah, dua, fitna, Hidayah, Tawhid, Shirk and Shafa’ah. Prompt requires exact token retention. Post-processing rejects missing/duplicated tokens before TTS instead of silently dropping sacred material. Shortening keeps the existing translation if a sacred term is lost. Original retranslate_shorter keyword signatures retained. Missing token failure is deliberate; automatic targeted retries are not implemented.

The active splice translator wraps its existing translation routine, retaining its own prompt/timing logic. This full pipeline process imported translator before the new guard edits, so its success proves the pre-existing auth/pipeline fix. A separate real-provider demo and controlled integration tests prove the new guard path.

## Splice verification
Command: `node C:/Users/shaya/.openclaw/scripts/job.mjs start --name lip-sync-splice-verify --cmd "python -u C:\Users\shaya\.openclaw\workspace\projects\lip-sync\run_splice_with_env.py H3hijSGhdlo" --cwd C:/Users/shaya/.openclaw/workspace/project-lip-sync`

Job `20261104-210353-lip-sync-splice-verify`: **DONE, exit 0**, elapsed **1580.76 s**. 558 captions -> 328 clusters -> **300 translated sentences**, all nonempty. All six pipeline stages ran. Tokens: 13,644 input / 4,927 output.

Outputs in output/H3hijSGhdlo: MP4 **691,258,399 bytes**, MP3 **23,022,280 bytes**, translation JSON and manifest. ffprobe confirms VP9 video/AAC audio and MP3. Audio duration **1438.84 s**, video **1484.933313 s**: final ~46 seconds have no dubbed track because the dub ends at the caption horizon. **21 slots <=0.1 s**, timing warnings and mean estimated speedup **1.210x** remain quality limitations. Completion is not a certification of linguistic quality or timing intelligibility.

No live HTTP 401 occurred. Source inspection plus controlled HTTP 401 responses prove the actual batch branch dispatches to Claude in both copies. Isolated synthetic .env tests prove both module loaders discover credentials without relying on inherited LLM_API_KEY. No credentials were logged.

## Kaggle version 5 — COMPLETE / independently verified
Commands:
- `kaggle kernels push -p kaggle_eval` -> version **5** accepted.
- `kaggle kernels status shayaanhaq/lipsync-gemma-eval-en-bn` polled until **COMPLETE**.
- Existing `python -u -X utf8 stream_eval_logs.py` captured live progress, GPU MATMUL OK and VERDICT PASS.
- `kaggle kernels output shayaanhaq/lipsync-gemma-eval-en-bn/5 -p kaggle_eval_output/run_v5 --force` (UTF-8 environment) downloaded all output files and kernel log.
- `python -X utf8 verify_dataset_eval.py --run-dir kaggle_eval_output/run_v5 --report kaggle_eval_output/run_v5/verification.json` -> **PASS**.

Despite its Gemma slug/title, this notebook and published dataset evaluate **TinyLlama/TinyLlama-1.1B-Chat-v1.0**, not Gemma4. Dataset: shayaanhaq/lipsync-lora-adapter-en-bn. Full validation **3218 rows**, Tesla T4, fp16, batch 64, greedy generation up to 256 tokens. Inference elapsed **1748.74 s**.

| Metric | Base | LoRA | Improvement |
|---|---:|---:|---:|
| BLEU | 0.0580541472 | 4.9139451085 | +4.8558909613 |
| chrF | 9.8040684470 | 27.0074121124 | +17.2033436654 |
| Hit 256-token limit | 2814 | 51 | |
| Empty predictions | 0 | 0 | |

Independent verification checks row alignment against local validation, CSV/JSONL row counts, recomputes BLEU and chrF with sacrebleu 2.5.1, verifies unchanged training-source hashes, GPU MATMUL and final PASS markers. Base/LoRA predictions differ on all 3218 rows. Scores reproduce prior v4 results, but this is a new completed v5 run. All downloaded artifacts are isolated under kaggle_eval_output/run_v5; prior outputs preserved.

## Tests and limitations
`python -X utf8 -m unittest test_sacred_terms`: **2 tests PASS**, with assertions spanning both copies/provider paths, dotenv discovery, 401 fallback, repetitions, false-positive words, wrapped abbreviations, Arabic symbols/phrases, missing/duplicate rejection, timings/speaker preservation, unchanged inputs and shortening. `python -m py_compile translator.py ../projects/lip-sync/translator.py`: PASS.

`python -X utf8 -u demonstrate_sacred_terms.py`: real splice-provider translation retains ﷺ, দোয়া, আল্লাহ without placeholder leakage. Output and source are in sacred_terms_live_demo.json. This only proves term retention; the model phrase “আল্লাহ-এর জন্য” for “to Allah” remains a semantic limitation requiring review.

First test found Windows PowerShell-to-Python stdin Unicode corruption. Corrected via a UTF-8 script file and reran successfully; no corrupted source remains. First Kaggle output download failed while printing Bangla log text with a Windows charmap encoding; setting PYTHONUTF8=1 and repeating download succeeded. Live stream disconnected several times and the existing script reconnected normally.

`node C:/Users/shaya/.openclaw/scripts/system-map.mjs check`: MAP CLEAN (no drift), one unrelated pre-existing Instagram Event Scanner missing-file outage. No R2 cron/plugin/task/port/runtime entry point changed, so system map files unchanged. Existing project verifier only gained backward-compatible --run-dir/--report arguments.

## Workboard evidence and durable handoff
Create request **36ae0c2c** failed at 21:11:25Z after 180 seconds. Read-only SQLite check found no new card. No duplicate create filed. Original task-card recovery claim **182f3d77** also timed out. Several checkpoint comments also timed out. Logging is best effort per user instruction; no technical work was stopped for it. Full findings and completion requests are filed once against the existing splice card; their execution is unconfirmed. Thus the new-card/claim/marked-complete acceptance criterion remains unmet, even though technical work completed.

Project memory and PROJECT_BOARD are updated with final technical outcomes. Full findings are this file, independent of the transcript. Native request/result journals are C:/Users/shaya/.openclaw/outsource/native-requests.jsonl and native-results.jsonl; runtime log is C:/Users/shaya/.openclaw/logs/native-proxy.log.

## Artifacts
- C:/Users/shaya/.openclaw/workspace/project-lip-sync/translator.py
- C:/Users/shaya/.openclaw/workspace/projects/lip-sync/translator.py
- C:/Users/shaya/.openclaw/workspace/project-lip-sync/test_sacred_terms.py
- C:/Users/shaya/.openclaw/workspace/project-lip-sync/demonstrate_sacred_terms.py
- C:/Users/shaya/.openclaw/workspace/project-lip-sync/sacred_terms_test.log
- C:/Users/shaya/.openclaw/workspace/project-lip-sync/sacred_terms_live_demo.json
- C:/Users/shaya/.openclaw/workspace/project-lip-sync/splice_verify_20261104.log
- C:/Users/shaya/.openclaw/workspace/project-lip-sync/splice_verify_job_20261104.json
- C:/Users/shaya/.openclaw/workspace/project-lip-sync/splice_verify_result_20261104.json
- C:/Users/shaya/.openclaw/workspace/project-lip-sync/verify_splice_outputs.py
- C:/Users/shaya/.openclaw/workspace/project-lip-sync/output/H3hijSGhdlo/manifest.json
- C:/Users/shaya/.openclaw/workspace/project-lip-sync/output/H3hijSGhdlo/H3hijSGhdlo_dubbed.mp4
- C:/Users/shaya/.openclaw/workspace/project-lip-sync/output/H3hijSGhdlo/H3hijSGhdlo_dubbed.mp3
- C:/Users/shaya/.openclaw/workspace/project-lip-sync/output/H3hijSGhdlo/H3hijSGhdlo_translation.json
- C:/Users/shaya/.openclaw/workspace/project-lip-sync/verify_dataset_eval.py
- C:/Users/shaya/.openclaw/workspace/project-lip-sync/kaggle_eval_output/run_v5/metrics.json
- C:/Users/shaya/.openclaw/workspace/project-lip-sync/kaggle_eval_output/run_v5/results.json
- C:/Users/shaya/.openclaw/workspace/project-lip-sync/kaggle_eval_output/run_v5/predictions.csv
- C:/Users/shaya/.openclaw/workspace/project-lip-sync/kaggle_eval_output/run_v5/predictions.jsonl
- C:/Users/shaya/.openclaw/workspace/project-lip-sync/kaggle_eval_output/run_v5/verification.json
- C:/Users/shaya/.openclaw/workspace/project-lip-sync/kaggle_eval_output/run_v5/push_receipt.json
- C:/Users/shaya/.openclaw/workspace/project-lip-sync/kaggle_eval_output/run_v5/lipsync-gemma-eval-en-bn.log
