# Face Gate and Any-Enrolled Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate Password and Your Face behind a centered face-check popup, and let any enrolled person unlock Windows while Password is always set up first.

**Architecture:** The shared `CameraWorker` gains a quiet gate mode that emits the current best match about twice a second. A new `FaceGateDialog` consumes those results, requires three stable matches, and plays the bundled success/failure clips. Navigation intercepts indices 1 and 2 before switching pages. Settings gain `any_identity`, and the agent treats an empty identity as wildcard only when that flag is set.

**Tech Stack:** Python 3.12, PySide6, OpenCV, pytest, Windows.

---

## Files

- `glance/desktop/backend.py` — `Settings.any_identity`, config persistence, unlock validation.
- `glance/agent/glance_agent.py` — wildcard verifier mode and config resolution.
- `glance/desktop/camera.py` — gate mode and `gate` signal.
- `glance/desktop/capture_widgets.py` — scalable `ScanAnimation`.
- `glance/desktop/face_gate.py` — new gate dialog.
- `glance/desktop/main.py` — navigation interception and `request_face_gate`.
- `glance/desktop/pages.py` — first-run enrollment guard, dashboard gate, Recognition scope, copy.
- `tests/test_desktop.py`, `tests/test_agent_paths.py`, `tests/test_backend_helper.py` — regression coverage.

## Task 1: Settings scope and unlock validation

- [ ] Add failing tests: settings round-trip preserves `any_identity`; enable-unlock accepts anyone mode with enrolled faces and rejects specific mode with a stale identity.
- [ ] Implement `any_identity` in `Settings.load/save`, default `True` when the key is absent.
- [ ] Update `backend.enable_windows_unlock` to require at least one enrolled face and, in specific mode, a matching `settings.identity`.
- [ ] Run the focused tests.

## Task 2: Agent wildcard mode

- [ ] Add failing verifier tests: anyone mode grants for any enrolled match; specific mode rejects a different person; empty store fails closed.
- [ ] Add `Verifier(..., any_identity=False)`; skip the single-name checks only in anyone mode.
- [ ] Add `resolve_any_identity(config)` and wire it through `main()`; pass `any_identity` to the verifier.
- [ ] Run the focused tests.

## Task 3: Camera gate signal

- [ ] Add `gate = Signal(str, float, str)` and `set_gate(active)` to `CameraWorker`.
- [ ] While gate mode is active, emit the best match (or an empty name plus a hint) about twice a second without affecting capture.
- [ ] Add a unit test that toggling gate mode is thread-safe and idempotent.
- [ ] Run the camera tests.

## Task 4: Scalable animation

- [ ] Add optional `size` and `art` parameters to `ScanAnimation`, defaulting to today's 80/64 so enrollment is unchanged.
- [ ] Confirm the existing desktop tests still pass.

## Task 5: Gate dialog

- [ ] Create `FaceGateDialog` with the large centered animation, status/hint text, and Retry/Cancel.
- [ ] Start scanning on show, require three stable matches at the saved threshold, and time out after 20 seconds.
- [ ] Play `unlockanimation.mp4` on success and `unsuccessfulunlockanimation.mp4` on failure; expose `exec()` acceptance.
- [ ] Fail closed with a clear message when models or the camera are unavailable; skip is handled by the caller when nobody is enrolled.
- [ ] Add offscreen tests with a fake camera: success accepts, no match times out to failure, Retry restarts scanning.

## Task 6: Navigation and first-run order

- [ ] Add `MainWindow.request_face_gate(purpose)` returning whether the page may open; it skips the dialog when `identity_summary(0)` is empty.
- [ ] Intercept `_navigate`: Password and Your Face require the gate when faces exist; a rejected gate restores the sidebar selection.
- [ ] Redirect Your Face to Password with an explanation when no Windows credential is stored, before any camera starts.
- [ ] Double-check the credential in `EnrollPage._toggle_capture` and gate the Dashboard “Set password…” shortcut.
- [ ] Add offscreen navigation tests for skip, success, cancel restore, and the password-first redirect.

## Task 7: Recognition scope and copy

- [ ] Replace the single-person picker with “Anyone enrolled” plus each person; default to anyone when no explicit scope is saved.
- [ ] Persist `any_identity` and an empty or specific identity; update the description text.
- [ ] Update the dashboard wording to show “any enrolled face” or the chosen name.
- [ ] Add tests for both saved scopes.

## Task 8: Verification and package

- [ ] Run the full suite offscreen and report exact counts.
- [ ] Build the model-free package and installer in a fresh output directory; do not install it.
- [ ] Confirm the animations are inside the package.
