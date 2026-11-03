# Glare cue face-crop design

## Problem

Both the in-app face gate and Windows unlock agent call the same glare detector
on a crop expanded 15% beyond the detected face box. The detector counts bright,
near-neutral pixels and their grid concentration across the whole crop. A bright
wall, window, or screen just outside the face can therefore contribute to a
glare denial. A live in-memory sample on this PC did not reproduce the denial
(24 face frames stayed below the cue threshold); the report from other v1.10
machines remains the motivating case.

## Decision

For glare only, analyze pixels inside the detected face bounding box, clipped to
the frame. Keep the existing expanded crop for texture analysis. Both call sites
must use the same helper so in-app and lock-screen checks remain consistent.

Keep the glare thresholds, confidence calculation, three-frame deny behavior,
and all other liveness cues unchanged. Do not save camera frames or alter face
embeddings, recognition thresholds, enrollment data, or the motion check.

## Verification

- A synthetic bright neutral border outside the face box does not trigger the
  glare cue when evaluating the face-only crop.
- A synthetic specular patch inside the face box still triggers the existing
  glare cue.
- Both the in-app gate and agent route glare through the same face-only crop.
- Run focused liveness/desktop tests, then the full suite and Windows build.

## Non-goals

Do not disable the glare cue or globally raise its threshold. Do not add frame
capture or persistent image diagnostics. No release or tag is part of this fix.
