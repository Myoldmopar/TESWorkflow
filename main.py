from datetime import datetime, timedelta
from json import dumps, loads
from pathlib import Path
import plotext as plt
from shutil import copy, make_archive, rmtree
from subprocess import call
from sys import argv
from energyplus_api_helpers.import_helper import EPlusAPIHelper
from collections import defaultdict


def calc_sensible_volume_gal(Q_joules, delta_T=5.0): #default delta_T is 5C, typical range for Chilled Water TES Delta_T
    CP_WATER = 4186  # J/kg·K
    DENSITY_WATER = 997  # kg/m3 at 25C
    volume_m3 = Q_joules / (DENSITY_WATER * CP_WATER * delta_T)
    volume_gal = volume_m3 / 0.00378541
    return volume_gal

def calc_latent_volume_gal(Q_joules):
    LF_ICE = 334000  # J/kg, latent heat of fusion for ice
    DENSITY_ICE = 917  # kg/m³
    volume_m3 = Q_joules / (DENSITY_ICE * LF_ICE)
    volume_gal = volume_m3 / 0.00378541
    return volume_gal

eplus_dir = Path(argv[1])
if len(argv) > 2 and argv[2] == 'local':
    output_dir = Path(__file__).parent / 'output'
    if output_dir.exists():
        rmtree(output_dir)
    output_dir.mkdir()
    local = True
else:
    output_dir = Path(__file__).parent
    local = False

helper = EPlusAPIHelper(eplus_dir)
api = helper.get_api_instance()
state_1 = api.state_manager.new_state()
state_2 = api.state_manager.new_state()

root_epjson = Path(__file__).parent / 'PlantLoadProfile_TESsizing.epJSON'
if not root_epjson.exists():
    raise FileNotFoundError("'PlantLoadProfile_TESsizing.epJSON' not found.")
idf_to_run = Path(helper.path_to_test_file('PlantLoadProfile_TESsizing.idf'))
weather_file = eplus_dir / 'WeatherData' / 'USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw'

# create a directory to handle IDF->JSON conversion and even JSON modifications
json_convert_dir = output_dir / 'json_convert'
json_convert_dir.mkdir()
copied_epjson = json_convert_dir / 'PlantLoadProfile_TESsizing.epJSON'
copy(root_epjson, copied_epjson)
print("Copied EPJSON from root directory.")
baseline_json = json_convert_dir / 'PlantLoadProfile_TESsizingBase.epJSON'
modified_json = json_convert_dir / 'PlantLoadProfile_TESsizingMod.epJSON'
print("Made JSON conversion directory and set up file paths.")

# now read in the JSON file that was created
baseline_json_data = loads(copied_epjson.read_text())
secondary_json_data = loads(copied_epjson.read_text())
print("Read in JSON file from root.")

# write out the baseline file with any required modifications
baseline_json_data['OutputControl:Files'] = {'OutputControl:Files 1': {'output_json': 'Yes'}}
baseline_json_data['Output:JSON'] = {'Output:JSON 1': {'output_json': 'Yes', 'option_type': 'TimeSeries'}}
baseline_json.write_text(dumps(baseline_json_data, indent=2))

# write out the secondary file with all required modifications
secondary_json_data['Timestep']['Timestep 1']['number_of_timesteps_per_hour'] = 10
secondary_json_data['OutputControl:Files'] = {'OutputControl:Files 1': {'output_json': 'Yes'}}
secondary_json_data['Output:JSON'] = {'Output:JSON 1': {'output_json': 'Yes', 'option_type': 'TimeSeries'}}
modified_json.write_text(dumps(secondary_json_data, indent=4))
print("Modified JSON and rewrote to new file")

# run EnergyPlus with the baseline and modified JSON files
output_dir_baseline = output_dir / 'output_baseline'
output_dir_secondary = output_dir / 'output_secondary'
api.runtime.run_energyplus(state_1, ['-r', '-w', weather_file.__str__(), baseline_json.__str__(), '-d', output_dir_baseline.__str__()])
api.runtime.run_energyplus(state_2, ['-r', '-w', weather_file.__str__(), modified_json.__str__(), '-d', output_dir_secondary.__str__()])
print("Ran EnergyPlus with baseline JSON input file.")

def parse_eplus_timestamp(ts: str) -> datetime:
    date_part, time_part = ts.split()
    hour, minute, second = map(int, time_part.split(':'))
    if hour == 24:
        dt = datetime.strptime(date_part, "%m/%d")
        dt += timedelta(days=1)
        return dt.replace(hour=0, minute=minute, second=second)
    else:
        return datetime.strptime(ts, "%m/%d %H:%M:%S")

# grab the time series data from each output json
base_output = loads((output_dir_baseline / 'eplusout_hourly.json').read_text())
base_cols = base_output['Cols']
base_times = []
base_qdot = []
time_labels = []

# Find column indices for needed variables
mass_flow_index = cp_index = tin_index = tout_index = None
for idx, col in enumerate(base_cols):
    if col['Variable'] == 'SUPPLY OUTLET NODE:System Node Mass Flow Rate':
        mass_flow_index = idx
    elif col['Variable'] == 'SUPPLY OUTLET NODE:System Node Specific Heat':
        cp_index = idx
    elif col['Variable'] == 'SUPPLY INLET NODE:System Node Temperature':
        tin_index = idx
    elif col['Variable'] == 'SUPPLY OUTLET NODE:System Node Temperature':
        tout_index = idx

assert None not in (mass_flow_index, cp_index, tin_index, tout_index), "Missing one or more required variables in baseline output"

for row_num, row in enumerate(base_output['Rows']):
    time_stamp, data = next(iter(row.items()))
    time_stamp_dt = parse_eplus_timestamp(time_stamp)
    m_dot = data[mass_flow_index]
    cp = data[cp_index]
    t_in = data[tin_index]
    t_out = data[tout_index]
    qdot = m_dot * cp * (t_in - t_out)
    base_times.append(row_num)
    time_labels.append(time_stamp_dt.strftime("%m-%d %H:%M"))
    base_qdot.append(qdot)

# Same for secondary
secondary_output = loads((output_dir_secondary / 'eplusout_hourly.json').read_text())
secondary_cols = secondary_output['Cols']
secondary_times = []
secondary_qdot = []

mass_flow_index = cp_index = tin_index = tout_index = None
for idx, col in enumerate(secondary_cols):
    if col['Variable'] == 'SUPPLY OUTLET NODE:System Node Mass Flow Rate':
        mass_flow_index = idx
    elif col['Variable'] == 'SUPPLY OUTLET NODE:System Node Specific Heat':
        cp_index = idx
    elif col['Variable'] == 'SUPPLY INLET NODE:System Node Temperature':
        tin_index = idx
    elif col['Variable'] == 'SUPPLY OUTLET NODE:System Node Temperature':
        tout_index = idx

assert None not in (mass_flow_index, cp_index, tin_index, tout_index), "Missing one or more required variables in secondary output"

for row_num, row in enumerate(secondary_output['Rows']):
    time_stamp, data = next(iter(row.items()))
    time_stamp_dt = parse_eplus_timestamp(time_stamp)
    m_dot = data[mass_flow_index]
    cp = data[cp_index]
    t_in = data[tin_index]
    t_out = data[tout_index]
    qdot = m_dot * cp * (t_in - t_out)
    secondary_times.append(row_num)
    time_labels.append(time_stamp_dt.strftime("%m-%d %H:%M"))
    secondary_qdot.append(qdot)

# Plot Qdot
plt.plot(base_times, base_qdot, label="Baseline")
plt.plot(secondary_times, secondary_qdot, label="Secondary")
plt.title("Calculated Cooling Power (Q̇)")
plt.xticks(base_times[::24], time_labels[::24])
plt.xlabel("Time")
plt.ylabel("Watts")  # since J/s
plt.theme("pro")
plt.show()

if not local:
    make_archive('conversion', 'zip', root_dir=json_convert_dir)
    print("Zipped json_convert directory.")
    make_archive('baseline', 'zip', root_dir=output_dir_baseline)
    print("Zipped output_baseline directory.")
    make_archive('secondary', 'zip', root_dir=output_dir_secondary)
    print("Zipped output_dir_secondary directory.")

# Integrate energy during peak hours
Peak_start_hour = 12 # peak hours for summer starts at 12PM
Peak_end_hour = 21 # peak hours for summer ends at 9PM
Seconds_per_timestep = 360  # 'number_of_timesteps_per_hour' of secondary json data = 10,  6 minutes = 360 seconds

# Store daily energy totals
daily_peak_energy = defaultdict(float)

# truncate time_labels to match secondary_qdot length, to multiply correctly secondary_qdot[i] * Seconds_per_timestep
time_labels = time_labels[-len(secondary_qdot):] 

for i, time_str in enumerate(time_labels):
    dt = datetime.strptime(time_str, "%m-%d %H:%M")
    hour = dt.hour

    if Peak_start_hour <= hour < Peak_end_hour:
        energy_joules = secondary_qdot[i] * Seconds_per_timestep
        date_key = dt.strftime("%m-%d")
        daily_peak_energy[date_key] += energy_joules

# Print energy usage in kWh
print("\nDaily Peak Energy Use (peak hours: 12PM – 9PM):")
for date, energy_j in sorted(daily_peak_energy.items()):
    energy_kwh = energy_j / (3600 * 1000)
    print(f"{date}: {energy_kwh:.2f} kWh")

# Find the maximum daily peak energy use
peak_day, peak_energy_j = max(daily_peak_energy.items(), key=lambda x: x[1])
peak_energy_kwh = peak_energy_j / (3600 * 1000)
print(f"\nMaximum Daily Peak Energy Use : {peak_day} = {peak_energy_kwh:.2f} kWh")

# estimate tank sizes
print("\nEstimated Tank Sizes:")
sensible_gal = calc_sensible_volume_gal(peak_energy_j)
latent_gal = calc_latent_volume_gal(peak_energy_j)
print(f"Estimated Water Tank (Sensible) Size: {sensible_gal:.2f} gal")
print(f"Estimated Ice Tank (Latent) Size: {latent_gal:.2f} gal")
