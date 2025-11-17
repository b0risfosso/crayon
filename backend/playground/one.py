import math
import random


class NitrogenSpeciesState:
    """
    Represents total nitrogen stored as an NH3/NH4+ pair with simple pH-dependent speciation.
    """

    def __init__(self, total_n_mol: float, pH: float, temperature_K: float = 298.15):
        self.total_n_mol = total_n_mol
        self.pH = pH
        self.temperature_K = temperature_K
        self.fraction_as_NH3 = 0.0
        self.fraction_as_NH4 = 0.0
        self.update_speciation_from_pH()

    def update_speciation_from_pH(self):
        """Update NH3/NH4+ split using a simple Henderson–Hasselbalch model."""
        pKa = 9.25
        ratio = 10 ** (self.pH - pKa)  # [NH3]/[NH4+]
        self.fraction_as_NH3 = ratio / (1 + ratio)
        self.fraction_as_NH4 = 1 - self.fraction_as_NH3

    def add_nitrogen(self, moles: float):
        self.total_n_mol += max(moles, 0.0)

    def remove_nitrogen(self, moles: float):
        self.total_n_mol = max(self.total_n_mol - max(moles, 0.0), 0.0)

    def get_concentrations(self, volume_L: float):
        """Return (NH3 mol/L, NH4+ mol/L)."""
        if volume_L <= 0:
            return 0.0, 0.0
        nh3 = self.total_n_mol * self.fraction_as_NH3 / volume_L
        nh4 = self.total_n_mol * self.fraction_as_NH4 / volume_L
        return nh3, nh4


class Solution:
    """
    Generic liquid solution with species tracked as total moles and simple conductivity model.
    """

    def __init__(self, volume_L: float, temperature_K: float = 298.15):
        self.volume_L = volume_L
        self.temperature_K = temperature_K
        # species: ion name -> moles
        self.species: dict[str, float] = {}

    def add_species(self, ion: str, moles: float):
        self.species[ion] = self.species.get(ion, 0.0) + moles

    def remove_species(self, ion: str, moles: float):
        self.species[ion] = max(self.species.get(ion, 0.0) - moles, 0.0)

    def get_concentration(self, ion: str):
        """Return mol/L of an ion."""
        if self.volume_L <= 0:
            return 0.0
        return self.species.get(ion, 0.0) / self.volume_L

    def mix_with(self, other: "Solution"):
        """Mix another solution into this one (volume and moles)."""
        total_volume = self.volume_L + other.volume_L
        if total_volume <= 0:
            return
        for ion, moles in other.species.items():
            self.species[ion] = self.species.get(ion, 0.0) + moles
        self.volume_L = total_volume

    def compute_conductivity(self):
        """
        Crude conductivity estimate: sum(|z_i| * c_i) * constant.
        All ions treated as monovalent except sulfate (z = 2).
        """
        k = 0.02  # scaling factor
        conductivity = 0.0
        for ion, moles in self.species.items():
            c = self.get_concentration(ion)
            if ion.lower() in ("so4", "so4--", "sulfate"):
                z = 2
            else:
                z = 1
            conductivity += abs(z) * c
        return k * conductivity  # S/m (approximate)


class AirStream:
    """
    Represents a flowing air segment with NH3 at a given ppm.
    """

    def __init__(
        self,
        flow_rate_m3_per_s: float,
        nh3_concentration_ppm: float,
        temperature_K: float = 298.15,
        humidity_rel: float = 0.5,
        volume_segment_m3: float = 0.01,
    ):
        self.flow_rate_m3_per_s = flow_rate_m3_per_s
        self.nh3_concentration_ppm = nh3_concentration_ppm
        self.temperature_K = temperature_K
        self.humidity_rel = humidity_rel
        self.volume_segment_m3 = volume_segment_m3

    def get_nh3_moles_in_segment(self):
        """Convert ppm → moles in the modeled segment using ideal gas."""
        P = 101325  # Pa
        R = 8.314
        n_total = P * self.volume_segment_m3 / (R * self.temperature_K)
        x_nh3 = self.nh3_concentration_ppm * 1e-6
        return n_total * x_nh3

    def set_nh3_from_moles(self, moles: float):
        """Update ppm from moles of NH3 in the segment."""
        P = 101325
        R = 8.314
        n_total = P * self.volume_segment_m3 / (R * self.temperature_K)
        if n_total <= 0:
            self.nh3_concentration_ppm = 0.0
        else:
            x_nh3 = moles / n_total
            self.nh3_concentration_ppm = max(x_nh3 * 1e6, 0.0)

    def remove_nh3_flux(self, moles_per_s: float, dt: float):
        """Remove NH3 due to flux into the membrane-side film."""
        moles_segment = self.get_nh3_moles_in_segment()
        removed = min(moles_per_s * dt, moles_segment)
        self.set_nh3_from_moles(moles_segment - removed)
        return removed

    def update_flow_rate(self, new_flow_rate: float):
        self.flow_rate_m3_per_s = max(new_flow_rate, 0.0)


class Fan:
    """
    Simple fan that sets airflow based on speed and system resistance.
    """

    def __init__(self, max_flow_rate_m3_per_s: float, system_resistance: float = 1.0):
        self.max_flow_rate_m3_per_s = max_flow_rate_m3_per_s
        self.system_resistance = system_resistance
        self.speed_setting = 0.0  # 0–1
        self.on = False

    def set_speed(self, new_setting: float):
        self.speed_setting = min(max(new_setting, 0.0), 1.0)
        self.on = self.speed_setting > 0

    def turn_on(self):
        if self.speed_setting == 0:
            self.speed_setting = 1.0
        self.on = True

    def turn_off(self):
        self.on = False

    def compute_flow_rate(self):
        """Approximate delivered flow rate."""
        if not self.on:
            return 0.0
        return self.max_flow_rate_m3_per_s * self.speed_setting / (1.0 + self.system_resistance)


class CationExchangeMembrane:
    """
    Ion-selective membrane transporting NH4+ in response to current.
    """

    def __init__(
        self,
        area_m2: float,
        thickness_m: float,
        ionic_conductivity_S_per_m: float,
        nh4_transport_number: float,
    ):
        self.area_m2 = area_m2
        self.thickness_m = thickness_m
        self.ionic_conductivity_S_per_m = ionic_conductivity_S_per_m
        self.nh4_transport_number = nh4_transport_number
        # Simple representation of available NH4+ pool at feed side
        self.feed_nh_pool_mol = 0.0

    def compute_resistance(self):
        if self.ionic_conductivity_S_per_m <= 0 or self.area_m2 <= 0:
            return float("inf")
        return self.thickness_m / (self.ionic_conductivity_S_per_m * self.area_m2)

    def compute_nh4_flux(self, current_A: float):
        """
        Faraday's law:
        mol/s = t+ * I / (z F), with z = 1 for NH4+.
        """
        F = 96485.3329
        if current_A <= 0 or self.nh4_transport_number <= 0:
            return 0.0
        return self.nh4_transport_number * current_A / F

    def step(self, dt: float, current_A: float, acid_reservoir: "AcidReservoir"):
        """Transport NH4+ from feed pool to acid reservoir."""
        flux = self.compute_nh4_flux(current_A)  # mol/s
        moles = flux * dt
        removed = min(moles, self.feed_nh_pool_mol)
        self.feed_nh_pool_mol -= removed
        acid_reservoir.add_ammonium(removed)
        return removed


class AcidReservoir:
    """
    Acid loop holding captured ammonium as fertilizer solution.
    """

    def __init__(self, solution: Solution):
        self.solution = solution

    def add_ammonium(self, moles: float):
        # Add NH4+ to solution; assume counterion already present.
        self.solution.add_species("NH4+", moles)

    def get_ammonium_concentration(self):
        return self.solution.get_concentration("NH4+")

    def get_conductivity(self):
        return self.solution.compute_conductivity()


class PowerSupply:
    """
    Bench-top DC power supply: constant current or constant voltage mode.
    """

    def __init__(self, mode: str = "constant_current", setpoint_value: float = 0.1, max_current_A: float = 0.5):
        self.mode = mode  # "constant_current" or "constant_voltage"
        self.setpoint_value = setpoint_value
        self.max_current_A = max_current_A
        self.on = True

    def set_mode(self, mode: str, setpoint_value: float):
        self.mode = mode
        self.setpoint_value = setpoint_value

    def turn_on(self):
        self.on = True

    def turn_off(self):
        self.on = False

    def compute_current(self, cell_resistance_ohm: float):
        if not self.on:
            return 0.0
        if self.mode == "constant_current":
            return min(self.setpoint_value, self.max_current_A)
        # constant voltage mode
        if cell_resistance_ohm == float("inf") or cell_resistance_ohm <= 0:
            return 0.0
        current = self.setpoint_value / cell_resistance_ohm
        return min(current, self.max_current_A)


class ConductivitySensor:
    """
    Conductivity probe attached to the acid reservoir.
    """

    def __init__(self, acid_reservoir: AcidReservoir, noise_std: float = 0.0, cell_constant_m_per_cm: float = 1.0):
        self.acid_reservoir = acid_reservoir
        self.noise_std = noise_std
        self.cell_constant_m_per_cm = cell_constant_m_per_cm

    def measure(self):
        """Return a conductivity reading with optional Gaussian noise."""
        true_sigma = self.acid_reservoir.get_conductivity()
        noise = random.gauss(0.0, self.noise_std)
        reading = max(true_sigma * self.cell_constant_m_per_cm + noise, 0.0)
        return reading


class ManualSwitch:
    """
    Simple switch with OFF / LOW / HIGH positions mapped to current and fan speed.
    """

    def __init__(self):
        self.positions = ["OFF", "LOW", "HIGH"]
        self.current_position = "OFF"

    def set_position(self, position: str):
        if position in self.positions:
            self.current_position = position
        else:
            raise ValueError(f"Invalid switch position: {position}")

    def get_current_setpoints(self):
        """Return dict with 'current_A' and 'fan_speed' for this position."""
        if self.current_position == "OFF":
            return {"current_A": 0.0, "fan_speed": 0.0}
        if self.current_position == "LOW":
            return {"current_A": 0.05, "fan_speed": 0.5}
        if self.current_position == "HIGH":
            return {"current_A": 0.2, "fan_speed": 1.0}
        return {"current_A": 0.0, "fan_speed": 0.0}


class ElectrochemicalCell:
    """
    Wraps membrane + acid reservoir + extra ohmic losses into a single cell object.
    """

    def __init__(self, membrane: CationExchangeMembrane, acid_reservoir: AcidReservoir, extra_ohmic_ohm: float = 5.0):
        self.membrane = membrane
        self.acid_reservoir = acid_reservoir
        self.extra_ohmic_ohm = extra_ohmic_ohm

    def compute_total_resistance(self):
        return self.membrane.compute_resistance() + self.extra_ohmic_ohm

    def step(self, dt: float, current_A: float):
        """Advance membrane transport for this time step."""
        return self.membrane.step(dt, current_A, self.acid_reservoir)


class AirChannel:
    """
    Air-side contactor: moves NH3 from bulk air to the membrane-side pool.
    """

    def __init__(
        self,
        air_stream: AirStream,
        membrane: CationExchangeMembrane,
        membrane_area_m2: float,
        mass_transfer_coefficient_m_per_s: float,
    ):
        self.air_stream = air_stream
        self.membrane = membrane
        self.membrane_area_m2 = membrane_area_m2
        self.mass_transfer_coefficient_m_per_s = mass_transfer_coefficient_m_per_s

    def compute_nh3_flux(self):
        """
        J_total = k_L * A * C_NH3.
        Treat C_NH3 from ppm using ideal gas law.
        """
        P = 101325
        R = 8.314
        T = self.air_stream.temperature_K
        x_nh3 = self.air_stream.nh3_concentration_ppm * 1e-6
        C_total = P / (R * T)  # mol/m3
        C_nh3 = C_total * x_nh3
        J = self.mass_transfer_coefficient_m_per_s * self.membrane_area_m2 * C_nh3  # mol/s
        return J

    def step(self, dt: float):
        """Move NH3 from air into the membrane feed pool."""
        flux = self.compute_nh3_flux()
        removed = self.air_stream.remove_nh3_flux(flux, dt)
        self.membrane.feed_nh_pool_mol += removed
        return removed


class NitrogenSinkSystem:
    """
    Top-level minimal nitrogen sink system tying all components together.
    """

    def __init__(
        self,
        air_stream: AirStream,
        fan: Fan,
        air_channel: AirChannel,
        electrochemical_cell: ElectrochemicalCell,
        power_supply: PowerSupply,
        sensor: ConductivitySensor,
        manual_switch: ManualSwitch,
    ):
        self.air_stream = air_stream
        self.fan = fan
        self.air_channel = air_channel
        self.electrochemical_cell = electrochemical_cell
        self.power_supply = power_supply
        self.sensor = sensor
        self.manual_switch = manual_switch
        self.time_s = 0.0
        self.cumulative_n_captured_mol = 0.0

    def step(self, dt: float):
        """
        Advance the system by one time step:
        - apply manual setpoints to fan and PSU
        - update airflow
        - move NH3 from air to membrane pool
        - move NH4+ from membrane pool to acid reservoir
        """
        # Manual control → setpoints
        setpoints = self.manual_switch.get_current_setpoints()
        self.fan.set_speed(setpoints["fan_speed"])

        # Airflow update
        flow_rate = self.fan.compute_flow_rate()
        self.air_stream.update_flow_rate(flow_rate)

        # NH3 transport from air to feed pool
        removed_from_air = self.air_channel.step(dt)

        # Ion transport from feed pool to acid reservoir
        R_cell = self.electrochemical_cell.compute_total_resistance()
        # Respect current setpoint but keep PSU mode as configured
        self.power_supply.set_mode(self.power_supply.mode, setpoints["current_A"])
        current = self.power_supply.compute_current(R_cell)
        moved_to_acid = self.electrochemical_cell.step(dt, current)
        self.cumulative_n_captured_mol += moved_to_acid

        # Time and outputs
        self.time_s += dt
        return {
            "time_s": self.time_s,
            "air_nh3_ppm": self.air_stream.nh3_concentration_ppm,
            "ammonium_concentration_mol_L": self.electrochemical_cell.acid_reservoir.get_ammonium_concentration(),
            "conductivity_reading": self.sensor.measure(),
            "cumulative_n_captured_mol": self.cumulative_n_captured_mol,
            "current_A": current,
            "flow_rate_m3_per_s": flow_rate,
            "removed_from_air_mol": removed_from_air,
            "moved_to_acid_mol": moved_to_acid,
        }

    def run_simulation(self, total_time_s: float, dt: float):
        """Run a simple time-marched simulation and return a list of state snapshots."""
        steps = int(total_time_s / dt)
        history = []
        for _ in range(steps):
            history.append(self.step(dt))
        return history


# Example smoke-test wiring if you want to try it immediately:
if __name__ == "__main__":
    # Air with 50 ppm NH3
    air = AirStream(flow_rate_m3_per_s=0.01, nh3_concentration_ppm=50.0)
    fan = Fan(max_flow_rate_m3_per_s=0.05)

    # Membrane and acid reservoir
    membrane = CationExchangeMembrane(
        area_m2=0.01,
        thickness_m=1e-4,
        ionic_conductivity_S_per_m=5.0,
        nh4_transport_number=0.5,
    )
    acid_solution = Solution(volume_L=0.5)
    acid_solution.add_species("SO4", 0.1)  # arbitrary sulfate
    acid_res = AcidReservoir(acid_solution)

    cell = ElectrochemicalCell(membrane, acid_res, extra_ohmic_ohm=5.0)
    psu = PowerSupply(mode="constant_current", setpoint_value=0.1, max_current_A=0.2)
    sensor = ConductivitySensor(acid_res, noise_std=0.0)

    channel = AirChannel(
        air_stream=air,
        membrane=membrane,
        membrane_area_m2=0.01,
        mass_transfer_coefficient_m_per_s=1e-4,
    )

    switch = ManualSwitch()
    switch.set_position("HIGH")  # run in high-capture mode

    system = NitrogenSinkSystem(
        air_stream=air,
        fan=fan,
        air_channel=channel,
        electrochemical_cell=cell,
        power_supply=psu,
        sensor=sensor,
        manual_switch=switch,
    )

    history = system.run_simulation(total_time_s=600.0, dt=1.0)
    print(history[-1])
