"""Configured alias variants must not expand into unrelated ambient words."""
import pytest
from tolesh.config import get_default_config
from tolesh.listening.wake_detection import is_wake_word_detected

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('text', [
    "Doesn't matter if her oppresses A, first joins",
    'the first person joins the game',
    'toleshian is a made-up word',
    'the chavisville address',
])
def test_ambient_words_do_not_trigger_default_wake_detection(text):
    cfg = get_default_config()
    assert not is_wake_word_detected(text.lower(), cfg['wake_word'], cfg['wake_aliases'],
                                     cfg['wake_fuzzy_ratio'])


@pytest.mark.parametrize('alias', get_default_config()['wake_aliases'])
def test_each_configured_alias_remains_a_valid_address(alias):
    cfg = get_default_config()
    assert is_wake_word_detected(f'{alias}, what time is it?', cfg['wake_word'],
                                 cfg['wake_aliases'], cfg['wake_fuzzy_ratio'])


@pytest.mark.parametrize('name,alias,near_primary', [
    ('tolesh', 'joris', 'jarvas'),
    ('friday', 'computer', 'fridai'),
])
def test_primary_approximation_and_exact_alias_remain_usable(name, alias, near_primary):
    assert is_wake_word_detected(f'{near_primary}, hello', name, [alias])
    assert is_wake_word_detected(f'{alias}, hello', name, [alias])


@pytest.mark.parametrize('text,name,alias', [
    ('FRİDAY, hello', 'tolesh', 'friday'),
    ('ΓΕΙΑ, ΑΘΗΝΑ!', 'athena', 'αθηνα'),
    ('你好，小助手！', 'tolesh', '小助手'),
    ('Tolesh, hello', 'tolesh', ''),
])
def test_unicode_punctuation_and_case_are_supported(text, name, alias):
    assert is_wake_word_detected(text, name, [alias])


def test_empty_alias_cannot_wake_ambient_speech():
    assert not is_wake_word_detected('ambient discussion', 'tolesh', [''])


@pytest.mark.parametrize('text,expected', [
    ('tolesh explain toleshian philosophy', 'explain toleshian philosophy'),
    ('tolesh tell me about chavisville', 'tell me about chavisville'),
])
def test_request_preservation_keeps_names_inside_other_words(text, expected):
    from test_hot_window_input import _create_listener, _process_transcript
    listener, _ = _create_listener()
    try:
        _process_transcript(listener, text)
        assert listener.state_manager.get_pending_query() == text
        assert expected in listener.state_manager.get_pending_query()
    finally:
        listener.stop()


def test_explicit_fuzzy_threshold_is_respected():
    assert not is_wake_word_detected('jarvas hello', 'tolesh', ['joris'], fuzzy_ratio=1.0)


@pytest.mark.parametrize('name', ['Frédérique', 'Αθηνά', 'Müller'])
@pytest.mark.parametrize('decomposed_config', [False, True])
def test_canonical_unicode_aliases_remain_valid_addresses(name, decomposed_config):
    import unicodedata
    from tolesh.listening.intent_judge import IntentJudge, IntentJudgeConfig
    alias = unicodedata.normalize('NFD', name) if decomposed_config else name
    heard_name = name if decomposed_config else unicodedata.normalize('NFD', name)
    heard = heard_name + ', what time is it?'
    assert is_wake_word_detected(heard, 'tolesh', [alias], fuzzy_ratio=1.0)
    judge = IntentJudge(IntentJudgeConfig(assistant_name='Tolesh', aliases=[alias]))
    assert judge._normalize_aliases(heard) == 'Tolesh, what time is it?'


@pytest.mark.parametrize('heard,alias', [
    ('cab\u0301 is another word', 'cab'),
    ('a\u0301cab is another word', 'cab'),
    ('कला means art', 'कल'),
    ('कल is another word', 'क'),
])
def test_marks_do_not_create_alias_boundaries(heard, alias):
    from tolesh.listening.intent_judge import IntentJudge, IntentJudgeConfig
    assert not is_wake_word_detected(heard, 'tolesh', [alias], fuzzy_ratio=1.0)
    judge = IntentJudge(IntentJudgeConfig(assistant_name='Tolesh', aliases=[alias]))
    assert 'Tolesh' not in judge._normalize_aliases(heard)


@pytest.mark.parametrize('heard', ['कला', 'cab\u0301'])
def test_fuzzy_primary_tokens_keep_combining_marks(heard):
    primary = 'क' if heard == 'कला' else 'cab'
    assert not is_wake_word_detected(heard, primary, [], fuzzy_ratio=1.0)


def test_alias_replacement_treats_configured_name_literally():
    from tolesh.listening.intent_judge import IntentJudge, IntentJudgeConfig
    judge = IntentJudge(IntentJudgeConfig(assistant_name=r'Tolesh\1', aliases=['computer']))
    assert judge._normalize_aliases('computer, hello') == r'Tolesh\1, hello'


@pytest.mark.parametrize('heard,aliases,expected', [
    ('hey cab\u0301, what time is it?', ['hey', 'hey cab'], 'Tolesh cab\u0301, what time is it?'),
    ('cab-cab\u0301, what time is it?', ['cab', 'cab-cab'], 'Tolesh-cab\u0301, what time is it?'),
    ('hey cab, what time is it?', ['hey', 'hey cab'], 'Tolesh, what time is it?'),
])
def test_valid_shorter_alias_is_not_hidden_by_invalid_longer_alias(heard, aliases, expected):
    from tolesh.listening.intent_judge import IntentJudge, IntentJudgeConfig
    assert is_wake_word_detected(heard, 'tolesh', aliases, fuzzy_ratio=1.0)
    judge = IntentJudge(IntentJudgeConfig(assistant_name='Tolesh', aliases=aliases))
    assert judge._normalize_aliases(heard) == expected
