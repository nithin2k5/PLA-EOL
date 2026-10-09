"""Application settings, backed by the .config file next to this module.

Settings are grouped into sections for readability, but keys are unique
across the whole file, so callers just ask for a key by name:

    import config

    port = config.get('PLC_COM_PORT', 'COM5')
    config.set('PLC_COM_PORT', 'COM7')     # written to disk immediately

Database credentials are not kept here - those live in db.py.
"""

import configparser
import os
import threading

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.config')

# Section a key belongs to when it is written for the first time. Anything
# not listed lands in FALLBACK_SECTION.
FALLBACK_SECTION = 'Settings'

DEFAULTS = {
    'Machine': {
        'MACHINE_ID': '',
        'PRIMARY_BACKUP_PATH': '',
        'SECONDARY_BACKUP_PATH': '',
    },
    'PLC': {
        'PLC_COM_PORT': 'COM5',
        'PLC_BAUD_RATE': '38400',
        'PLC_STATION_ID': '1',
        'PLC_REG_ADDRESS': '',
        'PLC_POINTS_TO_READ': '1',
        'PLC_BYTESIZE': '8',
        'PLC_PARITY': 'N',
        'PLC_STOPBITS': '1',
        'PLC_TIMEOUT': '1.0',
        'PLC_READ_TIMEOUT': '500',
        'PLC_WRITE_TIMEOUT': '500',
        'PLC_RETRIES': '0',
        'PLC_RX_DATA': '',
    },
    'ModbusTCP': {
        'MODBUS_TCP_IP': '',
        'MODBUS_TCP_PORT': '',
    },
    'Loadcell': {
        'LOADCELL_01_COM_PORT': '',
        'LOADCELL_01_BAUD_RATE': '9600',
        'LOADCELL_01_RX_DATA': '',
        'LOADCELL_02_COM_PORT': '',
        'LOADCELL_02_BAUD_RATE': '9600',
        'LOADCELL_02_RX_DATA': '',
        'LOADCELL_03_COM_PORT': '',
        'LOADCELL_03_BAUD_RATE': '9600',
        'LOADCELL_03_RX_DATA': '',
        'LOADCELL_04_COM_PORT': '',
        'LOADCELL_04_BAUD_RATE': '9600',
        'LOADCELL_04_RX_DATA': '',
    },
    'LVDT': {
        'LVDT_COM_PORT': '',
        'LVDT_BAUD_RATE': '9600',
    },
    'Camera': {
        'CAMERA_01_COM_PORT': '',
        'CAMERA_01_BAUD_RATE': '9600',
        'CAMERA_02_COM_PORT': '',
        'CAMERA_02_BAUD_RATE': '9600',
    },
    'Testing': {
        'ALC_INPUT_TIME_INTERVAL': '3000',
        'PRINTED_LABEL_SCAN_TIME_INTERVAL': '4000',
        'PRINTED_LABEL_SCAN_WAIT_TIME': '6000',
        'ALERT_ON_TIME_INTERVAL': '5000',
        # Windows printer that barcode labels are sent to, as raw printer
        # commands
        'LABEL_PRINTER_NAME': 'EOL_LABEL_PRNTR',
    },
    'Flags': {
        'PLC_SIMULATION_MODE': 'false',
        'DATABASE_SIMULATION_MODE': 'false',
        'DEBUG_MODE': 'true',
        'ENABLE_CONSOLE_OUTPUT': 'true',
    },
    'Files': {
        'INPUT_SENSORS_FILE': 'InputSensors.txt',
        'PROCESS_STATUS_FILE': 'ProcessStatus.txt',
        'INPUT_REGISTERS_FILE': 'InputRegisters.txt',
        'MACHINE_ON_PLC_ADDRESS_FILE': 'MachineOnPLCCoilAddress.txt',
        'ALERT_ON_PLC_ADDRESS_FILE': 'AlertOnPLCCoilAddress.txt',
        'EMPLOYEE_CODES_FILE': 'EmployeeCodes.txt',
    },
    'Screen': {
        'SCREEN_WIDTH': '1920',
        'SCREEN_HEIGHT': '1080',
    },
    'Support': {
        'SERVICE_ACCOUNT_USER': '',
        'SERVICE_ACCOUNT_PASSWORD_HASH': '',
        # Shown on the Contact page. Blank means the page says so rather
        # than printing a number nobody answers.
        'SUPPORT_COMPANY': 'Nice Computers & Industrial Solutions',
        'SUPPORT_EMAIL': '',
        'SUPPORT_PHONE': '',
    },
}

TRUE_VALUES = ('1', 'true', 'yes', 'on')

_lock = threading.RLock()
_parser = None


def _new_parser():
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # keep keys upper case instead of folding them
    return parser


def _default_section_for(key):
    for section, entries in DEFAULTS.items():
        if key in entries:
            return section
    return FALLBACK_SECTION


def _write(parser):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as handle:
        parser.write(handle)


def _create_default_file():
    parser = _new_parser()
    for section, entries in DEFAULTS.items():
        parser[section] = dict(entries)
    _write(parser)
    return parser


def _load():
    """Return the parser, reading .config the first time it is needed."""
    global _parser
    with _lock:
        if _parser is None:
            if os.path.exists(CONFIG_FILE):
                _parser = _new_parser()
                _parser.read(CONFIG_FILE, encoding='utf-8')
            else:
                _parser = _create_default_file()
        return _parser


def reload():
    """Drop the cached settings so the next read picks up file changes."""
    global _parser
    with _lock:
        _parser = None
    return _load()


def get(key, default=''):
    """Value for key from any section, or default when it is missing or blank."""
    parser = _load()
    with _lock:
        for section in parser.sections():
            if parser.has_option(section, key):
                value = parser.get(section, key).strip()
                return value if value else default
    return default


def get_int(key, default=0):
    try:
        return int(str(get(key, default)).strip())
    except (TypeError, ValueError):
        return default


def get_float(key, default=0.0):
    try:
        return float(str(get(key, default)).strip())
    except (TypeError, ValueError):
        return default


def get_bool(key, default=False):
    value = get(key, None)
    if value is None:
        return default
    return str(value).strip().lower() in TRUE_VALUES


def set(key, value):
    """Store a value and save straight away, so other consoles see it."""
    parser = _load()
    with _lock:
        target = None
        for section in parser.sections():
            if parser.has_option(section, key):
                target = section
                break

        if target is None:
            target = _default_section_for(key)
            if not parser.has_section(target):
                parser.add_section(target)

        parser.set(target, key, '' if value is None else str(value))
        _write(parser)
    return value


def has(key):
    parser = _load()
    with _lock:
        return any(parser.has_option(section, key) for section in parser.sections())


def as_dict():
    """Every setting as a flat key/value mapping."""
    parser = _load()
    with _lock:
        values = {}
        for section in parser.sections():
            values.update(dict(parser.items(section)))
        return values


if __name__ == '__main__':
    print(f"{CONFIG_FILE}\n")
    for section, entries in sorted(_load().items()):
        if section == 'DEFAULT':
            continue
        print(f"[{section}]")
        for key, value in entries.items():
            print(f"  {key} = {value}")
        print()
