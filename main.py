from json import dumps, loads
from pathlib import Path
from shutil import copyfile, make_archive
from subprocess import call
from sys import argv

from energyplus_api_helpers.import_helper import EPlusAPIHelper

eplus_dir = Path(argv[1])
convert_input_format = eplus_dir / 'ConvertInputFormat'

helper = EPlusAPIHelper(eplus_dir)
api = helper.get_api_instance()
state = api.state_manager.new_state()

current_dir = Path(__file__).parent
json_convert_dir = current_dir / 'epjson_convert'
output_dir_baseline = current_dir / 'output_baseline'
output_dir_secondary = current_dir / 'output_secondary'

# this simplifies when we just downselect a file and use the epjson version of it
idf_to_run = Path(helper.path_to_test_file('5ZoneAirCooled.idf'))
copied_idf = json_convert_dir / idf_to_run.name
converted_json = json_convert_dir / '5ZoneAirCooled.epJSON'
copyfile(idf_to_run, copied_idf)
call([convert_input_format, idf_to_run.name], shell=False, cwd=json_convert_dir)
print("Converted IDF to JSON format.")

return_value = api.runtime.run_energyplus(state, ['-D', converted_json, '-d', output_dir_baseline])
assert(return_value == 0, "EnergyPlus run failed with original JSON input file.")
print("Ran EnergyPlus with baseline JSON input file.")

contents = loads(converted_json.read_text())
print("Read JSON contents to prepare for modifications.")

contents['Timestep']['Timestep 1']['number_of_timesteps_per_hour'] = 1
new_json_file = output_dir_secondary / '5ZoneAirCooledMod.epJSON'
new_json_file.write_text(dumps(contents, indent=4))
print("Rewrote JSON file with modified timestep.")

return_value = api.runtime.run_energyplus(state, ['-D', new_json_file, '-d', output_dir_secondary])
assert(return_value == 0, "EnergyPlus run failed with modified JSON input file.")
print("Ran EnergyPlus with baseline JSON input file.")

make_archive('baseline', 'zip', root_dir=output_dir_baseline)
print("Zipped output_baseline directory.")

make_archive('secondary', 'zip', root_dir=output_dir_secondary)
print("Zipped output_dir_secondary directory.")
