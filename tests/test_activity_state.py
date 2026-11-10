"""Core activity publication works without the desktop or Qt packages."""
import importlib.abc
import sys

import pytest
from tolesh.activity import ToleshState

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('publisher', ['direct', 'piper', 'chatterbox', 'collection'])
def test_activity_publication_does_not_load_the_desktop(monkeypatch, tmp_path, publisher):
    class RejectDesktop(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split('.')[0] in {'desktop_app', 'PyQt6'}:
                raise AssertionError('Core activity publication imported the desktop')

    for name in tuple(sys.modules):
        if name.split('.')[0] in {'desktop_app', 'PyQt6'}:
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, 'meta_path', [RejectDesktop(), *sys.meta_path])
    from tolesh import activity
    path = tmp_path / 'activity'
    monkeypatch.setattr(activity, '_get_tolesh_state_file', lambda: str(path))
    monkeypatch.setattr(activity, '_tolesh_state_instance', None)
    state = activity.get_tolesh_state()
    expected = activity.ToleshState.SPEAKING
    if publisher == 'direct':
        state.set_state(expected)
    elif publisher == 'collection':
        from tolesh.listening.state_manager import StateManager
        manager = StateManager()
        manager.start_collection('A new request', language=None)
        expected = activity.ToleshState.LISTENING
    else:
        from tolesh.output.tts import PiperTTS, ChatterboxTTS, SpeechRequest
        backend = PiperTTS if publisher == 'piper' else ChatterboxTTS
        engine = backend()
        engine._begin_playback(SpeechRequest('Owned speech'), 1.0)
    assert path.read_text() == expected.value
    assert state.state is expected
    assert not any(name.split('.')[0] in {'desktop_app', 'PyQt6'} for name in sys.modules)


@pytest.fixture
def state(monkeypatch, tmp_path):
    from tolesh import activity
    path = tmp_path / 'state'
    monkeypatch.setattr(activity, '_get_tolesh_state_file', lambda: str(path))
    return activity.ToleshStateManager(), path


@pytest.mark.parametrize('value', list(ToleshState))
def test_every_activity_state_is_shared_with_file_observers(state, value):
    manager, path = state
    manager.set_state(value)
    assert path.read_text() == value.value
    assert manager.state is value


def test_invalid_external_state_keeps_the_last_local_activity(state):
    from tolesh.activity import ToleshState
    manager, path = state
    manager.set_state(ToleshState.THINKING)
    path.write_text('invalid')
    assert manager.state is ToleshState.THINKING


def test_unavailable_state_file_keeps_the_local_activity(state):
    from tolesh.activity import ToleshState
    manager, path = state
    path.unlink()
    path.mkdir()
    manager.set_state(ToleshState.LISTENING)
    assert manager.state is ToleshState.LISTENING
