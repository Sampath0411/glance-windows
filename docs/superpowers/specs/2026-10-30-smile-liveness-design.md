# Smile Liveness Design

## Goal

Improve Glance's unlock success for real users while keeping a liveness challenge
on both Windows sign-in and in-app face-gated pages. The challenge is smile-only:
users look at the camera, smile, then relax. Blink is not requested or required.

## User experience

- Show a short neutral-baseline instruction, followed by “Smile at the camera”
  and “Relax your face” guidance as the scan progresses.
- Continue to reject no-face, multiple-face, too-small-face, and identity mismatch
  cases with actionable messages.
- When a liveness cue cannot be measured reliably, ask the user to adjust position
  or lighting and retry; do not describe an uncertain observation as a detected
  phone or photo.
- Keep a bounded scan timeout and allow retry/cancel.

## Detection and authorization

- Require a sequence from a neutral baseline to a smile and back toward neutral.
  Detect the temporal change from multiple consecutive camera frames, rather than
  accepting a single mouth shape or a pre-smiling still image.
- Use aligned face imagery and normalized mouth geometry/appearance, with quality
  checks for face size, alignment, and exposure. Calibrate thresholds using
  synthetic regression cases and real-camera observations; do not claim RGB-only
  spoof detection is a guarantee.
- Apply the same decision component and policy to the Windows agent and the
  desktop face gate so their behavior cannot drift.
- The smile sequence is the required confirm cue. Existing glare, screen glow,
  device-edge, and print/moire checks remain independent deny cues. A cue with
  insufficient confidence abstains. Denials must record and surface the actual
  cue, and thresholds should be adjusted only with evidence from real faces and
  spoof samples.
- Movement alone, a single static smile, and diagnostic Light mode must never
  authorize a grant.

## Diagnostics

- Preserve the final denial reason and include the cue name in local agent logs.
- In the in-app gate, show a specific actionable message for a confident deny;
  for uncertain readings, show positioning/lighting guidance instead.
- Do not save camera frames or face crops for diagnostics.

## Tests and acceptance

- Unit tests: neutral-to-smile-to-neutral confirms; neutral-only, static smile,
  and mouth motion without a smile do not confirm; identity changes, face loss,
  poor alignment, low confidence, or insufficient face size reset/abstain safely.
- Integration tests: neither the Windows verifier nor the in-app gate can grant
  from a still image or movement-only sequence; both grant on the same qualifying
  smile sequence; confident spoof-deny cues block both paths.
- Regression tests cover variable illumination and alignment noise to avoid
  treating ordinary measurement variation as either a smile or spoof evidence.
- Manual validation with the user's camera: real-face completion under normal
  and dim lighting, and rejection of the user's phone photo. Report limitations
  honestly; a replay video may contain the prompted expression.

## Scope

This change replaces blink as the required user action and improves feedback for
false phone/photo alerts. It does not add IR/depth sensing, store camera data,
change identity matching, or claim protection against all video replays.
