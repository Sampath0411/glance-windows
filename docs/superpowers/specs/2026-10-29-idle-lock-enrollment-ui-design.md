# Idle Lock and Enrollment UI Fixes

## Context

The Windows `Glance Agent` scheduled task currently runs the installed
`C:\Program Files\Glance\GlanceAgent.exe` version 0.1.2. The agent configuration
contains no idle-lock keys. That installed agent predates the input-idle policy in
the working tree, so UI changes alone cannot make idle locking work.

## Approved behavior and presentation

- Keep idle locking opt-in and driven by Windows keyboard/mouse inactivity, only
  while protection is running. Keep the configurable 1–120 minute interval and
  require fresh input after an idle-triggered lock before face verification.
- Do not install an agent or edit the user's scheduled task as part of this work.
  Build an updated package containing the updated agent so it can be installed
  and tested explicitly afterward.
- Move the face animation above the “Name” label and input in the enrollment
  form. Preserve the sample counter, quality feedback, and save controls.
- Restyle enrollment identity cards with semantic colors from Glance's active
  light/dark palette instead of hard-coded charcoal/gray values. Keep the
  red-to-green 18-segment sample coverage display and remove controls.
- Preserve the user's existing uncommitted feature work; do not publish or
  install the package.

## Verification

- Add regression tests for enrollment widget ordering and themed card styling.
- Exercise the idle-lock configuration/polling path with deterministic Windows
  API fakes; verify disabled settings and elapsed interval behavior.
- Run the full test suite and build a model-free Windows package. Do not trigger
  `LockWorkStation`, alter the scheduled task, or run the installer in automated
  verification.
- Manual follow-up: install/run the updated package, verify enrollment layout,
  then test idle lock with a one-minute interval and a known PIN/password.
