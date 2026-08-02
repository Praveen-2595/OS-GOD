# tests/smoke_test.py
import yaml
import os
import pytest

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')

@pytest.fixture
def config():
    with open(CONFIG_PATH, 'r') as f:
        return yaml.safe_load(f)
    
# ── Test 1: Config file exists and loads ──────────────────────────────────────
def test_config_loads(config):
    """config.yaml must exist and be valid YAML"""
    assert config is not None
    assert 'modes' in config, "config.yaml missing 'modes' key"


# ── Test: No duplicate mode names ─────────────────────────────────────────────
def test_no_duplicate_mode_names(config):
    """Mode names must be unique — duplicates cause silent overrides in YAML"""
    with open(CONFIG_PATH, 'r') as f:
        raw = f.read()
    
    mode_names = [
        line.split(':')[0].strip()
        for line in raw.splitlines()
        if line.strip() and not line.startswith(' ') and ':' in line
    ]
    duplicates = [name for name in mode_names if mode_names.count(name) > 1]
    assert not duplicates, f"Duplicate keys found in config: {set(duplicates)}"

# ── Test 2: Every mode has required fields ────────────────────────────────────
def test_all_modes_have_required_fields(config):
    """Every mode must have at least a volume — nothing else is guaranteed"""
    required_fields = ['volume']
    SPECIAL_MODES = {'shutdown', 'gg'}  # these are actions, not environments

    for mode_name, mode_data in config['modes'].items():
        if mode_name in SPECIAL_MODES or not isinstance(mode_data, dict):
            continue  # skip action-type modes
        for field in required_fields:
            assert field in mode_data, (
                f"Mode '{mode_name}' is missing required field: '{field}'"
            )


# ── Test 3: Volume values are sane ────────────────────────────────────────────
def test_volume_values_are_valid(config):
    """Volume must be an integer between 0 and 100"""
    SPECIAL_MODES = {'shutdown', 'gg'}

    for mode_name, mode_data in config['modes'].items():
        if mode_name in SPECIAL_MODES or not isinstance(mode_data, dict):
            continue  # skip action-type modes
        vol = mode_data.get('volume')
        assert isinstance(vol, int), f"Mode '{mode_name}' volume is not an int"
        assert 0 <= vol <= 100, (
            f"Mode '{mode_name}' volume {vol} is out of range [0, 100]"
        )

# Example: Bug was — "gaming state" closed Discord instead of keeping it open
def test_gaming_state_does_not_close_discord(config):
    gaming = config['modes'].get('gaming state', {})
    close_list = gaming.get('close', [])
    assert 'discord' not in close_list, "gaming state should NOT close discord"

SPECIAL_MODES = {'shutdown', 'gg'}

def regular_modes(config):
    """Helper — returns only real environment modes, not action modes"""
    return {
        name: data for name, data in config['modes'].items()
        if name not in SPECIAL_MODES and isinstance(data, dict)
    }


# ── Bug: speak function silent on mode change ─────────────────────────────────
def test_sound_files_are_wav_not_mp3(config):
    """speak/playsound often silently fails on non-wav files"""
    for mode_name, mode_data in regular_modes(config).items():
        sound = mode_data.get('sound')
        if sound:
            assert sound.endswith('.wav'), (
                f"Mode '{mode_name}' uses '{sound}' — must be .wav, not mp3/ogg"
            )

def test_sound_files_exist_on_disk(config):
    """Sound declared but missing = silent failure, no error thrown"""
    for mode_name, mode_data in regular_modes(config).items():
        sound = mode_data.get('sound')
        if sound:
            assert os.path.exists(sound), (
                f"Mode '{mode_name}': sound file '{sound}' not found on disk"
            )

def test_dj_track_files_exist(config):
    """DJ tracks declared but missing = silent crash mid-session"""
    dj = config['modes'].get('dj pawnin state', {})
    for i, track in enumerate(dj.get('tracks', [])):
        assert os.path.exists(track), (
            f"DJ track {i+1} '{track}' not found on disk"
        )


# ── Bug: iPad can't trigger shutdown ─────────────────────────────────────────
def test_shutdown_mode_is_correctly_structured(config):
    """shutdown must be exactly {'shutdown': True} — nothing else"""
    shutdown = config['modes'].get('shutdown')
    assert shutdown is not None, "shutdown mode is missing from config"
    assert shutdown == {'shutdown': True}, (
        f"shutdown mode has unexpected structure: {shutdown}"
    )

def test_trigger_words_are_defined(config):
    """iPad voice commands need trigger words — empty list = no remote control"""
    words = config.get('trigger_words', [])
    assert len(words) > 0, "trigger_words is empty — voice/iPad control won't work"
    assert all(isinstance(w, str) for w in words), "trigger_words must all be strings"


# ── Bug: iPad volume doesn't work ────────────────────────────────────────────
def test_volume_is_integer_not_float(config):
    """Some volume APIs silently fail on floats — must be int"""
    for mode_name, mode_data in regular_modes(config).items():
        vol = mode_data.get('volume')
        if vol is not None:
            assert isinstance(vol, int) and not isinstance(vol, bool), (
                f"Mode '{mode_name}' volume must be int, got {type(vol).__name__}"
            )

def test_no_mode_exceeds_safe_volume(config):
    """Catch accidental volume: 800 typos before they blast your ears"""
    for mode_name, mode_data in regular_modes(config).items():
        vol = mode_data.get('volume', 50)
        assert vol <= 100, f"Mode '{mode_name}' volume {vol} exceeds 100"


# ── Bug: apps and sites open out of sync ─────────────────────────────────────
def test_no_mode_opens_and_closes_same_app(config):
    """App in both open and close list = race condition, unpredictable result"""
    for mode_name, mode_data in regular_modes(config).items():
        apps = set(mode_data.get('apps', []))
        close = set(mode_data.get('close', []))
        conflict = apps & close
        assert not conflict, (
            f"Mode '{mode_name}' opens AND closes: {conflict} — pick one"
        )

def test_websites_are_valid_urls(config):
    """Malformed URLs open blank tabs silently"""
    for mode_name, mode_data in regular_modes(config).items():
        for url in mode_data.get('websites', []):
            assert url.startswith('http://') or url.startswith('https://'), (
                f"Mode '{mode_name}' has invalid URL: '{url}'"
            )