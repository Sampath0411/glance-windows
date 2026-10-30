# Smile Liveness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fragile blink authorization with a smile-only temporal challenge for Windows and in-app gates, while reducing and explaining false spoof denials.

**Architecture:** Add smile as a shared temporal liveness cue in `glance/liveness.py`; it consumes aligned face imagery and normalized SCRFD mouth/eye landmarks, requires neutral→smile→relax across quality-qualified frames, and is the only confirm cue that can authorize. Both the Windows `Verifier` and desktop `_GateVerifier` feed it into the same evaluator. Keep spoof deny cues, but improve quality/confidence abstention and log/show the actual reason instead of asserting an uncertain photo/device detection.

**Tech Stack:** Python 3.12, OpenCV, NumPy, PySide6, pytest, existing ONNX face pipeline.

---

## Files and responsibilities

- Modify `glance/liveness.py`: smile cue vocabulary, temporal detector, confidence/quality handling, shared evaluator policy, cue reasons.
- Modify `glance/agent/glance_agent.py`: provide smile frames to the shared evaluator; ensure Windows grant requires smile; useful denial/wait logs.
- Modify `glance/desktop/camera.py`: provide aligned images and 5-point landmarks to the shared smile evaluator; remove eye/blink authorization; actionable in-app status.
- Modify `glance/desktop/face_gate.py`: preserve cue-specific gate message and avoid replacing it with generic text.
- Modify `glance/desktop/pages.py`: describe smile-only challenge in Recognition settings.
- Modify `tests/test_antispoof.py`, `tests/test_desktop.py`, and `tests/test_liveness.py`: unit, cross-path integration, and synthetic-frame regressions.
- Modify `README.md`: accurately describe the smile challenge and RGB-camera limitations.

## Task 1: Specify smile measurements with failing tests

**Files:** `tests/test_antispoof.py`, `glance/liveness.py`

- [ ] Add tests for neutral→smile→neutral confirming, neutral-only remaining pending, static smile remaining pending, insufficient face/landmark confidence abstaining, and illumination variation not generating a smile.
- [ ] Run `.venv\Scripts\python.exe -m pytest tests/test_antispoof.py -q -k smile` and verify the tests fail because the smile API/cue is absent.
- [ ] Define a bounded `SmileSample` and temporal transition helper. Normalize mouth-corner span by interocular distance; use aligned mouth-region evidence to reject a static wide mouth. Track phases neutral→smile→relax, reset on identity/face loss/low quality, and require several qualified samples. Keep thresholds named and covered by tests.
- [ ] Re-run the smile-focused tests and verify expected outcomes.
- [ ] Commit `feat: add temporal smile liveness cue`.

## Task 2: Make the shared evaluator authorize smile only

**Files:** `glance/liveness.py`, `tests/test_antispoof.py`, `tests/test_liveness.py`

- [ ] Add failing evaluator tests that movement and legacy blink alone remain pending, smile confirms, any confident deny cue overrides smile, and diagnostic Light cannot authorize.
- [ ] Run `.venv\Scripts\python.exe -m pytest tests/test_antispoof.py tests/test_liveness.py -q -k "smile or movement or blink"` and confirm the behavior failures.
- [ ] Add `Cue.SMILE` and have Heavy/Strict require it. Keep `Cue.BLINK` and micro-motion diagnostic-only (or remove their authorization paths) so old configuration/CLI inputs cannot bypass smile. Adjust `LivenessTuning`, snapshots, and user-facing cue reason strings.
- [ ] Verify deny-cue precedence, malformed-mode fallback, and all focused tests.
- [ ] Commit `fix: require smile rather than blink or movement`.

## Task 3: Wire Windows verifier and grant integration

**Files:** `glance/agent/glance_agent.py`, `tests/test_antispoof.py`

- [ ] Add a failing verifier integration case showing a static photo with synthetic landmark movement never writes a grant, while a qualifying neutral→smile→neutral script grants only after stable identity matches.
- [ ] Run `.venv\Scripts\python.exe -m pytest tests/test_antispoof.py -q -k verifier` and confirm the new case fails before implementation.
- [ ] Feed each matched aligned crop and face landmarks into the shared smile sample pipeline. Reset liveness sequence on identity mismatch, missing/too-small face, low-quality alignment, timeout, or camera error. Log `denied by <cue>` only for a cue that actually fired; log `waiting for smile` on no-confirm timeout.
- [ ] Verify the integration cases and run all `tests/test_antispoof.py`.
- [ ] Commit `fix: gate Windows grants on smile challenge`.

## Task 4: Wire in-app gate and improve guidance

**Files:** `glance/desktop/camera.py`, `glance/desktop/face_gate.py`, `glance/desktop/pages.py`, `tests/test_desktop.py`, `tests/test_antispoof.py`

- [ ] Add failing tests: in-app static still and movement-only never emit a verified match; valid smile sequence emits a match; identity changes/face loss reset progress; deny cue preserves its specific reason; low-quality input gives positioning/light guidance, not “phone detected.”
- [ ] Run `.venv\Scripts\python.exe -m pytest tests/test_desktop.py tests/test_antispoof.py -q -k "smile or gate"` and verify the expected failures.
- [ ] Change `_GateVerifier` to use the shared smile sequence, expose phase feedback (“Look at the camera”, “Smile”, “Relax”), and keep the existing per-attempt reset behavior. Change face-gate text to relay device/glare/texture reasons only when confident; don't infer spoof from uncertainty.
- [ ] Update Recognition copy to describe the smile challenge and note that camera spoof checks are imperfect.
- [ ] Run the focused tests and `tests/test_desktop.py`.
- [ ] Commit `feat: use smile challenge in in-app face gate`.

## Task 5: Calibrate false-denial behavior and documentation

**Files:** `glance/liveness.py`, `tests/test_liveness.py`, `README.md`

- [ ] Add regression cases for ordinary face highlights, dim/bright exposure changes, mild crop/alignment jitter, and visible synthetic phone/moire cues. Require uncertain samples to abstain and stable high-confidence spoof samples to deny.
- [ ] Run `.venv\Scripts\python.exe -m pytest tests/test_liveness.py tests/test_antispoof.py -q` and verify the new tests expose any current false denials.
- [ ] Adjust only the implicated cue's quality gate/threshold based on those cases; preserve independent deny cues and cue-specific diagnostics. Do not lower identity matching threshold as a liveness workaround.
- [ ] Update README instructions and explicit RGB/video-replay limitations; remove claims that a photo “cannot” unlock.
- [ ] Run `.venv\Scripts\python.exe -m pytest -q -k "not test_enable_unlock_accepts_anyone_mode_with_an_enrolled_face"`, then `.venv\Scripts\python.exe -m compileall -q glance` and `git diff --check`.
- [ ] Commit `fix: reduce liveness false denials and document limits`.

## Task 6: Build and real-camera validation handoff

**Files:** `VERSION`, `glance/__init__.py`, `glance/desktop/main.py`, generated build output.

- [ ] Bump app version and What's New text for the smile challenge.
- [ ] Run full tests and build `packaging/build_windows.ps1 -BuildInstaller -SkipModelDownload -OutputDirectory build\release-0.1.6`.
- [ ] Verify installer and both binaries report the expected version and package contains updated `Glance.exe` and `GlanceAgent.exe`.
- [ ] Give user a real-camera validation checklist: real neutral→smile→relax in bright and dim rooms, still phone photo, printed photo, and phone display with visible bezel. Treat any real-user deny or photo grant as a failure, capture only cue diagnostics (no frames), and do not declare spoof resistance validated from synthetic tests.

## Self-review

- Covers smile-only interaction, shared Windows/in-app policy, deny cue reasons, quality abstention, tests, docs, version/What's New, packaging, and manual validation.
- No feature authorizes from movement, a static smile, or diagnostic Light mode.
- The five-point model's smile measurements are heuristic; real-camera validation remains required and is explicitly a release acceptance gate.
