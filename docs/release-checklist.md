# Windows release checklist

This checklist is for a maintainer manually preparing a future GitHub release. CI
only builds and uploads temporary artifacts; it never publishes a GitHub Release.

- [ ] Build on clean Windows 11 x64 using Python 3.12 x64 and pinned lockfiles.
- [ ] Run desktop, CLI, liveness, grant, packaging, and model-fetch tests.
- [ ] Confirm the official model archive size and pinned SHA-256 in `glance/models.py`;
      a bundled local build may include only `det_500m.onnx`, `w600k_mbf.onnx`, and
      `MODEL.LICENSE`.
- [ ] Build the publishable installer with `-WithoutModels` and verify the standalone
      output and the installer contain no `.onnx` files.
- [ ] Recheck current InsightFace `buffalo_s` terms and redistribution permissions.
- [ ] Confirm the installer and notices explicitly disclose non-commercial,
      research-only model restrictions; do not label model weights MIT or
      commercially unrestricted.
- [ ] Audit licenses/attribution for every exact direct and transitive runtime
      component actually shipped, including Python, Qt/PySide6, shiboken6, NumPy,
      OpenCV, ONNX Runtime, and compiler/runtime components. Resolve all notices
      before publishing.
- [ ] Install as a non-admin user, verify Start Menu/optional desktop shortcuts,
      first-run setup and model download into `%LOCALAPPDATA%\Glance\models`,
      preview/enrollment, and shutdown.
- [ ] Verify config and identities are written only to
      `%LOCALAPPDATA%\Glance\data`; test upgrade and uninstall preserve user data.
- [ ] Verify installer does not install/register a credential provider, create
      scheduled tasks, write machine-level provider registry values, or copy to
      `System32`.
- [ ] Confirm the source archive contains no `.venv`, local `data`, model binaries,
      model archive, credentials, grant keys, or machine logs.
- [ ] Document unsigned publisher/SmartScreen behavior and the provider boundary.
- [ ] Manually choose whether and when to publish; no workflow publishes for you.
