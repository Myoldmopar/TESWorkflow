from datetime import datetime, timedelta
from json import dumps, loads
from pathlib import Path
import plotext as plt
from shutil import copy, make_archive, rmtree
from subprocess import call
from sys import argv

from energyplus_api_helpers.import_helper import EPlusAPIHelper

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
convert_input_format = eplus_dir / 'ConvertInputFormat'

helper = EPlusAPIHelper(eplus_dir)
api = helper.get_api_instance()
state_1 = api.state_manager.new_state()
state_2 = api.state_manager.new_state()

idf_to_run = Path(helper.path_to_test_file('5ZoneAirCooled.idf'))

# create a directory to handle IDF->JSON conversion and even JSON modifications
json_convert_dir = output_dir / 'json_convert'
json_convert_dir.mkdir()
copied_idf = json_convert_dir / idf_to_run.name
converted_json_file = json_convert_dir / '5ZoneAirCooled.epJSON'
baseline_json = json_convert_dir / '5ZoneAirCooledBase.epJSON'
modified_json = json_convert_dir / '5ZoneAirCooledMod.epJSON'
print("Made JSON conversion directory and set up file paths.")

# actually copy the IDF to the conversion directory and convert it to JSON
copy(idf_to_run, json_convert_dir)
call([convert_input_format, idf_to_run.name], shell=False, cwd=json_convert_dir)
print("Converted IDF to baseline JSON file.")

# now read in the JSON file that was created
baseline_json_data = loads(converted_json_file.read_text())
secondary_json_data = loads(converted_json_file.read_text())
print("Read in JSON file from baseline IDF conversion.")

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
api.runtime.run_energyplus(state_1, ['-D', baseline_json.__str__(), '-d', output_dir_baseline.__str__()])
api.runtime.run_energyplus(state_2, ['-D', modified_json.__str__(), '-d', output_dir_secondary.__str__()])
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
base_space_3_times = []
base_space_3_temp = []
base_col_index = 0
for base_col_index, b in enumerate(base_cols):
    if b['Variable'] == 'SPACE3-1:Zone Air Temperature':
        break
for row_num, row in enumerate(base_output['Rows']):
    time_stamp, data = next(iter(row.items()))
    time_stamp_dt = parse_eplus_timestamp(time_stamp)
    # base_space_3_times.append(time_stamp_dt)
    base_space_3_times.append(row_num)
    base_space_3_temp.append(data[base_col_index])
secondary_output = loads((output_dir_secondary / 'eplusout_hourly.json').read_text())
secondary_cols = secondary_output['Cols']
secondary_space_3_times = []
secondary_space_3_temp = []
secondary_col_index = 0
for secondary_col_index, b in enumerate(secondary_cols):
    if b['Variable'] == 'SPACE3-1:Zone Air Temperature':
        break
for row_num, row in enumerate(secondary_output['Rows']):
    time_stamp, data = next(iter(row.items()))
    time_stamp_dt = parse_eplus_timestamp(time_stamp)
    # secondary_space_3_times.append(time_stamp_dt)
    secondary_space_3_times.append(row_num)
    secondary_space_3_temp.append(data[secondary_col_index])

plt.plot(base_space_3_times, base_space_3_temp, label="Baseline")
plt.plot(secondary_space_3_times, secondary_space_3_temp, label="Secondary")
plt.title("SPACE3-1:Zone Air Temperature")
plt.xlabel("Time")
plt.ylabel("Temperature")
plt.theme("pro")
plt.show()

# print(base_space_3_temp)
# print(secondary_space_3_temp)

if not local:
    make_archive('conversion', 'zip', root_dir=json_convert_dir)
    print("Zipped json_convert directory.")
    make_archive('baseline', 'zip', root_dir=output_dir_baseline)
    print("Zipped output_baseline directory.")
    make_archive('secondary', 'zip', root_dir=output_dir_secondary)
    print("Zipped output_dir_secondary directory.")
