"""Local-only browser entry point. No reloader or import-time subprocesses."""
import argparse
import json
import logging
from pathlib import Path
import sys
import threading
import webbrowser

from flask import Flask, jsonify, request, send_file
from werkzeug.serving import make_server

from .catalog import CASES
from .runtime import ConflictError, DemoRuntime


def create_app(runtime):
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

    @app.get("/api/cases")
    def cases():
        return jsonify(cases=CASES)

    @app.get("/api/state")
    def state():
        return jsonify(runtime.state(max(0, int(request.args.get("after", 0))),
                                     max(0, int(request.args.get("sample_after", 0)))))

    def body():
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError("请求内容必须为 JSON 对象")
        return data

    @app.post("/api/runs")
    def start_run():
        return jsonify(run_id=runtime.start_run(body().get("case_ids"))), 202

    @app.post("/api/runs/cancel")
    def cancel():
        body()
        runtime.cancel()
        return jsonify(ok=True), 202

    @app.post("/api/manual")
    def manual():
        data = body()
        runtime.manual(data.get("action"), data.get("fault"))
        return jsonify(ok=True)

    @app.post("/api/reset")
    def reset():
        body()
        runtime.reset()
        return jsonify(ok=True), 202

    @app.get("/api/artifacts/<name>")
    def artifact(name):
        if name not in ("junit.xml", "events.jsonl", "pytest.log") or not runtime.run:
            return jsonify(error="报告不存在"), 404
        path = Path(runtime.run["artifacts"]) / name
        if not path.is_file():
            return jsonify(error="报告尚未生成"), 404
        return send_file(path, as_attachment=True)

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description="SimulatorX PLC browser demo (local only)")
    parser.add_argument("--port", type=int, default=0, help="Local web port; 0 selects an available port")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--artifacts", help="Directory for pytest run artifacts")
    parser.add_argument("--ready-file", help="Write local URL after startup")
    parser.add_argument("--managed", action="store_true", help="Exit on parent stdin input or EOF")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("Port must be in 0..65535")
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    runtime = DemoRuntime(artifacts=args.artifacts)
    server = None
    try:
        # Reserve the HTTP port before starting the PLC; a conflict owns nothing.
        server = make_server("127.0.0.1", args.port, create_app(runtime), threaded=True)
        runtime.start()
        url = "http://127.0.0.1:" + str(server.server_port)
        if args.ready_file:
            path = Path(args.ready_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"url": url, "opcua_endpoint": runtime.endpoint}), encoding="utf-8")
        print(json.dumps({"event": "ready", "url": url, "opcua_endpoint": runtime.endpoint}), flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        if args.managed:
            def watch_parent():
                sys.stdin.readline()
                server.shutdown()
            threading.Thread(target=watch_parent, name="demo-parent", daemon=True).start()
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        if server:
            server.server_close()
        runtime.stop()


if __name__ == "__main__":
    main()
