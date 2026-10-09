"""Where the machine's data files live: PLC address lists, employee codes,
barcode print file names and the label printer (.prn) files.

On the test machine they sit in the project folder itself; a development
copy may keep them in txt_files/. The project folder is looked in first.

    import data_files

    with open(data_files.path(data_files.PROCESS_STATUS)) as f:
        ...
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SEARCH_DIRS = (BASE_DIR, os.path.join(BASE_DIR, 'txt_files'))

ALERT_ON_PLC_COIL_ADDRESS = 'AlertOnPLCCoilAddress.txt'
BARCODE_PRINT_FILE_NAMES = 'BarcodePrintFileNames.txt'
EMPLOYEE_CODES = 'EmployeeCodes.txt'
INPUT_REGISTERS = 'InputRegisters.txt'
INPUT_SENSORS = 'InputSensors.txt'
MACHINE_ON_PLC_COIL_ADDRESS = 'MachineOnPLCCoilAddress.txt'
PART_LABELS = 'partlabels.txt'
PLC_ON_REGISTER = 'PLC_on_register.txt'
PROCESS_STATUS = 'ProcessStatus.txt'
PROGRAM_SELECTION_IN_PLC = 'ProgramSelectionInPLC.txt'

# Names a file went by before, still found if the new name is not there
FORMER_NAMES = {
    INPUT_REGISTERS: ('HoldRegistersRead.txt',),
}


def path(name):
    """Full path of a data file.

    A file that is in neither folder gets its project folder path, so a
    warning names the place it should be and a new file is created there.
    A label file named without its extension is also found as name.prn.
    """
    names = (name,) + FORMER_NAMES.get(name, ())
    if not os.path.splitext(name)[1]:
        names += (name + '.prn',)
    for directory in SEARCH_DIRS:
        for candidate in names:
            full = os.path.join(directory, candidate)
            if os.path.isfile(full):
                return full
    return os.path.join(BASE_DIR, name)
