"""Central MySQL access for the EOL Tester.

Every console goes through here for its connection settings and for the
schema, so the table definitions live in one place instead of being
repeated - and contradicted - across modules.

Typical use:

    import db

    conn = db.connect()
    cursor = conn.cursor()

Call init_database() once at start-up to create the database and any
missing tables. Existing tables are never altered.
"""

import mysql.connector

HOST = 'localhost'
PORT = 3306
USER = 'root'
PASSWORD = '12345'
DATABASE = 'EOL'


def get_config(**overrides):
    """Connection settings as a dict, for code that wants to keep its own copy."""
    config = {
        'host': HOST,
        'port': PORT,
        'user': USER,
        'password': PASSWORD,
        'database': DATABASE,
    }
    config.update(overrides)
    return config


def connect(**overrides):
    """Open a connection. Extra keyword arguments are passed to the driver."""
    return mysql.connector.connect(**get_config(**overrides))


def connect_server(**overrides):
    """Open a connection to the server without selecting a database."""
    config = get_config(**overrides)
    config.pop('database', None)
    return mysql.connector.connect(**config)


# Table definitions, created in order so the foreign key below resolves.
SCHEMA = [
    ("EMPLOYEE_INFO", """
        CREATE TABLE IF NOT EXISTS EMPLOYEE_INFO (
            ID INT AUTO_INCREMENT PRIMARY KEY,
            EMPLOYEE_FULL_NAME VARCHAR(255),
            EMPLOYEE_NUMBER VARCHAR(50) UNIQUE,
            PASSWORD VARCHAR(255),
            DESIGNATION VARCHAR(100),
            DEPARTMENT VARCHAR(100),
            MOBILE_NUMBER VARCHAR(20),
            MACHINE_ID VARCHAR(100),
            IS_ACTIVE BOOLEAN DEFAULT TRUE,
            CREATED_DATE TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """),

    ("TBL_MODEL_MASTER", """
        CREATE TABLE IF NOT EXISTS TBL_MODEL_MASTER (
            ID INT AUTO_INCREMENT PRIMARY KEY,
            MM_PART_NUMBER VARCHAR(255) UNIQUE,
            MM_MODEL_NAME VARCHAR(255),
            MM_ALC_CODE VARCHAR(255),
            MM_PLC_ADDRESS VARCHAR(255),
            MM_BARCODE_LABEL_CODE VARCHAR(255),
            MM_IMAGE_PATH VARCHAR(255),
            MM_VENDOR_CODE VARCHAR(255),
            MM_EO_NUMBER VARCHAR(255),
            MM_SPECIAL_DATA VARCHAR(255),
            MM_INITIAL_ID VARCHAR(255),
            MM_SUPPLIER_SECTION VARCHAR(255),
            MM_CREATED_BY VARCHAR(255),
            MM_CREATED_DATE DATETIME,
            MM_STATUS TINYINT(1),
            MM_MODIFIED_BY VARCHAR(255),
            MM_MODIFIED_DATE DATETIME,
            MM_LABEL_POSITIONS JSON,
            MM_LABEL_COORDINATES JSON
        )
    """),

    ("TBL_MODEL_SPECIFICATION", """
        CREATE TABLE IF NOT EXISTS TBL_MODEL_SPECIFICATION (
            ID INT AUTO_INCREMENT PRIMARY KEY,
            MS_PART_NUMBER VARCHAR(255),
            MS_DESCRIPTION VARCHAR(255),
            MS_DEVICE VARCHAR(255),
            MS_UNIT VARCHAR(255),
            MS_MASTER_MIN VARCHAR(255),
            MS_MASTER_MAX VARCHAR(255),
            MS_NORMAL_MIN VARCHAR(255),
            MS_NORMAL_MAX VARCHAR(255),
            MS_CREATED_DATE TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (MS_PART_NUMBER)
                REFERENCES TBL_MODEL_MASTER(MM_PART_NUMBER) ON DELETE CASCADE
        )
    """),

    ("TBL_MODEL_LABEL_DETAILS", """
        CREATE TABLE IF NOT EXISTS TBL_MODEL_LABEL_DETAILS (
            MLD_ID INT AUTO_INCREMENT PRIMARY KEY,
            MLD_PART_NUMBER VARCHAR(100) NOT NULL,
            MLD_LABEL_ID VARCHAR(50) NOT NULL,
            MLD_ON_STATUS VARCHAR(100),
            MLD_OFF_STATUS VARCHAR(100),
            MLD_X INT DEFAULT 0,
            MLD_Y INT DEFAULT 0,
            MLD_FONT VARCHAR(100) DEFAULT 'Arial, 12pt',
            MLD_CREATED_DATE TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """),

    ("TBL_TEST_RESULTS", """
        CREATE TABLE IF NOT EXISTS TBL_TEST_RESULTS (
            ID INT AUTO_INCREMENT PRIMARY KEY,
            LOT_NUMBER VARCHAR(50),
            PART_NUMBER VARCHAR(100),
            L1 DECIMAL(10,3),
            L2 DECIMAL(10,3),
            L3 DECIMAL(10,3),
            L4 DECIMAL(10,3),
            P1 DECIMAL(10,3),
            P2 DECIMAL(10,3),
            P3 DECIMAL(10,3),
            P4 DECIMAL(10,3),
            RESULT VARCHAR(10),
            SCAN_RESULT VARCHAR(10),
            CREATED_BY VARCHAR(50),
            EMP_CODE VARCHAR(50),
            SPEC_DATA TEXT,
            CREATED_DATE TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """),

    ("TBL_TEST_DATA", """
        CREATE TABLE IF NOT EXISTS TBL_TEST_DATA (
            ID INT AUTO_INCREMENT PRIMARY KEY,
            TD_MACHINE_ID VARCHAR(50),
            TD_PART_NUMBER VARCHAR(100),
            TD_LOT_NUMBER VARCHAR(50),
            TD_TRACEABILITY_CODE VARCHAR(100),
            TD_RECORD_DATE DATE,
            TD_DATETIME DATETIME,
            L1 DECIMAL(10,3),
            L2 DECIMAL(10,3),
            L3 DECIMAL(10,3),
            L4 DECIMAL(10,3),
            P1 DECIMAL(10,3),
            P2 DECIMAL(10,3),
            P3 DECIMAL(10,3),
            P4 DECIMAL(10,3),
            CAM1 VARCHAR(20),
            TD_OVERALL_STATUS VARCHAR(10),
            TD_EMP_CODE VARCHAR(50),
            TD_BARCODE_SCAN_RESULT VARCHAR(10),
            TD_CREATED_DATE TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """),

    ("TBL_PART_RUNNING_SERIAL", """
        CREATE TABLE IF NOT EXISTS TBL_PART_RUNNING_SERIAL (
            PRS_ID INT AUTO_INCREMENT PRIMARY KEY,
            PART_NUMBER VARCHAR(100) NOT NULL,
            TEST_DAY_DATE DATE NOT NULL,
            TEST_DAY_LAST_DATE_TIME DATETIME,
            TRACEABILITY_CODE VARCHAR(100),
            RUNNING_LOT_NUMBER VARCHAR(50),
            PRS_CREATED_DATE TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY unique_part_date (PART_NUMBER, TEST_DAY_DATE)
        )
    """),

    ("TBL_LOT_SEQUENCE", """
        CREATE TABLE IF NOT EXISTS TBL_LOT_SEQUENCE (
            ID INT AUTO_INCREMENT PRIMARY KEY,
            DATE_STR VARCHAR(6) NOT NULL,
            MACHINE_DIGIT VARCHAR(1) NOT NULL,
            LAST_INCREMENT INT DEFAULT 0,
            CREATED_DATE TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UPDATED_DATE TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY unique_date_machine (DATE_STR, MACHINE_DIGIT)
        )
    """),

    # Who did what on the Test console: logins, part loads, saved tests,
    # NG cable checks and label scans.
    ("TBL_OPERATOR_LOG", """
        CREATE TABLE IF NOT EXISTS TBL_OPERATOR_LOG (
            ID INT AUTO_INCREMENT PRIMARY KEY,
            OL_DATETIME DATETIME NOT NULL,
            OL_MACHINE_ID VARCHAR(50),
            OL_EMPLOYEE_CODE VARCHAR(50),
            OL_ACTION VARCHAR(50) NOT NULL,
            OL_DESCRIPTION VARCHAR(500),
            OL_PART_NUMBER VARCHAR(100),
            KEY idx_operator_log_datetime (OL_DATETIME)
        )
    """),
]


def ensure_database():
    """Create the schema's database if the server does not have it yet."""
    conn = connect_server()
    try:
        cursor = conn.cursor()
        cursor.execute("CREATE DATABASE IF NOT EXISTS `" + DATABASE + "`")
        cursor.close()
    finally:
        conn.close()


def init_database(raise_on_error=False):
    """Create the database and any missing tables. Returns True on success."""
    try:
        ensure_database()

        conn = connect()
        try:
            cursor = conn.cursor()
            for name, statement in SCHEMA:
                cursor.execute(statement)
            conn.commit()
            cursor.close()
        finally:
            conn.close()
        return True

    except mysql.connector.Error as err:
        print(f"Database initialization error: {err}")
        if raise_on_error:
            raise
        return False


def table_names():
    """Names of the tables this module manages."""
    return [name for name, _ in SCHEMA]


if __name__ == '__main__':
    print(f"Initializing {USER}@{HOST}:{PORT}/{DATABASE} ...")
    if init_database(raise_on_error=True):
        print("Tables ready:")
        for name in table_names():
            print(f"  - {name}")
