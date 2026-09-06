import pytest
pytest.importorskip("flask")
from demo.tests.support import (
    assert_five_real_cases, assert_manual_fault_disconnect_and_recovery,
    assert_forced_cancel_rebuilds_only_active_device,
)

DEMO_PROTOCOLS = ("tcp",)


@pytest.mark.parametrize("repeat", range(2))
def test_five_real_cases_repeated(hub, repeat):
    assert_five_real_cases(hub, "tcp")


def test_manual_fault_disconnect_and_recovery(hub):
    assert_manual_fault_disconnect_and_recovery(hub, "tcp", ("check",))


def test_forced_cancel_rebuilds_only_active_device(hub):
    assert_forced_cancel_rebuilds_only_active_device(hub, "tcp")
