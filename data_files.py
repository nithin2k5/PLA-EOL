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


PART_LABEL_COUNT = 16
PART_LABEL_LENGTH = 3


def part_label_names():
    """Names shown for sensor labels L1-L16, and what is wrong with the file.

    partlabels.txt holds 16 comma separated names, three characters each,
    no two alike. If it is missing or breaks a rule the labels keep their
    own names, and the reason comes back so the screen can say so. Only
    what is shown changes: positions, results and PLC addresses are still
    keyed L1-L16.
    """
    keys = [f"L{n}" for n in range(1, PART_LABEL_COUNT + 1)]
    file_path = path(PART_LABELS)
    try:
        with open(file_path, 'r') as f:
            content = f.read().strip()
    except OSError:
        return dict(zip(keys, keys)), f"{PART_LABELS} not found at {file_path}"

    names = [name.strip() for name in content.split(',')]
    if len(names) != PART_LABEL_COUNT:
        problem = (f"{PART_LABELS} must hold exactly {PART_LABEL_COUNT} comma "
                   f"separated label names, with nothing after the last one")
    elif any(len(name) != PART_LABEL_LENGTH for name in names):
        problem = f"Every label name in {PART_LABELS} must be {PART_LABEL_LENGTH} characters long"
    elif len(set(names)) != len(names):
        problem = f"{PART_LABELS} must not repeat a label name"
    else:
        return dict(zip(keys, names)), None
    return dict(zip(keys, keys)), problem


def shown_label_text(names, key, text):
    """Text a label shows: what was saved for it, or its name from
    partlabels.txt when nothing was or the label's own key was saved."""
    if not text or text == key:
        return names.get(key, key)
    return text


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
