from json import dumps, loads
from pathlib import Path
from shutil import copy, make_archive
from subprocess import call
from sys import argv

from energyplus_api_helpers.import_helper import EPlusAPIHelper

eplus_dir = Path(argv[1])
convert_input_format = eplus_dir / 'ConvertInputFormat'

helper = EPlusAPIHelper(eplus_dir)
api = helper.get_api_instance()
state_1 = api.state_manager.new_state()
state_2 = api.state_manager.new_state()

current_dir = Path(__file__).parent
idf_to_run = Path(helper.path_to_test_file('5ZoneAirCooled.idf'))

# create a directory to handle IDF->JSON conversion and even JSON modifications
json_convert_dir = current_dir / 'json_convert'
json_convert_dir.mkdir()
copied_idf = json_convert_dir / idf_to_run.name
baseline_json = json_convert_dir / '5ZoneAirCooled.epJSON'
modified_json = json_convert_dir / '5ZoneAirCooledMod.epJSON'
print("Made JSON conversion directory and set up file paths.")

# actually copy the IDF to the conversion directory and convert it to JSON
copy(idf_to_run, json_convert_dir)
call([convert_input_format, idf_to_run.name], shell=False, cwd=json_convert_dir)
print("Converted IDF to baseline JSON file.")

# now read in the JSON file that was created
contents = loads(baseline_json.read_text())
print("Read in JSON file from baseline IDF conversion.")

# then modify it and write it out as the modified version
contents['Timestep']['Timestep 1']['number_of_timesteps_per_hour'] = 1
modified_json.write_text(dumps(contents, indent=4))
print("Modified JSON and rewrote to new file")

# run EnergyPlus with the baseline and modified JSON files
output_dir_baseline = current_dir / 'output_baseline'
output_dir_secondary = current_dir / 'output_secondary'
api.runtime.run_energyplus(state_1, ['-D', baseline_json.__str__(), '-d', output_dir_baseline.__str__()])
api.runtime.run_energyplus(state_2, ['-D', modified_json.__str__(), '-d', output_dir_secondary.__str__()])
print("Ran EnergyPlus with baseline JSON input file.")

make_archive('baseline', 'zip', root_dir=output_dir_baseline)
print("Zipped output_baseline directory.")

make_archive('secondary', 'zip', root_dir=output_dir_secondary)
print("Zipped output_dir_secondary directory.")
