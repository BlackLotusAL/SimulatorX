"""Protocol cases and fault presets."""
ROWS = [
    ('normal', '正常检测', '读取就绪状态和真实测量值 100。'),
    ('recover', '未就绪后恢复', '先返回未就绪，随后正常完成检测。'),
    ('device_error', '设备状态异常', '状态寄存器返回 7，识别设备错误。'),
    ('measurement_error', '测量读取异常', '测量读取返回 Modbus 异常 4。'),
    ('timeout', '持续未就绪超时', '持续返回未就绪，验证检测超时。'),
]
CASES = [dict(id=key, title=title, description=description,
              category="正常流程" if key == "normal" else "故障场景", test="test_" + key)
         for key, title, description in ROWS]


def inject(client, fault):
    if not isinstance(fault, str) or fault not in {row["id"] for row in CASES} - {"normal"}:
        raise ValueError("未知故障条件")
    target, values = {
        "recover": ("04:0:2", [{"registers": [0, 0]}] * 15 + [{"registers": [0, 1]}]),
        "device_error": ("04:0:2", [{"registers": [7, 1]}]),
        "measurement_error": ("04:2:1", [{"exception": 4}]),
        "timeout": ("04:0:2", [{"registers": [0, 0]}] * 200),
    }[fault]
    client.returns[target].set_sequence(values)
