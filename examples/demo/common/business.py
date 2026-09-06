"""Translate reference SUT feedback for the browser."""
def controller(status):
    return {"status": {"succeeded": "completed", "failed": "fault"}.get(status["state"], "running"),
            "fault": status["error_code"], "message": status["message"] or
            {"running": "业务执行中", "succeeded": "业务已完成", "failed": "业务失败"}[status["state"]],
            "observed": status["observed"], "operation_id": status["operation_id"]}
