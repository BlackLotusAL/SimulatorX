# SimulatorX

面向本地串行测试的仿真框架，统一提供 **PLC、SDK、TCP** 三类独立服务。PLC 通过 OPC UA 节点模拟单真空腔室；SDK 模拟有限行程旋转轴；TCP 按设备协议生成应答。三者分别管理状态、启动、停止和 Reset。

SDK 通过 Linux `.so` 适配库访问外部运动模型，并支持函数返回值序列、卡住和限位；TCP 提供应答字段序列。完整需求、行为契约、参考 ABI、协议及接入边界见[统一 PRD](docs/PRD.md)。下文提供快速运行与 pytest 接入说明。

运行、SDK 编译和测试统一使用 **Python 3.9.12（64 位）**。安装依赖后直接运行源码。

## 目录与职责

```text
src/
├── main.py                 # 选择并启动一个组件，或编译参考 SDK
├── pytest_plugin.py        # 统一注册三类 fixture 和命令行选项
├── local_service/
│   ├── common/             # 控制通信、序列、通用客户端、子进程源码路径
│   ├── plc/                # OPC UA 服务、真空模型、节点资源和进程管理
│   ├── sdk/                # SDK 服务、旋转轴模型、客户端、编译及 native 源码
│   ├── tcp/                # TCP 服务、协议及控制客户端
│   └── process.py          # SDK/TCP 子进程管理
└── testing/                # common、plc、sdk、tcp 的 fixture 实现
tests/
├── local_service/          # plc、sdk、tcp 行为与协议测试
└── testing/                # 插件生命周期及失败清理测试
```

`src` 是源码搜索根，导入使用 `local_service.*`、`testing.*` 和 `pytest_plugin`。资源随源码目录保存，`local_service` 不依赖 pytest。

## 安装与启动

安装 [Python 3.9.12](https://www.python.org/downloads/release/python-3912/)，确认当前解释器版本，再在项目根目录执行：

~~~powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe src/main.py plc --opcua-port 4840 --fast
~~~

第一条命令应输出 Python 3.9.12。Linux/macOS 对应使用版本为 3.9.12 的解释器和 .venv/bin/python。

默认地址为 opc.tcp://127.0.0.1:4840/simulatorx/。端口 0 自动分配；指定端口被占用时启动失败。Ctrl+C 停止服务并释放自身资源。

`python src/main.py plc|sdk|tcp` 只启动指定服务，各服务参数见对应命令的 `--help`。例如 `python src/main.py tcp --port 0 --control-port 0` 启动 TCP，SDK 的 Unix socket 参数见 [PRD 运行配置](docs/PRD.md#running)。`python src/main.py build-sdk --output artifacts/native` 编译参考 `.so`；此操作需要 Linux／WSL 和 C 编译器。

启动脚本通过自身位置定位源码。切换工作目录后可以使用脚本的绝对路径；命令中显式传入的配置和输出相对路径仍相对于当前工作目录。子进程会得到绝对 `src` 搜索路径并保留已有 `PYTHONPATH`，自定义 TCP 协议模块也可通过 `PYTHONPATH` 提供。

## 真空腔室可视化 Demo

在 Python 3.9.12 环境中安装独立的演示依赖，运行 `demo` 子命令：

~~~powershell
python -m pip install -r requirements-demo.lock
python src/main.py demo
~~~

程序自动启动独享 PLC 仿真并打开本地浏览器，网页端口自动分配。界面提供真空腔室示意图、压力曲线、五个真实 pytest 用例、执行记录以及空闲时的手动控制和故障注入。运行过程中由 pytest 独占控制，停止或结束后恢复环境；预期故障被正确处理时用例通过。

`--port 8080` 指定网页端口，`--no-browser` 只输出地址而不打开浏览器。Linux／WSL 中也可运行，并通过输出的本机地址访问。依赖安装后运行不需要联网。Ctrl+C 退出并回收本轮进程；只关闭浏览器标签不会停止 Python 服务。

完整操作步骤、结果解释、清理与报告位置见[演示说明](docs/DEMO.md)。

## 节点与基础行为

Namespace URI：urn:simulatorx:mvp:vacuum。对象：Objects/SimulatorX/VacuumChamber1。客户端按 URI 查询实际命名空间索引。

| 逻辑名称 | OPC UA 类型 | Reset 初值 | 含义 |
|---|---|---|---|
| vacuum.valve_command | UInt16 | 0 | 0 无命令、1 开阀、2 关阀 |
| vacuum.valve_open | Boolean | false | 破真空阀反馈 |
| vacuum.valve_result | UInt16 | 0 | 0 空闲、1 执行中、2 成功、3 失败 |
| vacuum.command | UInt16 | 0 | 0 无命令、1 抽真空、2 破真空、3 停止并关阀 |
| vacuum.pressure_pa | Double | 101325 | 当前压力，单位 Pa |
| vacuum.state_code | UInt16 | 0 | 0 空闲、1 抽气、2 破真空、3 真空达标、4 故障 |
| vacuum.result_code | UInt16 | 0 | 真空操作结果，编码同 valve_result |
| vacuum.alarm_code | UInt16 | 0 | 0 无报警、1 抽气与开阀冲突、2 非法命令 |

全部节点允许原生读写。服务校验标量类型，允许类型范围内的异常业务值：UInt16 可写 0–65535，Double 可写负值、NaN 和无穷，空值使用 Null Variant。

命令处理后归零，等待归零后可以重复提交。阀门与真空结果独立；阀门立即执行，客户端可能直接看到成功。抽气要求关阀；抽气中开阀使真空操作失败并产生报警 1，阀门自身仍可成功。破真空先退出抽气再开阀。停止命令关阀并将真空状态、结果归零，保留报警。有效抽气、破真空命令或 Reset 清除报警。

行为每 100 ms 更新一次：

~~~text
P_next = P_current × exp(-dt / tau) + P_target × (1 - exp(-dt / tau))
~~~

开阀趋向常压 101325 Pa，关阀抽气趋向 1000 Pa，完成容差 20 Pa；其余状态保持压力。普通抽气/破真空时间常数为 3 s / 1.5 s，--fast 和默认 pytest 服务使用 0.2 s / 0.15 s。

自动行为始终运行，下一轮计算从节点当前压力继续，可能更新测试刚写入的值。持续异常由测试安排重复写入；多次请求按实际时序生效，不承诺跨请求的多节点事务。压力质量非 Good、空值或非有限时跳过压力计算，继续处理命令与 Reset。

## 接入既有 pytest 框架

本仓库的 `pyproject.toml` 已配置 `pythonpath = ["src"]`。接入其他测试项目时，在其 pytest 配置中加入本仓库 `src` 的绝对路径，例如：

~~~toml
[tool.pytest.ini_options]
pythonpath = ["/absolute/path/to/SimulatorX/src"]
~~~

也可以通过 `PYTHONPATH` 提供该路径。随后在测试项目的公共 conftest.py 注册插件：

~~~python
pytest_plugins = ["pytest_plugin"]
~~~

| Fixture | 作用域 | 准备与释放 |
|---|---|---|
| plc_service | session | 默认启动独享 fast 进程，返回 endpoint，整轮结束停止自身进程 |
| plc_client | function | 建立原生 opcua.Client 连接，用例结束断开 |
| plc_nodes | function | Reset，返回逻辑名称到 opcua.Node 的字典，用例结束再次 Reset |
| sdk_service | session | 启动独享 SDK 服务或复用 --sdk-control；返回控制客户端，仅停止自建服务 |
| sdk_axis / sdk_returns | function | 共用一次用例前后 Reset；分别提供轴控制和返回值序列 |
| sdk_library | session | 编译参考 .so，或使用 --sdk-library 指定的库；本身不启动服务 |
| tcp_service | session | 启动独享 TCP 服务或复用 --tcp-control；返回控制客户端，仅停止自建服务 |
| tcp_responses | function | 用例前后 Reset，提供 TCP 应答字段序列 |

加载插件只注册功能，测试或公共准备 fixture 必须通过依赖启用所需 fixture。插件不提供 sut fixture，真实 SUT 的业务调用和生命周期由既有框架负责。

服务 fixture 为会话级，只有资源 fixture（`plc_nodes`、`sdk_axis` / `sdk_returns`、`tcp_responses`）触发逐用例 Reset。Reset 只作用于对应服务。统一插件管理自动生命周期，各 fixture 保持各自接口；外部服务的启动和停止由调用方负责。

SUT 和后台任务 fixture 应依赖实际使用的资源 fixture，并登记停止与等待退出的清理回调，确保停止业务和写入者后再 Reset。SDK/TCP 在 Reset 前保存诊断；清理失败会中止后续用例。三类服务可以按需组合使用。

plc_nodes 是项目 fixture，set_value() 是 python-opcua 原生 Node 方法。场景准备逻辑可以连续改值：

~~~python
from opcua import ua

def test_native_node_writes(plc_nodes):
    pressure = plc_nodes["vacuum.pressure_pa"]
    pressure.set_value(200000.0, ua.VariantType.Double)
    pressure.set_value(-200000.0, ua.VariantType.Double)
    plc_nodes["vacuum.alarm_code"].set_value(7, ua.VariantType.UInt16)
    assert pressure.get_value() == -200000.0
~~~

业务异常用例应在 SUT 到达目标阶段后改值，再通过 SUT 接口验证报警或处理结果。写入成功只表示节点更新完成，不表示 SUT 已识别异常；预期异常被正确处理时，用例可以通过。

复用已有服务时：

~~~powershell
python -m pytest --opcua-endpoint opc.tcp://127.0.0.1:4840/simulatorx/
~~~

外部服务的最终退出由调用方负责。当前插件要求串行执行，并行 CI 作业应分别启动独享实例。

## DataValue 与清理约定

质量码和时间戳通过原生接口写入：

~~~python
from datetime import datetime
from opcua import ua

data = ua.DataValue(ua.Variant(None, ua.VariantType.Null))
data.StatusCode = ua.StatusCode(ua.StatusCodes.BadSensorFailure)
data.SourceTimestamp = datetime(2020, 1, 2, 3, 4, 5)  # UTC
plc_nodes["vacuum.pressure_pa"].set_value(data)
observed = plc_nodes["vacuum.pressure_pa"].get_attributes([ua.AttributeIds.Value])[0]
assert observed.StatusCode.value == ua.StatusCodes.BadSensorFailure
~~~

get_value()/get_data_value() 遇到 Bad 质量会抛异常；检查异常质量时使用 get_attributes([Value]) 或 client.uaclient.read() 取得完整 DataValue。

腔室下提供原生 Reset()，恢复绑定基线、Good、源/服务器时间戳和运行计时，保留连接与订阅：

~~~python
from opcua import ua

ns = plc_client.get_namespace_index("urn:simulatorx:mvp:vacuum")
chamber = plc_client.get_node(ua.NodeId("VacuumChamber1", ns))
chamber.call_method(ua.NodeId("VacuumChamber1.Reset", ns))
~~~

接入方的真实 SUT fixture、后台写入任务 fixture 应依赖 plc_nodes，并登记停止和等待退出的清理回调。顺序为：停止 SUT 和写入任务 → Reset → 断开测试连接 → 整轮结束后停止自建服务。

plc_nodes 在初次 Reset 前注册最终清理，准备失败和断言失败后仍尝试恢复。Reset 或客户端断开失败会产生 pytest 错误并终止后续用例。真实 SUT 停止失败、进程强制结束等情况由接入方处理，确认写入者退出并恢复环境后才可复用。

## XML、绑定与新增节点

[节点 XML](src/local_service/plc/resources/vacuum.xml) 定义地址空间、类型、权限及导入初值；[bindings.json](src/local_service/plc/resources/bindings.json) 定义逻辑名称、Namespace URI、NodeId、类型、权限和 Reset 基线。二者人工配套维护，JSON 不会自动创建节点。启动和 Reset 最终采用绑定的 baseline。

~~~powershell
python src/main.py plc --nodeset dependency.xml --nodeset chamber.xml --bindings bindings.json --profile profile.json
~~~

当前行为要求包含上述八个逻辑节点及 VacuumChamber1 对象。新增业务节点先补充 XML 和绑定，需要自动变化时再适配行为循环。服务的自定义绑定不会自动传入当前 pytest fixture，接入方需同步调整测试侧映射。

profile 可设置 pump_tau、vent_tau、target_pressure、tolerance、tick_interval；--profile 与 --fast 同时出现时使用显式 profile。

## 框架自测与报告

在已激活的 Python 3.9.12 环境中运行：

~~~powershell
python -m pytest -q --junitxml=artifacts/junit.xml
python -m pytest tests/local_service/plc/test_integration.py --setup-show -q
~~~

自测覆盖 PLC 原生节点、SDK 运动与实际 .so 调用、TCP 协议、三类服务独立生命周期和 fixture 失败清理。完整验收在 Linux／WSL 中进行，使用仅安装 requirements.lock 依赖的环境，不安装本框架。JUnit 保存结果、耗时、断言失败与准备/清理错误。流水线应保留 pytest 退出码，并在失败时也归档 JUnit 和控制台日志。

核心代码见 [simulator.py](src/local_service/plc/simulator.py)，插件见 [pytest_plugin.py](src/pytest_plugin.py)，行为边界见 [PRD](docs/PRD.md)。
