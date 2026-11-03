# Glance desktop and Mac-parity audit report

**Date:** 2026-11-03  
**Scope:** Existing Windows desktop polish and Mac-parity implementation on `mac-parity-face-screen`, including face identity persistence, enrollment, camera/display settings, recognition controls, password/session behavior, face-store sealing, runtime system compatibility checks, natural-motion liveness, Windows agent configuration, documentation, and regression tests. The Mac reference repository remained read-only. Credential-provider implementation and grant protocol were not changed.

## Findings and changes

| Finding | Change | Verification |
|---|---|---|
| The active settings and agent path allowed an `any_identity`/wildcard setting to authorize any enrolled face, contrary to the required single-person policy. | The verifier now rejects wildcard settings before opening the camera. Recognition no longer offers “Anyone enrolled”; a person must be selected. Missing, stale, disabled, and legacy wildcard selections fail closed. Provider enablement rejects wildcard and invalid selections. | Agent matching tests cover selected-versus-other-person, missing/stale selection and legacy wildcard rejection. Backend tests cover enablement checks. |
| Dashboard and setup text could imply a wildcard identity was ready to unlock. | Dashboard reports that a valid person must be selected; setup guide, Recognition labels, and README now describe the single-person policy. | Desktop and guide tests; README audited for old wildcard wording. |
| The compact first-run wizard duplicated setup content and disappeared after first use. | Removed the separate onboarding dialog. Added a seven-part scrollable setup guide inside About, with actions routed to the existing camera privacy, model, Password, Your Face, Recognition, General, and Diagnostics flows. Model-download/terms behavior is unchanged. | About guide navigation/content test and startup model-flow test. |
| Startup had no bounded brand splash. | Added an app-icon splash capped at 1.4 seconds; it closes once the main window is shown and is explicitly closed on initialization errors. Model/setup work still runs after the main window is visible. | Splash lifecycle and startup-exception tests. |
| Page changes had no accessibility-aware transition. | Added a single 160 ms opacity transition for non-camera pages. It skips the camera page and checks Windows client-area animation accessibility settings; API errors disable motion. | Reduced-motion, transition duration/interruption and camera exclusion tests. |
| Startup work included constructing the lengthy guide even if About was never opened. | Guide construction is deferred until the user opens it. This reduces eager work but did not produce a stable numeric improvement in the noisy offscreen probe. | About test checks the guide is lazy and scrollable. |
| Recapture used a person's editable name as its storage key, and legacy records had no persistent identity key. | Added and persisted UUID identity IDs (including migration for older records); recapture now targets the ID and replaces that identity's samples. | Legacy-ID persistence, ID-based replacement, and UI-button targeting regressions. |
| The mixed rated/unrated quality percentage omitted unrated samples from the denominator. | It now follows the Mac formula: all samples are in the denominator, unrated samples are not counted as poor, and all-unrated identities show “not recorded.” | Quality percentage tests include the Mac 3-poor-of-18 example and mixed rated/unrated data. |
| Dashboard constructed a hidden, unused camera preview card and retained its camera owner/signal wiring. | Removed the dead card and preview ownership; the Camera page remains the preview surface. | Desktop regression asserts the Dashboard no longer exposes preview controls. |
| Installer platform requirements were not repeated at application startup. | The installer retains Windows 11 build 22000+, x64, and administrator gates with a clearer unsupported-Windows message. Runtime repeats OS/build/architecture checks and shows a blocking “This app can’t run on this PC” popup only for unsupported systems; missing models/camera/account setup remains repairable and face unlock fails closed. | Compatibility boundary tests, installer-policy regression, full packaged build. Local Windows 11 x64 and installed model/credential status pass. |
| Windows unlock and the in-app gate required a prompted head-turn sequence. | Removed that challenge from agent, in-app, and CLI verification. Heavy/Strict now require sustained natural facial landmark micro-motion, while repeated spoof-deny cues still override it. The warning and UI now state plainly that a moving photo/replay may pass; this is not proof of a live person. | Evaluator/agent/in-app regressions; static frames do not grant, natural landmark motion can grant, and deny cues still deny. |

## Parity choices and limitations

- Enrollment keeps Windows' existing 18 samples and five pose buckets (front/left/right/up/down); existing enrollments remain valid.
- Windows quality is an in-app capture signal, not Vision's score and not an authentication/security score. Existing samples remain unrated.
- Detection distance uses Windows face-area fractions rather than copying Mac face-width fractions; the Default stop retains the prior Windows 1.2% identification minimum.
- Windows Heavy/Strict require natural facial landmark micro-motion, not a prompted head-turn. It is weaker than an unpredictable challenge and replay video can pass. Light remains diagnostics-only for credential-provider grants, and the in-app check remains at least Heavy. Turning liveness off explicitly warns that a photo could pass.
- The installer and runtime both gate unsupported Windows builds/architectures. Camera/model/password/enrollment gaps are not PC incompatibility: Glance remains available for setup, and unlock fails closed until repaired.
- PnP camera friendly names are best-effort paired with OpenCV indices by enumeration order. Windows does not guarantee these orders correspond, so the UI retains numeric fallback and camera selection must be confirmed on the target machine. Display enumeration itself is via Windows monitor APIs.
- “Start when I sign in” remains background protection. Opening the desktop window at sign-in is a separate opt-in setting.
- Face-store DPAPI sealing is user-scoped, not equivalent to the Mac Touch ID-gated Keychain session key. The README's Windows Hello, replay-spoof, and DPAPI limitations remain applicable.

## Performance measurements

The probe stubs external Windows state, camera/model work, and logs. It uses offscreen Qt and reads local app settings without writing them.

| Probe | Window construction | First paint | Dashboard snapshot | Navigation call | Preview paint |
|---|---:|---:|---:|---:|---:|
| Initial baseline, 3-run median | 336.7 ms | 373.1 ms | 22.2 ms | 22.4 ms | 8.6 ms |
| Final probe, 5-run median | 736.7 ms | 809.7 ms | 26.5 ms | 64.8 ms | 18.5 ms |

The final run was highly variable (window construction ranged from 730 ms to 1,971 ms; first paint from 804 ms to 2,085 ms). These are not comparable enough to claim a performance regression or improvement, and the offscreen environment does not represent physical-device latency. Dashboard refresh and camera processing already run outside the UI thread. No additional speculative performance refactor was retained; profile a stable physical Windows environment before making a stronger performance claim.

## Verification

Fresh final checks after the system-readiness and motion-check changes (run in the Windows checkout):

- `python -m pytest -q`: **331 passed, 16 subtests passed** (latest full run after compact-layout and settings-preservation regressions).
- All test commands listed in the README's “Run the tests” section passed, including desktop, CLI, liveness, grant, packaging, helper/policy, and model/icon/notice suites.
- `python -m compileall -q glance tools scripts packaging`: passed.
- `provider/build/provider_harness.exe`: **44 passed, 0 failed**.
- `git diff --check`: passed (Git emitted only line-ending conversion notices).
- Windows compatibility check passed on this PC; the webcam was opened and one 640×480 frame read in memory only (not saved); model files and stored credential were detected.
- `packaging/build_windows.ps1 -BuildInstaller -SkipModelDownload -OutputDirectory build/package-systemcheck-motion`: standalone app and Inno installer built successfully. The packaged GUI stayed running for an 8-second offscreen startup smoke test. Installer: `build/package-systemcheck-motion/GlanceSetup.exe`; standalone app: `build/package-systemcheck-motion/launch_glance.dist/Glance.exe`.
- No password was entered, identity data changed, or Windows lock-screen unlock attempted.

## Remaining acceptance checks

- Camera name/index correspondence, multi-monitor selection, and sign-in/window-autostart behavior still need confirmation on other hardware/accounts.
- A real Windows lock-screen attempt was not performed and requires separate explicit approval.
- Verify the selected-person flow on the target account and confirm normal PIN/password fallback remains available.
- Work stays on `mac-parity-face-screen`; no v1.10 tag or release until a real Windows lock-screen unlock is verified. No lock-screen attempt was performed during this pass.
