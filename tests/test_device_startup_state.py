from audio_enhancer.device_state import device_controls_state


def test_device_controls_are_disabled_while_discovery_is_pending():
    assert device_controls_state(waiting=True, has_loopbacks=False, has_speakers=False) == "disabled"


def test_device_controls_are_enabled_when_discovery_finishes():
    assert device_controls_state(waiting=False, has_loopbacks=True, has_speakers=True) == "readonly"


def test_device_controls_remain_readonly_when_no_devices_are_found():
    assert device_controls_state(waiting=False, has_loopbacks=False, has_speakers=False) == "readonly"
