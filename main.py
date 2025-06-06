from pathlib import Path
from sys import argv

from energyplus_api_helpers.import_helper import EPlusAPIHelper

eplus_dir = argv[1]

helper = EPlusAPIHelper(Path(eplus_dir))
api = helper.get_api_instance()
state = api.state_manager.new_state()
return_value = api.runtime.run_energyplus(state, ['-D', helper.path_to_test_file('5ZoneAirCooled.idf')])