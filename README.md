<h1 align="center">
  <br>
  <a href="https://github.com/Sampath0411/glance-windows"><img src="assets/GlanceIcon.png" alt="Glance" width="150"></a>
  <br>
  Glance
  <br>
</h1>

<h3 align="center">Face unlock for your Windows PC</h3>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-black.svg" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/Windows-11%20x64-black.svg" alt="Windows 11 x64">
  <img src="https://img.shields.io/badge/Python-3.12%20%2B%20PySide6-black.svg" alt="Python 3.12 + PySide6">
</p>

Glance brings the FaceID-like experience of your phone to a Windows PC near you. Unlock
Windows with a glance — no typing, no reaching for the fingerprint reader. Everything runs
on-device using ONNX Runtime and InsightFace, so your face data and Windows password never
touch the internet. The app lives in a native Windows window with a dark navy interface, and
the lock screen stays Windows' own.

---

> [!WARNING]
> ## Read before installing
> ## Glance is not as secure as Windows Hello
>
> Most Windows PCs don't have the infrared or depth sensors that make Windows Hello
> trustworthy and secure. A normal webcam sees a flat 2D image. That means:
>
> - Glance defeats, with reasonable confidence, a printed photo and a photo on a phone
>   screen when **Heavy** liveness detection is on.
> - Glance does not reliably defeat a video of you.
> - Windows has no public API that lets a third-party app authorize a normal sign-in, so
>   Glance installs a Credential Provider. After a face check, that provider submits your
>   stored Windows password to the sign-in screen.
> - The stored password is encrypted with machine-scoped DPAPI. That protects it at rest,
>   but any process running on the PC can decrypt it while you are signed in.
>
> Glance is a convenience feature, not a security upgrade. Keep your Windows PIN and
> password available, and only install it on a PC you control.

## Installation

<p align="center">
  <a href="https://github.com/Sampath0411/glance-windows/releases"><img width="300" src="assets/GlanceDownload.png" alt="Download for Windows" /></a>
</p>

**Requirements:**

- Windows 11 x64
- A webcam
- Administrator approval for installation and for enabling the credential provider

Run `GlanceSetup.exe` and follow the installer. Glance installs its protected components
under `%ProgramFiles%\Glance`; the app and face agent themselves run unelevated. The
installer is currently unsigned, so Windows may show an unverified-publisher or SmartScreen
warning.

After installation:

1. Open **Glance** from Start.
2. Enroll your face.
3. Verify and save your Windows password.
4. Turn on **Face unlock** for the Windows lock screen.

The installer copies the credential provider and helper files but does **not** enable
machine-wide face unlock by itself. You enable it inside Glance, and Windows asks for
approval when Glance needs administrator access.

## Permissions

| Permission | Why |
|---|---|
| **Camera** | To see your face. Frames are processed in memory and never written to disk. |
| **Administrator** | To install under `%ProgramFiles%`, register the credential provider, and update the protected helper. Glance asks only when needed. |
| **Windows account password** | The credential provider must submit a real password after a face grant. It is verified by Windows before storage and encrypted with DPAPI. |

## How it works

1. Launch the app and follow onboarding to enroll your face. Glance guides you through
   capturing several samples, turning your head slightly between them. Each frame becomes a
   512-number *embedding* — a mathematical fingerprint — and the image is thrown away.
2. Enter your Windows password once. Windows verifies it before Glance stores it.
3. When Windows locks, the face agent starts the camera and looks for the selected identity.
4. If it's you — and the liveness checks agree you're a real person — the agent writes a
   short-lived, HMAC-signed grant bound to your account and logon session.
5. The Glance credential provider verifies that grant and submits the stored password.
   Windows' normal password and PIN providers remain available.

## Features

| Feature | Description |
|---|---|
| **Face unlock** | Runs after Windows locks. The agent does nothing while the desktop is unlocked. |
| **Multiple identities** | Enroll several people or several versions of yourself. Exactly one selected identity may authorize an unlock. |
| **Liveness checks** | Watches for glare and screen-shaped objects, with optional blink confirmation. *Light*, *Heavy*, or off. |
| **Native Windows** | A real Win32 window with a fixed sidebar, rounded Windows 11 corners, snap, shadows, and the standard title bar. |
| **Dark navy UI** | Six focused screens: Overview, Your face, Windows sign-in, Camera, Recognition, and About. |
| **Camera & recognition** | Choose the camera, match threshold, verification timeout, and autostart behavior. |
| **Fail closed** | No selected identity, a missing credential, a stale grant, or any provider error falls back to the normal Windows password tile. |
| **Your data, your call** | Edit or delete your enrollment or stored password at any time. |
| **Local by design** | Recognition and liveness run on-device. No face data, camera frames, or credentials are sent to a server. |

---

# Privacy and Security

Glance is designed to keep biometric data and credentials on the device.

### Face data

Glance never stores camera images. During enrollment, each captured face is converted into a
**512-dimensional embedding** using an ArcFace model. The original frame is then discarded.

Embeddings are stored locally in `%LOCALAPPDATA%\Glance\data\identities.json` and protected
by your Windows profile's file permissions. This build does **not** encrypt the embedding
file. A 512-number embedding is not an image and cannot be turned back into one, but it is
still biometric data: use a Windows account with a strong password, and delete the file if
you stop using Glance.

### Credentials

Your Windows password is never written to disk in plaintext. It is encrypted with **DPAPI**
at **machine scope** and stored under `HKLM\SOFTWARE\Glance`.

Limitation, stated rather than buried: machine-scoped DPAPI defends the password against a
copied registry hive, but not against a process running on this PC. A service-held secret with
a narrower ACL is future hardening work. Treat Glance as convenience software, not as a vault.

### Unlock pipeline

Glance won't submit your password simply because a face matches. An unlock requires all of
the following:

1. The selected identity is enrolled and enabled.
2. The PC is actually locked.
3. A face matches above the configured similarity threshold.
4. Liveness checks accept the detected face.
5. A fresh grant is written for this account and logon session.
6. The credential provider verifies the grant before reading the stored password.

Recognition and liveness run independently and must both succeed before the grant is written.
The grant expires after a few seconds and can be used once.

### Local by design

Face recognition, enrollment, and liveness detection run entirely on-device with ONNX
Runtime. Glance does not send face data, camera frames, or credentials to a server.

### How it tells a face from a photo

Five independent cues over a rolling window, in two roles:

- **Deny cues** are evidence of a spoof — screen glare, or a device-shaped rectangle framing
  the face. Either one fails the scan outright and overrides anything else.
- **Confirm cues** are evidence of a real face — face geometry, nose parallax across head
  turns, blinks. Any one is enough, and their absence is never a failure, since a live person
  can sit still and not blink.

**Light** detection only uses deny cues. **Heavy** detection also requires a confirm cue. In
the current Windows port, blink is the working confirm cue; the two geometry-based confirm
cues abstain until a head-yaw source is added.

---

## Building from source

### Prerequisites

- Windows 11 x64
- Python 3.12 x64
- Visual Studio Build Tools with the **Desktop development with C++** workload
- Inno Setup 6 (only for building the installer)

### Run from source

1. Clone the repository:

   ```powershell
   git clone https://github.com/Sampath0411/glance-windows.git
   cd glance-windows
   ```

2. Create the environment and install pinned dependencies:

   ```powershell
   py -3.12 -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements-lock.txt -r requirements-build.txt
   ```

3. Fetch the face models:

   ```powershell
   .venv\Scripts\python.exe tools\fetch_models.py models\buffalo_s
   ```

4. Launch Glance:

   ```powershell
   Glance.bat
   ```

### Build the installer

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -BuildInstaller
```

Installers and unpacked builds are written to `dist\` and `build\`. The build downloads and
validates the model weights; they are not stored in the repository.

### Run the tests

```powershell
.venv\Scripts\python.exe -m tests.test_desktop
.venv\Scripts\python.exe -m tests.test_cli
.venv\Scripts\python.exe -m tests.test_liveness
.venv\Scripts\python.exe -m tests.test_grant
.venv\Scripts\python.exe -m unittest tests.test_packaging -v
.venv\Scripts\python.exe -m unittest tests.test_backend_helper tests.test_admin_helper_policy tests.test_pending_grant tests.test_agent_paths tests.test_agent_startup_diagnostics -v
.venv\Scripts\python.exe -m unittest tests.test_fetch_models tests.test_packaged_icon tests.test_collect_notices -v
```

The interactive developer menu lives in `scripts\run.bat`.

### Project structure

```
glance/              application package
  core.py            face detection, alignment, and recognition
  liveness.py        anti-spoof cues
  paths.py           source and installed path resolution
  agent/             lock-screen verification agent
  desktop/           PySide6 desktop UI
  cli/               enrollment, evaluation, and repair commands
tests/               regression suites
scripts/             source launchers and the developer menu
tools/               model, icon, and notice build utilities
tools/diagnostics/   developer probes and diagnostics
packaging/           Nuitka build, Inno Setup script, notices
provider/            C++ credential provider and grant helper
assets/              approved app artwork
docs/                release checklist
```

## Contributing

This is a personal Windows port. Issues and focused fixes are welcome; for larger changes,
open an issue first so the security-sensitive sign-in path can be reviewed.

## Acknowledgements

- **[Glance for macOS](https://github.com/jonnyoo/glance)** by Jonathan Zhou — the original
  app, recognition pipeline, and liveness design this port follows.
- **[InsightFace](https://github.com/deepinsight/insightface)** — the ArcFace and SCRFD models
  doing recognition and detection.
- **[Qt for Python](https://doc.qt.io/qtforpython-6/)** — the native-feeling desktop UI.
- **[ONNX Runtime](https://onnxruntime.ai/)** — on-device inference.
- **[Nuitka](https://nuitka.net/)** and **[Inno Setup](https://jrsoftware.org/isinfo.php)** —
  the Windows build and installer.

## License

[MIT](LICENSE) © 2026 Glance contributors

The bundled InsightFace `buffalo_s` weights have a separate **non-commercial
research-only** restriction; the MIT license does not apply to those weights. See
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). Do not use or redistribute the weights
commercially without separate permission.
