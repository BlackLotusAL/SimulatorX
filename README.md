# SimulatorX

按「整机 → 子系统 → 硬件」组织的本地仿真环境，面向串行自动化测试。参考整机包含真空 PLC、旋转轴和示例通信硬件，分别通过 OPC UA、原生 SDK、TCP 暴露接口。各硬件独立维护模型、配置和状态，协议宿主管理服务，整机统一管理生命周期。

可选的 [三协议浏览器演示](docs/DEMO.md) 提供 PLC、SDK、TCP 标签页、真实设备交互和十五个 pytest 场景；安装 `requirements-demo.lock` 后运行 `python src/main.py demo`。静态结构说明见 [离线架构总览](docs/architecture.html)。

## 目录

```text
src/
├── device.json                 # 参考整机配置
├── framework/                  # 配置、编排、进程、消息与服务运行
├── protocols/                  # 可复用协议宿主
│   ├── opcua/                  # host、service、client、contracts、bindings
│   ├── sdk/                    # host、service、client、contracts
│   └── tcp/                    # host、service、client、contracts
├── subsystems/                 # 独立设备实现
│   ├── vacuum/chamber_plc/     # 腔室模型与 resources
│   ├── motion/rotary_axis/     # 轴模型、ABI、适配器、构建与 native
│   └── detector/modbus_tcp/    # 寄存器模型与协议编解码
├── test/                       # framework、protocols、设备和整机回归
├── main.py
└── pytest_plugin.py
examples/demo/                   # 统一三协议演示
├── hub.py                      # 协议装配、延迟初始化及全局互斥
├── web/                        # 网页服务、API 与静态资源
├── common/                     # 公共运行、事件、报告及测试支持
├── plc/                        # PLC 控制、场景及 pytest 插件
├── sdk/                        # SDK 控制、场景及 pytest 插件
├── tcp/                        # TCP 控制、场景及 pytest 插件
└── tests/                      # 公共、网页及协议验收
    ├── common/                 # 跨协议互斥、报告与恢复
    ├── web/                    # 网页入口、测试收集与 managed 退出
    ├── plc/                    # PLC 验收
    ├── sdk/                    # 原生 SDK 验收
    └── tcp/                    # Modbus TCP 验收
examples/sut_integration/        # 独立业务侧接入与验收
docs/                           # 需求契约与测试审查资料
```

`src` 是源码搜索根，例如 `from framework.runtime import DeviceRuntime`。框架与协议宿主不引用具体子系统；硬件通过 `type` 选择宿主，三协议均按子系统 ID 与硬件 ID 定位本包 `model.py:create`。`device.json` 只保留整机装配内容，通用运行能力放 `framework`；设备之间不导入、继承或委托彼此的业务实现。

## 按任务阅读

- **运行项目**：先读下方“安装与运行”，修改 `src/device.json` 或使用 `--select`。
- **开发设备**：进入 `src/subsystems/<子系统>/<硬件>`，按“新手新增独立腔室的步骤”操作。
- **接入 SUT**：阅读 [独立示例](examples/sut_integration/README.md)，由业务系统通过真实协议调用设备。
- **修改协议宿主**：先读 下方“职责与依赖”，再看 `src/protocols/<协议>/host.py` 和 `service.py`。

包结构仅在 `framework`、`protocols`、`subsystems`、`test` 和示例 `reference_sut` 顶层保留 `__init__.py`。框架及示例测试使用 pytest importlib 模式。

## 职责与依赖

| 位置 | 职责 | 修改时关注 |
|---|---|---|
| `src/device.json` | 参考整机选择与硬件运行设置 | 设备 ID、type、port、nodeset |
| `src/framework` | 配置、生命周期、进程管理、消息和服务运行 | 所有协议消费者的回归 |
| `src/protocols/<协议>/host.py` | 预检、装配、连接、服务工厂 | 父子进程使用同一入口 |
| `src/protocols/<协议>/service.py` | 协议服务与控制操作 | 并发、Reset、健康与连接回收 |
| `src/subsystems/<子系统>/<硬件>` | 独立业务模型、协议映射和资源 | 本设备的行为及业务验收 |
| `src/test` | 框架和设备回归 | 按 framework、protocols、设备、integration 分类 |
| `examples/sut_integration` | 外部 SUT 的业务调用与验收 | 与框架测试分别运行 |

框架按 `type` 加载 `protocols.<type>.host`，然后依据设备 ID 加载 `subsystems.<子系统>.<硬件>.model:create`。这是受限的装配路径，不允许清单任意指定工厂。共享框架和协议宿主不直接导入具体设备，设备之间不互相依赖。

消息规则集中在 `framework.messages`。Socket 传输位于 `framework.transport`，父子进程管道位于 `framework.pipe`，服务运行循环位于 `framework.runner`。依赖方向为 transport → runner → pipe → messages，runner 不依赖 transport；transport 同时提供公共通信符号导出。

旋转轴 `abi.py` 定义结果码和函数标识，模型及 ABI 适配器共同引用；`adapter.py` 只负责设备调用编解码。模型的固定 `create()` 工厂仍负责声明装配，不启动资源。

新增协议需扩展内置类型支持及对应宿主，并验证全部生命周期契约。公共基础设施变更需回归所有消费者，设备业务变更需验证对应行为和 SUT 场景。

## 安装与运行

使用 **Python 3.9.12（64 位）**，安装锁定依赖后直接运行源码：

```sh
python -m pip install -r requirements.lock
# 完整参考设备支持 Windows x64 和 Linux／WSL
python src/main.py run --device src/device.json
# 仅需部分硬件时，显式选择子集
python src/main.py run --select vacuum/chamber_plc --select detector/modbus_tcp
# 原生 SDK：Windows 使用 MinGW-w64 GCC，Linux 使用 cc/gcc
python src/main.py build-sdk --hardware motion/rotary_axis --output artifacts/native
```

默认设备配置为 `src/device.json`。`--select` 可重复，省略时运行整机；不会自动跳过平台不支持的硬件。所有选中硬件通过预检后才开始启动，全部就绪后输出包含完整硬件标识及实际地址的 JSON。端口 0 由系统分配。Ctrl+C 停止；`--managed` 从 stdin 收到一行或 EOF 后停止；`--ready-file` 输出就绪 JSON。

任一硬件启动失败会逆序回收已启动资源。运行时健康检查失败会退出并清理自建进程。原生 SDK 支持 Windows DLL（本机 TCP）及 Linux `.so`（Unix socket）；环境变量及 ABI 见 [PRD](docs/PRD.md)。

## 装配配置

`src/device.json` 是唯一的运行配置入口，各硬件设置直接内联，不再创建 hardware.json 或填写 config/definition。`type` 选择协议宿主，子系统 ID 与硬件 ID 必须是合法、非关键字的 Python 包名，并与 `src/subsystems/<子系统>/<硬件>/` 目录一致。

```json
{
  "id": "reference_machine",
  "subsystems": [
    {
      "id": "vacuum",
      "hardware": [
        {
          "id": "chamber_plc",
          "type": "opcua",
          "port": 0,
          "nodeset": "vacuum.xml"
        }
      ]
    },
    {
      "id": "motion",
      "hardware": [
        {
          "id": "rotary_axis",
          "type": "sdk"
        }
      ]
    },
    {
      "id": "detector",
      "hardware": [
        {
          "id": "modbus_tcp",
          "type": "tcp",
          "port": 0
        }
      ]
    }
  ]
}
```

三个宿主统一加载该设备包的 `model.py:create`；整机 ID 仅用于运行标识，不参与定位。整机清单可放在其他目录，设备源码仍固定在 src 下。不存在实例别名、显式入口或包路径覆盖。

`type` 必填，仅接受小写 `opcua`、`sdk`、`tcp`，不支持 `plc` 别名或自定义工厂。旧 `factory` 需改为 `type`，旧 `opcua_port` 需改为 `port`，否则预检报错。父子进程使用相同类型规则，SDK 不需要端口设置。

OPC UA 的命名空间 URI 属于设备协议，由本包 `model.py`、XML 与 `bindings.json` 一致声明，不需要在整机清单重复填写。客户端按 URI 查找实际编号，不写死 `ns=2`；同一设备的隔离副本无需更换 URI。

| 硬件 | 内联运行设置 |
|---|---|
| PLC | nodeset 必填，XML 文件名可变；port 默认 0 |
| SDK | 无需额外运行设置；socket 与诊断文件自动创建并隔离 |
| TCP | port 默认 0，表示仿真业务端口 |

TCP 只开放一个仿真业务端口。框架通过父子进程管道完成 Reset、响应序列、健康检查和诊断，不需要管理端口；旧 `control_port` 字段须删除，预检会明确拒绝。SUT 仍通过实际 `tcp` 地址连接设备，fixture 的使用方式不变。

资源目录固定为设备包的 resources。PLC 的 bindings.json 固定在该目录，nodeset 相对该目录解析，也支持绝对 XML 路径；它不相对整机清单或工作目录。CLI 显式路径仍相对工作目录。

所有服务由框架创建、管理和停止。旧 config/definition/package/mode 字段、已有服务连接配置、PLC bindings 覆盖会在预检拒绝。新增设备直接添加硬件条目；临时运行子集使用 --select，不引入配置文件继承或覆盖机制。隔离测试使用两个运行对象加载同一设备，而不是给一个包配置不同硬件 ID。

业务参数固定在设备代码中：真空腔室的 `PARAMETERS` 保留参考整机原 fast 参数（抽气 0.2 s、放气 0.15 s）；旋转轴同样使用设备内 `PARAMETERS`。自建配置中的 `profile`、`parameters`、`protocol` 已删除，出现即在预检时报迁移错误。XML 和 bindings 必须配套，硬件句柄使用相同绑定暴露逻辑节点。

## Python 与 pytest 接入

将本项目 `src` 加入 `PYTHONPATH` 或 pytest 的 `pythonpath`：

```python
from framework.runtime import DeviceRuntime

with DeviceRuntime.from_config("src/device.json", ["vacuum/chamber_plc"]) as device:
    plc = device.subsystems["vacuum"].hardware["chamber_plc"]
    print(plc.endpoints["opcua"])
    print(plc.nodes["vacuum.pressure_pa"].get_value())
    plc.reset()                    # 只重置本硬件
    device.reset()                 # 重置选中的全部硬件
```

公共生命周期接口为 `start/stop/reset/check_health`，`stop` 可重复调用。已停止或失败的整机对象不能重新启动，需创建新对象。整机 Reset 不保证跨硬件事务；任一 Reset 失败即禁止复用。

在测试项目公共 `conftest.py` 注册：

```python
pytest_plugins = ["pytest_plugin"]
```

| Fixture | 作用域 | 行为 |
|---|---|---|
| `device_service` | session | 按配置启动选中硬件，会话结束清理；自身不 Reset |
| `device` | function | 依赖 device_service，用例前后 Reset；结束时先保存诊断 |

业务流水线采用“用例准备 SimulatorX → 调用 SUT 业务 → SUT 通过设备协议交互并同步状态 → 用例查询 SUT → 断言业务结果”的链路。SimulatorX 的节点、快照和诊断不能替代 SUT 业务断言。

可运行的三协议闭环、Python 参考 SUT、场景 fixture 和完整用例见独立的 [SUT 接入示例项目](examples/sut_integration/README.md)。SUT 是框架的外部使用方，代码归档于 `examples/sut_integration/`，不属于 `src`、框架公共 API 或运行依赖。框架不会导入或启动它。真实服务接入时在测试项目中替换 SUT 适配器。

```sh
python -m pytest src/test --simulatorx-device src/device.json --simulatorx-select vacuum/chamber_plc
```

`--simulatorx-select` 可重复；`--simulatorx-artifacts` 指定诊断目录，默认 `artifacts/simulatorx`。每例诊断按完整硬件标识保存节点/序列、事件与自建服务日志。准备或断言失败仍执行清理；Reset、健康检查或诊断失败会中止后续用例。

SUT 和后台写入 fixture 必须依赖 `device` 并登记退出清理，使顺序为：停止写入者 → 保存诊断 → 健康检查 → Reset。测试控制连接在会话结束时释放。SDK 业务进程应使用轴句柄的 `client.launch_environment`，原生库路径由接入方提供。

## 扩展约定

新增同协议硬件时，在设备包提供设备定义工厂、行为模型和所需协议资源（三协议统一位于 `model.py`），通过 `type` 选择 `opcua`、`sdk` 或 `tcp` 宿主，再加入整机 JSON。模型无需管理子进程、工作线程或业务连接；设备定义工厂不得启动资源。即使行为相同或仅参数不同，独立设备也分别维护业务代码和映射；不通过共享模型配不同参数来实现。

OPC UA 模型通过节点访问接口实现 `step(dt)`；SDK 模型与 ABI 适配器分开；TCP 正常应答模型与编解码器分开。只有生命周期需求无法由现有宿主表达时才考虑实现 `framework.contracts.Hardware`，并承担相应回归责任。完整行为契约见 [PRD](docs/PRD.md)。

协议客户端位于 `protocols.<协议>.client`。所有设备运行设置内联，PLC 必须填写 XML 路径；参考 TCP 使用 Modbus TCP。

真空 PLC 保留聚合腔室模型；不包含跨硬件物理耦合、热插拔、并行 pytest 或同一 SUT 进程访问多个 SDK 实例的路由。

### 新手新增独立腔室的步骤

PLC 开发者主要维护三份文件；嵌套设备目录使用命名空间包，无需新增 `__init__.py`：

| 文件 | 用途 |
|---|---|
| `model.py` | 行为、固定业务参数、设备标识与 `create()` 装配函数 |
| `resources/vacuum.xml` | OPC UA 节点结构 |
| `resources/bindings.json` | 人工维护业务简称、节点映射、类型与 Reset 基线，供 fixture SetUp 定位并写入节点 |

无需另外编写 `definition.py`、`bindings.py` 或服务器包装类。公共映射读取使用 `protocols.opcua.bindings.load_bindings(path)`；原生 Reset 使用 `protocols.opcua.client.reset_nodes(client, definition)`，通常直接调用硬件客户端的 `reset()`。

1. 将 `src/subsystems/vacuum/chamber_plc` 复制为自己的设备包，例如 `src/subsystems/vacuum/load_lock_plc`；这是开发起点，不是运行时依赖。各设备保持独立的模型、节点资源和测试。
2. 在新包的 `model.py` 修改业务规则及 `PARAMETERS`，独立维护压力、阀门和联锁逻辑；不导入旧包模型，不恢复参数文件切换。
3. 修改新包的 NodeSet、bindings、对象与 Reset 标识，确保它们相互匹配；`model.py` 中的 `create()` 声明行为工厂与协议标识；宿主从 ID 对应设备包的 resources 目录加载资源。
4. 在现有整机清单的 vacuum 子系统添加 `{"id": "load_lock_plc", "type": "opcua", "port": 0, "nodeset": "vacuum.xml"}`。ID 必须匹配新包目录，框架固定加载 subsystems.vacuum.load_lock_plc.model:create；该包只是说明示例，仓库未提供此示例设备。
5. 增加本设备行为、节点接口、Reset 和 SUT 用例。公共基础设施修复回归全部消费者；相似业务缺陷需要对各设备分别评估和修复。

PLC 模型工厂只接收节点访问对象；SDK/TCP 模型工厂无参数。TCP `respond(request)` 必须由设备显式实现。业务请求中的运动速度与目标、测试控制通道中的故障与序列仍有效，它们不是模型装配参数。SDK 诊断中的 `profile` 仅描述固定参数，不是配置入口。

## 验证

下面验证框架本身，允许直接断言仿真状态；[示例业务验收](examples/sut_integration/README.md#运行与验收) 使用独立命令和报告，不在根目录默认测试集中。

```sh
python -m pytest -q --junitxml=artifacts/junit.xml
```

完整验收支持 Windows x64 + MinGW-w64 GCC 及 Linux／WSL + C 编译器。仅验证 PLC/TCP 时可显式指定子集：

```sh
python -m pytest src/test --simulatorx-select vacuum/chamber_plc --simulatorx-select detector/modbus_tcp -q
```

默认仍执行完整主套件。仅修改模型或配置时，可选用快速筛选：

```sh
python -m pytest -q -m "not integration"
```

`integration` 标记真实服务、网络连接、子进程及原生构建测试；它不按文件夹自动推断。
快速层不代替提交前的完整验收。设备测试按 `src/test/subsystems/<子系统>/<硬件>/`
组织，共享协议测试位于 `src/test/protocols/`，静态依赖检查位于 `test_architecture.py`。


## 参考 Modbus TCP 探测器

`detector/modbus_tcp` 使用共享 TCP 宿主，支持 03/04/06/16，端口仍由 `port` 指定。设备包的 `model.py` 独立维护寄存器行为，`protocol.py` 维护 MBAP/PDU 编解码；无 CRC，无额外监听端口。

保持寄存器地址 0 为使能（默认 1，允许 0/1），地址 1 为参考值（默认 100，uint16）；输入寄存器地址 0/1/2 分别为状态、就绪和测量结果。所有地址从 0 开始。使能时测量值等于参考值，禁用时为 0；这只是联调参考模型，不代表厂商协议或物理检测过程。

```python
client = device.subsystems["detector"].hardware["modbus_tcp"].client
client.responses["04:0:2"].set_sequence([
    {"registers": [0, 0]},  # 未就绪
    {"registers": [0, 1]},  # 就绪
])
client.responses["06:1:1"].set_sequence([{"exception": 4}])
```

序列按功能码、地址、数量匹配。自然非法请求返回 Modbus 异常且不消耗序列；合法写入先执行，即使覆盖为异常响应也不会撤销写入。Reset 恢复默认寄存器并关闭旧连接。详细报文与异常规则见 [PRD 附录 B](docs/PRD.md#protocol)。SUT 示例 `DetectorSUT.start_check()` 独立编码并校验 Modbus 报文。

迁移时将旧子系统／硬件 ID、选择路径与包导入改为 `detector/modbus_tcp`，将旧 READ_STATUS／READ_ANGLE 序列改为寄存器目标；不保留旧包或 ID 别名。
