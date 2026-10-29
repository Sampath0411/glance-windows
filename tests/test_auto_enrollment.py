from types import SimpleNamespace

import numpy as np

def test_cropped_face_is_rejected_before_alignment():
    face = SimpleNamespace(area=6000, bbox=(-2, 10, 90, 90),
                           kps=np.array([[25, 30], [60, 30], [45, 50], [30, 70], [60, 70]]))
    from glance.desktop.camera import full_face_in_frame
    assert not full_face_in_frame(face, (100, 100))


def test_session_rejects_duplicates_and_caps_at_eighteen():
    from glance.desktop.enrollment import EnrollmentSession
    s = EnrollmentSession()
    base = np.zeros(128); base[0] = 1
    index = 0
    def sample():
        nonlocal index
        index += 1
        e = base.copy()
        e[index] = .15
        return e / np.linalg.norm(e)
    first = sample()
    assert s.accept(first, (0, .5))[0]
    assert not s.accept(first, (0, .5))[0]
    assert not s.accept(-first, (0, .5))[0]
    for pose, count in [((0, .5), 5), ((-.2, .5), 3), ((.2, .5), 3), ((0, .35), 3), ((0, .65), 3)]:
        for _ in range(count):
            assert s.accept(sample(), pose)[0]
    assert s.complete
    assert len(s.samples) == 18
    assert not s.accept(sample(), (0, .5))[0]


def test_invalid_embeddings_never_count():
    from glance.desktop.enrollment import EnrollmentSession
    s = EnrollmentSession()
    for e in [np.zeros(128), np.full(128, np.nan), np.full(128, np.inf)]:
        assert not s.accept(e, (0, .5))[0]
    assert not s.samples


def test_unshown_animation_does_not_start_a_native_decoder_thread():
    from PySide6.QtWidgets import QApplication
    from glance.desktop.capture_widgets import ScanAnimation
    global _qt_app
    _qt_app = QApplication.instance() or QApplication([])
    animation = ScanAnimation()
    assert not animation.isVisible()
    # Decoder startup is deferred until the page becomes visible, avoiding a
    # running QThread when a page is constructed only for tests/navigation.
    from glance.desktop import capture_widgets
    assert capture_widgets._loader is None or not capture_widgets._loader.isRunning()
    animation.deleteLater()
    _qt_app.processEvents()
