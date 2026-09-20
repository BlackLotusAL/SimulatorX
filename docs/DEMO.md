# PLC、SDK、TCP 可视化演示

统一入口提供 PLC 真空腔室、SDK 旋转轴和 Modbus TCP 探测器三个标签页，全部使用真实设备实例、业务调用与 pytest 结果。示例统一位于 `examples/demo/`：`web/` 提供页面和 API，`common/` 复用运行、事件与报告，`plc/`、`sdk/`、`tcp/` 各自维护控制、故障场景和 pytest 插件，`hub.py` 负责装配与全局互斥。演示模块不属于框架公共 API；外部参考 SUT 保留在 `examples/sut_integration/`。

PLC 在启动时就绪，SDK、TCP 在首次切换时启动。切换标签页保留状态、曲线、选中用例和报告，不取消后台操作。全局一次只运行一轮自动用例，“运行全部用例”只运行当前协议的五例；其他设备仍可手动操作。正在运行手动业务的设备须先停止，才能运行自动用例。

SDK 构建或某个演示启动失败时，只影响该标签页。修复依赖后点击“重试启动”，其他演示继续可用。

## 启动

在项目根目录使用 Python **3.9.12（64 位）**，建议使用虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-demo.lock
.\.venv\Scripts\python.exe src/main.py demo
```

Linux／WSL 对应使用 `.venv/bin/python`。启动输出包含网页 URL 和 OPC UA 地址；WSL 可从 Windows 浏览器访问输出的 `http://127.0.0.1:端口`。依赖安装完成后，页面、图形、控制与用例执行均可离线运行，无 CDN 或前端构建步骤。

| 参数 | 行为 |
|---|---|
| `--port 0` | 默认值，系统分配空闲网页端口 |
| `--port 8080` | 指定网页端口；被占用时失败，不影响占用进程 |
| `--no-browser` | 不自动打开浏览器，仍输出可访问 URL |
| `--artifacts 路径` | 报告根目录，默认项目的 `artifacts/demo/`，按协议与运行 ID 隔离 |
| `--sdk-library 路径` | 已构建的参考 DLL／`.so`；未传时首次选择 SDK 自动构建 |
| `--ready-file 路径` | 为进程管理和自动验收输出就绪地址 |
| `--managed` | 从父进程 stdin 收到一行或 EOF 时退出 |

仅监听 `127.0.0.1`。一次只执行一轮内置用例；界面不接受任意脚本或外部测试路径。Ctrl+C 退出会等待测试清理并停止所有自建设备；关闭浏览器标签只停止页面显示。

## SDK 旋转轴

Windows 原生支持 **x64 Python + MinGW-w64 GCC**（目标 `x86_64-w64-mingw32`，`gcc` 需在 PATH）。Linux／WSL 使用 `cc` 或 `gcc`。Windows 不需要 WSL；缺编译器或无法加载库时显示实际错误，不使用替代控制调用。

```sh
python src/main.py build-sdk --hardware motion/rotary_axis --output artifacts/native
# Windows 指定已构建库；Linux 对应 libsimulatorx_sdk.so
python src/main.py demo --sdk-library artifacts/native/simulatorx_sdk.dll
```

手动演示先“使能”→“回零”，再执行绝对或相对移动。默认目标／增量为 30°，速度 90°/s；目标和最终位置范围 −180..180°，速度必须大于 0 且不超过 90°/s。也可停止、禁用、清故障；模型故障条件通过“重置环境”清除。

中央表盘蓝线表示真实位置，橙线表示目标；曲线分别显示位置、目标和速度。状态来自设备只读快照，控制操作在独立进程调用真实 `SX_*` DLL／`.so`。

| 用例 | 条件与业务判定 |
|---|---|
| 正常定位 | 使能、回零、定位至 30°，SUT 检查完成标志及 ±0.01° 位置容差 |
| 运动命令返回错误 | MoveAbsolute 返回 −41，SUT 识别 SDK 错误并停止 |
| 状态查询返回错误 | GetState 返回 77，SUT 识别查询错误并停止 |
| 正限位 | 设置 positive_limit，运动命令被模型拒绝 |
| 堵转超时 | 设置 stalled，SUT 在 4 s 业务期限后超时并停止 |

Windows DLL 使用 cdecl；`SX_State` 为 48 字节。Windows 宿主为测试控制与原生业务调用分别分配本机 TCP 端口；Linux 保留两个 Unix socket。`SX_*` ABI、报文格式和返回码不变，Windows 库使用 `SIMULATORX_SDK_ENDPOINT=tcp://127.0.0.1:<port>`。Linux 使用原有 `SIMULATORX_CONTROL_SOCKET`；错误文件和超时变量在两平台保持相同语义。

## TCP 探测器

“开始检测”通过真实 Modbus TCP 连接读取状态、就绪和测量值；默认测量值为 100。“停止检测”结束业务连接，重置恢复使能寄存器 1、参考值 100，并清除响应序列。

| 用例 | 条件与业务判定 |
|---|---|
| 正常检测 | 正常响应，SUT 检查就绪且测量为 100 |
| 未就绪后恢复 | 前 15 次状态读取未就绪，随后就绪，业务成功 |
| 设备状态异常 | 返回状态 7，SUT 报告设备错误 |
| 测量读取异常 | 测量请求返回 Modbus 异常 4，SUT 报告设备错误 |
| 持续未就绪超时 | 预置 200 次未就绪响应，覆盖 4 s 业务期限 |

页面仅展示 SUT 已接收的反馈、宿主诊断和序列消耗，不额外读取业务输入寄存器；未读取的值显示“—”。环境重置后的基线验证使用保持寄存器读取，不消耗检测输入序列。诊断中可能保留该基线读取的空序列统计，它不代表预置故障。

SDK、TCP 均支持操作前预置故障；运行中注入只影响尚未发生的调用，不改变已完成的响应。故障被业务正确识别时，pytest 仍显示通过。

## 公共 API 与测试所有权

`GET /api/demos` 返回可用状态与全局正在运行的协议。`/api/demos/<plc|sdk|tcp>/` 下提供 `initialize`、`state`、`cases`、`manual`、`runs`、`runs/cancel`、`reset` 与 `artifacts/<name>`。原有 `/api/state` 等无协议前缀接口继续映射 PLC。

初始化是异步操作，页面显示启动进度。状态使用共同的 `mode/snapshot/controller/run/events/samples` 外层结构；PLC 保留节点字段，SDK/TCP 使用协议对应的 `snapshot.values`、`sequences`、`traffic`。

后端独占设备生命周期；pytest 使用后端连接描述文件连接页面中的同一实例。SDK/TCP 测试控制经本机 HTTP 转发，须携带当前运行专属令牌，只开放健康、诊断、Reset、序列以及 SDK 模型故障操作；结束后令牌失效。TCP 宿主仍只有业务监听端口，测试控制最终经原有父子进程管道执行。

各例停止 SUT 后保存诊断、检查健康并 Reset；准备或清理失败终止后续用例。停止本轮先协作取消，超过 10 s 时终止本轮 pytest 并仅重建对应设备。退出时 HTTP 控制转发保持可用直至测试清理完成。

## PLC 推荐演示顺序


1. 点击“运行全部用例”。中央示意图依次展示抽气、压力下降、阀门动作和恢复常压；下方记录展示实际准备、控制、注入、断言与清理步骤。
2. 观察异常用例中三个独立结果：PLC 报警、示例控制器故障、pytest 用例结果。控制器正确识别异常并安全停止时，用例显示“通过”。
3. 全部结束后，可再次运行选中的用例，验证 Reset 后相同场景可以重复执行。
4. 手动演示：点击“抽真空”，在抽气过程中选择并“注入故障”，观察控制器响应；点击“重置环境”清除故障并恢复基线。
5. 在持续高压用例中点击“停止本轮”，观察当前用例退出、写入任务停止和环境恢复；清理结束后才恢复手动控制。

| 用例 | 注入条件 | 示例控制器与断言 |
|---|---|---|
| 正常抽气与破真空 | 无 | 抽气至 1000 ±20 Pa，再恢复 101325 ±20 Pa |
| 抽气中开阀 | 原生阀门命令 | 识别 PLC 联锁报警 1，停止并关阀 |
| 压力传感器失效 | Null + BadSensorFailure | 识别坏质量，停止并关阀 |
| 压力值异常 | 压力写为 −50000 Pa | 识别无效测量，停止并关阀 |
| 抽气超时 | 每 20 ms 写入 101325 Pa | 12 s 未完成抽气时超时，停止并关阀 |

自动用例在发出抽气命令前建立注入连接，确认真实节点进入抽气状态后立即写入，避免快速设备已经完成动作才连接。持续高压由后台重复写入实现，PLC 自身的压力计算仍在运行，所以采样间压力可能略有波动。

## 状态解释

- 腔室压力、破真空阀和 PLC 报警来自现有八个节点。泵动画由 `vacuum.state_code == 1` 推导，不代表额外的硬件反馈节点。
- 示例控制器使用独立 OPC UA 连接，每 50 ms 检查状态，抽气／破真空超时分别为 12 s／8 s。坏质量、Null、NaN、无穷或非正压力均视为异常测量。其故障信息不写入新的 PLC 报警码。
- 完成判定同时核对 PLC 状态、结果和实际压力：抽气须不高于 1020 Pa，破真空须达到 101325 ±20 Pa。只有完成标志而压力不符时继续等待或报告超时。
- 观测连接每 100 ms 读取完整 DataValue，浏览器每轮请求完成后间隔 200 ms 更新。坏质量与无效压力在曲线上显示断线；断连时清除当前节点显示并停止动画。
- 连接发生错误后，即使观测连接已经恢复，也会显示“请重置环境”并阻止控制或启动用例；重置验证基线后重新开放操作。
- PLC 正常状态下 `Good` 只代表质量码，负数仍可显示为“Good · 值异常”。控制器检测故障后会停止 PLC，故障原因保留用于讲解，直至新操作或重置。
- 演示使用当前参考设备的固定参数：抽气／破真空时间常数为 0.2 s／0.15 s。故障用例按实际节点状态同步注入，结果展示保留停留时间；不提供 profile 参数覆盖。

## 用例隔离、取消与报告

后端通过当前整机配置启动并持有独享 PLC 子进程，将端点、绑定路径和 Reset 标识写入连接描述文件。pytest 使用独立配置与演示插件，通过 `--demo-connection` 连接同一个可见实例；不加载框架的服务启动 fixture，也不创建或停止 PLC 进程。示例控制器与注入器 fixture 依赖 `device`，结束顺序为：停止注入器和控制器 → 保存诊断 → Reset；会话结束断开测试连接。界面观测连接只读，不干扰清理。整轮结束后后端再验证基线，开放手动操作。

“停止本轮”通过取消标记协作退出，当前测试在 JUnit 中记录为跳过，界面显示“已取消”。超过 10 s 仍未退出时，只终止本轮 pytest 并重建自有 PLC，整轮记录环境错误。服务断连或清理失败也记录环境错误并中止后续用例；恢复完成后可以重新运行。

每轮结果写入 `artifacts/demo/<plc|sdk|tcp>/<run_id>/`：

| 文件 | 内容 |
|---|---|
| `events.jsonl` | 用例、步骤、控制器、注入与三个 pytest 阶段的结构化事件 |
| `pytest.log` | pytest 控制台日志与失败详情 |
| `junit.xml` | pytest 原生 JUnit 结果 |

网页底部可下载最近一轮的三个报告。最终用例状态汇总 setup、call、teardown：断言通过但清理失败时显示环境错误。内存保留最近 2000 个事件和约两分钟压力采样，页面显示最近 300 条记录；完整用例事件保存在 JSONL。

## 验证

`examples/demo/tests/` 按职责划分：`plc/`、`sdk/`、`tcp/` 保存各协议验收，`common/` 检查跨协议互斥及公共报告恢复，`web/` 检查网页入口、测试收集与 managed 退出。根目录的 `conftest.py` 和 `support.py` 复用设备夹具及断言；夹具只初始化测试模块需要的协议，单独运行 TCP 验收不需要 SDK 编译器。

```sh
python -m pytest -c examples/demo/pytest.ini -q --junitxml=artifacts/demo-acceptance.xml
python -m pytest -c examples/demo/pytest.ini examples/demo/tests/sdk -q
python -m pytest -c examples/demo/pytest.ini examples/demo/tests/tcp -q
python -m pytest -q --junitxml=artifacts/junit.xml
```

演示验收覆盖五例重复运行、手动操作、取消、强制取消重建、异常数值／坏质量、并发写入拦截、断连恢复、准备／断言／清理失败和仓库外启动。未安装演示依赖时，原有框架测试仍可运行，独立演示验收会跳过。根目录默认测试集不收集演示测试。SDK 回归在 Windows 使用 MinGW-w64 GCC，在 Linux／WSL 使用 C 编译器。新增演示验收覆盖 SDK/TCP 各五例重复执行、跨协议互斥、手动操作、取消／强制重建、断连、准备／断言／清理失败和初始化重试。

参考框架的运行与接口契约见 [PRD](PRD.md)，离线结构说明见 [架构网页](architecture.html)。
