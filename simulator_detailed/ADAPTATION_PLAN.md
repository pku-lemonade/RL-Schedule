# simulator_detailed ADA2S-32 Flit-Level Adaptation Plan

> Goal: Adapt `simulator_detailed/` from the current abstract 4×4 message-level model to a flit-level wormhole model matching ADA2S-32 silicon characteristics (8×4 mesh, 128 B/cyc links, 512 B flits, credit-based flow control, 16 DMA nodes).
>
> Modeling granularity: **flit (512 B)** — phit (128 B) is not modeled explicitly; the 4-cyc/flit serialization is a constant. Resource allocation, buffer management, credit flow control, arbitration, and routing all operate at flit granularity.

> **Implementation status (2026-08-24): Phase 2 complete.** The detailed
> sub-step text below records the design process. The implemented contracts and
> calibrated timing are documented in `simulator_detailed/docs/`; the standalone
> regression suite is `simulator_detailed/tests/test_phase2_noc.py`. Phase 3 is
> the next implementation phase.

---

## Hardware Target Parameters (from silicon measurement, NOC_ARCHITECTURE.md §2.4/§9.7)

| Parameter | Value | Source |
|-----------|-------|--------|
| Mesh dimensions | X=4 cols, Y=8 rows (32 routers, 32 PEs) | §2.1 ★ |
| Routers | 32 (PE-position only, no separate DMA routers) | §2.1 |
| Phit width | 1024 bits = 128 B/cycle/direction | Derived, Appendix B |
| Flit size | 512 B = 4 phits | §2.4 ★ |
| Flit serialization | 4 cycles ideal, ~4.3 cycles effective (6% bubble) | §2.4 / Appendix B |
| Packet format | Single-flit: H+payload; Multi-flit: 1 H + P body + 1 T | §2.4 ★ |
| Header size | ~12 B in first phit; body/tail overhead ~4 B CRC/seq | §3.1 (derived) |
| Logical payload per head flit | 512 B | Measured packet-count behavior (§9.2) |
| Logical payload per body/tail flit | 512 B | Measured packet-count behavior (§9.2) |
| Virtual channels | 1 per port | §2.4 ★ |
| Input buffer depth | 1 flit (512 B) per port | §2.4 ★ |
| Flow control | Credit-based, credit RTT ≈ 17 cycles (1 hop) | §2.4 ★ |
| Routing | XY deterministic, X-first then Y | §2.4 ★, §3.3 |
| Arbitration | Round-robin across input ports | §2.4 (measured) |
| Multicast | In-router single-write-multi-read (zero-cost fan-out) | §2.4 ★ |
| Router pipeline (head flit) | RC:1 + SA:2 + ST:1 + LT:0.5 ≈ 4.5 cycles | §2.4 (derived) |
| Per-hop one-way latency (complete flit) | ~8.5 cycles | §9.1 linear fit |
| Directional link wire BW | 144 GB/s = 128 B/cyc @ 1125 MHz | §2.4 |
| Effective link BW | ~135 GB/s = ~120 B/cyc (94%, bubbles+CRC) | §9.3 |
| X fast path (Y=0/7 rows) | ~11 cyc/hop RTT X vs ~17 cyc/hop Y | §2.4 (measured) |
| Clock | ACI domain 1125 MHz (PE/GM/routers); DDR 1150 MHz | §1.3 |
| PE NMC channels | CH0 + CH1, independent command queues | §4.2 |
| NMC SRAM port (shared) | ~120 GB/s aggregate (~106 B/cyc), read+write time-shared | §4.2 |
| Single NMC channel BW | ~118-120 GB/s per direction | §4.2 |
| CH0+CH1 full-duplex aggregate | ~120 GB/s total (~60 send + ~60 recv), NOT 240 | §4.2 |
| NMC endpoint setup latency | static ~80 cyc/endpoint; dynamic ~125 cyc/endpoint | §4.13 |
| Dynamic shape dispatch overhead | ~45 cycles | §4.13 |
| c2r (core-router) link width | 128 B/cyc (same as r2r) | §2.5 derived |
| GM attachment | 4 GM_RDMA + 4 GM_WDMA on Y=7 (routers 28-31) | §2.3 |
| GM_RDMA ports | Port 14 (single-side) per instance; 4 independent ports | §2.5 |
| GM_WDMA ports | CH0=port10, CH1=port11 per instance; CH0/CH1 share 1 physical port | §2.5 ★ |
| DDR attachment | 4 corner routers (0,28,3,31) | §2.3 |
| DDR_RDMA ports | Port 7 (single-side); 4 independent | §2.5 |
| DDR_WDMA ports | CH0=port3, CH1=port4 per instance; CH0/CH1 share 1 port | §2.5 |
| DDR CDC penalty | <10 cycles fixed overhead | §1.3 (measured) |
| GM aggregate BW | 576 GB/s (~512 B/cyc, 4 instances × 128 B/cyc) | §1.2 |
| DDR aggregate BW | 533 GB/s; DDR_RDMA read limited to ~103 GB/s/ch (DDR MC bottleneck) | §9.10 |
| GM_RDMA send cap | ~120-125 GB/s aggregate per instance (outcast) | §9.7 |
| GM_WDMA recv cap | ~100-120 GB/s aggregate per instance (incast, fair RR) | §9.7 |
| DDR_RDMA recv cap | ~103 GB/s/ch (DDR MC limited, not NoC) | §9.10 |
| DDR_WDMA send cap | ~122 GB/s/ch (NMC limited), large msg hop latency hidden | §9.10 |
| DMA scalar core dispatch | Serial, bottleneck for many small fan-out transfers | §5.2 |
| GM_WDMA writeSum (atomic add/max) | Fully pipelined, ~120 GB/s aggregate, float32 only | §5.6 (measured) |
| Single vs dual-side | <3% difference (effectively same performance) | §9.8 |
| PE Local SRAM | 3 MB per PE (0x100000-0x3FFFFF) | §1.2 / Appendix A |
| PE Weight SRAM | 16 MB per PE (0x400000-0x13FFFFF) | §1.2 / Appendix A |
| Broadcast latency overhead | +7 cyc (PE bcast spanning-tree), +155 cyc (GM bcast) | §9.7 |
| Reduction (in-router Add/Max) | Per-hop overhead TBD; GM-side atomic confirmed zero-overhead | §3.4 |
| Jitter under zero load | <1 cycle (deterministic) | §9.1 |
| Bottleneck location | Endpoint NMC/DMA ports, NOT NoC fabric | §2.5 (measured) |
| Local port ID map (DataNocLocalId) | PE=0, DDR_WDMA_CH0=3, DDR_WDMA_CH1=4, DDR_RDMA_LOC=5, DDR_WDMA_LOC=6, DDR_RDMA=7, DDR_WDMA=8, GM_WDMA_CH0=10, GM_WDMA_CH1=11, GM_RDMA_LOC=12, GM_WDMA_LOC=13, GM_RDMA=14, GM_WDMA=15 | §2.5 |

---

## Current Model vs Hardware Gaps

| Aspect | Current `simulator_detailed/` | Hardware target | Gap severity |
|--------|-------------------------------|-----------------|-------------|
| Mesh size | 4×4 (configurable but defaults are 4×4) | 8×4 (32 PEs) | 🔴 Critical |
| DMA nodes | None (only PEs modeled) | 16 DMA nodes on 8 routers | 🔴 Critical |
| Router local ports | 1 hardcoded `'core'` port | Up to 13 local ports per corner/bottom router | 🔴 Critical |
| Link width | 16 B/cyc (config default) | 128 B/cyc | 🔴 Critical |
| Flit model | None (whole-message store-and-forward) | 512 B flit, wormhole cut-through | 🔴 Critical |
| Input buffer | `simpy.Store(capacity=1)` per message (unbounded data) | 1 flit (512 B) per port | 🔴 Critical |
| Flow control | None (infinite buffering) | Credit-based, 17 cyc RTT | 🔴 Critical |
| Per-hop router latency | `per_hop_time=1` cycle | ~8.5 cyc/flit hop (4.5 pipeline + 4 ser + credit) | 🔴 Critical |
| Virtual channels | `vc=2` (unused) | 1 VC/port | 🟡 Wrong but unused |
| Arbitration | FIFO (Store ordering) | Round-robin | 🟡 Affects fairness |
| Bandwidth jitter | Gamma distribution per-link per-message | Deterministic (jitter <1 cyc zero load) | 🟡 Inaccurate |
| NMC channels | 1 channel (single data_in/data_out) | 2 channels (CH0/CH1), shared ~120 GB/s SRAM port | 🔴 Critical |
| NMC startup latency | `start_up_time=1` (unused) | 80 cyc static / 125 cyc dynamic | 🟡 Affects small-msg latency |
| SPM model | Single 2GB SPM | 3 MB Local + 16 MB Weight SRAM | 🟡 Capacity affects tiling |
| LSU width | 4 B/cyc (arbitrary) | NMC SRAM port ~106 B/cyc aggregate | 🔴 Critical |
| c2r link width | 8 B/cyc (config) | 128 B/cyc | 🔴 Critical |
| r2r link delay | 0 cycles | ~0.5 cycle LT (link traversal) — included in 8.5 total | 🟢 Small |
| X fast path | Not modeled | Y=0/7 X-hop ~5.5 cyc one-way vs Y-hop ~8.5 | 🟡 Affects corner/edge latency |
| DDR clock domain | Not modeled | 1150 MHz vs ACI 1125 MHz, CDC <10 cyc fixed | 🟢 Small effect |
| DMA scalar core dispatch | Not modeled | Serial command dispatch bottleneck for small transfers | 🟢 Deferred |
| Broadcast/multicast | Not modeled | In-router SWMR, zero-cost fan-out | 🟡 Phase 3 |
| In-router reduction | Not modeled (sync modes exist but no reduction logic) | Add/Max in-flight | 🟡 Phase 3 |
| FIXPATH source routing | Not modeled | TransType=1 fixed path | 🟢 Phase 3 |
| GM/DDR memory model | None (mem config exists but unused) | Bandwidth-limited memory with atomic ops | 🔴 Critical for PE↔GM/DDR |

---

## File-by-File Change Plan

### Phase 1: Config & Types (infrastructure for everything else)

| ✅ Done | `configs/schemas/arch_config.py` | Added FlitConfig, RouterPipelineConfig, NMCConfig, DMAEngineConfig, MemoryControllerConfig, DMAType/MemType enums; updated existing classes with ADA2S defaults; backward compatible. Commit `8a83656`. |
| ✅ Done | `utils/definitions.py`, `endpoint_registry.py` | Added NodeType, FlitType, TransType enums; port constants (0-15 local, 100-103 direction); Flit and immutable EndpointAddress classes; architecture-owned PE/DMA attachment resolution; helpers (direction_to_port, is_direction_port, is_local_port, compute_flit_count, port_to_direction); addressed Message packetization plus broadcast/reduce/sync metadata; extended Event/TraceItem/TimeSlice with node_type/flit_count/is_dma/dmas list; removed FIXPATH (MULTICAST=1, BROADCAST=2). |
| ⏳ Deferred | `configs/schemas/failure_configs.py` | Add DmaFail/MemFail/NmcFail — needed only when DMA model exists and fail-slow injection is tested. No code breakage from postponing. |
| ⏳ Deferred | `configs/schemas/mapping_config.py` | Add DstType enum — needed only when mapper.py generates PE→GM/DDR SEND nodes. |
| ⏳ Deferred | `configs/instances/ada2s_32.json` | Full 8×4 config with 16 DMA engines — needed when architecture.py wires up DMA nodes. |
| ⏳ Deferred | `configs/instances/ada2s_normal.json` | No-failure config for ada2s. |

---

### Phase 2: NoC Flit-Level Model (core of the change)

**Status:** ✅ Complete (2026-08-24)

The final implementation uses 4.0-cycle serialization, 0.5-cycle wire
propagation, and 0.3-cycle credit return as separate processes. This produces a
4.5-cycle first-flit Link latency and a 4.8-cycle steady receive gap. The final
Link, Router, topology, data-type, and trace contracts are described in
`simulator_detailed/docs/` and supersede conflicting pseudocode below.

#### noc.py rewrite, broken into sub-steps (each ~50-150 lines, independently testable):

##### Step 2a. Imports & utilities cleanup (≈10 lines changed)
- Remove `from .distribution import NoCDist` (no Gamma jitter — deterministic model)
- Remove `import contextlib` (old any_of loop)
- Add imports from definitions: `Flit`, `FlitType`, `NodeType`, `TransType`, `PORT_PE`, `DIR_NORTH/SOUTH/EAST/WEST`, `direction_to_port`, `is_direction_port`, `is_local_port`, `port_to_direction`, `compute_flit_count`
- Add `from .configs.schemas.arch_config import *` is already there (FlitConfig, RouterPipelineConfig etc.)

##### Step 2b. Link class rewrite (≈80 lines total)
The Link changes from a message-level store-and-forward pipe to a flit-level credit-based pipe.

Key changes to [Link](noc.py):
| Attribute/Method | Old behavior | New behavior |
|---|---|---|
| `__init__` | `self.store = simpy.Store(env, capacity=1)` (one whole msg), `self.bandwidth = config.width`, creates `NoCDist` Gamma | `self.flit_buffer = simpy.Store(env, capacity=buffer_depth_flits=1)` (1 flit), `self.phit_width = config.width` (128 B/cyc), `self.wire_delay = config.delay` (0.5 cyc LT), `self.credits_out = simpy.Container(env, init=1, capacity=1)` (credit tokens held by *receiver*, decremented on send, returned when flit consumed) — wait, simpler: credit is tracked by the receiver's buffer, sender waits on `credit_event` before each flit. Remove `NoCDist`. |
| `calc_latency(msg)` | Sleep `msg_size/bandwidth + delay` cycles, then `store.put(msg)` | **Removed.** Replaced by `send_flit(flit)` process: sleep `serialization_cycles` (~4.3 cyc for one flit) + wire_delay, then `flit_buffer.put(flit)` |
| `put(msg)` | Append Event, start `calc_latency` process | **Split.** `send_msg(msg)` is a higher-level method called by endpoints (Core/DMA) that: (1) computes flit_count, (2) creates HEAD flit, (3) calls `send_flit(head)`, (4) creates BODY flits, calls `send_flit` each (with credit waits), (5) creates TAIL flit, calls `send_flit`. `put()` is replaced by `send_flit(flit)` for flit-level forwarding between routers. |
| `get()` | `self.store.get()` → returns Message | `self.flit_buffer.get()` → returns Flit |
| `len()` | `len(self.store.items)` | `len(self.flit_buffer.items)` |
| credit return | None | After receiver consumes a flit from `flit_buffer`, it calls `link.return_credit()` which fires an event the sender is waiting on |
| Events | Record per-message event on put | Record per-flit start/end events OR per-message event with flit_count (message-level is sufficient for tracing; add flit_count to Event) |
| `change_delay/recover_delay` | Multiply delay_factor | Keep same interface — multiply serialization time by delay_factor for fail-slow |
| `bind()` | Keep as-is | Keep `corefrom`, `coreto`, `tag`, `corefromid`, `coretoid` for event tracing |
| Remove | `self.rate`, `self.shape`, `self.para_dist`, `self.hop_num` | Dead code |

Design decision: Link operates as a **1-flit-deep pipeline**. At any moment, exactly one flit can be in flight on the link (since serialization + wire delay ≈ 4.8 cycles and buffer_depth=1, this models a single-flit pipeline naturally). The credit system ensures the sender doesn't inject a flit until the receiver has buffer space.

##### Step 2c. Router port system rewrite (≈50 lines changed in __init__, new bind_port method)
The Router changes from hardcoded 5 ports (N/S/E/W/'core') to dynamic integer port keys.

Key changes to [Router](noc.py) constructor and port binding:
| Item | Old | New |
|---|---|---|
| `self.links` dict | Hardcoded keys: `Direction.NORTH/SOUTH/EAST/WEST` and `'core'` (string) | Dynamic dict: keys are **integer port IDs**. Local ports: 0 (PE), 3/4 (DDR WDMA), 7 (DDR RDMA), 8 (DDR WDMA), 10/11 (GM WDMA), 14 (GM RDMA), 15 (GM WDMA). Direction ports: 100 (N), 101 (S), 102 (E), 103 (W). Init with direction ports only; local ports are added via `bind_port()`. |
| Config storage | `self.virtual_channel = config.vc`, `self.routing_algorithm = config.type`, `self.per_hop_time=1`, `self.start_up_time=1` | `self.vc=config.vc`, `self.routing_algorithm=config.type`, `self.flit_config = config.flit`, `self.pipeline = config.pipeline` (for latency lookup), `self.x_dim=x`, `self.y_dim=y` (mesh dimensions needed for X-fast-path check) |
| New state | None | `self.crossbar_reservations: Dict[int, int]` (msg_id → out_port) — wormhole path reservation; `self.rr_pointer: int = 0` — round-robin arbitration pointer; `self.output_busy: Dict[int, bool]` — per-output-port busy flag; `self.flit_in_flight: Dict[int, Flit]` — flit currently being routed per input |
| `bind_link(direction, link_in, link_out)` | Directly assigns to `self.links[direction]` | **Keep as wrapper** that maps Direction → port ID and calls `bind_port()` |
| `bind_port(port_id, link_in, link_out)` | Does not exist | **New method.** Adds entry to `self.links[port_id] = {'in': link_in, 'out': link_out}`; initializes `output_busy[port_id] = False` |
| `router_fail/router_recover` | Iterates `Direction` 4 dirs + ignores 'core' | Iterate all ports in `self.links`, apply change_delay/recover_delay to each |

##### Step 2d. Router routing logic update (≈40 lines changed)
Modify `calculate_next_router()` and add flit routing helpers.

| Method | Old | New |
|---|---|---|
| `calculate_next_router(target_id)` → (Direction, next_id) | XY routing returning Direction enum | Same XY logic (X-first, verified correct for 4×8 non-square mesh), but returns **(out_port_id, next_router_id)**. If dst is a direction neighbor, out_port_id = direction_to_port(Direction.EAST/etc.). If target is this router (dst_router == self.id), out_port_id is the flit's dst_local_port. |
| `route(msg, next_dir, next_router)` | `links[next_dir]['out'].put(msg)` | **Removed/replaced** by `forward_flit(flit, out_port_id)` which calls `self.links[out_port_id]['out'].send_flit(flit)` |
| `route_core(msg)` | `links['core']['out'].put(msg)` | Merged into `forward_flit` (port 0 is PE) |
| `routing(msg)` | Checks `msg.dst == self.id`, else timeout(per_hop_time) + route | **Removed** — flit-level routing done inside run() loop for HEAD flits |
| `to_xy(id)`/`to_id(x,y)` | Keep | Keep unchanged |
| X-fast-path check | None | New helper `is_x_fast_path(out_port_id)`: returns True if self.y is 0 or 7 AND out_port is EAST or WEST (Y=0/7 rows have optimized X links) |
| `get_latency_for_head(out_port_id)` | N/A | Returns `pipeline.x_fast_hop_cycles` if X-fast-path, `pipeline.y_hop_cycles` if out_port is N/S direction, else `pipeline.head_hop_cycles` for regular X hop |
| Other topology routing (Torus_XY, RingRoad, Dragonfly) | Present | **Mark as legacy** — they won't work with flit model initially; wrap with comment that only Mesh XY is flit-level. They won't be called since ADA2S only uses Mesh. |

##### Step 2e. Router.run() flit-level main loop (≈120 lines, the biggest change)
This is the core behavioral change. Replace the message-level any_of loop with a flit-level event loop.

**Old run() loop behavior (lines 146-183):**
1. Build list of all in-link `get()` events
2. `any_of` → wait for any link to have a message
3. Take the message, mark event end_time
4. Start `routing(msg)` process
5. Drain the same link for any other queued messages
6. Repeat

**New run() loop behavior (flit-level wormhole):**
```
while True:
    # Phase 1: Collect flits arriving at input buffers
    events = {}
    for port_id, port_links in self.links.items():
        if port_links['in'] is not None and port not blocked:
            events[port_id] = port_links['in'].get()
    
    if any events triggered:
        # Round-robin select among triggered inputs (fairness)
        selected_port = rr_select(triggered_ports, self.rr_pointer)
        flit = events[selected_port].value
        
        # Mark event completion for tracing
        record_flit_arrival(flit, port_id)
        
        # Phase 2: Route based on flit type
        if flit.flit_type == HEAD:
            # Compute output port (XY routing)
            out_port = route_head_flit(flit)
            hop_delay = get_latency_for_head(out_port)
            
            # Arbitrate: wait until output port is free
            yield wait_until_output_free(out_port)
            
            # Reserve crossbar path
            self.crossbar_reservations[flit.msg_id] = out_port
            self.output_busy[out_port] = True
            
            # Pipeline delay (RC+SA+ST)
            yield env.timeout(hop_delay)
            
            # Forward HEAD flit
            yield env.process(forward_flit(flit, out_port))
            self.output_busy[out_port] = False
            
        elif flit.flit_type == BODY:
            # Look up reserved output port
            out_port = self.crossbar_reservations[flit.msg_id]
            
            # Wait for output serialization (body flits follow at serialization rate)
            yield wait_until_output_free(out_port)
            self.output_busy[out_port] = True
            yield env.timeout(self.pipeline.body_hop_cycles)
            yield env.process(forward_flit(flit, out_port))
            self.output_busy[out_port] = False
            
        elif flit.flit_type == TAIL:
            out_port = self.crossbar_reservations[flit.msg_id]
            yield wait_until_output_free(out_port)
            self.output_busy[out_port] = True
            yield env.timeout(self.pipeline.body_hop_cycles)
            yield env.process(forward_flit(flit, out_port))
            self.output_busy[out_port] = False
            
            # Release reservation
            del self.crossbar_reservations[flit.msg_id]
        
        # Advance RR pointer
        self.rr_pointer = (self.rr_pointer + 1) % len(active_ports)
    else:
        # No flits arriving — yield for a small time
        yield env.timeout(1)
```

Wait — there's a subtlety. In wormhole routing, flits from the SAME message must follow each other through the router without interleaving. Once a HEAD establishes a path, BODY/TAIL for that msg_id must follow immediately through the same crossbar path, and they cannot be blocked by other flits at the SAME input port (because they come in order and the input buffer is only 1 flit deep — the sender won't send the next flit until credit returns).

But **different input ports** can compete for the same output port — that's where RR arbitration matters (HoL blocking is a consequence of this).

Let me refine the loop design:

Actually, the correct model for a 1-VC, 1-flit-buffer wormhole router is simpler than what I outlined above:

1. Each clock cycle (or event-driven), look at all input ports
2. If an input has a flit (from `flit_buffer.get()` event):
   - For HEAD: route it, if output is free, reserve and forward; if output busy and this is a different message than the one currently reserving that input, **block** (wait for output to become free, which stalls the input — this creates backpressure via credits)
   - For BODY/TAIL: they have a reserved path, forward immediately when the link is free (no arbitration needed because only one message can have a reservation on a given output from a given input at a time)
3. Since we're event-driven (simpy), we don't tick cycle-by-cycle. Instead we use events: when an output becomes free, wake up blocked inputs.

**Simplified correct approach for simpy:**
- Each input port runs its own forwarder process (like old model but per-port instead of per-router)
- The forwarder gets flits one by one from the in-link
- HEAD: compute route, request output port (RR arbiter), after grant → sleep pipeline_delay → send flit out, then hold output port reservation until TAIL passes
- BODY/TAIL: sleep body_delay → send flit out, TAIL releases reservation

This naturally handles wormhole blocking because the forwarder process for a blocked HEAD will be waiting on the output arbiter, and subsequent flits on the same in-link queue up behind it.

##### Step 2f. NoC class: build_connection_mesh for 8×4 + local port binding (≈80 lines changed)
| Method | Old | New |
|---|---|---|
| `__init__` | Stores `x,y,router_config,link_config`, `r2r_links=[], routers=[]` | Add `self.dma_engines_config = config.dma_engines`, `self.mem_controllers_config = config.mem_controllers`, `self.c2r_links = []` |
| `build_connection_mesh()` | Creates `x*y` routers, builds E/W and N/S r2r links with `Direction` binding | Create 32 routers (0-31) with new constructor (passing flit_config, pipeline_config, x_dim=4, y_dim=8). Build r2r links same as before (E/W ±1, N/S ±x) but using flit-level Link with width=128, delay=0.5. **Do NOT bind PE port here** — that will be done by architecture.py via `bind_port()`. Actually wait — need to think about this. Currently architecture.py creates c2r links and binds them to 'core' direction. We should either: (a) keep that pattern and have NoC provide `bind_local_port()` method, or (b) have NoC create PE ports too. Going with (a) for minimum change to architecture.py in this step. |
| New method: `bind_local_port(router_id, port_id, link_in, link_out)` | N/A | Calls `self.routers[router_id].bind_port(port_id, link_in, link_out)` |
| Other topologies (torus/ringroad/dragonfly) | Present | Keep but don't update for flit-level (they won't be called for ADA2S Mesh). Add `# LEGACY: not yet updated for flit-level model` comment. |
| `get_layer()`, `get_ring_nodes_ordered()`, ring/dragonfly helpers | Present | Keep unchanged (used by legacy topologies) |

##### Step 2g. Remove NoCDist dependency / update distribution.py references (≈5 lines)
- Remove `from .distribution import NoCDist` from noc.py
- distribution.py `NoCDist` class stays but is unused by NoC path (can be cleaned up later or kept for other purposes)

---

#### Validation after Step 2 (before endpoints):

After noc.py is rewritten but before modifying core.py/dma.py, write a small standalone probe test that:
1. Creates a minimal 8×4 NoC with flit-level Links/Routers
2. Directly injects flits into router 0's PE port (port 0), sends to router 31's PE port
3. Measures end-to-end latency for single-flit messages
4. Verifies hop-by-hop latency matches head_hop_cycles + body_hop_cycles
5. Tests credit backpressure (saturate a link, verify second message is delayed)

---

### Phase 3: Endpoint Models (Core NMC channels + DMA nodes)

#### 6. `core.py` — NMC dual-channel + SRAM model

| Method/Attr | Current | New |
|-------------|---------|-----|
| `ScratchpadMemory` | Single SPM with size+delay | Split into `LocalSRAM` (3 MB, 128B-aligned access) and `WeightSRAM` (16 MB, 4KB-aligned); both share same PE-internal SRAM port bandwidth |
| `self.data_out/data_in` | Single pair of Links to router core port | Replace with **NMC channels**: `self.nmc_ch: List[Dict]` with two entries (CH0, CH1), each containing `{'upload_link': Link, 'download_link': Link}` — two independent upload links to port 0 (CH0 uses one logical unit, CH1 uses another on same physical router port). Physical sharing modeled via `self.nmc_sram_port: simpy.Container` or `simpy.Resource(capacity=1)` with rate limiter. |
| NMC SRAM port model | None (LSU does all serialization) | Add `self.nmc_port_budget: simpy.Container` or a rate-limiting Resource: aggregate bandwidth ~106 B/cyc shared by upload+download on both channels. Model as a `simpy.Resource` with a token bucket: each transfer consumes `bytes/106.0` cycles of port time; channels compete fairly. |
| LSU model | `LSU.width=4 B/cyc`, capacity=4 (4 concurrent?) | LSU is replaced/augmented by NMC model: NMC channels are the path to NoC; LSU handles SRAM-internal access (to Local/Weight SRAM). LSU width should match SRAM port bandwidth for on-PE accesses; NMC handles off-PE traffic. |
| TPU model | `TPU.flops=1024` | Parameterize based on actual Matrix Core FLOPs (TBD — leave configurable, default to a reasonable value like `flops=4096` for 4 TPC clusters). Not critical for NoC correctness but affects compute/comm overlap ratio. |
| NMC startup latency | Not modeled | Add `nmc_startup: int = 80` cycles for static-shape transfers, `125` for dynamic; before each upload/download, yield timeout(startup) to model NMC descriptor programming and outer-sync ACQUIRE wait. |
| `bind_with_router()` | Takes single data_in/data_out | Takes list of (upload_link, download_link) per channel; binds all to router port 0 (PE port). Since both CH0 upload and CH1 upload share one physical injection port at the router, the router port 0 out link must have capacity for both channels (this is where the NMC shared-port budget comes in). |
| `execute()` | Single loop: schedule → start comp/comm/io tasks → any_of wait → process done | Modified: (1) comm tasks can be on either CH0 or CH1 — scheduler assigns to idle channel; (2) before SEND, allocate NMC port bandwidth budget; (3) add NMC startup latency; (4) SEND splits message into flits implicitly via Link; (5) RECV waits on download_link.get() for flits (the endpoint reassembles flits into messages, but for scheduling we only care about message-level completion: TAIL flit arrival triggers RECV completion); (6) support dst_type PE/GM/DDR: message routed to correct router+local_port |
| SEND task execution | Constructs an addressed Message through `EndpointRegistry` | (1) resolve the destination endpoint and selected local port; (2) compute flit count; (3) wait for NMC channel idle; (4) wait for NMC startup latency; (5) allocate NMC SRAM port budget for payload bytes; (6) packetize and send flits through the selected upload lane |
| RECV task execution | data_in.get() | Wait for download_link.get() (message reassembled at endpoint), trigger post-receive processing |
| Fail-slow (`tpu_fail/lsu_fail`) | Divide flops/width | Add `nmc_fail(times)`: divide nmc SRAM port bandwidth by factor |
| `events` tracking | Per-message event objects | Add flit tracking for link utilization (but trace/Event remains message-level for simplicity; link-level flit counts tracked inside Link) |
| Scheduler | Works on `core_id` integer | Scheduler needs to know about non-PE destinations (GM/DDR) to push SEND tasks correctly; `update()` checks child node's `dst_type` to route to correct endpoint (PE core or DMA node) |

#### 7. `dma.py` — **NEW FILE** (DMA engine model)

```
dma.py
├── DMAEngine class (generic multi-channel DMA)
│   ├── __init__(env, config: DMAEngineConfig)
│   │   ├── self.width = config.port_bw * config.clock_scale (B/cyc)
│   │   ├── self.channels = simpy.Store(capacity=channels) — channel tokens
│   │   ├── self.dispatch = simpy.Resource(capacity=1) — scalar core serial
│   │   └── self.delay_factor = 1 (fail-slow)
│   ├── transfer(data_bytes): SimPy process
│   │   ├── yield dispatch request (1 cycle scalar issue)
│   │   ├── ch = yield channel_store.get()
│   │   ├── yield timeout(ceil(data_bytes / self.width) * delay_factor + cdc_penalty)
│   │   └── channel_store.put(ch)
│   └── change_delay(times) / recover_delay(times)
│
├── DMANode class (endpoint attached to router)
│   ├── __init__(self, env, config: DMAEngineConfig, noc_ref)
│   │   ├── self.engine = DMAEngine(env, config)
│   │   ├── self.node_type = NodeType.GM_RDMA/GM_WDMA/etc
│   │   ├── self.local_ports = config.local_ports
│   │   ├── self.in_links / self.out_links (per channel)
│   │   ├── self.receive_queue = simpy.Store (incoming messages)
│   │   ├── self.events = [] (for tracing)
│   │   └── env.process(self.run())
│   ├── bind_links(port_links_dict): attach to router local ports
│   ├── run(): main loop
│   │   ├── For WDMA (NoC→memory): wait on all in_links for incoming flits
│   │   │   (TAIL flit marks complete message)
│   │   │   → assemble message → allocate mem_controller BW → engine.transfer()
│   │   │   → if writeSum atomic, yield mem atomic unit
│   │   ├── For RDMA (memory→NoC): wait for read requests (from outer-sync or
│   │   │   DMA-initiated sends) → allocate mem BW → engine.transfer() → send
│   │   │   response via out_links
│   │   └── Credit management: return credits after flit consumption
│   ├── send(msg): construct flits and transmit (RDMA push or WDMA response)
│   └── fail(times) / recover(times): bandwidth degradation
│
└── create_dma_nodes(env, noc, arch_config) factory function
    └── Creates 16 DMANode instances per config, binds to routers
```

Key simplifications for DMA modeling:
- DMA nodes do NOT run compute tasks, only transfer data.
- WDMA is a sink: messages arrive, consume memory bandwidth, are consumed (optionally with atomic add).
- RDMA is a source: initiated by DMA scalar core programs, injects data into NoC.
- DMA scalar core dispatch is modeled as a 1-cycle Resource bottleneck for each transfer initiation.
- DDR CDC penalty = 5 fixed cycles per transfer.
- DMA nodes attach to the same routers as PEs (no dedicated routers). The router's local ports connect to both PE and DMA — each gets its own in/out Link pair to the same router (router already supports multiple local ports).

---

### Phase 4: Assembly & Wiring

#### 8. `architecture.py` — Assemble 8×4 system with DMA

| Method | Current | New |
|--------|---------|-----|
| `__init__` | Builds NoC + cores, no DMA | Add `self.dma_nodes: List[DMANode] = []`, `self.mem_controllers: List = []` (if modeling MC) |
| `build_noc()` | Factory for 4 topologies | Default to Mesh 8×4; pass dma_engines config to NoC |
| `build_cores()` | Creates x*y cores, binds single data_in/data_out to router 'core' port | Create 32 cores (x=4,y=8); create 2 NMC channels per core (CH0: links ch0_up/ch0_dn, CH1: ch1_up/ch1_dn); bind all 4 links to router port 0 using new multi-port binding; c2r links width=128; configure NMC with shared SRAM port budget |
| New method: `build_dma_nodes()` | None | Call `create_dma_nodes(env, self.noc, arch_config)`; for each DMANode, bind its port links to the corresponding router's local port(s) via `router.bind_port(port_id, link_in, link_out)`; handle WDMA dual-channel (two port bindings per instance) and RDMA single-channel. |
| New method: `build_mem_controllers()` | None | For GM and DDR: create memory controller models (simple bandwidth-limited sink/source); connect to DMA engines; atomic writeSum uses dedicated atomic path. |
| `initialize()` | Pushes zero-degree nodes to core schedulers | Also initialize DMA-initiated transfers (e.g., GM_RDMA initial weight loading); push DMA tasks to DMA node queues. |
| `execute()` | Calls run_fail_slow(), then env.run() | Also start DMA node `run()` processes; add DMA fail handlers. |
| `run_fail_slow()` | Handles router/link/lsu/tpu failures | Add handlers for `dma_fail` (DMANode.fail(times)) and `mem_fail` (MC bandwidth reduction). |
| `link_fail/router_fail` | Present | Keep unchanged — work on direction links which are now flit-level Links |
| New: `dma_fail/mem_fail` | None | Coroutine: wait until start_time → apply bandwidth reduction → wait until end_time → recover |

#### 9. `utils/mapper.py` — DFG generation for PE↔GM/DDR

| Method | Current | New |
|--------|---------|-----|
| Default mesh size | x=4, y=4 in places | x=4, y=8 (x_size=4, y_size=8) per ADA2S; verify all loops use `self.x_size/self.y_size` not hardcoded |
| `xy2x(x,y)` | `y * self.x_size + x` | Correct formula already (fixed); verify default x_size=4,y_size=8 |
| `gen_dfg()` | Generates SEND/RECV between PEs only | For each output binding, determine destination type: (a) if dst is another PE → SEND/RECV as before; (b) if dst is GM → generate SEND to GM_WDMA on router=28+(dst_col) with local_port=10/11; (c) if dst is DDR → SEND to DDR_WDMA on corner router with local_port=3/4; generate corresponding RECV on DMA side (DMA tasks, not DFGNodes on PEs); for input loading, generate GM_RDMA→PE RECV nodes for initial data, or DDR_RDMA→PE. |
| `_select_sender_cover()` | Finds PE sender covering a slice | Extend to find GM_RDMA/DDR_RDMA as sender when source is memory. |
| `update()` | Pushes child tasks to `cores[child.core_id]` | Check child.dst_type: if PE → push to core scheduler; if GM/DDR → push message to DMA send queue (DMA node is always ready to receive writes). Wait — SEND to DMA doesn't need a peer RECV DFGNode in our model; DMA nodes just consume data. So SEND→GM is a terminal node (no child), or the child is a DMA-side transfer. |
| Remap actions (shift/split/replace/remove) | Work on core_id | Core IDs are 0-31 in 8×4; actions still work. Add validation that destination core_id exists (0-31). For PE↔GM/DDR paths, remapping doesn't move DMA nodes (fixed at routers 28-31/0/3/28/31). |
| `zero_degree()` | Returns DFGNodes with no parents | Include initial LOAD nodes that read from GM/DDR (their "parent" is the DMA node, which is implicit). |

#### 10. `utils/task.py` — Task execution with multi-destination support

| Component | Current | New |
|-----------|---------|-----|
| Task priority dict | STORE=0, SEND=1, CONV/POOL/FC=2, LOAD/RECV=3 | Split STORE into LOCAL_STORE, GM_STORE, DDR_STORE; add DMA_SEND, DMA_RECV for DMA-initiated tasks |
| `execute(core)` | Dispatches by OperatorType | For SEND: check node.dst_type → PE SEND uses NMC upload link; GM_STORE/DDR_STORE also uses upload link but dst_router/dst_local_port point to DMA; LOAD from GM/DDR: use NMC download, waiting for DMA push. |
| `SEND.execute` | Constructs a PE-to-PE Message using addresses from `EndpointRegistry` | Resolve PE/GM/DDR endpoints through the registry, select an explicit local port for multi-port DMAs, then construct Message with resolved source/destination addresses, transfer type, and channel assignment. Do not recompute router IDs in task code. |
| `RECV.execute` | Wait on core.data_in.get() | Wait on core.nmc_ch[ch]['download_link'].get(); support selecting channel. |
| `CONV/POOL/FC flops` | FLOP count formulas | Keep FLOP models; TPU bandwidth will be parameterized. |
| `STORE.execute` | lsu.occupy → spm.release | Add GM_STORE/DDR_STORE cases that go through NMC upload to memory. |
| New: DMA-side task types | None | DMA nodes don't use the Task class directly (they have their own run loop); but we may add simple DMAReadTask/DMAWriteTask for DMA-initiated transfers. |

#### 11. `tracing.py` — Flit-aware event tracing

| Function | Current | New |
|----------|---------|-----|
| `process_events()` | Collects core_events and link_events per time window | Also collect `dma_events` from DMANode.events; `mem_events` from memory controllers |
| `TraceItem` | {id, slow, ultilization, op_num} | Add `node_type` field to distinguish PE/GM_RDMA/GM_WDMA/DDR_RDMA/DDR_WDMA |
| `get_slice_events` | Works on Event list | No change needed — Event.type distinguishes operator types; new DMA transfer events will have proper types |
| Link utilization | Calculated from Event start/end times | With flit-level model, link utilization should be calculated from flit transmission events. Link.events records per-flit start/end (or aggregate per-message is sufficient for coarse-grained utilization, but flit-level gives more accurate buffer occupancy stats). Keep message-level aggregation for now and add per-link flit count for accuracy. |

#### 12. `distribution.py` — Deterministic model

| Component | Current | New |
|-----------|---------|-----|
| `NoCDist` (Gamma) | Used in Link.calc_latency to vary bandwidth | **Removed from NoC path.** Links operate at fixed serialization rate (4.3 cyc/flit). Gamma jitter was modeling unmodeled effects that are now captured explicitly (bubbles in serialization_cycles=4.3, contention via credit/RR arbitration). Keep `CoreDist` for core compute jitter if needed; or make it deterministic (fixed compute time) for reproducibility. |
| `failslow_prob()` | Used by predictor | Keep for predictor, but rename or mark as "post-simulation analysis" only. |

---

### Phase 5: Entry Point & Configs

#### 13. `run.py` — Wire up new default config

| Section | Current | New |
|---------|---------|-----|
| Default config | `gemini4_4.json` (4×4) | Default to new `ada2s_32.json` (8×4 with full DMA) |
| `simulate()` | Collects core_events, link_events | Also collect dma_events, mem_events; pass to process_events; include DMA/mem nodes in traces |
| Cycle-to-time conversion | Implicit (cycles only) | When reporting µs: `cycles / 1125e6` for ACI domain, `cycles / 1150e6` for DDR domain (Trace events tagged with clock domain) |
| `detect()` predictor call | Passes core_probs/link_probs shaped for 4×4 | Pass updated shapes for 8×4 + DMA nodes |
| `__main__` entry | Uses darknet19-4-4.json mapping | Add ada2s_32 config + appropriate workload mapping (need to generate or supply 8×4 mapping JSON) |

#### 14. 🆕 `configs/instances/ada2s_32.json` — New hardware config

```json
{
  "core": {
    "type": "Simple",
    "x": 4, "y": 8,
    "width": 128,
    "blk_size": 128,
    "local_spm": { "size": 3145728, "delay": 1 },
    "weight_spm": { "size": 16777216, "delay": 1 },
    "tpu": { "size": 1, "flops": 4096 },
    "lsu": { "size": 1, "width": 106 },
    "nmc": {
      "channels": 2,
      "sram_port_bw": 106.0,
      "startup_static": 80,
      "startup_dynamic": 125,
      "channel_bw_frac": 0.98
    }
  },
  "noc": {
    "type": "Mesh",
    "x": 4, "y": 8,
    "router": {
      "type": "XY",
      "vc": 1,
      "arbitration": "round_robin",
      "flit": {
        "phit_width": 128, "flit_size": 512,
        "header_bytes": 12, "body_overhead": 4,
        "buffer_depth_flits": 1,
        "serialization_cycles": 4.3,
        "credit_rtt_cycles": 17
      },
      "pipeline": {
        "rc_cycles": 1.0, "sa_cycles": 2.0,
        "st_cycles": 1.0, "lt_cycles": 0.5,
        "credit_overhead": 1.0,
        "head_hop_cycles": 4.5,
        "body_hop_cycles": 4.3,
        "x_fast_hop_cycles": 5.5,
        "y_hop_cycles": 8.5
      }
    },
    "link": { "width": 128, "delay": 0.5 },
    "dma_engines": [
      { "dma_type":"GM_RDMA","instance_id":0,"router_id":28,"channels":1,
        "local_ports":[14],"port_bw":106.0,"clock_scale":1.0,"cdc_penalty":0,"dispatch_interval":1 },
      { "dma_type":"GM_RDMA","instance_id":1,"router_id":29,"channels":1,
        "local_ports":[14],"port_bw":106.0,"clock_scale":1.0,"cdc_penalty":0,"dispatch_interval":1 },
      { "dma_type":"GM_RDMA","instance_id":2,"router_id":30,"channels":1,
        "local_ports":[14],"port_bw":106.0,"clock_scale":1.0,"cdc_penalty":0,"dispatch_interval":1 },
      { "dma_type":"GM_RDMA","instance_id":3,"router_id":31,"channels":1,
        "local_ports":[14],"port_bw":106.0,"clock_scale":1.0,"cdc_penalty":0,"dispatch_interval":1 },
      { "dma_type":"GM_WDMA","instance_id":0,"router_id":28,"channels":2,
        "local_ports":[10,11],"port_bw":92.0,"clock_scale":1.0,"cdc_penalty":0,"dispatch_interval":1 },
      { "dma_type":"GM_WDMA","instance_id":1,"router_id":29,"channels":2,
        "local_ports":[10,11],"port_bw":92.0,"clock_scale":1.0,"cdc_penalty":0,"dispatch_interval":1 },
      { "dma_type":"GM_WDMA","instance_id":2,"router_id":30,"channels":2,
        "local_ports":[10,11],"port_bw":92.0,"clock_scale":1.0,"cdc_penalty":0,"dispatch_interval":1 },
      { "dma_type":"GM_WDMA","instance_id":3,"router_id":31,"channels":2,
        "local_ports":[10,11],"port_bw":92.0,"clock_scale":1.0,"cdc_penalty":0,"dispatch_interval":1 },
      { "dma_type":"DDR_RDMA","instance_id":0,"router_id":0, "channels":1,
        "local_ports":[7], "port_bw":91.0,"clock_scale":0.978,"cdc_penalty":5,"dispatch_interval":1 },
      { "dma_type":"DDR_RDMA","instance_id":1,"router_id":28,"channels":1,
        "local_ports":[7], "port_bw":91.0,"clock_scale":0.978,"cdc_penalty":5,"dispatch_interval":1 },
      { "dma_type":"DDR_RDMA","instance_id":2,"router_id":3, "channels":1,
        "local_ports":[7], "port_bw":91.0,"clock_scale":0.978,"cdc_penalty":5,"dispatch_interval":1 },
      { "dma_type":"DDR_RDMA","instance_id":3,"router_id":31,"channels":1,
        "local_ports":[7], "port_bw":91.0,"clock_scale":0.978,"cdc_penalty":5,"dispatch_interval":1 },
      { "dma_type":"DDR_WDMA","instance_id":0,"router_id":0, "channels":2,
        "local_ports":[3,4],"port_bw":108.0,"clock_scale":0.978,"cdc_penalty":5,"dispatch_interval":1 },
      { "dma_type":"DDR_WDMA","instance_id":1,"router_id":28,"channels":2,
        "local_ports":[3,4],"port_bw":108.0,"clock_scale":0.978,"cdc_penalty":5,"dispatch_interval":1 },
      { "dma_type":"DDR_WDMA","instance_id":2,"router_id":3, "channels":2,
        "local_ports":[3,4],"port_bw":108.0,"clock_scale":0.978,"cdc_penalty":5,"dispatch_interval":1 },
      { "dma_type":"DDR_WDMA","instance_id":3,"router_id":31,"channels":2,
        "local_ports":[3,4],"port_bw":108.0,"clock_scale":0.978,"cdc_penalty":5,"dispatch_interval":1 }
    ],
    "mem_controllers": [
      { "mem_type":"GM", "aggregate_bw":512.0, "instances":[0,1,2,3], "atomic_supported":true, "atomic_bw":106.0 },
      { "mem_type":"DDR","aggregate_bw":465.0, "instances":[4,5,6,7], "atomic_supported":false }
    ]
  },
  "mem": { "width": 128, "delay": 5 }
}
```

(Port BW values derived from: GM_WDMA 92 B/cyc ≈ 104 GB/s per channel from §9.9; GM_RDMA 106 B/cyc ≈ 119 GB/s; DDR_RDMA 91 B/cyc ≈ 103 GB/s; DDR_WDMA 108 B/cyc ≈ 122 GB/s. These are starting values to be calibrated.)

#### 15. 🆕 `configs/instances/ada2s_normal.json` — No-failure config for 8×4

Same structure as existing `normal.json` but with empty DMA/mem failure lists.

#### 16. Existing 4×4 configs

Keep `gemini4_4.json`, `dragonfly4_4.json`, `torus4_4.json`, `ringroad4_4.json`, `tpu.json` as legacy/reference — they won't work with new flit-level model without updating their schema. Either (a) update them to new schema with flit/pipeline configs, or (b) move to `configs/instances/legacy/` and keep only ADA2S config as primary. **Recommendation: move to legacy/ and keep only ada2s_32 as primary config.**

---

### Phase 6: Predictor/Embedding Updates (ML-side, can be deferred)

#### 17. `predictor/data_loader.py`

| Component | Current | New |
|-----------|---------|-----|
| Mesh size | 4×4 (x=4,y=4) in `Mesh.__init__` | x=4, y=8, 32 cores; links 4×(8-1)+8×(4-1)=52 bidirectional directional links + 32 c2r links + 48 DMA links = larger graph |
| `_core_id(i,j)` | `j*self.x+i` | Same formula, but x=4,y=8 so IDs 0-31 |
| `_build_links()` | East(i+1), North(i+x) | Add DMA attachment links (router 28-31 connect to GM_RDMA/GM_WDMA; routers 0,3,28,31 connect to DDR_RDMA/DDR_WDMA); node count becomes 48 (32 PE + 16 DMA) |
| `manhattan_path_nodes` | XY path on 4×4 | XY path on 8×4; X fast path not modeled in predictor (predictor uses simple hop count which is fine for GNN features) |
| Node features | Core-only | Add node_type feature (0=PE, 1-4=DMA types) |
| `TraceDatasetBuilder` | Reads core_events, link_events | Also read dma_events |

#### 18. `predictor/predictor.py`

| Component | Current | New |
|-----------|---------|-----|
| Display coords | `divmod(core_id, self.mesh_y)` / `% mesh_x, // mesh_x` | mesh_x=4, mesh_y=8; formulas already correct with `%` and `//` |

#### 19. `embedding/hw_encoder.py`

| Component | Current | New |
|-----------|---------|-----|
| Router features | r_x, r_y from to_xy | Add node_type, port_count (number of local ports — corner/bottom routers have more) |
| Link features | src_id % noc.x and // noc.x for coords | x=4; links include DMA links; add link_type feature (directional=0, c2r=1, c2dma=2) |

#### 20. `embedding/action_decoder.py`

| Component | Current | New |
|-----------|---------|-----|
| Action space | src_core, dst_core, action_type | Core IDs 0-31 (8×4); actions don't target DMA nodes (DMA positions are fixed) |

---

### Phase 7: Cleanup & Compatibility

#### 21. `configs/schemas/__init__.py`, `embedding/__init__.py`, `predictor/__init__.py`, `utils/__init__.py`, `__init__.py`

No functional changes needed — may need to export new types (NodeType, FlitType, Flit) from definitions.

#### 22. `utils/mapper_old.py`

Legacy mapper — either update coordinate defaults to 4×8 or mark as deprecated. Recommend leave as-is (not used in main path).

#### 23. `utils/timing_logger.py`

No changes needed.

#### 24. Remove `match/case` syntax

noc.py currently uses `match/case` in Router.run() which is Python 3.10+. If target env is 3.9, replace with if/elif. Otherwise keep.

---

### Files NOT Modified (and why)

| File | Reason |
|------|--------|
| `configs/instances/normal.json` | Replace with `ada2s_normal.json` (old kept as legacy) |
| `configs/instances/tpu.json` | Failure config for tpu test, kept as legacy |
| `predictor/model_hetero.py` | GNN architecture is size-agnostic (adapts to node count); only input feature dim may need update |
| `predictor/predict.py` | Thin wrapper; no logic changes |
| `rl_agent/` (external) | In parent directory, not in simulator_detailed; will need separate update to use new API |
| `profiling_sim/` (external) | Separate simulator; this plan only covers simulator_detailed |

---

## Implementation Order (fine-grained)

```
Phase 1: Types & Config          ← DONE ✅
  1a. arch_config.py             ← commit 8a83656
  1b. definitions.py             ← commits 66af38f, 6b3bfae
  1c-1f. failure/mapping/JSON    ← deferred until needed

Phase 2: Flit-Level NoC          ← DONE ✅
  2a. Imports & cleanup          (~10 lines)
       - Remove NoCDist/contextlib imports
       - Add definitions imports
  2b. Link class rewrite         (~80 lines)
       - Flit buffer (1 flit deep) instead of message store
       - send_flit() with serialization delay
       - send_msg() for message-to-flit splitting
       - Credit-based flow control
       - Remove Gamma jitter
       - Verify: single link flit serialization = 4.3 cyc
  2c. Router port system         (~50 lines)
       - Integer port keys (0-15 local, 100-103 dir)
       - bind_port() method
       - Wormhole reservation state
       - RR arbitration pointer
  2d. Router routing logic       (~40 lines)
       - calculate_next_router returns port_id
       - X-fast-path detection
       - get_latency_for_head()
       - forward_flit() helper
       - Legacy topologies marked
  2e. Router.run() main loop     (~120 lines, biggest)
       - Per-input-port forwarder processes
       - HEAD: route + arbitrate + pipeline delay + reserve
       - BODY/TAIL: pipeline through reservation
       - TAIL: release reservation
       - Credit return on flit consumption
  2f. NoC build & mesh wiring    (~80 lines)
       - build_connection_mesh 8×4
       - bind_local_port() interface
       - Legacy topologies marked
  2g. Distribution cleanup       (~5 lines)
       - Remove NoCDist import
       - Standalone probe test to verify basic flit routing

Phase 3: Core NMC model          ← NEXT
  3a. core.py NMC dual-channel + SRAM port budget (~100 lines)
  3b. core.py SEND/RECV via flit-level Link (~80 lines)
  3c. core.py startup latency + fail-slow (~20 lines)

Phase 4: DMA nodes               ← NEW FILE
  4a. dma.py DMAEngine + DMANode (~150 lines)
  4b. architecture.py wiring (build_dma_nodes, bind to routers) (~80 lines)

Phase 5: Assembly & DFG
  5a. architecture.py 8×4 core/NMC binding (~60 lines)
  5b. mapper.py PE→GM/DDR DFG generation (~60 lines)
  5c. task.py multi-dst SEND/RECV (~50 lines)
  5d. tracing.py dma_events (~30 lines)
  5e. distribution.py NoCDist removal (~5 lines)

Phase 6: Entry point
  6a. ada2s_32.json config (NEW)
  6b. ada2s_normal.json config (NEW)
  6c. run.py default config + DMA event collection (~30 lines)
  6d. Legacy configs → legacy/

Phase 7: Predictor/Embedding     ← Deferred until simulator validated
  7a. data_loader.py 8×4 + DMA graph
  7b. hw_encoder.py node/link features
  7c. predictor.py/action_decoder.py updates

Phase 8: Calibration
  - Tune parameters against §9 measurements
```

## Validation Checkpoints (updated for sub-steps)

After each sub-step, verify:

| After step | Check | Expected |
|-----------|-------|----------|
| 2b (Link) | Single link, 1 flit transmission time | ~4.3 cyc serialization + 0.5 cyc wire = ~4.8 cyc |
| 2b (Link) | 512B payload (1 flit) -> single-flit msg | SINGLE flit with inline payload |
| 2b (Link) | 1024B payload (2 flits) -> HEAD+TAIL | Each flit accounts for 512 logical payload bytes |
| 2e (Router) | 2-router direct, single flit forward | ~4.5 cyc head hop (RC+SA+ST+LT) |
| 2f (Mesh) | PE0->PE1 1-hop 512B endpoint transport (no NMC) | 21.5 cyc (`8.5 * hops + 13`) |
| 2f (Mesh) | PE0→PE31 10-hop HEAD flit latency | ~10 × 4.5-8.5 cyc depending on direction; X hops ~4.5, Y hops ~8.5, X-fast (Y=7) ~5.5; total ≈ 75 cyc HEAD-only |
| 3 (NMC) | PE0↔PE1 512B RTT (with NMC startup) | ~250+17 = 267 cyc (matches §9.1 dynamic-shape) |
| 3 | PE0→PE1 large-msg asymptotic BW | ~118-120 GB/s (~106 B/cyc) §9.3 |
| 3 | CH0+CH1 full-duplex aggregate | ~120 GB/s total §4.2 |
| 4 (DMA) | PE0→GM_WDMA0 (7-hop, Y=7 X-fast) 512B fixed lat | ~263 cyc §9.9 |
| 4 | PE0→GM_WDMA0 large-msg BW | ~90-104 GB/s §9.9 |
| 4 | 32 PEs→4 GM_WDMA aggregate | ~314 GB/s §9.9 |
| 4 | DDR download BW (0-hop) | ~103 GB/s §9.10 |
| 5 (Full) | 4 disjoint PE pairs simultaneous | Each ~119 GB/s, zero interference §9.6 |
| 5 | N-way incast to 1 PE/WDMA | ~120-125 GB/s aggregate fair RR §9.7 |

Phase 2 regression results (2026-08-24):

- 10/10 standalone tests pass.
- SINGLE-flit endpoint latency is exactly `8.5*N + 13.0` cycles for 1-10 hops.
- A three-flit packet over three hops arrives at 38.5, 43.3, and 48.1 cycles.
- Direct-Link steady receive gap is 4.8 cycles (106.7 B/cycle).
- Credit backpressure, SA contention, SINGLE/TAIL reservation release, and 2x
  fail-slow scaling are covered.
- A combined contention/backpressure probe exercises all 11 `FlitAction` values.
