"""The browser may select only these built-in pytest cases."""

CASES = [
    {"id": "normal", "title": "正常抽气与破真空", "category": "正常流程",
     "description": "关阀、抽气达标，再恢复常压。", "test": "test_normal"},
    {"id": "interlock", "title": "抽气中开阀", "category": "阀门联锁",
     "description": "打开破真空阀，验证联锁与安全停止。", "test": "test_interlock"},
    {"id": "sensor", "title": "压力传感器失效", "category": "质量异常",
     "description": "注入 BadSensorFailure，验证控制器识别。", "test": "test_sensor"},
    {"id": "pressure", "title": "压力值异常", "category": "数值异常",
     "description": "写入负压力，验证无效测量的处理。", "test": "test_pressure"},
    {"id": "timeout", "title": "抽气超时", "category": "持续注入",
     "description": "持续写入常压，验证超时与写入任务回收。", "test": "test_timeout"},
]
CASE_BY_ID = {case["id"]: case for case in CASES}
CASE_BY_TEST = {case["test"]: case["id"] for case in CASES}
FAULTS = {"interlock": "抽气中开阀", "sensor": "传感器失效", "pressure": "负压力", "timeout": "持续高压"}
