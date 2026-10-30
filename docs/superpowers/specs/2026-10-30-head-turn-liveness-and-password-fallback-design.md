# Head-Turn Liveness and In-App Password Fallback

## Goal

Replace the smile-only liveness challenge with a head-turn challenge, and give
users a Windows-password fallback when an in-app face gate cannot verify them.
Apply the head-turn challenge consistently to Windows unlock and in-app face
checks. The password fallback applies only to the in-app gates for **Your Face**
and **Password**.

## User experience

- Prompt the user to face forward, turn their head left, turn right, and return
  forward.
- Use the same challenge in Windows unlock and in-app face verification.
- Continue to show actionable guidance for no face, multiple faces, poor pose
  quality, or camera problems.
- If an in-app face check fails or times out, show **Use Windows password** in
  addition to retry and cancel.
- The fallback asks for the current Windows account password. Verify it through
  Windows, do not persist the entered value, clear the input after verification,
  and open the requested page only after successful verification.
- Keep retry/cancel available. Incorrect credentials leave the sensitive page
  closed and allow another attempt or cancellation.

## Detection and authorization

- Use the existing five-point face landmarks to estimate relative yaw from the
  nose position against the eye landmarks, normalized by interocular distance.
- Require the ordered transition: forward baseline → left turn → right turn →
  forward return. Require multiple valid observations for each phase; arbitrary
  landmark movement, static pose, and wrong-order turns do not authorize.
- Use one shared tracker and policy for the Windows verifier and in-app gate.
- Reset progress on face loss, identity change, invalid geometry, or inadequate
  pose/face quality. Low-quality measurements abstain and show positioning
  guidance; they do not grant access.
- Preserve independent spoof cues and the existing confidence policy.
- A detected pose change is not proof of a live human. A moving photograph or
  replay video may still reproduce the requested apparent motion; product copy
  and release notes must not claim otherwise.
- A successful password fallback authorizes only the current in-app navigation
  request. It does not authorize Windows sign-in and does not disable liveness
  on Windows unlock.

## Password verification and privacy

- Reuse the supported Windows account-password verification path rather than
  comparing against or retrieving Glance's stored unlock credential.
- Use the current Windows account identity, and verify the password with Windows
  using the existing account verification mechanism.
- Never save the password entered in the fallback. Clear it after each check and
  avoid logging the password or retaining it in UI state after completion.
- If the account cannot be identified or Windows cannot verify the credentials,
  report the problem without opening the gated page.

## Testing and acceptance

- Unit tests cover the ordered forward → left → right → forward transition,
  static faces, wrong order, noisy/invalid landmarks, low confidence, face loss,
  and identity changes.
- Integration tests establish that both Windows and in-app gates require the
  same head-turn transition and that existing spoof-deny cues still block a
  face-based grant.
- UI tests verify that a failed/timed-out in-app face gate exposes the password
  fallback, correct Windows-verified credentials open the requested page, and
  wrong credentials/cancel do not.
- Password tests verify the submitted value is not saved or logged and is cleared
  from the dialog after checking.
- Update user guidance, README security disclosures, and version/release notes to
  describe the head-turn interaction and the limitations of RGB-only liveness.
- Rebuild the Windows installer after implementation. The previous 0.1.6
  installer contains the smile challenge and is not the release artifact for
  this design.

## Scope exclusions

- Do not add an app-specific password or PIN.
- Do not add the password fallback to Windows sign-in/unlock.
- Do not claim head motion proves humanity or defeats replay attacks.
- Do not retain camera frames or fallback password input for diagnostics.
