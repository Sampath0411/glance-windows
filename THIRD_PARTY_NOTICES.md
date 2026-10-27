# Third-party notices

The MIT license in [`LICENSE`](LICENSE) applies to Glance source code only. It
does not relicense bundled third-party software, model weights, or other assets.
Each component retains its own license and redistribution conditions.

## InsightFace `buffalo_s` model weights

The pretrained `buffalo_s` model weights used by Glance are provided by
InsightFace under a **non-commercial research-only** grant. This restriction
applies to the model weights independently of the MIT license for InsightFace
library code and Glance source. The weights must not be represented as
commercially usable or as MIT-licensed. Recheck the current upstream terms and
redistribution permissions before producing or publishing each installer.

Upstream references:

- [InsightFace model zoo and model license terms](https://github.com/deepinsight/insightface/blob/master/python-package/docs/model_zoo.md)
- [`buffalo_s` model license](https://github.com/deepinsight/insightface/blob/master/server/backend/insightface_server/licensing/defaults/buffalo_s/MODEL.LICENSE)
- [InsightFace source repository](https://github.com/deepinsight/insightface)

Glance currently uses only `det_500m.onnx` and `w600k_mbf.onnx` from the
`buffalo_s` archive. Model binaries are not included in the source repository;
the Windows build downloads and validates them separately.

## Bundled application dependencies

The current pinned Windows build manifest (`requirements-lock.txt`) contains:

| Component | Pinned version | Declared license | Notices / license source |
|---|---:|---|---|
| CPython runtime | 3.12.x | PSF License | <https://docs.python.org/3/license.html> |
| PySide6, Addons, Essentials | 6.12.0 | LGPL/GPL or commercial; Qt modules may have additional terms | <https://www.qt.io/licensing/>; package wheel license metadata |
| shiboken6 | 6.12.0 | LGPL/GPL or commercial | <https://doc.qt.io/qtforpython/licenses.html> |
| NumPy | 2.5.4 | BSD-3-Clause and bundled third-party licenses | <https://numpy.org/doc/stable/license.html> |
| OpenCV Python | 5.0.0.93 | Apache-2.0 plus bundled third-party notices | Wheel `LICENSE.txt` and `LICENSE-3RD-PARTY.txt`; <https://opencv.org/license/> |
| ONNX Runtime | 1.31.0 | MIT plus bundled third-party notices | Wheel `LICENSE` and `ThirdPartyNotices.txt`; <https://github.com/microsoft/onnxruntime> |
| flatbuffers | 25.12.19 | Apache-2.0 | <https://github.com/google/flatbuffers> |
| ml_dtypes | 0.6.0 | Apache-2.0; includes Eigen notices | Wheel license files; <https://github.com/jax-ml/ml_dtypes> |
| protobuf | 7.36.2 | BSD 3-Clause | Wheel `LICENSE`; <https://github.com/protocolbuffers/protobuf> |
| typing_extensions | 4.16.0 | PSF-2.0 | Wheel license file; <https://github.com/python/typing_extensions> |

Nuitka 4.3.1 (Apache-2.0), zstandard 0.23.0 (BSD), Pillow 13.0.0 (HPND), and
Inno Setup 6.7.3 are build tools rather than runtime dependencies. They are installed
in the build environment; confirm whether any build-tool code or resources are
present in the final artifact before release.

This is an initial inventory, not a completed legal review. The exact contents
of the PySide6/Qt modules and native DLLs collected by Nuitka must be audited
against their licenses and all applicable attribution/source-offer obligations.
Include the applicable Qt and third-party license texts in the final installer.
Do not publish if any bundled component or obligation remains unresolved. See
[`docs/release-checklist.md`](docs/release-checklist.md).
