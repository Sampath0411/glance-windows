# Glance Startup Design

## Goal

Start the Glance desktop window automatically after the current Windows user
signs in, and ensure the installed Glance Agent process reloads the updated
binary now.

## Design

- Keep the existing **Glance Agent** logon scheduled task and its action
  (`C:\Program Files\Glance\GlanceAgent.exe`) unchanged.
- Restart that task once to load the updated installed agent binary.
- Add a shortcut to `C:\Program Files\Glance\Glance.exe` in the current user's
  Startup folder. This is per-user and opens the UI at sign-in; it does not
  affect other Windows accounts or require an additional scheduled task.
- Verify the shortcut target and confirm the agent task returns to Running.

## Constraints

- Do not alter the scheduled task definition, its triggers, or its arguments.
- Do not configure system-wide startup for other users.
- Do not restart or shut down Windows.
