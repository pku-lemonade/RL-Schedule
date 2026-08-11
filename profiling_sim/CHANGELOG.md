# profiling_sim — 完整变更日志（CHANGELOG）

本文档记录 `profiling_sim/` 从仓库根目录原始 `simulator/` 包复制开始，到全部 6 个高难特性（H1–H6）完成的每一步修改。最终状态：**18 个 Python 源文件 + 1 份 JSON 配置 + 15 个测试套件，672 项检查全部通过**。

---

## 目录

- [阶段 0：从 simulator/ 复制并自包含化](#阶段-0从-simulator-复制并自包含化)
- [阶段 1：Easy 基础特性与冒烟测试](#阶段-1easy-基础特性与冒烟测试)
- [阶段 2：Medium 特性 F6–F11](#阶段-2medium-特性-f6f11)
- [阶段 3：H1 片上归约（Route Reduce）](#阶段-3h1-片上归约route-reduce)
- [阶段 4：H2 QoS / 优先级 / 突发 / FIFO 流控](#阶段-4h2-qos--优先级--突发--fifo-流控)
- [阶段 5：H3 十维高级数据布局](#阶段-5h3-十维高级数据布局)
- [阶段 6：H4 周期精确影子流水](#阶段-6h4-周期精确影子流水)
- [阶段 7：H5 多芯片 AdaLink 路由](#阶段-7h5-多芯片-adalink-路由)
- [阶段 8：H6 Pipeline 自动同步框架](#阶段-8h6-pipeline-自动同步框架)
- [文件清单](#文件清单)
- [测试清单](#测试清单)

---

## 阶段 0：从 simulator/ 复制并自包含化

### 0.1 起点：原始 `simulator/` 包

原始包位于仓库根目录，共 7 个文件：

| 文件 | 职责 |
|------|------|
| `__init__.py` | 导出 Core, Link, Router, NoC, Arch |
| `noc.py` | Link / Router / NoC，含 4 种拓扑（Mesh/Torus/RingRoad/Dragonfly）、NoCDist 随机带宽方差、fail-slow delay_factor、单 'core' 端口 |
| `architecture.py` | Arch 类，自建 simpy.Environment，注入 fail-slow（link/router/lsu/tpu），width=128 c2r 链路，4 种拓扑分派 |
| `core.py` | ScratchpadMemory / LSU / TPU / Scheduler / Core；Scheduler 单 comp/comm/io slot（各 capacity=1） |
| `distribution.py` | CoreDist（正态 fail-slow）+ NoCDist（Gamma 分布），含 numpy 依赖 |
| `run.py` | CLI 入口，argparse 加载 arch/failure/mapping，调用 predictor.detect 做 fail-slow 检测，log_timing 埋点 |
| `tracing.py` | 区间合并 + 时间片利用率统计，仅 cores/links 两类 |

原始包的外部依赖：
- `utils.definitions`（Slice, Direction, Message, ceil, Event, Trace, TimeSlice, TraceItem, OperatorType, comp/comm/io_operator）
- `utils.mapper`（NetworkMapper, parse_mapping）
- `utils.dfg`（DFGNode）
- `utils.task`（Task）
- `configs.schemas.arch_config`（Pydantic 配置）
- `configs.schemas.failure_configs`（FailSlow 及各 Fail 类型）
- `predictor.predict.detect`（fail-slow 检测器）
- `embedding.hw_encoder`（HardwareEmbedding）
- `utils.timing_logger`（log_timing）
- `numpy`

### 0.2 复制为 `profiling_sim/` 并做自包含化重构

将 `simulator/` 整体复制为 `profiling_sim/`，随后进行以下结构性改造：

**1. 消除所有外部包依赖，使其成为独立可运行的包**

- 将 `utils.definitions` 中的全部数据模型（Direction, DimSlice, Slice, Message, Event, Trace, TimeSlice, TraceItem, OperatorType, comp/comm/io_operator, ceil）内联到新文件 `definitions.py`。
- 将 `utils.dfg.DFGNode` 内联到新文件 `dfg.py`。
- 将 `utils.task.Task` 内联到新文件 `task.py`（task_priority 字典也随之迁入）。
- 将 `configs.schemas.arch_config` 的全部 Pydantic 配置模型内联到新文件 `config.py`，并使用 Pydantic v2 语法（`@field_validator`, `@model_validator(mode='after')`）。
- 删除对 `configs.schemas.failure_configs`、`predictor.predict.detect`、`embedding.hw_encoder`、`utils.timing_logger`、`numpy` 的全部依赖。

**2. 移除 fail-slow 机制（profiling 场景不需要故障注入）**

- `noc.py` Link：删除 `delay_factor` 字段、`change_delay()` / `recover_delay()` 方法；NoCDist 增加 `deterministic` 参数，deterministic 模式下返回 `shape/rate` 固定值而非随机采样。
- `architecture.py` Arch：删除 `failures` 参数、`preprocess_fail()`、`link_fail()`、`router_fail()`、`lsu_fail()`、`tpu_fail()`、`run_fail_slow()` 全部方法；`execute()` 不再调用 `run_fail_slow()`。
- `core.py` Core：删除 `tpu_fail()`/`tpu_recover()`/`lsu_fail()`/`lsu_recover()` 方法。
- `distribution.py`：删除 CoreDist 类（正态 fail-slow 分布）和 numpy 导入，仅保留 NoCDist 并增加 deterministic 模式。

**3. 拓扑裁剪为 8×4 Mesh XY only**

- `noc.py` NoC：删除 `build_connection_torus()`、`build_connection_ring_road()`、`build_connection_dragonfly()` 三个方法及其辅助函数（`get_layer`、`get_ring_nodes_ordered`、`get_dragonfly_info`、`outer`、`inner`、`ring_next`、`is_corner`）。
- 新增统一的 `build()` 方法（8×4 Mesh，XY 维序路由，row axis first via EAST/WEST then column via NORTH/SOUTH）和 `_connect()` 辅助方法。
- `architecture.py`：删除 `build_noc()` 拓扑分派方法，直接调用 `NoC(env, config, deterministic=deterministic).build()`。
- `Router.calculate_next_router()`：删除 Torus_XY / RingRoad / Dragonfly 分支，仅保留 XY 路由。
- 坐标映射固定为 `to_xy(id) = (id // y, id % y)`，与架构文档一致（y=4 列）。

**4. 本地端口（local port）泛化**

原始 Router 只有一个硬编码的 `'core'` 方向端口。改为支持最多 23 个本地端口（0–22）：

- `Router.links` 字典移除 `'core'` 键，仅保留 NORTH/SOUTH/EAST/WEST 四个 r2r 方向。
- 新增 `Router.local_ports: Dict[int, Dict[str, Link]]` 字典和 `bind_local_port(port, link_in, link_out)` 方法。
- 新增 `route_local(msg, port)` 方法将消息投递到指定本地端口。
- `Router.run()` 改为扫描所有 `local_ports`（按 port 号排序）而非单一 core 端口。
- 新增 `NoC.attach_local(router_id, port, node_id)` 方法：创建一对双向 Link、绑定到路由器的指定本地端口、维护 `node_router` 映射（全局 node_id → router_id）、记录到 `local_links` 列表。返回 `(router_to_node, node_to_router)`。
- 新增常量 `MAX_LOCAL_PORT = 22`（definitions.py）。

**5. 核心到路由器连接改为本地端口 0**

- `architecture.py._build_cores()`：不再手工创建 width=128 的 Link 并 bind 到 'core' 方向，而是调用 `self.noc.attach_local(cid, 0, cid)` 使用统一的本地端口机制，Link 宽度由 NoCConfig.link.width（默认 16）决定。

**6. 确定性模式（deterministic）贯穿全链路**

- `Arch.__init__` 新增 `deterministic: bool = False` 参数，透传到 `NoC` 和后续所有节点构建。
- `NoC.__init__` 接收 deterministic，在 `_connect()` 和 `attach_local()` 创建 Link 时传入。
- `Link.__init__` 接收 deterministic 并传给 NoCDist；deterministic 模式下 `var_bandwidth` 返回固定值 `shape/rate = bandwidth * 0.5 / 0.5 = bandwidth`，即无方差。
- `run.py` 的 `simulate()` 默认 `deterministic=True`。

**7. run.py 精简**

- 删除 argparse CLI、`fail_analyzer()`、fail-slow detect 调用、log_timing 埋点、simulate_old()。
- 精简为 `simulate(arch_path, mapper, slice_num=11, deterministic=True, verbose=False)` 函数：加载 JSON 配置 → 构建 Arch → execute → 收集 core/link/dma/mem 事件 → 计算 maxtime → process_events → 返回 `(maxtime, traces, arch)`。
- 新增 `setup_logging(level)` 和 `save_trace(traces, path)` 辅助函数。
- 配置加载改为 `config.load_arch(path)`（使用 `ArchConfig.model_validate(json.load(f))`）。

**8. tracing.py 扩展**

- `TimeSlice` 新增 `dma_links: List[TraceItem]` 和 `mem_bw: List[TraceItem]` 两个字段。
- `process_events()` 新增 `dma_events=None` 和 `mem_events=None` 参数，在每个时间片中统计 DMA 链路和内存带宽利用率。
- TraceItem 的 `slow` 字段保留默认 0.0（不再有 fail-slow 概率填充）。

**9. Event 模型扩展**

definitions.py 的 `Event` 模型在原始基础上新增字段：
- `src_id`, `dst_id`, `src_port`, `dst_port`（替代原有的 core-only pe_id，用于非 PE 节点）
- `flops`（计算任务的浮点运算量）
- `data_size`, `is_control`, `is_sync`, `reduce_count`, `priority`, `burst_index`, `burst_count`

**10. distribution.py 精简**

删除 CoreDist 和 numpy 依赖，仅保留 NoCDist，新增 `deterministic` 参数。deterministic=True 时 `generate()` 返回 `self.shape / self.rate`（固定均值），否则使用 `scipy.stats.gamma` 采样。

### 0.3 阶段 0 产出的文件

| 文件 | 状态 |
|------|------|
| `__init__.py` | 修改：导出 Core, NMC, Link, Router, NoC, Arch, DMAEngine |
| `definitions.py` | 新建：从 utils.definitions 内联并扩展 |
| `dfg.py` | 新建：从 utils.dfg 内联 DFGNode/DFG |
| `task.py` | 新建：从 utils.task 内联 Task + task_priority |
| `config.py` | 新建：从 configs.schemas 内联全部 Pydantic 配置 |
| `noc.py` | 大幅修改：去 fail-slow、去 3 种拓扑、本地端口泛化、deterministic |
| `architecture.py` | 大幅修改：去 fail-slow、去拓扑分派、attach_local、deterministic |
| `core.py` | 修改：去 fail-slow、NMC 多通道、Scheduler 多 slot |
| `distribution.py` | 精简：去 CoreDist/numpy、加 deterministic |
| `run.py` | 精简：去 CLI/detect/timing、加 dma/mem 事件收集 |
| `tracing.py` | 修改：加 dma_links/mem_bw |
| `configs/mesh_8x4.json` | 新建：8×4 Mesh 架构配置 |

---

## 阶段 1：Easy 基础特性与冒烟测试

### 1.1 NMC 多通道（NoC-Memory Controller）

原始 `core.py` 中 Scheduler 的 comm_slots 硬编码为 1，SEND/RECV 无法并发。改为：

- 新增 `NMCConfig`（config.py）：`channels: int = 2`, `start_up_time: int = 1`。
- 新增 `NMC` 类（core.py）：使用 `simpy.Store(capacity=channels)` 管理通道，提供 `acquire()` / `release(channel_id)` 方法。
- `Core.__init__` 创建 `self.nmc = NMC(env, config.nmc)`。
- `Task.execute()` 的 SEND/RECV 分支改为 `ch = yield core.nmc.acquire()` ... `finally: yield core.nmc.release(ch)`，使多通道上传/下载可并发。
- SEND 中新增 `yield env.timeout(core.nmc.start_up_time)` 模拟启动延迟。

### 1.2 Scheduler 多 comp/comm slot

- `Scheduler.__init__` 新增 `comm_slots=1` 和 `comp_slots=1` 参数。
- `schedule()` 方法从原始的"每个类别弹一个"改为"按 slot 数量弹"：comp 任务最多弹 `comp_slots` 个，comm 任务最多弹 `comm_slots` 个，io 仍弹一个。
- `Core` 将 `config.nmc.channels` 作为 comm_slots 传入；shadow 使能时 comp_slots = shadow occupancy。

### 1.3 任务优先级排序

- `task.py` 新增 `task_priority` 字典：STORE=0 > SEND=1 > CONV/POOL/FC=2 > LOAD_FEAT/LOAD_WGT/RECV=3。
- `Task.__lt__` 按 (priority, index) 排序，使 heapq 弹出高优先级任务。
- `Core.execute()` 改为将所有 pending 任务（comp_tasks + comm_tasks + io_task）统一放入 `pending` 列表并为每个创建进程，而非原始的三个独立 if 分支。

### 1.4 element_bytes 透传

- DFGNode 新增 `element_bytes: int = 1` 字段。
- Task 从 node 读取 `element_bytes`。
- Task.execute() SEND 分支构造 Message 时传入 `element_bytes=self.element_bytes`。
- CoreConfig 新增 `element_bytes: int = 1`，Core 保存 `self.element_bytes`。
- DFG.add_node() 接收并传递 element_bytes。

### 1.5 冒烟测试

新建 `tests/test_smoke.py`（77 项检查），覆盖：
- Arch 构建（8×4 = 32 cores, 104 r2r links, 32 local port pairs）
- 拓扑连通性（边界路由器方向链接正确，无越界）
- 基本消息传递（PE→PE 单播、XY 路由跳数、W=16 时 ceil 计算）
- deterministic 模式可复现性（两次运行 maxtime 一致）
- Scheduler 优先级（STORE 先于 SEND 先于 COMP）
- NMC 双通道并发
- SPM allocate/release
- Event 记录字段完整性
- trace 结构（time_slices 数量、cores/links/dma_links/mem_bw 列表长度）

### 1.6 基础特性测试

新建 `tests/test_features.py`（33 项检查），覆盖：
- DimSlice/Slice.size() 计算
- Message.byte_size() 与 element_bytes
- Direction 枚举值
- NoCDist deterministic 固定返回
- Pydantic 配置校验
- Link bind 属性
- Router to_xy/to_x 坐标映射
- attach_local node_router 映射
- Core 初始化与事件记录

---

## 阶段 2：Medium 特性 F6–F11

### 2.1 F6：多本地端口（Multi-local-port）

**背景**：除 PE 使用 port 0 外，GM/DDR WDMA 需要双通端口、AdaLink 每节点一个独立端口、ALL_ADALINK 广播端口等。

**实现**：
- `DataNocLocalId` IntEnum（nodes.py）定义全部 23 个端口号（0–22）：PE=0, FABRIC_BRIDGE=1, DNOC2AXI=2, DDR_WDMA_CH0/1=3/4, DDR_RDMA/WDMA_LOCAL_SRAM=5/6, DDR_RDMA=7, DDR_WDMA_SINGLE=8, GM_WDMA_CH0/1=10/11, GM_RDMA/WDMA_LOCAL_SRAM=12/13, GM_RDMA=14, GM_WDMA_SINGLE=15, ADALINK_0–4=16–20, ALL_ADALINK=21, MMU=22。
- `Router.bind_local_port()` 校验 port 范围 [0,22] 和重复绑定。
- `NoC.attach_local()` 已在阶段 0 实现泛化端口支持。

**测试**：`tests/test_f6_ports.py`（19 项检查）—— 端口绑定/重复绑定异常/多节点同路由器/端口 0–22 全覆盖/node_router 映射。

### 2.2 F7：非 PE 节点挂载（Non-PE node attachment）

**背景**：66 节点架构中除 32 个 PE 外，还有 GM_RDMA/WDMA、DDR_RDMA/WDMA、AdaLink 共 34 个非 PE 节点。

**实现**：
- 新建 `nodes.py`，定义：
  - `NodeType` IntEnum：PE=0, GM_RDMA=1, GM_WDMA=2, DDR_RDMA=3, DDR_WDMA=4, ADALINK=5。
  - `NODE_RANGES` 字典：PE(0,32), GM_RDMA(32,4), GM_WDMA(36,4), DDR_RDMA(40,4), DDR_WDMA(44,4), ADALINK(48,18)。
  - 路由器挂载位置常量：`GM_RDMA_ROUTERS=[28,29,30,31]`, `GM_WDMA_ROUTERS=[28,29,30,31]`, `DDR_RDMA_ROUTERS=[0,28,3,31]`, `DDR_WDMA_ROUTERS=[0,28,3,31]`。
  - `ADALINK_ATTACH`：路由器 28/29 各 5 个端口(16–20)，路由器 30/31 各 4 个端口(16–19)，共 18 个 AdaLink 节点。
  - 辅助函数：`node_type()`, `type_local_id()`, `global_node_id()`, `is_pe/gm_rdma/gm_wdma/ddr_rdma/ddr_wdma/adalink()`, `route_pos()`, `get_route_id()`, `get_data_noc_local_id()`, `build_attachment()`。
  - `NoCNode` 基类：封装 env/node_id/node_type/router_id/ports/noc，构造时自动为每个 port 调用 `noc.attach_local()` 并启动 `_listen(port)` 进程；`handle()` 默认 `yield timeout(0)`；`send(port, msg)` put 到 data_out。
  - `attach_nodes()` 工厂函数：按 `build_attachment()` 表构建全部非 PE 节点，处理 ALL_ADALINK 扇出（fanout）——port 21 收到的消息广播到同路由器上所有 AdaLink 节点。

**测试**：`tests/test_f7_nodes.py`（74 项检查）—— 节点 ID 范围/类型判定/路由位置/挂载表完整性/NoCNode 收发/ALL_ADALINK 扇出/attach_nodes 构建。

### 2.3 F8：GM/DDR 内存与 DMA 引擎

**背景**：GM（32 MiB）和 DDR（128 GiB）各自配有 RDMA（读）和 WDMA（写）引擎，需要建模通道数、引擎宽度、聚合带宽、AIU SRAM。

**实现**：
- 新建 `dma.py`：`DMAEngine` 通用多通道 DMA 引擎，`transfer(data_bytes)` 获取通道 → timeout(ceil(bytes, width*clock_scale)) → 归还通道。
- 新建 `memory.py`：
  - `Memory` 类：capacity/used/aggregate_bw，`lane_resource(engine_width)` 按宽度返回 `simpy.Resource`（容量 = aggregate_bw / engine_width），建模聚合带宽并发上限；`allocate(n_bytes)` 容量检查；`write(addr, value, write_sum)` 支持 write_sum=0（覆盖）/1（累加）/2（取最大）；`read(addr)`。
  - `DMANode(NoCNode)`：RDMA/WDMA 节点，持有 memory 引用、engine_width、channels、is_read 标志、AIU SRAM 端口；`transfer(n_bytes, addr, value, write_sum)` 获取通道 → memory.allocate/write → lane resource 请求 → timeout(engine_time) → 记录 mem.events；`handle(port, msg)` 非控制消息触发 transfer。
  - AIU 下载：DMANode 在 aiu_port 上监听，收到消息后校验 16B 对齐和 SRAM 容量，记录 aiu_events，回复 scalar sync 控制消息。
  - `build_memory_system()` 工厂：创建 GM/DDR Memory，按 ROUTERS 表创建 4 组 DMA 节点（GM_RDMA 1ch 读、GM_WDMA 2ch 写、DDR_RDMA 1ch 读、DDR_WDMA 2ch 写），DDR 引擎宽度乘以 clock.ddr_scale（1150/1125），可选创建 AdaLink 节点。
- config.py 新增：`ClockConfig`（aci_mhz=1125, ddr_mhz=1150, ddr_scale 属性）、`DMAEngineConfig`（channels=2, width=16）、`MemoryConfig`（gm_capacity=32MiB, ddr_capacity=128GiB, aggregate_bw, engine_width, aiu_sram_size=256KiB）、`NodeConfig`（enable_dma/enable_adalink/adalink_latency/include_all_adalink_port）。
- architecture.py：当 `ncfg.enable_dma` 时调用 `build_memory_system()`，挂到 `self.gm`/`self.ddr`/`self.nodes`；否则仅 attach_nodes。
- run.py：从 `arch.gm.events`/`arch.ddr.events` 收集内存带宽事件。

**测试**：`tests/test_f8_memory.py`（30 项检查）—— Memory 容量/聚合带宽 lane/DMANode 通道/AIU 16B 对齐/SRAM 容量/write_sum 原子加/DDR clock_scale/build_memory_system 节点计数。

### 2.4 F9：单边/双边传输（Single-side / Dual-side）

**背景**：双边传输（DUAL_SIDE）是标准 req+data 握手；单边传输（SINGLE_SIDE）仅 data 通道传输，带宽利用率 90%，但需要反向控制包通知目的端，且 PE-to-PE 单边不支持。

**实现**：
- definitions.py 新增 `TransferMode` IntEnum：DUAL_SIDE=0, SINGLE_SIDE=1。
- Message 新增 `transfer_mode` 字段（默认 DUAL_SIDE）。
- noc.py Link.calc_latency()：当 `transfer_mode == SINGLE_SIDE and not is_control` 时，`var_bw *= 0.9`（90% 带宽利用率）。
- noc.py Router.routing()：
  - `single = transfer_mode == SINGLE_SIDE and not is_control and not cross`。
  - 单边 + from_local + 两 PE 间 → 抛 `UnsupportedTransferMode`。
  - 单边 from_local 且 target != self：先发反向控制包（`_control_msg(req=True)`）通知目的端预留。
  - 消息到达目的端后（target == self and single），从目的端发回反向控制包（`_control_msg(req=False)`）给源端。
  - 新增 `_control_msg()` 静态方法：构造 16B 控制消息（is_control=True, DUAL_SIDE, SINGLECAST, burst_len_mode=-1, FIFO 字段=-1, layout=None）。
- 新增异常类 `UnsupportedTransferMode(ProfilingSimError)`。

**测试**：`tests/test_f9_transfer.py`（16 项检查）—— 双边时序/单边 90% 带宽/单边 PE-to-PE 异常/反向控制包/控制消息不受 90% 影响。

### 2.5 F10：多播/广播（Multicast/Broadcast）

**背景**：MULTICAST 按 dst_mask 位图复制转发；BROADCAST 是 mask=0 时的全节点广播。

**实现**：
- definitions.py 新增 `TransType` IntEnum：SINGLECAST=0, FIXPATH=1, MULTICAST=2, BROADCAST=3, REDUCE=4。
- Message 新增 `trans_type`（默认 SINGLECAST）和 `dst_mask`（默认 0）。
- noc.py Router.routing()：trans_type 为 MULTICAST/BROADCAST 时调用 `route_multicast()`。
- `route_multicast(msg, mask)`：
  - BROADCAST 且 mask=0 时 mask 设为 `(1 << N) - 1`（全节点）。
  - 当前路由器在 mask 中：投递到本地 dst_local_port。
  - 按 XY 方向计算 EAST/WEST/NORTH/SOUTH 四个子 mask（`_multicast_branches()`），每个非空方向发一个 replica（model_copy 更新 dst_mask 和 dst=邻居路由器 ID）。
  - sync 消息额外回发 sync 控制包。
  - 无投递方向时抛 `UnboundLocalPortError`。
- `_multicast_branches(mask)`：遍历所有目标路由器，按坐标相对位置分到四个方向的子 mask。
- `_neighbor(direction)`：返回指定方向的邻居路由器 ID。

**测试**：`tests/test_f10_multicast.py`（27 项检查）—— 单播不受影响/双分支架构/四分支架构/广播全节点/mask 裁剪/sync 回包/空 mask 警告。

### 2.6 F11：同步框架（Sync framework）

**背景**：消息级同步（outer sync）：发送方在数据发出后需要等待目的端的 sync 确认；目的端收到数据后回发 sync 控制包。

**实现**：
- Message 新增 `sync: bool = False` 字段。
- noc.py Router.routing()：
  - `do_sync = bool(msg.sync) and not msg.is_control`。
  - from_local 且 target != self：在发送数据前先发一个 sync 控制包到目的端（`_sync_msg(to_dst=True)`）。
  - 消息到达目的端后（target == self）：从目的端回发 sync 控制包给源端（`_sync_msg(to_dst=False)`）。
  - 跨芯片 outer sync 抛 `ProfilingSimError`。
- 新增 `_sync_msg()` 静态方法：构造 16B sync 控制消息（is_control=True, sync=True, DUAL_SIDE）。
- Event 模型新增 `is_sync` 字段。

**测试**：`tests/test_f11_sync.py`（29 项检查）—— 基本 sync 时序/非 sync 无额外包/multicast sync/控制消息不重复 sync/跨芯片 sync 异常。

### 2.7 集成测试

新建 `tests/test_integration.py`（33 项检查），覆盖：
- X1：完整 66 节点架构构建（32 PE + 4 GM_RDMA + 4 GM_WDMA + 4 DDR_RDMA + 4 DDR_WDMA + 18 AdaLink = 66）。
- X2：端到端 RDMA→PE→WDMA 流水线。
- X3：多播写回。
- X4：deterministic 确定性（两次运行结果完全一致）。
- X5：trace schema 验证（cores/links/dma_links/mem_bw 各时间片数量）。
- X6：默认配置加载（mesh_8x4.json）。

---

## 阶段 3：H1 片上归约（Route Reduce）

**架构规格**：§7.1，路由器内置归约引擎，多个输入沿 XY 路径汇聚到 root，binary tree reduction，延迟 = `R * ceil(log2(k))`。

### 3.1 definitions.py 变更

- `TransType.REDUCE = 4`。
- Message 新增字段：`reduce_op: int = 0`（0=pass-through, 1=sum, 2=max）、`task_id: int = 0`、`reduce_is_last: bool = False`、`reduce_count: int = 1`、`value: int = 0`、`addr: int = 0`、`write_sum: int = 0`。

### 3.2 config.py 变更

- `RouterConfig` 新增 `reduce_latency: int = 2`（每级 binary reduction 的周期数 R）。

### 3.3 noc.py 变更

**Router 类**：
- 新增 `reduce_latency`、`reduce_stores: Dict[task_id, Store]`、`reduce_active: Set[task_id]`、`reduce_trees`（引用 NoC 的共享字典）。
- `routing()` 首行：如果 `msg.trans_type == REDUCE`，调用 `_handle_reduce(msg)` 并 return（不按常规路由转发）。
- `_handle_reduce(msg)`：校验 task_id 已注册且本路由器在树上；将消息 put 到 reduce_stores[task_id]；如果该 task_id 尚未启动归约进程，启动 `_reduce_run(task_id)`。
- `_reduce_run(task_id)`：
  - 从树节点定义获取 children 集合和 k 值。
  - 从 store 获取 k 个输入操作数。
  - 如果 k >= 2，计算 binary tree 级数 `stages = ceil(log2(k))`，`yield timeout(reduce_latency * stages)`。
  - 按 reduce_op 合并 value（sum=1 求和, max=2 取最大, 其他=取第一个）。
  - merged 消息更新 reduce_count（累加）、reduce_is_last（是否 root）、value。
  - root 节点：`route_local(merged, dst_local_port)` 投递到本地。
  - 非 root 节点：`yield timeout(per_hop_time)` 后沿 parent 方向转发。

**NoC 类**：
- 新增 `reduce_trees: Dict` 共享字典（NoC 持有引用，Router 共享访问）。
- 新增 `register_reduce(task_id, source_routers, root_router, op=1)` 方法：
  - 校验 task_id 未重复、source 非空且无重复、所有 router ID 在范围 [0, N)。
  - 对每个 source 计算到 root 的 XY 路径（`_xy_path()`）。
  - 沿路径构建树：每个节点记录 children 集合（首节点加 "local"，后续节点加入站方向）、parent Direction、is_root 标志。
  - children 使用 Direction 枚举标识（来自哪个方向的输入）。
- 新增 `_xy_path(src, dst)` 辅助方法：按 XY 路由逐跳生成路径列表。

### 3.4 测试

新建 `tests/test_h1_reduce.py`（47 项检查），覆盖：
- T-H1.1–3：register_reduce 树结构构建（单 source、多 source、路径合并）。
- T-H1.4–8：k=2/3/4/8 binary reduction 级数与时延（R*ceil(log2(k))）。
- T-H1.9–12：sum/max/pass-through 操作数。
- T-H1.13–15：reduce_count 累加。
- T-H1.16–18：root 投递到 dst_local_port。
- T-H1.19–22：非 root 沿 parent 转发。
- T-H1.23–25：未注册 task_id 异常。
- T-H1.26–30：多 task_id 并发独立。
- T-H1.31–35：source 重复/为空/越界校验。
- T-H1.36–40：沿 XY 路径的跳数延迟。
- T-H1.41–47：reduce_is_last 标记和端到端投递。

---

## 阶段 4：H2 QoS / 优先级 / 突发 / FIFO 流控

**架构规格**：§7.2，4 级优先级（0 最高）、突发传输模式（1/2/4/8 beats）、burst bubble、FIFO credit 流控（WRITE_FULL 反压）。

### 4.1 definitions.py 变更

- Message 新增字段：`priority: int = 0`（0–3）、`burst_len_mode: int = -1`（-1=无限/整包, 0=1beat, 1=2beats, 3=4beats, 7=8beats）、`fifo_hw_id: int = -1`、`fifo_logic_id: int = -1`、`fifo_check_type: int = -1`。
- `_check_priority` 校验器：priority 必须在 [0,3]。
- `_check_burst` 校验器：burst_len_mode 必须在 (-1, 0, 1, 3, 7)。
- `is_aiu: bool = False` 字段（AIU 下载标记，后续 H5 使用）。
- Event 模型新增 `priority`、`burst_index`、`burst_count` 字段。

### 4.2 config.py 变更

- `RouterConfig` 新增 `burst_bubble: int = 1`（突发包间气泡周期数）。

### 4.3 noc.py 变更

**Link 类**：
- 新增 `busy = simpy.PriorityResource(env, capacity=1)`：链路仲裁使用优先级资源，`-msg.priority` 越小（优先级越高）越先获得。
- 新增 `burst_bubble` 参数。
- 新增 `_payload_chunks(msg)` 方法：
  - burst_len_mode=-1：返回 `[payload]`（整包一次传输）。
  - 否则：beat_bytes = `BURST_BEATS[mode] * bandwidth`，将 payload 切分为多个 chunk（最后一个可能不足）。
  - `BURST_BEATS = {0:1, 1:2, 3:4, 7:8}`。
- `calc_latency(msg)` 重写为突发分段传输：
  - 对每个 chunk i：tx_bytes = chunk + (i==0 ? header_bytes : 0) + (i==0 ? imm_bytes : 0)。
  - `with busy.request(priority=-msg.priority)` 获取链路仲裁。
  - 记录每个 burst 的 Event（burst_index, burst_count）。
  - `yield timeout(tx_time + (i==0 ? delay : 0))`。
  - burst 间 `yield timeout(burst_bubble)`。
  - 单边传输 var_bw *= 0.9（同 F9）。

**Router 类**：
- 新增 `fifos: Dict` 引用（NoC 共享）。
- 新增 `register_fifo(hw_id, logic_id, depth)`（NoC 方法）：
  - 校验 hw_id [0,63]、logic_id [0,31]、depth >= 1、key 未重复。
  - 创建 `{'depth': depth, 'credits': simpy.Container(init=depth, capacity=depth)}`。
- `_fifo_acquire(msg)`：非控制 SINGLECAST 且 fifo_check_type==0（WRITE_FULL）时，`yield fifo.credits.get(1)`（credit 不足则阻塞，实现反压）。
- `_fifo_return(msg)`：fifo_check_type in (0,2) 时，如果 credits 未满则 `put(1)` 归还。
- `routing()` 在 from_local 时、消息进入路由前调用 `_fifo_acquire(msg)`；消息投递到本地后调用 `_fifo_return(msg)`。

**NoC 类**：
- 新增 `fifos: Dict` 共享字典。
- `build()` 中将 fifos/reduce_trees 引用注入每个 Router。

### 4.4 测试

新建 `tests/test_h2_qos.py`（36 项检查），覆盖：
- T-H2.1–4：优先级仲裁（高优先级先获链路、PriorityResource -priority 排序）。
- T-H2.5–10：突发分段（mode 0/1/3/7 的 chunk 数和 beat_bytes、burst_bubble 间隔、header/imm 仅在首 burst）。
- T-H2.11–15：burst_len_mode=-1 整包传输。
- T-H2.16–20：FIFO register 校验（hw_id/logic_id 范围、depth、重复注册）。
- T-H2.21–26：FIFO credit 流控（WRITE_FULL 阻塞、credit 归还、满时 drop return）。
- T-H2.27–30：未注册 FIFO 引用异常。
- T-H2.31–36：优先级 + 突发 + FIFO 组合场景。

---

## 阶段 5：H3 十维高级数据布局

**架构规格**：§7.3，local side 最多 10 维、global side 最多 6 维循环布局，支持 dtype（12 种精度含 sub-byte）、mask（first/last）、BAUA 非对齐、transpose/reorder、gather/scatter。

### 5.1 新建 layout.py

- `AdaType` IntEnum：INT8=0, INT16=1, INT32=2, FP8_E4M3=3, FP16=4, BF16=5, FP32=6, TF32=7, INT64=8, FP8_E5M2=9, UINT4=10, UINT4X2=11。
- `ELEM_BITS` 字典：每种 dtype 的位宽（UINT4=4, 其余 8/16/32/64）。
- `ReorderMode` IntEnum：NONE=0, BMM=1, CONV2D=2, BISA=3, CONV3D=4。
- `MaskAxis(BaseModel)`：axis + num。
- `BAUA(BaseModel)`：base_axis_first/unalign_axis_first/num_first/second 两组。
- `GatherScatter(BaseModel)`：enabled/table_addr/addr_offset/num_entries/row_bytes/per_row_overhead。
- `TensorLayout(BaseModel)`：
  - 字段：loop_cnt: List[int], loop_stride: List[int], dtype, mask_first/last, pad_value, baua, transpose, reorder, transpose_overhead, gather, is_global。
  - 校验器：loop_cnt/stride 非负、dtype 合法、reorder 合法、transpose_overhead 非负、pad_value 范围 [0, 2^32)、loop_cnt 与 loop_stride 等长、ndim <= 6(global)/10(local)、loop count >= 1、mask axis 范围和 num 上限、mask first+last 共享 axis 不超限、BAUA axis 范围、sub-byte dtype 需至少一维。
  - 方法：`ndim()`, `elem_bits()`, `element_count_raw()`（loop_cnt 连乘）, `_removed_elements()`（mask 扣除）, `effective_element_count()`（gather 时返回 0）, `payload_bytes()`（gather 时 num_entries*row_bytes，否则 ceil(effective*bits, 8)）, `footprint_bytes()`（考虑 stride 的实际内存占用 + gather 索引开销）, `endpoint_overhead_cycles()`（transpose_overhead + gather per_row_overhead*num_entries）。

### 5.2 definitions.py 变更

- Message 新增 `layout: Optional[TensorLayout] = None`。
- `_check_layout_trans_type` model_validator：layout 仅对 SINGLECAST 有效，否则抛 ValueError。
- `byte_size()`：layout 非 None 时返回 `layout.payload_bytes()`，否则走 Slice 计算。
- `total_bytes()`：byte_size + header_bytes。
- `total_bytes_with_imm()`：byte_size + header_bytes + imm_bytes。
- `imm_bytes` 属性：adaLinkOp.has_imm2 时返回 8。
- 文件末尾 `Message.model_rebuild()` 解析前向引用 TensorLayout。

### 5.3 noc.py 变更

- Link.calc_latency：首 burst tx_bytes 包含 `msg.imm_bytes`。
- Router.routing()：from_local 时，如果 `msg.layout is not None`，yield `timeout(layout.endpoint_overhead_cycles())`。
- `_control_msg()` / `_sync_msg()`：update 中设置 `layout=None`（控制消息不携带布局）。

### 5.4 测试

新建 `tests/test_h3_layout.py`（46 项检查），覆盖：
- T-H3.1–5：AdaType/ELEM_BITS 位宽。
- T-H3.6–10：loop_cnt/stride 等长校验、ndim 上限（local 10/global 6）。
- T-H3.11–15：element_count_raw 连乘。
- T-H3.16–20：mask first/last 扣除元素数和 payload_bytes。
- T-H3.21–25：sub-byte dtype（UINT4 4-bit）ceil 到字节。
- T-H3.26–30：footprint_bytes 含 stride 间距。
- T-H3.31–35：BAUA axis 范围校验。
- T-H3.36–40：transpose overhead 和 gather overhead 周期。
- T-H3.41–45：GatherScatter payload/footprint 计算。
- T-H3.46：layout 仅 SINGLECAST 校验。

---

## 阶段 6：H4 周期精确影子流水

**架构规格**：§7.5，PE matrix（4 级）/vector（3 级）/SRAMC（单级）流水，MDMA channel/AIU，ACI func/AIU；4-entry spacing（occupancy=4）、initiation interval（II=1）。

### 6.1 新建 shadow.py

- `ShadowEntry` IntEnum，按 4-entry spacing 编排：
  - PE matrix：READ_F=4, READ_W=5, CAL=6, WRITE=7
  - PE vector：READ=8, CAL=9, WRITE=10
  - SRAMC：DNLD_0=12, UPLD_0=16, DNLD_1=20, UPLD_1=24（4 间隔）
  - MDMA：START=32, CHANNEL_0=36, CHANNEL_1=40, AIU_DOWNLOAD=44
  - ACI：START=64, FUNC=68, AIU_DOWNLOAD=92
- 辅助函数：`sramc_entry(channel, upload)`、`mdma_channel_entry(channel)`。
- `ShadowPipeline` 类：
  - 构造参数：env, name, entry_ids: List[int], stage_latencies: List[int], occupancy=4, ii=1。
  - `slots = simpy.Container(capacity=occupancy, init=occupancy)`：4-entry spacing，限制在途操作数。
  - `admit = simpy.Resource(capacity=1)` + `_next_admit`：II 控制，两次进入间隔至少 ii 周期。
  - `events: List[Dict]`：每个 stage 的 enter/exit 记录。
  - `occupancy_log: List[tuple]`：(time, in_flight_count) 采样。
  - `enter(tag, stage_overrides)`：自包含模式——获取 slot → admit → 顺序遍历所有 stage 并 timeout(latency) → 归还 slot。stage_overrides 可按 entry_id 键控覆盖延迟（用于 CAL 阶段动态计算周期数）。
  - `acquire(tag)`：包裹模式——获取 slot → admit → timeout(第一级 latency) → 返回 slot dict，调用方在自己的临界区结束后调用 `release(slot)`。
  - `release(slot)`：记录 event 并归还 slot。
  - `_admit()`：请求 admit Resource，等到 `_next_admit`，推进 `_next_admit = now + ii`。
  - `latency` 属性：sum(stage_latencies)。

### 6.2 config.py 变更

- 新增 `ShadowConfig(BaseModel)`：
  - `enabled: bool = False`
  - `occupancy: int = 4`
  - `ii: int = 1`
  - `matrix: List[int] = [0,0,0,0]`（4 级延迟）
  - `vector: List[int] = [0,0,0]`（3 级延迟）
  - `sramc_dnld/upld`, `mdma_channel/aiu`, `aci_func/aiu`：单级延迟
  - 校验器：occupancy/ii >= 1，matrix 长度 4，vector 长度 3，所有延迟 >= 0。
- `ArchConfig` 新增 `shadow: ShadowConfig = ShadowConfig()`。

### 6.3 core.py 变更

- `Core.__init__` 接收 `shadow_cfg`，校验 NMC channels <= 2（shadow 仅支持 2 通道 SRAMC）。
- comp_slots 在 shadow enabled 时为 `shadow_cfg.occupancy`，否则为 1。
- 新增 `_build_shadow_pipelines()`：
  - matrix_pipeline：[READ_F, READ_W, CAL, WRITE] 4 级，使用 cfg.matrix 延迟。
  - vector_pipeline：[READ, CAL, WRITE] 3 级，使用 cfg.vector 延迟。
  - sramc_pipelines：(channel 0/1, upload/download) 四个单级流水。
- 新增 `enter_matrix(tag, cal_cycles)` / `enter_vector(tag, cal_cycles)` / `enter_sramc(channel, upload, tag)` 方法。
- `shadow_events` 属性：汇总所有 PE 流水 events。
- Task.execute()：
  - CONV：shadow enabled 时 `yield core.enter_matrix(index, cal=flops//tpu.flops)`，否则 `tpu.occupy(flops)`。
  - POOL：同理使用 enter_vector。
  - SEND：shadow enabled 时 `yield core.enter_sramc(ch, upload=True, tag=index)`。
  - RECV：shadow enabled 时 `yield core.enter_sramc(ch, upload=False, tag=index)`。

### 6.4 memory.py（DMANode）变更

- DMANode 构造时接收 shadow_cfg，enabled 时为每个 channel 创建 `_mdma_pipelines[ch]`（单级 MDMA_CHANNEL entry）和一个 `_mdma_aiu_pipeline`（AIU_DOWNLOAD entry）。
- `transfer()`：获取通道后，如果 pipeline 存在则 `acquire()` → 执行引擎传输 → `release(slot)`。
- `_listen_aiu()`：AIU 下载经过 `_mdma_aiu_pipeline`。
- `shadow_events` 属性汇总。

### 6.5 nodes.py（AdaLinkNode）变更

- AdaLinkNode 构造时接收 shadow_cfg，enabled 时创建 `_aci_func_pipeline`（ST_ACI_FUNC）和 `_aci_aiu_pipeline`（ST_ACI_AIU_DOWNLOAD）。
- `_pipeline_for(msg)`：is_aiu 消息走 aci_aiu_pipeline，否则走 aci_func_pipeline。
- `handle()` 和 `_egress()` 和 `_listen_interchip()`：在 AdaLink 固定延迟前后用 acquire/release 包裹。
- `shadow_events` 属性汇总。
- `attach_nodes()` 和 `build_memory_system()` 透传 shadow_cfg。

### 6.6 architecture.py 变更

- `Arch.__init__` 接收 shadow_cfg（从 arch.shadow），透传到 Core、build_memory_system、attach_nodes。

### 6.7 测试

新建 `tests/test_h4_shadow.py`（72 项检查），覆盖：
- T-H4.1–5：ShadowEntry 枚举值和 4-entry spacing。
- T-H4.6–10：ShadowPipeline 构造校验（entry_ids/latencies 等长、occupancy/ii >= 1）。
- T-H4.11–15：enter() 自包含模式总延迟 = sum(stage_latencies)。
- T-H4.16–20：acquire()/release() 包裹模式。
- T-H4.21–25：occupancy=4 限制（第 5 个操作阻塞直到第一个完成）。
- T-H4.26–30：II=2 时两次进入间隔 2 周期。
- T-H4.31–35：stage_overrides 动态延迟（CAL 阶段）。
- T-H4.36–40：events 记录 enter/exit 时间和 tag。
- T-H4.41–45：occupancy_log 采样。
- T-H4.46–50：PE matrix 4 级流水端到端。
- T-H4.51–55：PE vector 3 级流水。
- T-H4.56–60：SRAMC 双通道 4 个独立流水。
- T-H4.61–65：MDMA channel/AIU 流水。
- T-H4.66–70：ACI func/AIU 流水。
- T-H4.71–72：shadow disabled 时无流水开销（向后兼容）。

---

## 阶段 7：H5 多芯片 AdaLink 路由

**架构规格**：§7.6，多芯片通过 AdaLink 互连；relocate table（20 项，port 2-bit）、跨片 credit 流控、CommID 阻塞、AIU 消息在入口终止、WRITE 延迟 `2H+2+L`、WRITE_SUM 原子加延迟 A、IMM2 增加 2H+2 周期。

### 7.1 definitions.py 变更

- 新增 `AdaLinkOp` IntEnum：WRITE=0x02, WRITE_SUM=0x03, WRITE_MAX=0x05, WRITE_WITH_IMM2=0xC2, WRITE_SUM_WITH_IMM2=0xC3, WRITE_MAX_WITH_IMM2=0xC5。
  - `has_imm2` 属性：0xC2/0xC3/0xC5。
  - `is_atomic` 属性：WRITE_SUM/WRITE_MAX 及其 WITH_IMM2 变体。
  - `atomic_op` 属性：sum=1, max=2, write=0。
- Message 新增 `dst_rank: int = 0`、`adalink_op: int = AdaLinkOp.WRITE`、`imm: int = 0`。
- `_check_dst_rank`：dst_rank >= 0。
- `_check_imm`：imm >= 0。
- `imm_bytes` 属性：has_imm2 时 8 字节。

### 7.2 noc.py 变更

**Router 类**：
- 新增 `chip_rank: int = 0`、`egress_table: Dict = {}`（NoC 共享引用）。
- `routing()` 新增跨芯片分支：
  - `cross = msg.dst_rank != self.chip_rank`。
  - cross + sync + 非控制 → 抛 ProfilingSimError（cross-chip outer sync 不支持）。
  - dst_rank 不在 egress_table → 抛 ProfilingSimError。
  - `target, egress_port = egress_table[dst_rank]`，target 是 egress AdaLink 节点所在路由器。
  - cross 时消息投递到 egress_port（而非 msg.dst_local_port）。
  - cross 消息不做单边 PE-to-PE 检查（single 仅在同片生效）。

**NoC 类**：
- `__init__` 新增 `rank: int = 0`，新增 `egress: Dict[int, Tuple[int, int]]` 字典（remote_rank → (router_id, port)）。
- `build()` 将 rank/egress 注入每个 Router。

### 7.3 nodes.py 变更（AdaLinkNode）

- `AdaLinkNode(NoCNode)` 类：
  - 构造参数新增 `chip_rank=0`、`atomic_latency=0`。
  - `commids`：5 个计数器数组（PRODUCE/SEND/RECEIVE/CREDIT/RNIC_PRODUCE，各 64 项）。
  - `_commid_events`：可重入事件字典，用于 `wait_comm_id` 阻塞。
  - shadow：aci_func / aci_aiu 两个 ShadowPipeline。
  - `interchip_in: simpy.Store`、`interchip_events`、`atomic_events`、`_peer_link`、`_return_link`、`_remote_rank`、`_remote_port`。
  - `bind_peer(link, return_link, remote_rank, remote_port)`：绑定跨片链路，创建 interchip_in Store，启动 `_listen_interchip()`。
  - `handle(port, msg)`：如果有 peer_link 且（broadcast 或 dst_rank != chip_rank）→ `_egress(msg)`；否则本地接收（commids RECEIVE++，shadow pipeline 包裹 latency）。
  - `_egress(msg)`：shadow acquire → timeout(latency) → commids SEND++ → peer_link.send(msg) → release。
  - `_listen_interchip()`：从 interchip_in 获取消息 → RECEIVE++ → shadow acquire → timeout(latency) → 非 AIU 消息：检查 is_atomic 则记录 atomic_events 并 timeout(atomic_latency)，然后 put 到本地 data_out；AIU 消息在入口终止（不再 re-inject）；finally release + return_credit。
  - `release_comm_id(comm_id, counter_type)`：计数器++，如果有等待事件则 succeed。
  - `acquire_comm_id(comm_id, counter_type)`：计数器++ 并返回值。
  - `wait_comm_id(comm_id, counter_type, expected)`：while 计数器 < expected，创建/复用 simpy.Event 并 yield。
- `attach_nodes()` 新增 `rank=0`、`atomic_latency=0` 参数，透传到 AdaLinkNode。

### 7.4 memory.py 变更

- `build_memory_system()` 新增 `rank=0`、`atomic_latency=0` 参数，创建 AdaLinkNode 时传入 `chip_rank=rank, atomic_latency=atomic_latency`。

### 7.5 architecture.py 变更

- `Arch.__init__` 新增 `env=None`（支持共享 Environment）、`rank=0`、`atomic_latency=0`。
- `self.env = env if env is not None else simpy.Environment()`。
- NoC 构造传入 `rank=rank`。
- build_memory_system 和 attach_nodes 透传 rank/atomic_latency。

### 7.6 新建 fabric.py

- `AdaLinkRelocateTable`：
  - 20 项（0–17 可用，18 保留，19 自身）。
  - `pack(rank, port) = (rank << 2) | port`（port 2-bit 0–3）。
  - `unpack(value) = (value >> 2, value & 0x3)`。
  - `set(local_id, remote_rank, remote_port)`：校验范围和重复。
  - `set_self(rank, port)`：设置 entry 19。
  - `lookup(local_id)`：entry 18 抛异常，否则返回 (rank, port)。
- `InterChipLink`：
  - `credits = simpy.Container(init=credits, capacity=credits)` 流控。
  - `send(msg)`：get credit → timeout(latency) → peer.interchip_in.put(msg)；发送失败则归还 credit。
  - `return_credit()`：接收方消费后归还 credit（capped）。
  - `bind(peer)`。
- `MultiChipFabric`：
  - 构造参数：env, chip_configs: Dict[rank, ArchConfig], mappers: dict, topology: List[(rank_a, idx_a, rank_b, idx_b)], link_latency=8, credits=64, atomic_latency=4, deterministic=False。
  - 按 sorted(rank) 为每个芯片创建 Arch（共享同一个 env，传入 rank 和 atomic_latency）。
  - 为每个 topology 边创建双向 InterChipLink，bind_peer 到两端 AdaLinkNode（gid=48+idx）。
  - 填充 `noc.egress[remote_rank] = (router_id, port)`。
  - 填充 relocate table（`set(idx, remote_rank, remote_port & 0x3)`）。
  - `execute()` 运行 env.run()。
  - 属性：`shadow_events`、`atomic_events`、`interchip_events`（汇总所有芯片 AdaLinkNode）。

### 7.7 测试

新建 `tests/test_h5_fabric.py`（68 项检查），覆盖：
- T-H5.1–4：AdaLinkRelocateTable pack/unpack/set/lookup/entry 18 保留/entry 19 self/port 2-bit 范围。
- T-H5.5–7：Message dst_rank/adalink_op/imm 校验、imm_bytes。
- T-H5.8–10：InterChipLink credit 流控（credit 耗尽阻塞、return_credit、发送失败归还）。
- T-H5.11–13b：egress 路由（同片消息不走 egress、跨片消息路由到 egress port、egress_table 缺失异常、cross-chip sync 异常）。
- T-H5.14–18：端到端跨片投递（WRITE 延迟 2H+2+L=24+5=29、WRITE_SUM 原子加 +A=4 → 33、IMM2 +14 → 43、shadow aci_func=3 adds 6、AIU 入口终止不 re-inject）。
- T-H5.19–20b：CommID wait/release/acquire 阻塞与唤醒。
- T-H5.21–23：向后兼容（Arch 默认 rank=0、env 自建、egress 为空、单片无跨片行为）。

---

## 阶段 8：H6 Pipeline 自动同步框架

**架构规格**：§7.4（仅 10 行规格），`cute::ada::Pipeline` 模板；缓冲区按 `(buf_id, chunk)` 键控，RAW/WAR/WAW 冒险通过零周期 simpy.Event 依赖解决。

### 8.1 设计决策（编码前经 subagent 评审修订）

1. **register-before-wait 原子发布**：write()/read() 在 yield 等待前，先原子地更新记分板状态（替换 write_event、重置 read_batch），杜绝多 waiter 覆盖彼此 release event 的竞态（B1 修复）。
2. **三阶段事件模型**：register（注册意图，立即更新状态）→ acquire（等待冒险清除）→ release（发布完成事件）。日志记录三个阶段（B2 修复）。
3. **reads_event batch 惰性重建**：读批次用 `[event, count]` 表示，count 追踪未完成的读；当 count 归 0 时 event succeed；新读到来时如果批次已完成（count==0）则创建新 event，避免对已 succeed event 重复 succeed（M3 修复）。
4. **wait() 快照屏障**：wait() 不注册访问，仅捕获当前记分板状态并等待；WRITE wait 同时等 write_event 和 read_batch，READ wait 仅等 write_event。
5. **与 H4 正交**：Pipeline 只追踪缓冲区就绪（零周期事件依赖），不追踪占用/II；H4 ShadowPipeline 负责后者。推荐先解除缓冲区冒险再占用影子槽位，被阻塞的冒险不占用槽位。
6. **auto_consume 双模式**：auto 模式 write()/read() 在注册后自动 yield 等待冒险清除后返回 slot；manual 模式注册后立即返回 slot，调用方自行调用 wait()。
7. **BufferSlot 幂等释放**：`release()` 用 `_released` 标志保证 try/finally 安全。

### 8.2 新建 pipeline.py

- `Access(Enum)`：READ="READ", WRITE="WRITE"。
- `BufferSlot`：`__slots__` 优化，持有 pipeline/buf_id/chunk/access/register_time/_released/_release_event/_batch；`release()` 幂等，委托 `pipeline._release()`。
- `_BufferState`：`write_event`（初始已 succeed，表示无未完成写）、`read_batch: Optional[List] = [event, count]`（初始 None 表示无在读）。
- `Pipeline`：
  - `__init__(env, auto_consume=True, name="pipeline")`：`_states: Dict[Any, _BufferState]`、`events: List[Dict]`。
  - `write(buf_id, chunk)`（别名 `produce`）：
    - 捕获 prev_write = st.write_event, prev_batch = st.read_batch。
    - 创建 my_release event，替换 st.write_event = my_release，重置 st.read_batch = None。
    - log register。
    - auto 模式：`yield prev_write`（WAW/RAW 等待前一个写完成）；如果 prev_batch 非 None，`yield prev_batch[0]`（WAR 等待前一批读完成）；log acquire。
    - 返回 BufferSlot（release_event=my_release）。
  - `read(buf_id, chunk)`（别名 `consume`）：
    - 捕获 prev_write = st.write_event。
    - 如果 read_batch 为 None 或 count==0，新建 `[env.event(), 0]`。
    - batch[1] += 1。
    - log register。
    - auto 模式：`yield prev_write`（RAW 等待前一个写完成）；log acquire。
    - 返回 BufferSlot（batch=batch）。
  - `wait(buf_id, chunk, access)`：快照屏障，不注册。WRITE 等 write_event + read_batch；READ 等 write_event；log acquire。非法 access 抛 ValueError。
  - `_release(slot)`：log release；WRITE 则 `release_event.succeed()`；READ 则 `batch[1] -= 1`，到 0 时 `batch[0].succeed()`。
  - `_validate_chunk(chunk)`：非 bool、非负 int。
  - `_key(buf_id, chunk)` 返回 `(buf_id, chunk)`，buf_id 支持任意 hashable（含 IntEnum）。

### 8.3 测试

新建 `tests/test_h6_pipeline.py`（65 项检查），覆盖：
- T-H6.1–3：构造校验（auto_consume 默认/手动、name）。
- T-H6.4–8：write slot 生命周期（register→acquire→release 时间点、幂等 release）。
- T-H6.9–12：read slot 及别名 produce/consume。
- T-H6.13–14：不同 (buf_id, chunk) 独立性。
- T-H6.15：RAW 冒险（W 0–10, R 10–15）。
- T-H6.16：三阶段日志（register/acquire/release 顺序和时间）。
- T-H6.17：WAW 冒险（W1 0–10, W2 10–20）。
- T-H6.18：WAR 冒险（R 0–10, W 10–15）。
- T-H6.19：WAW+WAR 独立等待（R1 4–5, W1 5–15, W2 15–20 链式）。
- T-H6.20：RR 并行（两读无依赖同时开始）。
- T-H6.21：多读 + 写（3 个并发读全部完成后写才能开始）。
- T-H6.22：链式 W→R→W→R。
- T-H6.23：零周期事件依赖（无 timeout 的纯事件链无额外周期）。
- T-H6.24：手动模式（注册后立即返回，wait() 显式屏障）。
- T-H6.25–27：双缓冲（两个 chunk 交替无冒险）。
- T-H6.28–30：事件日志格式和阶段计数。
- T-H6.31：晚到消费者（R1 0–10, W 10–15, R2 15–20，R2 在 W 注册后才注册但正确等待）。
- T-H6.32：32 writer 压力测试。
- T-H6.33：pending writer + late reader（W 等 R1, R2 在 W 注册后到达，W 不等 R2 因为 R2 在 W 之后注册）。
- T-H6.34–37：wait() 语义（WRITE wait 等写+读、READ wait 等写、wait 不注册、非法 access 异常）。
- T-H6.38–40：与 ShadowPipeline 组合（先 Pipeline 解冒险再 Shadow 占用槽位）。
- T-H6.41–45：DFG 无回归（内联 MockMapper，LOAD_FEAT→POOL→STORE 流水线，Pipeline 不自动接入 Core）。
- T-H6.46–50：IntEnum buf_id 等价性。
- T-H6.51–65：边界和异常（chunk 负数/bool/非 int、release 后事件 succeed、多 Pipeline 隔离、read_batch 惰性重建）。

---

## 文件清单

### 最终 profiling_sim/ 结构（18 个 Python 源文件）

| 文件 | 阶段 | 说明 |
|------|------|------|
| `__init__.py` | 0 | 包导出 |
| `definitions.py` | 0+1+2+H1–H5 | 全部枚举/Pydantic 数据模型/异常 |
| `config.py` | 0+2+H4 | 全部 Pydantic 配置模型 + load_arch() |
| `dfg.py` | 0 | DFGNode/DFG（从 utils.dfg 内联） |
| `task.py` | 0+1 | Task + task_priority（从 utils.task 内联） |
| `distribution.py` | 0 | NoCDist（精简，deterministic） |
| `noc.py` | 0+F6,F9,F10,F11,H1,H2,H3,H5 | Link/Router/NoC 核心网络 |
| `core.py` | 0+1+H4 | ScratchpadMemory/LSU/TPU/NMC/Scheduler/Core |
| `architecture.py` | 0+F7,F8,H4,H5 | Arch 顶层组装 |
| `run.py` | 0+F8 | simulate()/save_trace() |
| `tracing.py` | 0 | 时间片利用率统计（含 dma/mem） |
| `nodes.py` | F7,H5 | NodeType/DataNocLocalId/NoCNode/AdaLinkNode/attach_nodes |
| `memory.py` | F8,H4,H5 | Memory/DMANode/build_memory_system |
| `dma.py` | F8 | DMAEngine 通用多通道 DMA |
| `layout.py` | H3 | TensorLayout/AdaType/10 维布局 |
| `shadow.py` | H4 | ShadowEntry/ShadowPipeline |
| `fabric.py` | H5 | AdaLinkRelocateTable/InterChipLink/MultiChipFabric |
| `pipeline.py` | H6 | Access/BufferSlot/Pipeline 自动同步 |

### 配置文件

| 文件 | 说明 |
|------|------|
| `configs/mesh_8x4.json` | 8×4 Mesh 默认配置（32 PE, W=16, 1125/1150 MHz 双时钟域） |

### 测试文件（15 个套件）

| 文件 | 阶段 | 检查数 |
|------|------|--------|
| `tests/test_smoke.py` | 1 | 77 |
| `tests/test_features.py` | 1 | 33 |
| `tests/test_f6_ports.py` | F6 | 19 |
| `tests/test_f7_nodes.py` | F7 | 74 |
| `tests/test_f8_memory.py` | F8 | 30 |
| `tests/test_f9_transfer.py` | F9 | 16 |
| `tests/test_f10_multicast.py` | F10 | 27 |
| `tests/test_f11_sync.py` | F11 | 29 |
| `tests/test_integration.py` | 集成 | 33 |
| `tests/test_h1_reduce.py` | H1 | 47 |
| `tests/test_h2_qos.py` | H2 | 36 |
| `tests/test_h3_layout.py` | H3 | 46 |
| `tests/test_h4_shadow.py` | H4 | 72 |
| `tests/test_h5_fabric.py` | H5 | 68 |
| `tests/test_h6_pipeline.py` | H6 | 65 |
| **合计** | | **672** |

### 设计文档

| 文件 | 说明 |
|------|------|
| `tests/TEST_PLAN_MEDIUM.md` | F6–F11 测试计划（建模决策 + 测试用例） |
| `tests/TEST_PLAN_HARD.md` | H1–H6 测试计划（含 subagent 评审修订记录） |

---

## 测试清单

全部 672 项检查，0 失败。测试是独立 Python 脚本（无 pytest 依赖），每个文件自带 `check()` 断言函数和 passed/failed 计数，末尾 `sys.exit(1 if failed else 0)`。

运行方式：
```bash
PYTHONPATH=/Users/bytedance/LPFrame/RL-Schedule \
  /Users/bytedance/LPFrame/RL-Schedule/.venv-test/bin/python \
  -m profiling_sim.tests.<test_module>
```

环境：Python 3.12.13, simpy 4.1.2, pydantic 2.13.4, scipy（仅 NoCDist 随机模式使用）。

### 关键架构参数

- **拓扑**：8×4 Mesh，32 个路由器，104 条 r2r 双向链路，XY 维序路由
- **链路宽度**：W=16 字节/周期，per_hop_time=1，delay=0
- **节点总数**：66（32 PE + 4 GM_RDMA + 4 GM_WDMA + 4 DDR_RDMA + 4 DDR_WDMA + 18 AdaLink）
- **时钟域**：ACI 1125 MHz / DDR 1150 MHz（ddr_scale ≈ 1.022）
- **本地端口**：0–22（MAX_LOCAL_PORT=22）
- **调度优先级**：STORE(0) > SEND(1) > COMP(2) > LOAD/RECV(3)
- **H4 影子流水**：occupancy=4（4-entry spacing），II=1
- **H5 跨片**：link_latency=8, credits=64, atomic_latency=4
- **H6 Pipeline**：零周期事件依赖，auto_consume 默认开启
