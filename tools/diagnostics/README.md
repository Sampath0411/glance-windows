# Diagnostics

One-off probes used while bringing up the Windows port. They are not part of the
application, are not run by CI, and some need extra model files or a locked
session.

| Script | Purpose |
|---|---|
| `camera_lock_test.py` | Confirms the camera still delivers frames while the workstation is locked. |
| `lock_capture_probe.py` | Samples camera frames across a lock/unlock cycle. |
| `session_lock_probe.py` | Watches session lock notifications over a longer run. |
| `inspect_io.py` | Prints ONNX model inputs, outputs, and shapes. |
| `probe_models.py` | Checks model tensors against the expected ArcFace/SCRFD contract. |
| `preview_navy_ui.py` | Renders the desktop pages to PNG without touching real machine state. |
| `probe_106*.py`, `render_indices.py`, `verify_mapping.py` | Landmark-model experiments kept for reference; they need `2d106det.onnx` and are not used by the shipped pipeline. |

Run them from the repository root with the project virtual environment:

```powershell
.venv\Scripts\python.exe tools\diagnostics\<script>.py
```
