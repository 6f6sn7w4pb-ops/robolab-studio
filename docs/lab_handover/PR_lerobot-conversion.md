PR 标题（复制下面一行）：

Add LeRobot converter and openpi LoRA runner; document steps 2-4 and training rules

PR 描述（复制下面全部）：

## Summary

- **scripts/convert_p4_to_lerobot.py**: converts the P4 HDF5 training view to a LeRobot v2.1 dataset (the version pinned by openpi), run with openpi's interpreter. Baseline fields `image`, `wrist_image` (lossless PNG, `dtype: image`, no video), `state`, `actions`, `task`; fps 20; openpi LIBERO image orientation. `verify` is an independent code path that requires every image to equal the HDF5 image flipped horizontally, pixel for pixel; `append-test` and `compare` check that appending and re-converting are byte-identical. `EXTRA_FIELDS` is the hook for later contact labels.
- **scripts/openpi_robolab.py**: our pi0.5 LoRA `TrainConfig`, registered into openpi's `_CONFIGS_DICT` at run time (openpi's source is not edited), then openpi's own `compute_norm_stats.py` / `train.py` `main`; `train --measure` records compile and per-step time, JAX `peak_bytes_in_use`, GPU utilization, host memory and checkpoint size.
- **Decisions recorded (2026-11-25)**: batch size 32 for every compared model (`max_token_len` stays at the default 200); formal training rules in `docs/LAB_MASTER_PLAN.md` section 14.2: step counts from the validation curves of the 2,000-episode pilot, 2-3 seeds for M1-M3, `decay_steps` equal to `num_train_steps`, `keep_period` 10,000 with non-best checkpoints deleted after asking, no sleep or update restarts during long runs, `--resume` after an interruption. Storage estimate redone with the measured 9.6 GB checkpoints.
- **Docs**: new `docs/LAB_LORA_PROBE.md`; `LAB_STAGE_HANDOFF.md`, `LAB_MASTER_PLAN.md`, `P4_DATASET_FORMAT.md` and `NEXT_CHAT_HANDOFF.md` updated for 2026-11-23 and 2026-11-25; `D455_CAMERA_PARAMS.md` section 4 item 2 now gives the shift caused by a shared fovy in the 224x140 image (about 1.7 px at the top and bottom edges, 2.7 px at the left and right edges, 3.2 px in the corners).
- Merges the current `main` (PR #12 and #13). The one conflict, in `LAB_STAGE_HANDOFF.md` section 6, keeps this branch's rows 2-4 and `main`'s row 5.

Results on the lab machine: 120 episodes / 26,640 frames, all 53,280 images pixel-identical, 2.72 GB; appending is byte-identical to a one-pass conversion. LoRA probe (300 steps): batch 32 at 5.43 s/step and 23.3 GiB peak; batch 64 at 12.0 s/step and 31.5 GiB peak (needs memory fraction 0.9); GPU utilization about 100%; checkpoint 9.6 GB; loss 0.08 to 0.019.

**Please merge with "Create a merge commit"** (not "Squash and merge" or "Rebase and merge"). The dataset `robolab/p4d_v1_train` records code commit `0b6a173`; a merge commit keeps that commit in `main`'s history.

## Test plan

- [x] `bash scripts/test_engine.sh` after merging `main`: 112 tests pass, 3 GUI modules skipped by design
- [x] `bash -n` on every `scripts/*.sh` (as in CI)
- [x] Relative links in the changed docs resolve
- [x] `convert_p4_to_lerobot.py verify`: 120 episodes / 26,640 frames, 53,280 images pixel-identical
- [x] `convert_p4_to_lerobot.py append-test` and `compare`: byte-identical
- [x] Two 300-step LoRA runs completed (`docs/LAB_LORA_PROBE.md`)
- [ ] GitHub Lightweight checks pass

🤖 Generated with [Claude Code](https://claude.com/claude-code)
