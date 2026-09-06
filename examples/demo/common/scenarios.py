from .business import controller


def wait_terminal(sut, context, operation):
    def terminal():
        status = sut.get_status(operation)
        context.emit("controller", "业务状态同步", controller=controller(status))
        return status if status["state"] != "running" else None
    return context.wait(terminal, 8, "业务未在规定时间内结束")
