# Face Gate and Any-Enrolled Access

## Goal

Add an in-app face check before sensitive pages, and allow every enrolled
person to unlock Windows, change the saved password, and enroll new faces.

## Gate behavior

- Opening **Your Face** or **Password** in the sidebar asks for a face check
  every time. The Dashboard's **Set password…** shortcut asks too.
- The check runs in a centered modal dialog with the large Mac face animation,
  a short status line, and **Retry** / **Cancel**. No camera video is shown.
- Scanning starts automatically and lasts up to 20 seconds. A match requires
  three consecutive stable results for the same enrolled person at the saved
  recognition threshold.
- On a match, the dialog plays `unlockanimation.mp4` and then opens the page.
- On no match or timeout, it plays `unsuccessfulunlockanimation.mp4` and offers
  **Retry** or **Cancel**. Cancel or failure leaves the current page open and
  restores the sidebar selection.
- When no face is enrolled yet, the gate is skipped so first-time setup works.
- Missing models, camera failure, or a read error blocks the page with a clear
  message; the gate never falls open.

## First-run order

On a new installation the order is fixed:

1. **Password** comes first. With no face enrolled, the page opens without a
   face check and the user verifies and stores their Windows password.
2. **Your Face** stays blocked until that credential exists. Opening it earlier
   shows a message directing the user to Password and does not start the
   camera or enrollment.
3. Once a face is enrolled, every visit to Password and Your Face is gated by
   the face check described above.

Removing or resetting the stored credential blocks new enrollment again until
the password is set. Existing enrolled faces still gate the pages. The first-run
guide presents Password before Your Face to match this order.

### Animation sources

- Success: `C:\Users\sampa\glance\glance\Resources\unlockanimation.mp4`
- Failure: `C:\Users\sampa\glance\glance\Resources\unsuccessfulunlockanimation.mp4`

These files are byte-identical to `assets/animations/unlockanimation.mp4` and
`assets/animations/unsuccessfulunlockanimation.mp4` (SHA256 verified), so the
packaged assets are used and no external path is required at runtime.

## Recognition and unlock scope

- **Who may unlock** offers **Anyone enrolled** (default) or one specific
  person. The choice is saved as `any_identity` with `identity` empty for the
  anyone mode.
- In anyone mode the agent accepts a match from any enrolled identity; it still
  requires a non-empty store, three stable frames, and the existing liveness
  check. Specific-person mode keeps today's restriction.
- Enabling Windows unlock accepts either mode as long as at least one face is
  enrolled and the other prerequisites are met.
- Gate access is always based on the full enrolled set, even when Windows
  unlock is restricted to one person.
- Copy explains that every enrolled person can unlock, change the password, and
  enroll new faces.

## Security note

The in-app gate matches faces only and does not run the liveness check, so a
photograph could pass it. It is a convenience control for the app, not a
replacement for the lock-screen provider, which keeps liveness.

## Components

- `glance/desktop/face_gate.py`: gate dialog, stable-match accumulation,
  timeout, retry, and animation states.
- `glance/desktop/camera.py`: gate mode on the shared `CameraWorker` and a
  match signal; no second camera session.
- `glance/desktop/main.py`: navigation interception, sidebar restore, and the
  shared gate entry point.
- `glance/desktop/pages.py`: Dashboard shortcut gating and Recognition scope.
- `glance/desktop/backend.py`: `Settings.any_identity`, config persistence, and
  enable-unlock validation.
- `glance/agent/glance_agent.py`: wildcard identity handling.
- `glance/desktop/capture_widgets.py`: larger animation rendering for the
  dialog while keeping the existing enrollment widget size.

## Verification

- Offscreen Qt tests: gate skipped with no enrollments; success allows the
  page; cancel/timeout blocks it and restores navigation; Dashboard shortcut is
  gated; Recognition saves anyone/specific scope.
- Agent tests: anyone mode accepts any enrolled match; specific mode rejects a
  different person; empty store still fails closed.
- Full test suite passes; package builds without installing.
