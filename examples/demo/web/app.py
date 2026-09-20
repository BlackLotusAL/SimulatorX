"""Local-only browser entry point. No reloader or import-time subprocesses."""
import argparse
import json
import logging
import os
import socket
from pathlib import Path
import sys
import threading
import webbrowser

from flask import Flask, jsonify, request, send_file
from werkzeug.serving import ThreadedWSGIServer

from demo.common.runtime import ConflictError
from demo.hub import DemoHub


class LocalServer(ThreadedWSGIServer):
    """Reserve the HTTP listener exclusively before starting owned resources."""
    allow_reuse_address = False

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        return super().server_bind()


def create_app(runtime):
    hub = runtime if isinstance(runtime, DemoHub) else DemoHub(plc=runtime)
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config.update(TRUSTED_HOSTS=["127.0.0.1", "localhost"], MAX_CONTENT_LENGTH=16384)

    @app.before_request
    def local_requests():
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if origin and origin != request.host_url.rstrip("/"):
                return jsonify(error="仅允许本机页面发起操作"), 403
            if not request.is_json:
                return jsonify(error="请求必须使用 JSON"), 415

    @app.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(ConflictError)
    def conflict(error):
        return jsonify(error=str(error)), 409

    @app.errorhandler(RuntimeError)
    def runtime_error(error):
        return jsonify(error=str(error)), 503

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/demos")
    def demos():
        return jsonify(hub.summary())

    @app.post("/api/demos/<kind>/initialize")
    def initialize(kind):
        body()
        hub.initialize(kind)
        return jsonify(ok=True), 202

    @app.get("/api/cases")
    @app.get("/api/demos/<kind>/cases")
    def cases(kind="plc"):
        return jsonify(cases=hub.catalog(kind))

    @app.get("/api/state")
    @app.get("/api/demos/<kind>/state")
    def state(kind="plc"):
        return jsonify(hub.get(kind).state(max(0, int(request.args.get("after", 0))),
                                     max(0, int(request.args.get("sample_after", 0)))))

    def body():
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError("请求内容必须为 JSON 对象")
        return data

    @app.post("/api/runs")
    @app.post("/api/demos/<kind>/runs")
    def start_run(kind="plc"):
        return jsonify(run_id=hub.start_run(kind, body().get("case_ids"))), 202

    @app.post("/api/runs/cancel")
    @app.post("/api/demos/<kind>/runs/cancel")
    def cancel(kind="plc"):
        body()
        hub.get(kind).cancel()
        return jsonify(ok=True), 202

    @app.post("/api/manual")
    @app.post("/api/demos/<kind>/manual")
    def manual(kind="plc"):
        data = body()
        options = {} if kind == "plc" else {"target": data.get("target", 30), "speed": data.get("speed", 90)}
        hub.get(kind).manual(data.get("action"), data.get("fault"), **options)
        return jsonify(ok=True)

    @app.post("/api/reset")
    @app.post("/api/demos/<kind>/reset")
    def reset(kind="plc"):
        body()
        hub.get(kind).reset()
        return jsonify(ok=True), 202

    @app.get("/api/artifacts/<name>")
    @app.get("/api/demos/<kind>/artifacts/<name>")
    def artifact(name, kind="plc"):
        runtime = hub.get(kind)
        if name not in ("junit.xml", "events.jsonl", "pytest.log") or not runtime.run:
            return jsonify(error="报告不存在"), 404
        path = Path(runtime.run["artifacts"]) / name
        if not path.is_file():
            return jsonify(error="报告尚未生成"), 404
        return send_file(path, as_attachment=True)

    @app.post("/api/demos/<kind>/control")
    def control(kind):
        if kind == "plc":
            raise ValueError("PLC does not use the demo control bridge")
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        return jsonify(result=hub.get(kind).control(token, body()))

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description="SimulatorX PLC / SDK / TCP browser demo (local only)")
    parser.add_argument("--port", type=int, default=0, help="Local web port; 0 selects an available port")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--sdk-library", help="Existing reference DLL/.so; otherwise build on first SDK use")
    parser.add_argument("--artifacts", help="Directory for pytest run artifacts")
    parser.add_argument("--ready-file", help="Write local URL after startup")
    parser.add_argument("--managed", action="store_true", help="Exit on parent stdin input or EOF")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("Port must be in 0..65535")
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    runtime = DemoHub(artifacts=args.artifacts, library=args.sdk_library)
    server = None
    server_thread = None
    stopped = threading.Event()
    try:
        # Reserve the HTTP port before starting the PLC; a conflict owns nothing.
        server = LocalServer("127.0.0.1", args.port, create_app(runtime))
        url = "http://127.0.0.1:" + str(server.server_port)
        runtime.url = url
        runtime.start()
        if args.ready_file:
            path = Path(args.ready_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"url": url, "opcua_endpoint": runtime.get("plc").endpoint}), encoding="utf-8")
        print(json.dumps({"event": "ready", "url": url, "opcua_endpoint": runtime.get("plc").endpoint}), flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        if args.managed:
            def watch_parent():
                sys.stdin.readline()
                stopped.set()
            threading.Thread(target=watch_parent, name="demo-parent", daemon=True).start()
        server_thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        server_thread.start()
        while not stopped.wait(0.2):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        try:
            runtime.stop()
        finally:
            if server_thread:
                server.shutdown()
                server_thread.join(timeout=5)
            if server:
                server.server_close()


if __name__ == "__main__":
    main()
