# Face-Box Glare Crop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent bright pixels outside a detected face from triggering the glare deny cue, without weakening the cue inside the face box.

**Architecture:** Add one clipped face-box crop helper in `glance/liveness.py`. Both the in-app gate and Windows agent will use that crop for glare only; they will keep using their existing padded crop for texture analysis. Existing liveness thresholds and deny timing remain unchanged.

**Tech Stack:** Python 3.12, NumPy, OpenCV, pytest/unittest.

---

### Task 1: Add a face-box crop helper and detector regressions

**Files:**
- Modify: `glance/liveness.py`
- Test: `tests/test_antispoof.py`

- [ ] **Step 1: Add failing tests for exterior white pixels and interior specular pixels**

Add this test class to `tests/test_antispoof.py`:

```python
class FaceBoxGlareCropTests(unittest.TestCase):
    def test_bright_border_outside_face_box_does_not_trip_glare(self):
        frame = np.full((100, 100, 3), 255, dtype=np.uint8)
        frame[20:80, 20:80] = (80, 100, 120)

        crop = lv.crop_to_face_bbox(frame, (20, 20, 80, 80))
        reading = lv.gloss_glare_reading(lv.extract_glare(crop))

        self.assertLess(reading.level, lv.LivenessTuning().gloss_level)

    def test_specular_patch_inside_face_box_still_trips_glare(self):
        frame = np.full((100, 100, 3), (80, 100, 120), dtype=np.uint8)
        frame[20:80, 20:80] = (80, 100, 120)
        frame[35:55, 35:55] = (255, 255, 255)

        crop = lv.crop_to_face_bbox(frame, (20, 20, 80, 80))
        reading = lv.gloss_glare_reading(lv.extract_glare(crop))

        self.assertGreaterEqual(reading.level, lv.LivenessTuning().gloss_level)
```

- [ ] **Step 2: Run the tests and confirm they fail because the helper is missing**

Run: `.venv\Scripts\python.exe -m pytest tests/test_antispoof.py -k FaceBoxGlareCropTests -q`
Expected: both tests fail with `AttributeError` for `crop_to_face_bbox`.

- [ ] **Step 3: Implement the clipped crop helper**

In `glance/liveness.py`, add:

```python
def crop_to_face_bbox(frame: np.ndarray,
                      bbox: tuple[float, float, float, float]) -> np.ndarray:
    """Return only the detected face box, clipped to the source frame."""
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = (int(value) for value in bbox)
    x1, x2 = max(0, x1), min(width, x2)
    y1, y2 = max(0, y1), min(height, y2)
    if x2 <= x1 or y2 <= y1:
        return frame[0:0, 0:0]
    return frame[y1:y2, x1:x2]
```

- [ ] **Step 4: Rerun the focused tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_antispoof.py -k FaceBoxGlareCropTests -q`
Expected: 2 passed.

- [ ] **Step 5: Commit the helper and unit tests**

```powershell
git add glance/liveness.py tests/test_antispoof.py
git commit -m "fix: constrain glare analysis to face box"
```

### Task 2: Route both verification paths through the same crop

**Files:**
- Modify: `glance/desktop/camera.py`
- Modify: `glance/agent/glance_agent.py`
- Test: `tests/test_antispoof.py`

- [ ] **Step 1: Add failing integration assertions for both call sites**

Add this method to `DesktopLivenessTests` in `tests/test_antispoof.py`:

```python
def test_glare_crop_uses_face_box_not_padding(self):
    seen = []

    def record(crop):
        seen.append(crop.shape)
        return None

    with patch.object(lv, "extract_glare", side_effect=record):
        self.gate.verify(self.frame, self.face, self.open, "Sampath", .9)

    self.assertEqual(seen[0], (150, 120, 3))
```

Add this method to `AgentIntegrationTests` in `tests/test_antispoof.py`:

```python
def test_agent_glare_crop_uses_face_box_not_padding(self):
    seen = []

    def record(crop):
        seen.append(crop.shape)
        return None

    with patch.object(lv, "extract_glare", side_effect=record):
        _run_verifier([_synthetic_aligned(open_eye=True)], frames=1)

    self.assertEqual(seen[0], (150, 120, 3))
```

- [ ] **Step 2: Run both focused tests and confirm each fails on the padded shape**

Run: `.venv\Scripts\python.exe -m pytest tests/test_antispoof.py -k 'glare_crop_uses_face_box_not_padding or agent_glare_crop_uses_face_box_not_padding' -q`
Expected: both assertions observe a padded crop instead of `(150, 120, 3)`.

- [ ] **Step 3: Change only the glare input at both call sites**

In `glance/desktop/camera.py` and `glance/agent/glance_agent.py`, keep the existing padded `crop` for texture analysis and pass `lv.crop_to_face_bbox(frame, face.bbox)` to `lv.extract_glare`.

- [ ] **Step 4: Rerun the focused integration tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_antispoof.py -k 'glare_crop_uses_face_box_not_padding or agent_glare_crop_uses_face_box_not_padding' -q`
Expected: 2 passed.

- [ ] **Step 5: Commit both call sites and integration tests**

```powershell
git add glance/desktop/camera.py glance/agent/glance_agent.py tests/test_antispoof.py
git commit -m "fix: exclude padded background from glare cue"
```

### Task 3: Verify behavior and build the candidate

**Files:**
- No additional source files.

- [ ] **Step 1: Run focused liveness and desktop suites**

Run: `.venv\Scripts\python.exe -m pytest tests/test_antispoof.py tests/test_desktop.py -q`
Expected: all tests pass.

- [ ] **Step 2: Run the complete test suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: zero failures.

- [ ] **Step 3: Build to a new ignored output directory**

Run: `powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -SkipModelDownload -OutputDirectory build\glare-crop-verify`
Expected: standalone `Glance.exe` and `GlanceAgent.exe` build successfully with no repository source changes.

- [ ] **Step 4: Check formatting and working tree**

Run: `git diff --check; git status --short`
Expected: no whitespace errors; only intended source/test changes remain.

Do not create or update a release tag as part of this plan.
