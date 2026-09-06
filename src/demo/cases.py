"""Real pytest scenarios, explicitly selected by the local demo runner."""
from local_service.plc.bindings import read_values


def wait_completed(context, controller, timeout):
    context.wait(lambda: controller.snapshot()["status"] != "running", timeout, "控制器未及时结束")
    assert controller.snapshot()["status"] == "completed", controller.snapshot()
    controller.stop()


def test_normal(plc_nodes, demo_context, demo_controller):
    demo_context.emit("step", "关阀并抽真空，等待压力达标")
    demo_controller.start("pump")
    wait_completed(demo_context, demo_controller, 15)
    assert abs(plc_nodes["vacuum.pressure_pa"].get_value() - 1000) <= 20
    assert plc_nodes["vacuum.state_code"].get_value() == 3
    demo_context.emit("assertion", "抽气达标：压力进入目标容差，PLC 状态正确")
    demo_context.hold()
    demo_controller.start("vent")
    wait_completed(demo_context, demo_controller, 11)
    assert abs(plc_nodes["vacuum.pressure_pa"].get_value() - 101325) <= 20
    assert plc_nodes["vacuum.valve_open"].get_value() is True
    demo_context.emit("assertion", "破真空完成，压力恢复常压")
    demo_context.hold()


def fault_case(fault, expected, nodes, client, context, controller, injector):
    context.emit("step", "启动抽气，等待实际进入抽气状态")
    controller.start("pump")

    def pumping():
        values = read_values(client, nodes)
        return (values["vacuum.state_code"].Value.Value == 1
                and 1000 < values["vacuum.pressure_pa"].Value.Value < 30000)

    context.wait(pumping, 6, "PLC 未进入可注入的抽气阶段")
    injector.inject(fault)
    context.emit("step", "等待示例控制器识别故障并安全停止")
    context.wait(lambda: controller.snapshot()["status"] != "running", 15, "控制器未处理故障")
    controller.stop()  # Joins the safety stop and disconnection, not just the diagnosis.
    state = controller.snapshot()
    assert state["status"] == "fault", state
    assert state["fault"] == expected, state
    assert nodes["vacuum.valve_open"].get_value() is False
    assert nodes["vacuum.state_code"].get_value() == 0
    assert nodes["vacuum.result_code"].get_value() == 0
    if fault == "interlock":
        assert nodes["vacuum.alarm_code"].get_value() == 1
    context.emit("assertion", "预期故障已识别，停止与关阀断言通过", expected_fault=expected)
    context.hold()


def test_interlock(plc_nodes, plc_client, demo_context, demo_controller, demo_injector):
    fault_case("interlock", "interlock", plc_nodes, plc_client, demo_context, demo_controller, demo_injector)


def test_sensor(plc_nodes, plc_client, demo_context, demo_controller, demo_injector):
    fault_case("sensor", "sensor_quality", plc_nodes, plc_client, demo_context, demo_controller, demo_injector)


def test_pressure(plc_nodes, plc_client, demo_context, demo_controller, demo_injector):
    fault_case("pressure", "invalid_pressure", plc_nodes, plc_client, demo_context, demo_controller, demo_injector)


def test_timeout(plc_nodes, plc_client, demo_context, demo_controller, demo_injector):
    fault_case("timeout", "timeout", plc_nodes, plc_client, demo_context, demo_controller, demo_injector)
