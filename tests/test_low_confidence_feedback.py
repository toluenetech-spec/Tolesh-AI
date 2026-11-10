"""Background speech rejection does not occupy the desktop face."""
import pytest
from PyQt6.QtWidgets import QLabel
from PyQt6.QtTest import QTest

pytestmark = pytest.mark.unit


@pytest.fixture
def face(qapp, monkeypatch, tmp_path):
    from desktop_app import face_widget
    from tolesh import activity
    monkeypatch.setattr(activity, '_get_tolesh_state_file', lambda: str(tmp_path / 'state'))
    monkeypatch.setattr(activity, '_tolesh_state_instance', None)
    window = face_widget.FaceWindow()
    window.show()
    QTest.qWait(20)
    yield window
    window.close()
    window.deleteLater()
    qapp.processEvents()


def test_face_has_no_rejection_subtitle_or_reserved_row(face):
    assert not face.findChildren(QLabel)
    assert face.layout().count() == 1
    assert face.layout().itemAt(0).widget() is face.face


def test_daemon_stop_sleeps_face_without_subtitle_dependency(face):
    from desktop_app.app import ToleshSystemTray
    from tolesh.activity import ToleshState
    tray = object.__new__(ToleshSystemTray)
    tray.face_window = face
    face.face._state_manager.set_state(ToleshState.LISTENING)
    tray._set_face_asleep()
    assert face.face._state_manager.state == ToleshState.ASLEEP
