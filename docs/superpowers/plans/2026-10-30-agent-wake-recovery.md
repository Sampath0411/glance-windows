# Agent Wake Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep face protection accurately reported and automatically restore it after sleep/wake, including when the user paused it before sleep.

**Architecture:** Preserve the existing logon task as the user's sign-in preference. Add an always-available resume task triggered by the Windows resume event while Glance unlock remains enabled. The running agent handles power broadcasts and refreshes lock/camera state; the existing mutex prevents duplicate agents. A disabled logon task must not disable the separate wake trigger.

**Tech Stack:** Windows Task Scheduler COM/PowerShell task registration, Python Win32 message pump, PySide6 status UI, unittest.

---

### Task 1: Define wake-resume behavior with tests

**Files:**
- Modify: `tests/test_idle_lock.py`
- Modify: `tests/test_agent_paths.py`
- Create: `tests/test_agent_power_events.py`

- [ ] **Step 1: Add failing policy tests** for resume after wake when the agent was stopped, resume after a deliberate pause, no duplicate start when the agent is already alive, and logon-autostart preference remaining unchanged.
- [ ] **Step 2: Run `python -m unittest tests.test_idle_lock tests.test_agent_power_events -v`** and confirm the power-event behavior is absent.

### Task 2: Recover an agent that exited during sleep

**Files:**
- Modify: `provider/GlanceAdmin.ps1`
- Modify: `provider/install.ps1`
- Modify: `provider/uninstall.ps1`
- Modify: `provider/GlanceAdmin.ps1` status/reset operations
- Modify: `tests/test_packaging.py`

- [ ] **Step 1: Add failing packaging-policy tests** asserting install/enable registers a `Glance Agent Resume` task with a resume event trigger, that the task launches the installed agent unelevated as the correct user, and that provider disable/uninstall removes it.
- [ ] **Step 2: Run `python -m unittest tests.test_packaging -v`** and confirm the task policy checks fail.
- [ ] **Step 3: Register a separate resume task** for the active user using Task Scheduler event trigger `Microsoft-Windows-Power-Troubleshooter`, event ID `1`. Set it to start when available, allow battery operation, and ignore a new instance when one is already running. Keep the existing `Glance Agent` logon task as the user-controlled sign-in autostart task.
- [ ] **Step 4: Update status, disable-provider, reset, install, and uninstall paths** so the resume task is installed and removed with the agent lifecycle but is not disabled when the user disables logon autostart.
- [ ] **Step 5: Run `python -m unittest tests.test_packaging -v`** and verify task registration/removal policies.

### Task 3: Refresh a live agent after wake

**Files:**
- Modify: `glance/agent/glance_agent.py`
- Create: `tests/test_agent_power_events.py`
- Modify: `tests/test_agent_startup_diagnostics.py`

- [ ] **Step 1: Add failing tests** for `WM_POWERBROADCAST` resume events: refresh a verification worker if Windows is locked, do not open the camera while unlocked, and handle duplicate wake notifications safely.
- [ ] **Step 2: Run `python -m unittest tests.test_agent_power_events -v`** and confirm the agent currently ignores the resume message.
- [ ] **Step 3: Handle `PBT_APMRESUMEAUTOMATIC` in the agent window procedure.** Stop and join a stale verification worker, then start one fresh verification if the session is locked. Ignore the event while unlocked. Keep single-worker and existing mutex guarantees.
- [ ] **Step 4: Run `python -m unittest tests.test_agent_power_events tests.test_agent_startup_diagnostics -v`** and confirm power handling and exception logging pass.

### Task 4: Report actual state and retry safely

**Files:**
- Modify: `glance/desktop/backend.py`
- Modify: `glance/desktop/pages.py`
- Modify: `glance/desktop/main.py`
- Modify: `tests/test_desktop.py`
- Modify: `tests/test_backend_helper.py`

- [ ] **Step 1: Add failing UI/backend tests** that distinguish setup-disabled, agent-running, and agent-stopped states; verify dashboard/tray refresh does not label a stopped agent as protected and a resume retry cannot launch a duplicate.
- [ ] **Step 2: Run `python -m unittest tests.test_desktop tests.test_backend_helper -v`** and confirm missing state cases fail.
- [ ] **Step 3: Implement a lightweight status refresh after wake and on the existing dashboard/tray refresh cycle.** If the agent is absent while Windows unlock is enabled, invoke the wake-safe start operation; show paused plus an actionable retry if it fails.
- [ ] **Step 4: Make pause messaging explicit**: “Pause until the next wake” and the matching resume behavior, so users know a manual pause will not persist across sleep.
- [ ] **Step 5: Run `python -m unittest tests.test_desktop tests.test_backend_helper -v`** and confirm true state and retry behavior.

### Task 5: Verify integration

**Files:**
- Modify: `tests/test_packaging.py`
- Modify: `tests/test_agent_power_events.py`

- [ ] **Step 1: Run focused regression tests** with `python -m unittest tests.test_agent_power_events tests.test_idle_lock tests.test_backend_helper tests.test_desktop tests.test_packaging -v`.
- [ ] **Step 2: Run the full suite** with `python -m pytest -q` and fix any regressions.
- [ ] **Step 3: Manually validate on Windows**: enable unlock, pause protection, sleep and wake; verify the agent resumes, the camera reinitializes on the lock screen, the dashboard/tray reflect state, and disabling sign-in autostart still allows same-session wake recovery.
