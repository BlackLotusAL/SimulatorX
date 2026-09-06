"""Protocol cases and fault presets."""
ROWS = [
    ('normal', '正常定位', '使能、回零，再定位至 30°。'),
    ('sdk_error', '运动命令返回错误', 'MoveAbsolute 返回 −41，控制器安全停止。'),
    ('query_error', '状态查询返回错误', 'GetState 返回 77，识别查询失败。'),
    ('limit', '正限位', '设置正限位，验证运动被拒绝。'),
    ('timeout', '堵转超时', '轴保持堵转，验证业务超时与停止。'),
]
CASES = [dict(id=key, title=title, description=description,
              category="正常流程" if key == "normal" else "故障场景", test="test_" + key)
         for key, title, description in ROWS]


def inject(client, fault):
    if not isinstance(fault, str) or fault not in {row["id"] for row in CASES} - {"normal"}:
        raise ValueError("未知故障条件")
    if fault == "sdk_error":
        client.returns["rotary.MoveAbsolute"].set_sequence([-41])
    elif fault == "query_error":
        client.returns["rotary.GetState"].set_sequence([77])
    else:
        client.set_fault("positive_limit" if fault == "limit" else "stalled", True)
