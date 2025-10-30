from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
import math
import random

################################################################################
# Constants / Tunables (Observer can tweak at runtime)
################################################################################

DEFAULT_DT_SECONDS = 1.0  # simulation step (s)

I_MAX_E_DEFAULT = 10_000.0  # A, trunk segment current limit at nominal temp
R_E_PER_KM = 0.0005         # ohms per km (DC resistive loss)
T_SHUTDOWN = 120.0          # °C, emergency thermal trip
T_AMBIENT = 15.0            # °C, soil baseline
C_THERMAL_E = 5_000.0       # lumped thermal capacity (arbitrary units)
K_SOIL = 50.0               # W/°C effective cooling term (lumped)
SWITCH_MIN_MS = 10.0        # ms
CONSENSUS_TARGET_MS = 500.0 # ms
ISLANDING_TIMEOUT_S = 5.0   # s
ALPHA_LV = 0.01 / 3600.0    # 0.01 per hour => per second
K_TRUNK_SCALE = 1.0         # κ_trunk scaling

FAULT_RATE_LAMBDA = 0.02/3600.0  # 0.02 events / hour => per second
BETA_INVEST = 0.1           # β_inv
GAMMA_PRI = 0.8             # γ_PRI
CURTAILMENT_RAMP = 0.5      # κ for congestion throttling
FAIRNESS_FLOOR = 0.1        # φ_min
TAU_WARN = 10.0             # s above 0.8Imax before alert
TAU_COMM_ALLOW = 0.200      # s (200 ms) for hospital reflex
HOSPITAL_LOAD_SPIKE_FRAC = 0.15
HOSPITAL_REFLEX_RESPONSE_MS = 500.0

################################################################################
# Helper math
################################################################################

def clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

################################################################################
# Core data structures
################################################################################

@dataclass
class TrunkSegment:
    seg_id: str
    length_km: float
    resistance_ohm: float = None
    I_max_base: float = I_MAX_E_DEFAULT
    I: float = 0.0        # A
    T: float = T_AMBIENT  # °C
    failed: bool = False  # segment fault / outage
    connected_nodes: List[str] = field(default_factory=list)

    # congestion memory to compute C_e smoothing
    congestion_index: float = 0.0
    last_overload_time: float = 0.0

    def __post_init__(self):
        if self.resistance_ohm is None:
            self.resistance_ohm = self.length_km * R_E_PER_KM

    def thermal_limit(self) -> float:
        """
        I_thermal_limit(T): as T rises, allowable I shrinks.
        We'll do a simple linear derate: -0.2% per °C above ambient.
        """
        derate = 1.0 - 0.002 * max(0.0, self.T - T_AMBIENT)
        return max(0.1, derate) * self.I_max_base * K_TRUNK_SCALE

    def step_thermal(self, dt: float):
        """
        dT/dt = (I^2 * R - k_soil*(T-ambient)) / C_thermal
        """
        if self.failed:
            heat_in = 0.0
        else:
            heat_in = (self.I ** 2) * self.resistance_ohm
        cooling = K_SOIL * (self.T - T_AMBIENT)
        dTdt = (heat_in - cooling) / C_THERMAL_E
        self.T += dTdt * dt
        # thermal trip
        if self.T > T_SHUTDOWN:
            self.failed = True
            self.I = 0.0

    def update_congestion(self, dt: float):
        limit = max(1e-6, self.thermal_limit())
        raw_ratio = abs(self.I) / limit
        # smooth: dC/dt = (raw_ratio - C)/tau, choose tau=10s
        tau_cong = 10.0
        dCdt = (raw_ratio - self.congestion_index) / tau_cong
        self.congestion_index = clip(self.congestion_index + dCdt * dt, 0.0, 1.0)

        if raw_ratio > 0.8:
            self.last_overload_time += dt
        else:
            self.last_overload_time = 0.0

    def available_capacity_kw(self, v_dc: float) -> float:
        """
        How much power (kW) can be pushed through this segment right now,
        respecting thermal limit. P = V * I. We'll use remaining headroom.
        """
        limit_I = self.thermal_limit()
        headroom_I = max(0.0, limit_I - abs(self.I))
        return (v_dc * headroom_I) / 1000.0  # kW


@dataclass
class SolidStateSwitch:
    switch_id: str
    closed: bool = True
    last_switch_time: float = 0.0  # s
    min_switch_interval_ms: float = SWITCH_MIN_MS

    def can_switch(self, now_s: float) -> bool:
        elapsed_ms = (now_s - self.last_switch_time) * 1000.0
        return elapsed_ms >= self.min_switch_interval_ms

    def set_state(self, closed: bool, now_s: float):
        if self.closed != closed:
            # enforce anti-chatter
            if self.can_switch(now_s):
                self.closed = closed
                self.last_switch_time = now_s
                return True
        return False


@dataclass
class MicrogridController:
    node_id: str

    # DER / load capabilities
    soc_kwh: float
    soc_capacity_kwh: float
    pv_max_kw: float
    genset_max_kw: float
    load_max_kw: float

    # state
    p_gen_kw: float = 0.0         # current generation (PV + genset)
    p_load_kw: float = 0.0        # current local demand
    is_state: str = "grid-connected"  # Islanding state enum
    comm_latency_s: float = 0.001     # s, ~1ms baseline
    unmet_load_kw: float = 0.0

    # policy / social state
    pri: float = 0.5  # 0..1
    lv: float = 0.5   # 0..1
    mb: float = 0.5   # marginal social benefit proxy
    last_mb_update_s: float = 0.0

    # classification
    is_hospital: bool = False

    def net_power_kw(self) -> float:
        """
        P_net_i positive = export to trunk, negative = need import.
        """
        return self.p_gen_kw - self.p_load_kw

    def soc_frac(self) -> float:
        return clip(self.soc_kwh / max(1e-9, self.soc_capacity_kwh), 0.0, 1.0)

    def update_local_profiles(self, t_s: float):
        """
        Update p_gen_kw (PV profile + genset) and p_load_kw (demand profile).
        We'll implement simple diurnal PV + commuter peaks.
        """
        day_seconds = t_s % 86400.0
        # crude PV: sinusoid 6am-18pm
        if 6*3600 <= day_seconds <= 18*3600:
            daylight_frac = (day_seconds - 6*3600) / (12*3600)
            pv_profile = math.sin(math.pi * daylight_frac)  # 0..1
        else:
            pv_profile = 0.0

        pv_kw = pv_profile * self.pv_max_kw

        # genset dispatch: turned on only if low SOC or islanded
        genset_kw = 0.0
        if (self.is_state != "grid-connected" and self.soc_frac() < 0.5):
            genset_kw = self.genset_max_kw

        # load profile: base + commute peaks morning/evening
        base = 0.4 * self.load_max_kw
        morning_peak = 0.4 * self.load_max_kw if (7*3600 <= day_seconds <= 9*3600) else 0.0
        evening_peak = 0.4 * self.load_max_kw if (17*3600 <= day_seconds <= 20*3600) else 0.0
        hospital_bias = 0.2 * self.load_max_kw if self.is_hospital else 0.0

        self.p_load_kw = base + morning_peak + evening_peak + hospital_bias
        self.p_gen_kw = pv_kw + genset_kw

    def apply_power_allocation(self, imported_kw: float, dt: float):
        """
        imported_kw > 0 means we received that much from trunk.
        negative imported_kw means we exported that much to trunk.
        We'll update SOC and unmet load here.
        """
        need_kw = self.p_load_kw - self.p_gen_kw
        if need_kw < 0:
            # we are net exporter; imported_kw is negative or zero
            export_kw = min(-need_kw, -imported_kw if imported_kw < 0 else 0.0)
            # charge battery with any remainder after export
            charge_kw = (-need_kw - export_kw)
            if charge_kw > 0:
                self.charge(charge_kw, dt)
            self.unmet_load_kw = 0.0
        else:
            # we are net importer
            delivered_kw = max(0.0, imported_kw)
            deficit_kw = max(0.0, need_kw - delivered_kw)

            # try battery discharge for the remaining deficit
            discharge_kw = self.discharge(deficit_kw, dt)
            leftover_deficit = max(0.0, deficit_kw - discharge_kw)

            self.unmet_load_kw = leftover_deficit

    def charge(self, kw: float, dt: float, eta: float = 0.95):
        self.soc_kwh += kw * dt/3600.0 * eta
        self.soc_kwh = clip(self.soc_kwh, 0.0, self.soc_capacity_kwh)

    def discharge(self, kw_needed: float, dt: float, eta: float = 0.9) -> float:
        """
        Return how much kW we could actually supply from battery.
        """
        max_possible_kw = (self.soc_kwh * eta) * 3600.0 / dt
        give_kw = min(kw_needed, max_possible_kw)
        used_kwh = give_kw * dt/3600.0 / eta
        self.soc_kwh = clip(self.soc_kwh - used_kwh, 0.0, self.soc_capacity_kwh)
        return give_kw

    def update_lv_learning(self, dt: float):
        # LV_i <- LV_i + α*(MB_i - LV_i)
        self.lv = clip(self.lv + ALPHA_LV * (self.mb - self.lv) * dt, 0.0, 1.0)

    def maybe_island(self, comm_ok: bool, t_s: float, last_comm_good_s: float):
        """
        If comms bad for > τ_critical -> island.
        """
        tau_critical = ISLANDING_TIMEOUT_S
        if not comm_ok and (t_s - last_comm_good_s) > tau_critical:
            if self.is_state == "grid-connected":
                self.is_state = "isolated-operational"
        # could also degrade state if SOC low
        if self.is_state.startswith("isolated") and self.soc_frac() < 0.1:
            self.is_state = "isolated-degraded"


@dataclass
class CityRegistry:
    """
    Holds PRI, logs MB, exposes social benefit map, etc.
    """
    priority_map: Dict[str, float] = field(default_factory=dict)
    mb_history: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)

    def get_priority(self, node_id: str, fallback: float = 0.5) -> float:
        return self.priority_map.get(node_id, fallback)

    def set_priority(self, node_id: str, new_val: float):
        self.priority_map[node_id] = clip(new_val, 0.0, 1.0)

    def record_mb(self, node_id: str, t_s: float, mb: float):
        self.mb_history.setdefault(node_id, []).append((t_s, mb))


@dataclass
class FaultGenerator:
    rng: random.Random

    def maybe_fault_segment(self, seg: TrunkSegment, dt: float):
        # Poisson with rate λ_fault
        p = FAULT_RATE_LAMBDA * dt
        if self.rng.random() < p:
            # random fault type; we just mark failed for now
            seg.failed = True
            seg.I = 0.0

################################################################################
# World Graph / Routing Logic
################################################################################

class SpinalGridWorld:
    """
    The "world" that evolves every timestep.
    - Keeps trunk segments (edges)
    - Keeps microgrid nodes
    - Solves routing each step according to PRI and physical limits
    - Monitors spectacles (Hospital Continuity Reflex etc.)
    """

    def __init__(self,
                 v_dc: float = 1000.0,
                 seed: int = 0):
        self.time_s: float = 0.0
        self.v_dc: float = v_dc

        self.segments: Dict[str, TrunkSegment] = {}
        self.switches: Dict[str, SolidStateSwitch] = {}
        self.nodes: Dict[str, MicrogridController] = {}
        self.registry = CityRegistry()
        self.faultgen = FaultGenerator(random.Random(seed))

        # adjacency: node_id -> list of (neighbor_id, seg_id, switch_id)
        self.graph: Dict[str, List[Tuple[str, str, str]]] = {}

        # comm health tracking
        self.last_comm_good_s: Dict[str, float] = {}

        # logs for observer
        self.event_log: List[Dict] = []

        # spectacle trackers
        self._pre_hospital_load_kw: Dict[str, float] = {}
        self._last_hospital_reflex_time_s: float = -1e9

    ############################################################################
    # Build / mutation API
    ############################################################################

    def add_node(self,
                 node_id: str,
                 soc_kwh: float,
                 soc_capacity_kwh: float,
                 pv_max_kw: float,
                 genset_max_kw: float,
                 load_max_kw: float,
                 pri: float,
                 lv: float,
                 mb: float,
                 is_hospital: bool = False):
        self.nodes[node_id] = MicrogridController(
            node_id=node_id,
            soc_kwh=soc_kwh,
            soc_capacity_kwh=soc_capacity_kwh,
            pv_max_kw=pv_max_kw,
            genset_max_kw=genset_max_kw,
            load_max_kw=load_max_kw,
            pri=pri,
            lv=lv,
            mb=mb,
            is_hospital=is_hospital,
        )
        self.registry.set_priority(node_id, pri)
        self.last_comm_good_s[node_id] = 0.0
        self.graph.setdefault(node_id, [])

    def add_segment(self,
                    seg_id: str,
                    a: str,
                    b: str,
                    length_km: float):
        seg = TrunkSegment(seg_id=seg_id, length_km=length_km)
        self.segments[seg_id] = seg

        sw_id = f"sw_{seg_id}"
        sw = SolidStateSwitch(switch_id=sw_id)
        self.switches[sw_id] = sw

        # undirected graph
        self.graph.setdefault(a, []).append((b, seg_id, sw_id))
        self.graph.setdefault(b, []).append((a, seg_id, sw_id))

        seg.connected_nodes = [a, b]

    def inject_fault_segment(self, seg_id: str, reason: str = "manual"):
        if seg_id in self.segments:
            self.segments[seg_id].failed = True
            self.event_log.append({
                "t": self.time_s,
                "type": "fault_injected",
                "seg": seg_id,
                "reason": reason
            })

    def set_priority(self, node_id: str, val: float):
        self.registry.set_priority(node_id, val)
        if node_id in self.nodes:
            self.nodes[node_id].pri = self.registry.get_priority(node_id)

    ############################################################################
    # Core simulation step
    ############################################################################

    def step(self, dt: float = DEFAULT_DT_SECONDS):
        t = self.time_s
        self.time_s += dt

        # 1. UPDATE LOCAL PROFILES (PV, load, genset, comm latency etc.)
        self._update_nodes_profiles(t)

        # 2. FAULTS + THERMAL EVOLUTION ON SEGMENTS
        self._evolve_segments(dt)

        # 3. COMMUNICATION / CONSENSUS / ISLANDING CHECKS
        self._update_comms_and_islanding(t, dt)

        # 4. PRIORITY-BASED POWER ROUTING (allocation of imported_kw per node)
        allocations_kw = self._allocate_power_priority(dt)

        # 5. APPLY POWER ALLOCATION TO NODES (SOC, unmet load)
        self._apply_power_to_nodes(allocations_kw, dt)

        # 6. UPDATE LEARNING (LV_i from MB_i)
        for node in self.nodes.values():
            node.update_lv_learning(dt)

        # 7. CHECK SPECTACLES (Hospital Continuity Reflex, etc.)
        self._check_hospital_continuity_reflex(t, dt, allocations_kw)

        # 8. LOG KEY TELEMETRY
        self._log_snapshot(t)

    ############################################################################
    # Internal routines
    ############################################################################

    def _update_nodes_profiles(self, t_s: float):
        """
        Update each node's p_gen_kw, p_load_kw based on diurnal cycle etc.
        Also random comm latency spikes to simulate degrade.
        """
        for node in self.nodes.values():
            node.update_local_profiles(t_s)

            # random comm spikes esp. during "storms"
            if random.random() < 0.001:
                node.comm_latency_s = random.uniform(0.001, 5.0)  # up to 5s
            else:
                # drift back to healthy ~1ms
                node.comm_latency_s = 0.001 + 0.1*(node.comm_latency_s-0.001)

            # MB drift (proxy for marginal social benefit observations)
            # We'll nudge MB up if unmet load was high last step: social need visible.
            if node.unmet_load_kw > 0.1 * node.load_max_kw:
                node.mb = clip(node.mb + 0.001, 0.0, 1.0)
            else:
                node.mb = clip(node.mb - 0.0002, 0.0, 1.0)

            self.registry.record_mb(node.node_id, t_s, node.mb)

    def _evolve_segments(self, dt: float):
        """
        Apply random new faults, update thermal state, congestion index.
        Also reduce current if switch opened or segment failed.
        """
        for seg in self.segments.values():
            # random exogenous fault
            self.faultgen.maybe_fault_segment(seg, dt)

            # thermal evolution
            seg.step_thermal(dt)

            # recompute congestion index
            seg.update_congestion(dt)

            # enforce failure: if failed or open switches, I goes to 0
            if seg.failed:
                seg.I = 0.0

    def _update_comms_and_islanding(self, t_s: float, dt: float):
        """
        Communication consensus: if all nodes have comm_latency < τ_consensus,
        we consider consensus achieved citywide.
        If node comm_latency_s is huge locally, that node may island.
        """
        for node in self.nodes.values():
            comm_ok = (node.comm_latency_s * 1000.0) < CONSENSUS_TARGET_MS
            if comm_ok:
                self.last_comm_good_s[node.node_id] = t_s

            node.maybe_island(
                comm_ok=comm_ok,
                t_s=t_s,
                last_comm_good_s=self.last_comm_good_s[node.node_id]
            )

    def _allocate_power_priority(self, dt: float) -> Dict[str, float]:
        """
        Decide how much trunk-import each node gets.
        Core rule:
        - higher PRI nodes are served first
        - limit by trunk segment capacities / congestion / islanding
        Simplification:
        - assume we have a single "backbone pool" P_pool_kw available each step
          based on aggregate segment headroom.
        - allocate P_pool_kw to deficit nodes in descending priority.
        - curtail low-priority nodes if congestion is high.
        """
        # 1. compute available trunk pool from all healthy segments
        P_pool_kw = 0.0
        max_congestion = 0.0
        for seg in self.segments.values():
            if seg.failed:
                continue
            max_congestion = max(max_congestion, seg.congestion_index)
            P_pool_kw += seg.available_capacity_kw(self.v_dc)

        # congestion throttling for fairness
        # if congestion high, we will curtail low-priority demand
        # This roughly encodes "controllers impose curtailment on low-PRI nodes".
        # We'll later scale low-PRI allocations by (1 - CURTAILMENT_RAMP*C).
        # But enforce fairness floor to avoid starving them to zero.
        # (This is coarse but matches spec intent.)
        allocations_kw = {nid: 0.0 for nid in self.nodes.keys()}

        # 2. figure deficits for each node that is still grid-connected
        deficits = []
        for node in self.nodes.values():
            if node.is_state == "grid-connected":
                need_kw = node.p_load_kw - node.p_gen_kw
                if need_kw > 0:
                    deficits.append((node.node_id, need_kw, node.pri))
            else:
                # islanded nodes are on their own, no trunk help
                pass

        # sort by PRI desc
        deficits.sort(key=lambda x: x[2], reverse=True)

        # 3. allocate from pool
        for node_id, need_kw, pri in deficits:
            if P_pool_kw <= 0:
                break

            scale_lowpri = 1.0
            # if congestion is high and node is low priority, throttle them
            if max_congestion > 0.6 and pri < 0.5:
                scale_lowpri = 1.0 - CURTAILMENT_RAMP * max_congestion
                # fairness floor
                scale_lowpri = max(scale_lowpri, FAIRNESS_FLOOR)

            ask_kw = need_kw * scale_lowpri
            give_kw = min(ask_kw, P_pool_kw)
            allocations_kw[node_id] += give_kw
            P_pool_kw -= give_kw

        # NOTE: exports (nodes with surplus) not explicitly routed into pool.
        # You can extend this to track net transfer through specific segments
        # and enforce KCL, V_drop = I*R, etc. This first pass models behavior,
        # priority, and constraints, not full circuit simulation.

        # update segment currents crudely proportional to usage
        self._push_trunk_currents(max_congestion=max_congestion)

        return allocations_kw

    def _push_trunk_currents(self, max_congestion: float):
        """
        We fake the per-segment I to reflect global stress.
        If congestion is high, we drive I close to limit.
        If not, we back off.
        """
        for seg in self.segments.values():
            if seg.failed:
                seg.I = 0.0
                continue
            target_ratio = max_congestion  # 0..1
            seg.I = target_ratio * seg.thermal_limit() * 0.9

    def _apply_power_to_nodes(self, allocations_kw: Dict[str, float], dt: float):
        """
        Actually deliver allocations to each node and update SOC and unmet load.
        """
        for node_id, node in self.nodes.items():
            imported_kw = allocations_kw.get(node_id, 0.0)
            # if islanded and still importing something (shouldn't happen), zero it:
            if not node.is_state == "grid-connected":
                imported_kw = 0.0
            node.apply_power_allocation(imported_kw, dt)

    def _check_hospital_continuity_reflex(self,
                                          t_s: float,
                                          dt: float,
                                          allocations_kw: Dict[str, float]):
        """
        Spectacle #1: Hospital Continuity Reflex
        Trigger:
          - hospital node has sudden load increase >=15% Load_max within 60 s
          - adjacent congestion >=0.5
          - comm latency OK (<=200ms)
        Response:
          - reroute more power to hospital within 500ms equivalent
          - curtail low-PRI nodes to feed hospital
        We'll approximate: check once per step, use stored last load.
        """
        # track spike
        for node in self.nodes.values():
            if node.is_hospital:
                prev = self._pre_hospital_load_kw.get(node.node_id, node.p_load_kw)
                self._pre_hospital_load_kw[node.node_id] = node.p_load_kw

                # check spike
                spike = (node.p_load_kw - prev)
                spike_frac = 0.0
                if node.load_max_kw > 0:
                    spike_frac = spike / node.load_max_kw

                # congestion near hospital = max congestion of segments attached
                adj_congestion = 0.0
                for nbr_id, seg_id, _sw_id in self.graph.get(node.node_id, []):
                    seg = self.segments[seg_id]
                    adj_congestion = max(adj_congestion, seg.congestion_index)

                comm_ok = (node.comm_latency_s <= TAU_COMM_ALLOW)

                if (spike_frac >= HOSPITAL_LOAD_SPIKE_FRAC and
                    adj_congestion >= 0.5 and
                    comm_ok):

                    # Fire spectacle if not spammy
                    if (t_s - self._last_hospital_reflex_time_s) > 60.0:
                        self._last_hospital_reflex_time_s = t_s
                        self._hospital_reflex(node_id=node.node_id, t_s=t_s)

    def _hospital_reflex(self, node_id: str, t_s: float):
        """
        Force switches to favor the hospital node.
        We'll simulate by:
        - raising its PRI sharply
        - logging the action
        """
        # boost priority
        self.set_priority(node_id, clip(self.registry.get_priority(node_id) + 0.2, 0.0, 1.0))

        # optionally drop PRI of some lowest-priority nodes
        low_nodes = sorted(
            self.nodes.values(), key=lambda n: n.pri
        )[:3]
        for ln in low_nodes:
            if ln.node_id != node_id:
                self.set_priority(ln.node_id, clip(ln.pri - 0.1, 0.0, 1.0))

        self.event_log.append({
            "t": t_s,
            "type": "hospital_continuity_reflex",
            "node": node_id,
            "detail": "priority boost; low-PRI curtailment signaled"
        })

    def _log_snapshot(self, t_s: float):
        """
        Observer telemetry: unmet load, congestion heatmap proxy, LV distribution.
        """
        city_unmet = sum(n.unmet_load_kw for n in self.nodes.values())
        avg_lv = sum(n.lv for n in self.nodes.values()) / max(1, len(self.nodes))
        max_cong = max((s.congestion_index for s in self.segments.values()), default=0.0)

        snapshot = {
            "t": t_s,
            "city_unmet_kw": city_unmet,
            "avg_lv": avg_lv,
            "max_congestion": max_cong,
            "nodes": {
                n.node_id: {
                    "pri": n.pri,
                    "lv": n.lv,
                    "soc_frac": n.soc_frac(),
                    "unmet_kw": n.unmet_load_kw,
                    "is_state": n.is_state,
                    "p_load_kw": n.p_load_kw,
                    "p_gen_kw": n.p_gen_kw,
                    "comm_latency_ms": n.comm_latency_s * 1000.0,
                } for n in self.nodes.values()
            },
            "segments": {
                s.seg_id: {
                    "I_A": s.I,
                    "T_C": s.T,
                    "C_e": s.congestion_index,
                    "failed": s.failed,
                } for s in self.segments.values()
            }
        }
        self.event_log.append(snapshot)

    ############################################################################
    # Observer / external control API
    ############################################################################

    def get_snapshot(self) -> Dict:
        """Return last log entry with type 'snapshot' info (the last event_log item)."""
        if not self.event_log:
            return {}
        return self.event_log[-1]

    def force_island(self, node_id: str):
        if node_id in self.nodes:
            n = self.nodes[node_id]
            n.is_state = "isolated-operational"
            self.event_log.append({
                "t": self.time_s,
                "type": "manual_island",
                "node": node_id
            })

    def inject_demand_surge(self, node_id: str, frac_of_loadmax: float):
        """
        Sudden triage/shelter spike: increase load right now.
        """
        if node_id in self.nodes:
            n = self.nodes[node_id]
            surge_kw = frac_of_loadmax * n.load_max_kw
            n.p_load_kw += surge_kw
            self.event_log.append({
                "t": self.time_s,
                "type": "demand_surge",
                "node": node_id,
                "delta_kw": surge_kw
            })

################################################################################
# Example usage / quick demo
################################################################################

if __name__ == "__main__":
    world = SpinalGridWorld(v_dc=1000.0, seed=42)

    # Create a few microgrids representing neighborhoods + 1 hospital
    world.add_node(
        node_id="hospital_south",
        soc_kwh=500.0,
        soc_capacity_kwh=1000.0,
        pv_max_kw=200.0,
        genset_max_kw=800.0,
        load_max_kw=1500.0,
        pri=0.9,
        lv=0.7,
        mb=0.8,
        is_hospital=True
    )

    world.add_node(
        node_id="transit_hub",
        soc_kwh=200.0,
        soc_capacity_kwh=400.0,
        pv_max_kw=150.0,
        genset_max_kw=200.0,
        load_max_kw=800.0,
        pri=0.8,
        lv=0.6,
        mb=0.6,
        is_hospital=False
    )

    world.add_node(
        node_id="shelter_west",
        soc_kwh=100.0,
        soc_capacity_kwh=200.0,
        pv_max_kw=80.0,
        genset_max_kw=50.0,
        load_max_kw=300.0,
        pri=0.7,
        lv=0.5,
        mb=0.7,
        is_hospital=False
    )

    world.add_node(
        node_id="mall_lowpri",
        soc_kwh=80.0,
        soc_capacity_kwh=200.0,
        pv_max_kw=120.0,
        genset_max_kw=0.0,
        load_max_kw=600.0,
        pri=0.2,
        lv=0.3,
        mb=0.3,
        is_hospital=False
    )

    # Trunk segments forming a little backbone square
    world.add_segment("segA", "hospital_south", "transit_hub", length_km=2.0)
    world.add_segment("segB", "transit_hub", "shelter_west", length_km=1.0)
    world.add_segment("segC", "shelter_west", "mall_lowpri", length_km=2.5)
    world.add_segment("segD", "mall_lowpri", "hospital_south", length_km=3.0)

    # Run simulation for some steps
    for _ in range(3600):  # 1 hour @ 1s
        # inject rare demand surge at hospital around t=1800s to trigger reflex
        if abs(world.time_s - 1800.0) < 1e-6:
            world.inject_demand_surge("hospital_south", frac_of_loadmax=0.2)

        world.step(dt=1.0)

    # Print final snapshot and recent events
    snap = world.get_snapshot()
    print("==== FINAL SNAPSHOT @ t=%.1fs ====" % world.time_s)
    print("City unmet load (kW):", snap.get("city_unmet_kw"))
    print("Avg LV:", snap.get("avg_lv"))
    print("Max congestion:", snap.get("max_congestion"))
    for nid, nd in snap.get("nodes", {}).items():
        print(f"[{nid}] PRI={nd['pri']:.2f} LV={nd['lv']:.2f} SOC={nd['soc_frac']:.2f} "
              f"unmet={nd['unmet_kw']:.1f} is={nd['is_state']} comm={nd['comm_latency_ms']:.2f}ms")

    # Show any hospital reflex events that fired
    reflexes = [e for e in world.event_log if e.get("type") == "hospital_continuity_reflex"]
    for r in reflexes[-5:]:
        print("REFLEX EVENT:", r)
