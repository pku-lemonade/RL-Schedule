# ADA2S-32 NoC 微架构参数确认清单

## 说明

我们正在为 ADA2S-32 的 Data NoC 及传输子系统构建一个 event-driven 性能模型，目标是对典型 transformer-like workload 做相对精确的 profiling（端到端延迟误差 <15-20%）。目前已从 driver 头文件、compiler intrinsic 接口和实测数据中确定了：
- 完整的网络拓扑（8x4 mesh，48个节点，GM/DDR DMA 的挂载位置）
- NMC 寄存器接口（upload 58 参数 / download 91 参数，含10维stride、BAUA、mask、transpose、gather/scatter）
- 同步机制（inner/outer sync 的 counter-based release/acquire 协议）
- 数据类型支持、数据包头部 software-visible 字段、burst 控制、FIFO 流控接口
- 实测数据（单 NMC channel 稳态带宽 ~18 GB/s，单层 transformer ~109us/161us，EU issue latency ~0.9us）

以下是**无法从软件接口推断、必须确认的微架构参数**。每个问题都给出了常见选项，方便直接勾选或填入数值。共 17 个必问问题 + 4 个可选问题（可选问题可后续通过 microbenchmark 自测，但如果能直接告知更好）。

> 本清单已参考 BookSim2 的完整建模参数集做过 gap analysis，补充了对吞吐有显著影响但容易遗漏的微架构参数（speculative allocation、output buffer、switch hold policy、allocator algorithm、concentrated router 并发度等）。

---

## 一、Router 链路与数据包格式

**Q1. Flit 大小、packet 组成、beat 单位与默认 burst 长度：**
- Flit 大小：______ Bytes
- Packet header：______ 个 flit
- Payload：每个 packet 固定 ______ 个 flit，还是变长
- Tail flit：[ ] 有独立 tail flit  [ ] 没有（header 中标注长度）
- Beat 大小（`burstLenMode` 的基本单位）：______ Bytes
- 1 beat 是否等于 1 flit？[ ] 是  [ ] 否（1 beat = ______ flits）
- `BURST_LEN_DEFAULT`（-1）实际对应多少 beats？______ beats

---

## 二、Router 流水线与路由

**Q2. Router 流水线有多少级？每 hop 的零负载延迟（zero-load latency）是多少 cycles？是否使用 speculative allocation？**
- [ ] 2 级（如 route + switch）
- [ ] 4 级（经典：buffer write / route compute / switch alloc / switch traversal）
- [ ] 5 级
- 其他：______ 级 = 每 hop ______ cycles
- 是否使用 speculative allocation（head flit 的 VC allocation 和 switch allocation 并行执行，而非串行）？
  - [ ] 是，speculative（VC alloc 与 SW alloc 并行，zero-load latency 少 1 级）
  - [ ] 否，经典串行（VC alloc 完成后再做 SW alloc）
- 各级延迟分别为多少 cycles？route compute = ____，VC alloc = ____，SW alloc = ____，switch traversal = ____

**Q3. 使用什么路由算法？如何避免死锁（deadlock）？**
- [ ] XY 确定性路由（先走 X 方向，再走 Y 方向）
- [ ] YX 确定性路由
- [ ] 自适应路由（带 escape VC 防死锁）
- 其他：______
- 死锁避免机制（若为确定性路由可跳过；若为自适应请说明）：
  - [ ] 专用 escape VC（dateline 方案）
  - [ ] Bubble flow control
  - [ ] Turn-model（禁止某些转向）
  - 其他：______
- `FIXPATH` transfer type 是否意味着 source routing（软件指定完整路径）？还是只是固定 XY/YX 路径的提示？

---

## 三、缓冲、流控与仲裁

**Q4. Buffer 组织结构：**
- 每个端口的 Virtual Channel（VC）数量：______
- 每个 VC 的 buffer 深度：______ flits
- 共享 buffer（如果有）：______ flits
- Buffer 管理策略：[ ] private per-VC（每个 VC 独占）  [ ] shared pool（所有 VC 共享）
- 流控机制：[ ] credit-based  [ ] on/off (STOP/GO)  [ ] 其他：______
- Credit return latency：______ cycles
- VC 是否需要等 tail credit 返回后才能重新分配？[ ] 是（wait_for_tail_credit）  [ ] 否

**Q5. VC allocator 和 switch allocator 使用什么算法？仲裁策略是什么？crossbar grant 是否按 burst 保持？**
- Allocator 算法（VC alloc 和 SW alloc 可能不同，请分别注明）：
  - [ ] Per-input round-robin（简单轮询，每个 input 独立选 output）
  - [ ] iSLIP（____ iterations）
  - [ ] PIM（Parallel Iterative Matching，____ iterations）
  - [ ] Separable input-first / output-first（arbiter type: round_robin / matrix）
  - [ ] Wavefront allocator
  - [ ] LOA / max-size matching
  - 其他：______
- `shr_buf_port_priority`（0-3）在仲裁中如何作用？
  - [ ] 严格优先级抢占（高优先级可立即抢占低优先级已获得的 grant）
  - [ ] 加权 WRR（priority 作为权重，不抢占）
  - [ ] 仅在 buffer 分配时优先，switch 仲裁不区分
  - 其他：______
- Crossbar grant 的保持策略：
  - [ ] 每个 flit/beat 重新仲裁（flit-by-flit arbitration，flows 可交错）
  - [ ] 整个 packet/burst 保持 crossbar 连接直到 tail flit（hold_switch_for_packet）
  - [ ] 固定保持 ____ beats 后重新仲裁
- VC allocation 是否优先选择空 VC？[ ] 是  [ ] 否（标准 round-robin）

**Q6. Router output 端口是否有 output-side buffer？**
- [ ] 无，纯 input-queued（IQ router），flit 赢得 crossbar 后直接发往下游链路
- [ ] 有，每个 output port（N/S/E/W + local）buffer 深度 = ______ flits
- [ ] 仅 local（injection/ejection）端口有 output buffer，深度 = ______ flits
- Output buffer 和 input buffer 是同一 clock domain 吗？[ ] 是  [ ] 否

**Q7. Outer sync 包和数据包是否共用 VC/链路？**
- [ ] Sync 包使用独立的高优先级 VC（可抢占数据包）
- [ ] Sync 包和数据包共用 VC，同优先级
- [ ] Sync 包共用 VC 但仲裁优先级更高
- 其他：______

---

## 四、NMC（PE 端 DMA 控制器）

**Q8. 链路宽度与 NMC 通道端口结构：**
- 相邻 Router 之间的物理链路宽度（mesh link）：______ bits/cycle/方向
- 每个 NMC channel（CH0/CH1）到本地 router 的带宽：______ bits/cycle
- NMC channel 宽度与 inter-router link 宽度是否一致？
  - [ ] 一致（均为 ____ bits/cycle）
  - [ ] 不一致：NMC = ____ bits，link = ____ bits
- CH0 和 CH1 是否共享同一个物理 injection port？
  - [ ] 是，共享端口（带宽不叠加，同一 cycle 只能有一个 channel inject）
  - [ ] 否，各有独立端口（带宽可叠加，同一 cycle 两个 channel 可同时 inject）

**Q9. NMC 传输启动流水线：**
- Setup latency：从 scalar 写完最后一个配置寄存器（触发传输）到第一个 flit 出现在 NoC 上：______ cycles
- 同一个 channel 上能否 pipeline 多个传输（前一个未完成就 fire 下一个）？
  - [ ] 可以，最多支持 ______ 个 outstanding transfer
  - [ ] 不行，必须等当前传输完全结束才能 fire 下一个
- `maxOst`（0-255）的单位是什么：[ ] packets  [ ] bursts  [ ] 128B entries  [ ] flits  [ ] 其他：______
- 默认 `maxOst` 值：______

**Q10. PE SRAM 端口结构（直接影响 compute-IO overlap 能否成立）：**
- Local SRAM（3 MB）：______ 个读端口 + ______ 个写端口
- Weight SRAM（16 MB）：______ 个读端口 + ______ 个写端口
- 7 个 master（Matrix、Vector、DNLD0、DNLD1、UPLD0、UPLD1、Scalar）如何共享 SRAM 端口？
  - [ ] 全 crossbar，访问不同 bank 时无冲突
  - [ ] 共享总线 + 轮询仲裁
  - [ ] 固定优先级（如 Matrix > DMA > Vector > Scalar）
  - 其他（请描述）：______

---

## 五、DMA 节点（GM/DDR RDMA/WDMA）

**Q11. DMA 命令派发（dispatch）：**
- 每个 DMA 实例（4 个 GM_RDMA、4 个 GM_WDMA、4 个 DDR_RDMA、4 个 DDR_WDMA）是否有独立的 scalar core？
  - [ ] 是，每个 DMA 实例各有自己的 command processor
  - [ ] GM_RDMA 共享一个、GM_WDMA 共享一个、以此类推
  - 其他：______
- 命令派发速率：连续发起 DMA 命令的间隔是 ______ cycles（每个 scalar core）
- 备注：我们实测观察到 GM_RDMA 向 32 个 PE fanout 时存在串行化现象，初步推测命令 issue 间隔约为几十 cycles/命令，方便的话请确认。

**Q12. DMA 通道带宽：**
- WDMA 的 CH0 和 CH1 是否各自有独立的 NoC 带宽？
  - [ ] 是，独立通道（带宽叠加）
  - [ ] 否，共享一个物理端口
- GM 总带宽 576 GB/s 和 DDR 总带宽 533 GB/s 是：[ ] PHY 理论峰值  [ ] 实际可达到的 sustained 带宽

**Q13. Concentrated router（如 router 28-31）的 local port 并发能力：**
- 以 router 31 为例（同时挂载 PE、GM_RDMA、GM_WDMA CH0/CH1、DDR_RDMA、DDR_WDMA CH0/CH1），同一 cycle 内最多有多少个 local port 可以同时 inject/eject flit？
  - [ ] 所有 local port 均可独立并发（全 crossbar non-blocking）
  - [ ] 所有 local port 共享一条 injection/ejection bus（每 cycle 仅 1 个 local transfer）
  - [ ] 部分并发：最多 ______ 个 local transfer/cycle
  - [ ] CH0/CH1 pair 内共享带宽，但不同 DMA type 之间独立
- 在 concentrated router 上，PE 注入的 packet 是否会被 DMA 流量 block？还是 PE 有独立 guaranteed bandwidth？

---

## 六、Router 内归约与组播

**Q14. Route reduce（Add/Max）行为：**
- 每 hop 归约额外开销：______ cycles
- 归约方式：
  - [ ] Store-and-forward（等所有操作数到齐才向下转发）
  - [ ] Cut-through（操作数到齐即处理，部分 stall）
  - [ ] Pipelined（稳态下无额外延迟）
- 每个 router 最多同时支持多少个 reduction tree：______
- 归约支持的数据类型：[ ] INT8/INT16/INT32  [ ] FP16/BF16  [ ] FP32  [ ] 全部

**Q15. Multicast 实现方式：**
- [ ] Router 内包复制（tree-based，header 中带 bitmask）
- [ ] Source-routed 路径式（一个包依次经过所有目标）
- 其他：______
- 每多复制一个目的端口的额外延迟：______ cycles
- Multicast 包在不同 output port 上是否可以并行发送，还是串行复制？

---

## 七、Single-Side 传输开销

**Q16. Single-side 模式的包大小：**
- Request 包头大小：______ Bytes（携带目标地址 + stride 描述符）
- Response 包大小：______ Bytes
- Request/Response 往返延迟（PE 到本地 GM，无竞争）：______ cycles
- Single-side 模式是否支持 out-of-order response？[ ] 是  [ ] 否（按请求顺序返回）

**Q17. FIFO 流控细节：**
- `hw_id`（0-63）和 `logic_id`（0-31）对应的 FIFO 深度分别是多少？
  - hw FIFO depth：______ entries（或 ______ Bytes）
  - logic FIFO depth：______ entries
- FIFO credit return latency（从 receiver 更新 credit 到 sender 可见）：______ cycles
- FIFO check 是 per-packet 还是 per-burst？[ ] per-packet  [ ] per-burst  [ ] per-flit

---

## 可选问题（可以通过 microbenchmark 自测，能直接告知更好）

- DDR CDC（1150MHz ↔ 1125MHz）async FIFO 深度：______ entries；带宽 bubble 损耗约 ______%
- Hardware transpose 相比普通 DMA 的吞吐损耗：[ ] 零开销  [ ] 慢 ______%
- Gather/scatter 每个 entry 的 table walk 额外开销：______ cycles/entry
- GM writeSum 原子操作实现方式：[ ] read-modify-write  [ ] 专用累加单元；争用同一地址时的吞吐：______ ops/cycle
