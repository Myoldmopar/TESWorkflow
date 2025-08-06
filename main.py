from datetime import datetime, timedelta
from json import dumps, loads
from pathlib import Path
import plotext as plt
from shutil import copy, make_archive, rmtree
from subprocess import call
from sys import argv
from energyplus_api_helpers.import_helper import EPlusAPIHelper
from collections import defaultdict
import sys
import io

# force utf-8 stdout for plotext compatibility for Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def calc_sensible_volume_gal(Q_joules, delta_T=5.0):
    CP_WATER = 4184  # J/kgK
    DENSITY_WATER = 997  # kg/m3 at 25C
    volume_m3 = Q_joules / (DENSITY_WATER * CP_WATER * delta_T)
    volume_gal = volume_m3 / 0.00378541
    return volume_gal

def calc_latent_volume_gal(Q_joules):
    LF_ICE = 334000  # J/kg latent heat of fusion for ice 
    DENSITY_ICE = 917  # kg/m3 at 0C
    volume_m3 = Q_joules / (DENSITY_ICE * LF_ICE)
    volume_gal = volume_m3 / 0.00378541
    return volume_gal

def parse_energyplus_timestamp(ts: str) -> datetime:
    date_part, time_part = ts.split()
    hour, minute, second = map(int, time_part.split(':'))
    if hour == 24:
        dt = datetime.strptime("2025/" + date_part, "%Y/%m/%d")
        dt += timedelta(days=1)
        return dt.replace(hour=0, minute=minute, second=second)
    else:
        return datetime.strptime("2025/" + ts, "%Y/%m/%d %H:%M:%S")

def prepare_directory(eplus_dir: Path, local: bool):
    if local:
        output_dir = Path(__file__).parent / 'output'
        if output_dir.exists():
            rmtree(output_dir)
        output_dir.mkdir()
    else:
        output_dir = Path(__file__).parent
    return output_dir

def copy_and_modify_json(output_dir: Path):
    json_convert_dir = output_dir / 'json_convert'
    json_convert_dir.mkdir(exist_ok=True)

    root_epjson = Path(__file__).parent / 'PlantLoadProfile_TESsizing.epJSON'
    if not root_epjson.exists():
        raise FileNotFoundError("'PlantLoadProfile_TESsizing.epJSON' not found.")
    copied_epjson = json_convert_dir / 'PlantLoadProfile_TESsizing.epJSON'
    copy(root_epjson, copied_epjson)

    baseline_json = json_convert_dir / 'PlantLoadProfile_TESsizingBase.epJSON'
    modified_json = json_convert_dir / 'PlantLoadProfile_TESsizingMod.epJSON'

    baseline_json_data = loads(copied_epjson.read_text())
    secondary_json_data = loads(copied_epjson.read_text())

    # Modify baseline JSON
    baseline_json_data['OutputControl:Files'] = {'OutputControl:Files 1': {'output_json': 'Yes'}}
    baseline_json_data['Output:JSON'] = {'Output:JSON 1': {'output_json': 'Yes', 'option_type': 'TimeSeries'}}
    baseline_json.write_text(dumps(baseline_json_data, indent=2))

    # Modify secondary JSON
    secondary_json_data['Timestep']['Timestep 1']['number_of_timesteps_per_hour'] = 10
    secondary_json_data['OutputControl:Files'] = {'OutputControl:Files 1': {'output_json': 'Yes'}}
    secondary_json_data['Output:JSON'] = {'Output:JSON 1': {'output_json': 'Yes', 'option_type': 'TimeSeries'}}
    modified_json.write_text(dumps(secondary_json_data, indent=4))

    return baseline_json, modified_json, json_convert_dir

def run_energyplus_simulations(api, state_1, state_2, weather_file, baseline_json, modified_json, output_dir):
    output_dir_baseline = output_dir / 'output_baseline'
    output_dir_secondary = output_dir / 'output_secondary'

    api.runtime.run_energyplus(state_1, ['-r', '-w', str(weather_file), str(baseline_json), '-d', str(output_dir_baseline)])
    api.runtime.run_energyplus(state_2, ['-r', '-w', str(weather_file), str(modified_json), '-d', str(output_dir_secondary)])

    return output_dir_baseline, output_dir_secondary

def calc_qdot_from_output(output_path):
    output_json = loads(output_path.read_text())
    cols = output_json['Cols']

    mass_flow_index = cp_index = tin_index = tout_index = None
    for idx, col in enumerate(cols):
        if col['Variable'] == 'SUPPLY OUTLET NODE:System Node Mass Flow Rate':
            mass_flow_index = idx
        elif col['Variable'] == 'SUPPLY OUTLET NODE:System Node Specific Heat':
            cp_index = idx
        elif col['Variable'] == 'SUPPLY INLET NODE:System Node Temperature':
            tin_index = idx
        elif col['Variable'] == 'SUPPLY OUTLET NODE:System Node Temperature':
            tout_index = idx

    assert None not in (mass_flow_index, cp_index, tin_index, tout_index), "Missing variables in output"

    times = []
    qdot_list = []
    time_labels = []

    for row_num, row in enumerate(output_json['Rows']):
        time_stamp, data = next(iter(row.items()))
        time_stamp_dt = parse_energyplus_timestamp(time_stamp)
        m_dot = data[mass_flow_index]
        cp = data[cp_index]
        t_in = data[tin_index]
        t_out = data[tout_index]
        qdot = m_dot * cp * (t_in - t_out)
        times.append(row_num)
        time_labels.append(time_stamp_dt.strftime("%m-%d %H:%M"))
        qdot_list.append(qdot)

    return times, qdot_list, time_labels

def integrate_daily_peak_energy(qdot_list, time_labels, peak_start=12, peak_end=21, timestep_sec=360):
    daily_peak_energy = defaultdict(float)
    labels_filtered = time_labels[-len(qdot_list):]  # Ensure alignment

    for i, time_str in enumerate(labels_filtered):
        dt = datetime.strptime(time_str, "%m-%d %H:%M")
        hour = dt.hour
        if peak_start <= hour < peak_end:
            energy_joules = qdot_list[i] * timestep_sec
            date_key = dt.strftime("%m-%d")
            daily_peak_energy[date_key] += energy_joules
    return daily_peak_energy

def estimate_chiller_and_tank_sizes(peak_energy_j, peak_day, qdot_list, time_labels, peak_start=12, peak_end=21, timestep_sec=360):
    sensible_gal = calc_sensible_volume_gal(peak_energy_j)
    latent_gal = calc_latent_volume_gal(peak_energy_j)

    # Off-peak hours
    offpeak_hours = list(range(0, peak_start)) + list(range(peak_end, 24))
    offpeak_times = []
    offpeak_qdot = []
    offpeak_charging_qdot = []

    for i, time_str in enumerate(time_labels):
        dt = datetime.strptime(time_str, "%m-%d %H:%M")
        hour = dt.hour
        if hour in offpeak_hours:
            offpeak_times.append(time_str)
            building_load = qdot_list[i]
            offpeak_qdot.append(building_load)

            avg_charging_power = peak_energy_j / (len(offpeak_times) * timestep_sec)
            total_chiller_power = building_load + avg_charging_power
            offpeak_charging_qdot.append(total_chiller_power)

    max_chiller_power_kw = max(offpeak_charging_qdot) / 1000.0

    return sensible_gal, latent_gal, max_chiller_power_kw, offpeak_qdot, offpeak_charging_qdot

def tank_energy_depletion_check(peak_qdot_list, peak_energy_j, timestep_sec=360, threshold_ratio=0.05):
    initial_energy_j = peak_energy_j
    remaining_energy_j_list = []

    for qdot in peak_qdot_list:
        energy_used = qdot * timestep_sec
        initial_energy_j -= energy_used
        remaining_energy_j_list.append(initial_energy_j)

    final_energy_j = remaining_energy_j_list[-1]
    threshold_energy_j = peak_energy_j * threshold_ratio

    return final_energy_j, threshold_energy_j

def main():
    if len(argv) < 2:
        print("Usage: python main.py <EnergyPlus_dir> [local]")
        sys.exit(1)

    eplus_dir = Path(argv[1])
    local = len(argv) > 2 and argv[2].lower() == 'local'

    output_dir = prepare_directory(eplus_dir, local)

    helper = EPlusAPIHelper(eplus_dir)
    api = helper.get_api_instance()
    state_1 = api.state_manager.new_state()
    state_2 = api.state_manager.new_state()

    weather_file = eplus_dir / 'WeatherData' / 'USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw'

    baseline_json, modified_json, json_convert_dir = copy_and_modify_json(output_dir)

    output_dir_baseline, output_dir_secondary = run_energyplus_simulations(
        api, state_1, state_2, weather_file, baseline_json, modified_json, output_dir
    )

    base_times, base_qdot, base_time_labels = calc_qdot_from_output(output_dir_baseline / 'eplusout_hourly.json')
    sec_times, sec_qdot, sec_time_labels = calc_qdot_from_output(output_dir_secondary / 'eplusout_hourly.json')

    # Plot cooling power comparison
    plt.plot(base_times, base_qdot, label="Baseline")
    plt.plot(sec_times, sec_qdot, label="Secondary")
    plt.title("Calculated Cooling Power (Qdot)")
    plt.xticks(base_times[::24], base_time_labels[::24])
    plt.xlabel("Time")
    plt.ylabel("Watts")
    plt.theme("pro")
    plt.show()

    # Energy integration and tank/chiller sizing
    daily_peak_energy = integrate_daily_peak_energy(sec_qdot, sec_time_labels)
    print("\nDaily Peak Energy Use (peak hours: 12PM – 9PM):")
    for date, energy_j in sorted(daily_peak_energy.items()):
        energy_kwh = energy_j / (3600 * 1000)
        print(f"{date}: {energy_kwh:.2f} kWh")

    peak_day, peak_energy_j = max(daily_peak_energy.items(), key=lambda x: x[1])
    peak_energy_kwh = peak_energy_j / (3600 * 1000)
    print(f"\nMaximum Daily Peak Energy Use : {peak_day} = {peak_energy_kwh:.2f} kWh")

    max_qdot_watts = max(sec_qdot)
    max_qdot_kw = max_qdot_watts / 1000
    print(f"\nMaximum Cooling Power: {max_qdot_kw:.2f} kW")

    peak_qdot_list = []
    for i, time_str in enumerate(sec_time_labels):
        dt = datetime.strptime(time_str, "%m-%d %H:%M")
        hour = dt.hour
        date_key = dt.strftime("%m-%d")
        if 12 <= hour < 21 and date_key == peak_day:
            peak_qdot_list.append(sec_qdot[i])

    sensible_gal, latent_gal, max_chiller_power_kw, offpeak_qdot, offpeak_charging_qdot = estimate_chiller_and_tank_sizes(
        peak_energy_j, peak_day, sec_qdot, sec_time_labels
    )

    print("\nEstimated Tank Sizes:")
    print(f"Estimated Water Tank (Sensible) Size: {sensible_gal:.2f} gal")
    print(f"Estimated Ice Tank (Latent) Size: {latent_gal:.2f} gal")

    print("\nChiller Sizing Considering Off-Peak Recharging and Building Load:")
    print(f"Estimated Maximum Chiller Power Needed (including recharge and load): {max_chiller_power_kw:.2f} kW")

    # Plot off-peak loads
    plt.clear_data()
    plt.plot(range(len(offpeak_qdot)), [q / 1000 for q in offpeak_qdot], label="Off-peak Load Only")
    plt.plot(range(len(offpeak_charging_qdot)), [q / 1000 for q in offpeak_charging_qdot], label="Off-peak Load + Recharge")
    plt.title("Off-Peak Chiller Load (kW)")
    plt.xlabel("Time")
    plt.ylabel("Power (kW)")
    plt.show()

    # Tank energy depletion check
    final_energy_j, threshold_energy_j = tank_energy_depletion_check(peak_qdot_list, peak_energy_j)

    final_energy_kj = final_energy_j / 1000.0
    threshold_energy_kj = threshold_energy_j / 1000.0

    print("\nTank Energy Depletion Check (End of Peak Period):")
    print(f"Final Tank Energy: {final_energy_kj:.2f} kJ ({final_energy_j} J)")
    print(f"5% Threshold Energy: {threshold_energy_kj:.2f} kJ")

    if final_energy_j <= 0:
        print("Warning: Threshold energy is zero or negative. Consider adjusting tank size.")
    elif final_energy_j <= threshold_energy_j:
        print("Tank energy was sufficiently depleted during the peak period.")
    else:
        print("Tank energy was not fully utilized. Consider adjusting tank size or charging strategy.")

    # Create zip archives if not running locally
    if not local:
        make_archive('conversion', 'zip', root_dir=json_convert_dir)
        print("Zipped json_convert directory.")
        make_archive('baseline', 'zip', root_dir=output_dir_baseline)
        print("Zipped output_baseline directory.")
        make_archive('secondary', 'zip', root_dir=output_dir_secondary)
        print("Zipped output_dir_secondary directory.")

if __name__ == "__main__":
    main()