"""Create only the protocol instances requested by each acceptance module."""
import threading
import pytest
from demo.tests.support import until


@pytest.fixture(scope="module")
def hub(tmp_path_factory, request):
    pytest.importorskip("flask")
    from demo.hub import DemoHub
    from demo.web.app import LocalServer, create_app
    hub = DemoHub(artifacts=tmp_path_factory.mktemp("three-demos"), hold=0)
    server = LocalServer("127.0.0.1", 0, create_app(hub))
    hub.url = "http://127.0.0.1:" + str(server.server_port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        hub.start()
        for kind in request.module.DEMO_PROTOCOLS:
            hub.initialize(kind)
            until(lambda: kind not in hub.starting)
            assert kind not in hub.errors, hub.errors
        yield hub
    finally:
        hub.stop()
        server.shutdown()
        server.server_close()
        thread.join(5)
