# Account Help and First-Run Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users identify and test their Windows sign-in from Glance, understand the Hello-only setting, see updates once, and choose quiet agent startup without PowerShell.

**Architecture:** Put account detection and helper execution in testable desktop backend modules; keep the UI in AboutPage and focused dialogs. Store UI-only notice/reminder state separately from the agent's recognition config. Invoke Windows binaries directly with captured output and a no-console flag.

**Tech Stack:** Python 3.12, PySide6, Windows `whoami.exe`, existing `provider/grant_tool.exe`, unittest.

---

### Task 1: Windows sign-in detection

**Files:**
- Create: `glance/desktop/account_info.py`
- Create: `tests/test_account_info.py`
- Modify: `glance/desktop/backend.py`

- [ ] **Step 1: Add failing classification tests** for local `COMPUTER\user`, Microsoft `MicrosoftAccount\email`, and work/school UPN evidence, plus ambiguous/missing command output.
- [ ] **Step 2: Run `python -m unittest tests.test_account_info -v`** and confirm the new tests fail because `identify_account()` is not implemented.
- [ ] **Step 3: Implement a result type and `identify_account()`** that calls `whoami` and `whoami /upn` directly using `subprocess.run(..., capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=5)`. Classify only from explicit evidence; return `unknown` rather than infer when evidence is insufficient. Return the account string needed by Glance and raw outputs for the UI's expandable details.
- [ ] **Step 4: Run `python -m unittest tests.test_account_info -v`** and confirm all cases pass.

### Task 2: In-app password check and Windows setting shortcut

**Files:**
- Modify: `glance/desktop/backend.py`
- Modify: `glance/desktop/pages.py`
- Modify: `tests/test_backend_helper.py`
- Modify: `tests/test_desktop.py`

- [ ] **Step 1: Add a failing backend test** asserting the password check invokes `GRANT_TOOL` with `check`, `--account`, the detected account, and `--password-stdin`; assert the password is supplied as stdin and is absent from argv.
- [ ] **Step 2: Run `python -m unittest tests.test_backend_helper -v`** and confirm the new method is missing.
- [ ] **Step 3: Implement `backend.check_account_password(account, password)`** with a bounded direct subprocess call, `CREATE_NO_WINDOW`, `input=password`, no persistence, and output parsing for `verify=ok`, `verify=rejected`, and helper errors. Clear the password from the dialog after every result.
- [ ] **Step 4: Add About-page tests** for account result display, the masked password-check dialog, and opening `ms-settings:signinoptions` without attempting to modify the Windows setting.
- [ ] **Step 5: Add an About sign-in help group** with Identify account, Check password, and Open Sign-in options actions. Run long operations in the existing `Task` worker pattern; show a plain-language outcome and an expandable/copyable diagnostic detail.
- [ ] **Step 6: Run `python -m unittest tests.test_backend_helper tests.test_desktop -v`** and confirm the new behavior and existing pages pass.

### Task 3: Versioned What's New and one-time startup reminder

**Files:**
- Create: `glance/desktop/ui_state.py`
- Create: `tests/test_ui_state.py`
- Modify: `glance/desktop/main.py`
- Modify: `glance/desktop/pages.py`
- Modify: `tests/test_desktop.py`
- Modify: `VERSION`
- Modify: `glance/__init__.py`
- Modify: `packaging/inno/Glance.iss`

- [ ] **Step 1: Add failing state tests** for marking a version as shown once, persisting a dismissed startup reminder, and keeping UI state separate from agent configuration.
- [ ] **Step 2: Run `python -m unittest tests.test_ui_state -v`** and confirm the state API is missing.
- [ ] **Step 3: Implement atomic per-user UI-state read/write** under Glance's user data directory; malformed or absent state must default safely and never prevent startup.
- [ ] **Step 4: Bump the app from 0.1.3 to 0.1.4** in `VERSION`, `glance/__init__.py`, and the Inno Setup fallback; run `python -m unittest tests.test_packaging.BuildManifestTests -v` and confirm metadata agrees.
- [ ] **Step 5: Add the What's New dialog** keyed by `glance.__version__`; show it once after install/update and persist the version only after the dialog has been presented.
- [ ] **Step 6: Add the startup prompt** after Windows unlock is enabled when the agent logon task is disabled. Enable calls `backend.set_autostart(True)`; Later persists dismissal without repeated prompts. Keep the existing Settings toggle as the path to enable startup later.
- [ ] **Step 7: Run `python -m unittest tests.test_ui_state tests.test_desktop -v`** and confirm the version and reminder prompts are shown at most once.

### Task 4: User-facing documentation and full regression

**Files:**
- Modify: `README.md`
- Modify: `tests/test_packaging.py`

- [ ] **Step 1: Add a failing README policy test** for account checks being available in-app, the Windows Hello setting shortcut, and quiet agent startup.
- [ ] **Step 2: Update the README** to describe the in-app actions and explain that Glance does not silently change Windows Sign-in options.
- [ ] **Step 3: Run `python -m unittest tests.test_packaging -v`** and confirm the documentation checks pass.
- [ ] **Step 4: Run the complete local suite** with `python -m pytest -q`; investigate any failure before continuing.
