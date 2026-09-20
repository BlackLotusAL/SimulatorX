"""Failed model reset poisons services while preserving diagnostics."""
import pytest
from test.helpers import sdk_service


@pytest.mark.parametrize("kind", ["sdk", "tcp"])
def test_model_reset_failure_poisoning_preserves_diagnostics(kind, tmp_path, monkeypatch):
    if kind == "sdk":
        service = sdk_service(tmp_path / "control", tmp_path / "sdk", clock=lambda: 0)
    else:
        from protocols.tcp.service import TCPService
        from subsystems.detector.modbus_tcp.model import create
        spec = create()
        service = TCPService(spec.protocol_factory(), spec.model_factory())

    def fail():
        raise ValueError("model reset failed")

    monkeypatch.setattr(service.model, "reset", fail)
    with pytest.raises(RuntimeError, match="model reset failed"):
        service.dispatch({"op": "reset"})
    with pytest.raises(RuntimeError, match="model reset failed"):
        service.dispatch({"op": "reset"})
    assert "model reset failed" in service.dispatch({"op": "diagnostics"})["failed_reason"]
