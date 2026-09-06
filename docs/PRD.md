# SimulatorX 基础仿真框架

## 1. 目标与边界

以软件提供本机单真空腔室的 OPC UA 节点，支持基础流程、原生改值和环境重置，供既有自动化框架控制 PLC 条件。SUT（被测业务系统）的接口调用、异常判定和生命周期由接入方负责。

交付 OPC UA 服务、基础行为、Reset、节点资源、进程管理、pytest 环境插件和框架自测。运行、构建、测试固定使用 Python 3.9.12（64 位）；opcua 为 0.98.13，测试基线为 pytest 8.4.2，完整依赖固定在 requirements.lock。

默认单实例串行，监听 127.0.0.1；高精度物理特性和真实设备通信时序一致性需另行验证。

## 2. 核心架构

VacuumSimulator 管理节点初始化、启动、停止、行为更新和 Reset。节点保存唯一业务状态，每轮读取当前值计算后续输出。一个锁同步原生请求、每轮行为和 Reset。

CLI 解析资源、端口和 profile 后启动同一服务。SimulatorProcess 使用该入口启动独享子进程，确认可连接后返回 endpoint，只停止自身创建的进程。外部实例由调用方管理。

包配置声明 Python 版本、运行依赖、可选测试依赖和 XML/JSON 资源，供其他项目安装并加载插件。

## 3. 节点契约与配置

Namespace URI：urn:simulatorx:mvp:vacuum。对象：Objects/SimulatorX/VacuumChamber1。按 URI 解析实际索引，八个变量均为可读写标量。

| 逻辑节点 | 类型 | Reset 初值 | 定义 |
|---|---|---|---|
| vacuum.valve_command | UInt16 | 0 | 0 无命令、1 开阀、2 关阀 |
| vacuum.valve_open | Boolean | false | 破真空阀反馈 |
| vacuum.valve_result | UInt16 | 0 | 0 空闲、1 执行中、2 成功、3 失败 |
| vacuum.command | UInt16 | 0 | 0 无命令、1 抽真空、2 破真空、3 停止并关阀 |
| vacuum.pressure_pa | Double | 101325 | 当前压力，Pa |
| vacuum.state_code | UInt16 | 0 | 0 空闲、1 抽气、2 破真空、3 真空达标、4 故障 |
| vacuum.result_code | UInt16 | 0 | 真空操作结果，编码同 valve_result |
| vacuum.alarm_code | UInt16 | 0 | 0 无报警、1 抽气与开阀冲突、2 非法命令 |

XML 定义层级、NodeId、类型、权限及导入初值；bindings.json 定义逻辑映射、类型和权限校验信息及 baseline。二者人工同步。启动和 Reset 采用绑定基线，单独修改 JSON 不会创建节点。

保留标量类型约束，允许类型范围内的异常业务值。UInt16 接受 0–65535，Double 接受负值和非有限值，Null Variant 表示空值。错误类型返回 BadTypeMismatch，保持原节点值。

## 4. 基础行为

| 动作或条件 | 阀门反馈与结果 | 真空状态、结果与报警 |
|---|---|---|
| 开阀或关阀 | 反馈对应变化，结果 1 → 2 | 独立操作不改真空结果；抽气开阀触发联锁 |
| 阀门命令非法 | 反馈保持，阀门结果 3 | 报警 2 |
| 关阀时抽真空 | 阀门保持 | 状态 1、结果 1、报警 0 |
| 开阀时请求抽气或抽气中开阀 | 阀门可执行成功 | 状态 4、结果 3、报警 1 |
| 破真空 | 先退出抽气再开阀 | 状态 2、结果 1、报警 0 |
| 抽气达标 | 保持关闭 | 状态 3、结果 2 |
| 破真空达到常压 | 保持打开 | 状态 0、结果 2 |
| 停止并关阀 | 关闭、阀门结果 2 | 状态 0、结果 0，报警保持 |
| 真空命令非法 | 反馈保持 | 状态 4、结果 3、报警 2 |

处理命令后归零，确认归零后可重复提交；归零前的新写入可能替换尚未处理的命令。阀门立即反馈，客户端可能看不到短暂执行中状态。有效抽气/破真空命令和 Reset 清除报警。

每 100 ms 采用 P_next = P_current × exp(-dt / tau) + P_target × (1 - exp(-dt / tau)) 更新。开阀趋向 101325 Pa，关阀抽气趋向 1000 Pa，其他状态保持压力；完成容差 20 Pa。普通抽气/破真空时间常数为 3 s / 1.5 s，fast 为 0.2 s / 0.15 s。

测试写入值直接参与后续计算，自动行为可能继续更新该值。持续异常由测试重复写入。压力质量非 Good、Null、NaN 或无穷时跳过计算并保留 DataValue，继续处理命令和 Reset；无可计算压力时不能判定完成。

状态、结果和报警随动作或状态转换更新，静态状态节点无需持续更新时间戳。异常值本身不会自动产生业务报警，业务判断由 SUT 完成。

## 5. 原生接口与 Reset

使用 opcua.Node.set_value(value, VariantType) 写数值，或传入 ua.DataValue 写质量码及源/服务器时间戳。多次请求按实际时序生效，不承诺跨请求的多节点事务或持续覆盖。

get_value()/get_data_value() 遇到 Bad 质量会抛异常；完整异常 DataValue 通过 get_attributes([Value]) 或原生 Read 请求读取。

VacuumChamber1.Reset 为同一命名空间的原生方法，无输入，成功返回 Boolean true。恢复绑定基线、Good、源/服务器时间戳和计时，保留连接与订阅。恢复失败后服务不可复用，需要重启。

Reset 前必须停止 SUT 和后台写入任务；Reset 不阻止其他客户端随后再次写入。

## 6. pytest 环境插件

公共 conftest.py 注册 pytest_plugins = ["simulatorx.pytest_plugin"]。

| Fixture | 作用域 | 职责 |
|---|---|---|
| plc_service | session | 启动独享 fast 服务并自动分配端口，或复用 --opcua-endpoint；仅停止自身服务 |
| plc_client | function | 建立原生连接，结束时断开 |
| plc_nodes | function | Reset，返回逻辑名称到原生 Node 的映射，结束时再次 Reset |

加载插件不会执行全部 fixture，需通过用例依赖或公共准备 fixture 启用。插件不提供 sut fixture，真实 SUT 由既有框架管理。SUT 和写入任务 fixture 应依赖 plc_nodes，并登记停止和等待退出回调。

清理顺序：停止 SUT/写入任务 → Reset → 断开测试连接 → 整轮结束停止自建服务。plc_nodes 在首次 Reset 前登记最终清理，准备和断言失败后仍尝试恢复。Reset 或断开连接失败记录 pytest 错误并停止后续用例。

SUT 停止失败或 pytest 被强制结束时，接入方负责终止本轮、回收写入者和恢复环境。插件要求串行运行，并行作业应使用不同实例。

改值可以放在场景准备逻辑中，业务用例仍调用 SUT 接口并断言可观察结果。写入成功不等于异常检测成功；预期异常被正确处理时测试可以通过。

## 7. 启动、扩展和报告

默认端口 4840，0 自动分配；冲突应报错并保留已有进程。CLI 支持 --nodeset（多次传入时按依赖顺序导入）、--bindings、--profile、--fast，显式 profile 优先，默认 pytest 使用 fast。

新增节点需同步 XML 和绑定，需要自动变化时再适配行为。当前行为要求八个真空逻辑节点和 VacuumChamber1 对象。服务自定义绑定不会自动传入插件，测试侧应同步映射。

JUnit 记录数量、结果、耗时、断言失败与准备/清理错误；控制台日志辅助定位。流水线保留退出码，失败时也归档产物。虚拟环境、报告及构建临时目录不纳入源码提交。

## 8. 验收与归属

| 验证内容 | 对应文件 |
|---|---|
| 行为、联锁、非法命令、单一压力来源和配置校验 | [test_simulator.py](../tests/test_simulator.py) |
| 运行计时、Reset 失败和行为线程异常 | [test_lifecycle.py](../tests/test_lifecycle.py) |
| 原生读写、类型、DataValue、连接订阅和端口生命周期 | [test_integration.py](../tests/test_integration.py) |
| 插件按需启用、准备/断言失败清理、写入任务停止顺序和清理失败中止 | [test_fixtures.py](../tests/test_fixtures.py) |

实现见 [simulator.py](../simulatorx/simulator.py)、[bindings.py](../simulatorx/bindings.py) 和 [pytest_plugin.py](../simulatorx/pytest_plugin.py)。在仅包含交付文件的环境中安装并运行 Python 3.9.12 自测，以当次结果作为验收依据。
