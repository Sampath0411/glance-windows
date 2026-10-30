# Account Setup and Agent Reliability Design

## Goal

Make Glance's Windows-account setup understandable without PowerShell, inform users
about updates, start face monitoring quietly when requested, and restore monitoring
after the computer wakes from sleep. Preserve explicit user control and never claim
that Glance is as secure as Windows Hello.

## User experience

### Windows account help in About

- Add a Windows sign-in help area to About with actions to identify the current
  account, check the account/password pair, and open Windows Sign-in options.
- Identify the account by running the relevant Windows identity checks from the app
  (including `whoami` and `whoami /upn`) without opening a console. Show the detected
  account type (local, Microsoft, or work/school), the account string Glance expects,
  and a clear uncertain/unsupported result instead of guessing when evidence is
  ambiguous.
- The password-check action uses the discovered account string and the existing
  `grant_tool.exe check --account ...` operation. Collect the password in a masked
  Glance dialog and pass it through standard input; do not put it in command-line
  arguments, logs, or persistent storage. Translate helper output into a plain-language
  result while retaining useful status details for troubleshooting.
- The Sign-in options action opens the Windows Settings page. Glance explains that
  Microsoft-account users may need to turn Off “Only allow Windows Hello sign-in…”;
  the user makes that Windows setting change themselves.

### First-run, update, and background startup

- On first launch after install or update, show a version-specific What’s New view
  once. Store the last successfully shown version; do not show it on every launch.
- After Windows unlock is enabled, if the agent is not configured to start at user
  sign-in, offer a one-time prompt with Enable and Later. Later dismisses the prompt
  without repeatedly nagging; the existing Settings control remains available.
- The background agent, not the Glance window, is what starts quietly at Windows
  sign-in. Keep the GUI closed unless the user opens it.

### Paused protection and sleep/wake recovery

- Distinguish the user's sign-in autostart preference from whether the agent was
  active in the current session. Turning sign-in autostart off prevents launch after
  a new Windows sign-in; it does not prevent recovery after sleep in a session where
  protection had been running.
- When Windows wakes, resume protection even if the user had manually paused it
  before sleep, as explicitly requested. Make the Pause action's temporary-until-wake
  behavior clear in the UI.
- Recover both cases: an agent that exited during sleep and an agent process that
  remains alive but whose camera/session state needs reinitializing. Use Task Scheduler
  for recovery when the process is absent, plus agent-side resume handling to refresh
  session/camera state. Preserve the single-agent mutex and avoid starting duplicates.
- Reflect actual agent state in the dashboard and tray, and explain restart failures
  with a user-facing status rather than silently reporting protection as active.

## Architecture boundaries

- Keep Windows identity detection and helper invocation in the backend; UI pages only
  present actions, progress, and parsed results.
- Use direct process invocation with captured output, not an interactive PowerShell
  console. Keep credentials out of arguments and logs.
- Keep version-notice and one-time-reminder state in per-user Glance settings.
- Keep wake recovery in the agent/task lifecycle, not dependent on the Glance window
  staying open.

## Failure handling

- Missing helper, unsupported account type, ambiguous identity, permission failures,
  rejected password, and Windows Settings launch failures get distinct plain-language
  messages with the next useful action.
- If resume cannot restart the agent or camera, report protection as paused and show
  a retry path; never show a protected state until the agent is confirmed running.
- A dismissed Later reminder is not repeated automatically; users can turn startup
  on from Settings.

## Acceptance criteria

1. A user can identify supported local, Microsoft, and work/school account details
   from About without opening PowerShell; ambiguous results are explicitly marked.
2. A user can run the helper password check from About and see understandable success,
   rejection, and error outcomes; the password is absent from argv, logs, and disk
   after the check.
3. The Windows Hello guidance opens Sign-in options and describes the Microsoft
   account switch without changing it automatically.
4. What’s New appears once per installed version, including for an existing user
   updating Glance.
5. The first-run startup prompt appears only when sign-in autostart is off; Enable
   turns it on, Later suppresses repeated prompts, and the agent runs without opening
   the Glance window.
6. Protection that was running before sleep resumes after wake, including when the
   agent exited during sleep; manual pause before sleep does not override the requested
   resume-on-wake behavior.
7. Disabling sign-in autostart prevents automatic launch after a fresh Windows sign-in
   but does not block wake recovery during an already active session.
8. Dashboard/tray status reflects the true agent state and failures remain visibly
   paused with a retry action.

## Verification

- Unit-test account classification, helper argument/stdin handling, output mapping,
  notice-once logic, startup-prompt persistence, wake recovery, and duplicate-agent
  protection.
- Exercise the existing Windows packaging and regression suites. Manual validation on
  Windows should include local and Microsoft accounts, work/school account reporting,
  install/update notices, a sleep/wake cycle, and failure-to-resume feedback.
