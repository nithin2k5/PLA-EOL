import tkinter as tk
from tkinter import ttk, messagebox
import mysql.connector
from mysql.connector import Error
from pymodbus.client import ModbusSerialClient
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException
import serial
import serial.tools.list_ports
import os
import time
import threading
import config
import data_files
import ui
from plc_address import bit_address
import json
from datetime import datetime

class DeviceRow:
    """One row of the device table: its widgets and the device it drives."""


class ComPortSettings:
    # The machine carries four loadcells and two barcode cameras.
    LOADCELL_COUNT = 4
    CAMERA_COUNT = 2
    BAUD_RATES = [9600, 19200, 38400, 57600, 115200]

    # Device table columns: heading and minimum width. The last one takes
    # whatever width is left over.
    COLUMNS = (("Device", 200), ("COM Port", 170), ("Baud Rate", 150),
               ("Station ID", 120), ("Action", 230), ("Status", 0))

    def __init__(self, root):
        self.root = root
        ui.apply(root)

        # Initialize lists and dictionaries at the start
        self.all_comboboxes = []  # every COM port and baud rate box
        self.all_entries = []     # every typed setting, frozen with the boxes
        self.loadcell_ports = {}  # loadcell row -> open serial port
        self.loadcell_rows = []
        self.modbus_client = None
        self.modbus_tcp_client = None

        # LVDT stream
        self.lvdt_port = None
        self.lvdt_thread = None
        self.lvdt_running = False

        # Camera combos, keyed by camera number
        self.camera_combos = {}

        # Get machine ID from environment variable
        self.machine_id = config.get('MACHINE_ID', 'Not Set')

        # Set title with machine ID
        title = "COM Port Settings"
        if self.machine_id and self.machine_id != 'Not Set':
            title += f" - Machine ID: {self.machine_id}"
        self.root.title(title)

        # Make it full screen
        self.root.state('zoomed')

        # Configure the main background color
        self.root.configure(bg=ui.APP_BG)

        # Create and setup the UI
        self.setup_ui()

        # Settings are read through config.py, which creates .config on demand.
        config.reload()

        # Recent loadcell readings, kept for this session only
        self.loadcell_data = {}

        # Initialize loadcell data in environment
        self.initialize_loadcell_env()

        # Load saved settings and values
        self.load_saved_settings()
        self.load_device_values()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def setup_ui(self):
        # The pink title bar every console carries
        ui.page_header(self.root, "COM Port Settings")

        body = tk.Frame(self.root, bg=ui.APP_BG)
        body.pack(fill=tk.BOTH, expand=True, padx=20, pady=(12, 20))

        self.available_ports = [port.device for port in serial.tools.list_ports.comports()]

        # Heading line, with the EDIT / SAVE / RESET controls on the right
        top = tk.Frame(body, bg=ui.APP_BG)
        top.pack(fill='x', pady=(0, 8))
        tk.Label(top, text="Device Ports", bg=ui.APP_BG, fg=ui.ACCENT,
                 font=ui.FONT_SECTION).pack(side='left')

        self.control_buttons = {}
        # Packed from the right, so they read EDIT, SAVE, RESET
        for text, bg, command in (("RESET", ui.DANGER, self.reset_settings),
                                  ("SAVE", ui.SUCCESS, self.save_settings),
                                  ("EDIT", ui.ACCENT_FILL, self.enable_editing)):
            btn = self.action_button(top, text, command, bg=bg, width=10)
            btn.pack(side='right', padx=(6, 0))
            self.control_buttons[text] = btn

        # Initially enable Save button and disable Edit button
        self.control_buttons["SAVE"].config(state="normal")
        self.control_buttons["EDIT"].config(state="disabled")

        self.create_device_table(body)
        self.create_options(body)
        self.create_log(body)

        # The Rx text the tests write to is the one shared log
        self.rx_text = self.log_text
        self.rx_tcp_text = self.log_text

    def action_button(self, parent, text, command, bg=ui.ACCENT_FILL, width=9):
        """A flat button in one of the palette's colours."""
        return tk.Button(parent, text=text, command=command, bg=bg,
                         fg=ui.readable_on(bg), activebackground=bg,
                         relief='flat', bd=0, width=width, cursor='hand2',
                         font=(ui.FONT_FAMILY, 10, 'bold'), padx=4, pady=3)

    def create_device_table(self, parent):
        """One row per serial device: port, baud rate, station ID and its tests."""
        table = tk.Frame(parent, bg=ui.SURFACE,
                         highlightbackground=ui.BORDER, highlightthickness=1)
        table.pack(fill='x')
        self.table = table
        self._row_index = 0

        for column, (heading, width) in enumerate(self.COLUMNS):
            table.grid_columnconfigure(column, minsize=width,
                                       weight=0 if width else 1)
            tk.Label(table, text=heading, bg=ui.NAVY, fg=ui.TEXT_ON_DARK,
                     font=ui.FONT_BODY_BOLD, anchor='w', padx=12, pady=8).grid(
                         row=0, column=column, sticky='nsew')

        self.add_plc_row()
        for i in range(1, self.LOADCELL_COUNT + 1):
            self.add_loadcell_row(i)
        self.add_lvdt_row()
        for i in range(1, self.CAMERA_COUNT + 1):
            self.add_camera_row(i)

    def add_row(self, device):
        """Start a table row, returning one cell frame per column."""
        self._row_index += 1
        bg = ui.SURFACE if self._row_index % 2 else ui.APP_BG
        cells = []
        for column in range(len(self.COLUMNS)):
            cell = tk.Frame(self.table, bg=bg)
            cell.grid(row=self._row_index, column=column, sticky='nsew')
            cells.append(cell)
        tk.Label(cells[0], text=device, bg=bg, fg=ui.TEXT, font=ui.FONT_BODY,
                 anchor='w').pack(side='left', padx=12, pady=7)
        return cells

    def blank_cell(self, cell):
        """Mark a column that doesn't apply to the device."""
        tk.Label(cell, text="-", bg=cell['bg'], fg=ui.TEXT_MUTED,
                 font=ui.FONT_BODY).pack(side='left', padx=14)

    def port_combos(self, cells):
        """The COM port and baud rate boxes, in the row's second and third cells."""
        com_combo = ttk.Combobox(cells[1], width=14, state="readonly",
                                 values=self.available_ports or [""])
        com_combo.pack(side='left', padx=12, pady=7)
        baud_combo = ttk.Combobox(cells[2], width=11, state="readonly",
                                  values=self.BAUD_RATES)
        baud_combo.pack(side='left', padx=12, pady=7)
        self.all_comboboxes.extend([com_combo, baud_combo])
        return com_combo, baud_combo

    def status_label(self, cell, text=""):
        label = tk.Label(cell, text=text, bg=cell['bg'], fg=ui.TEXT_MUTED,
                         font=ui.FONT_SMALL, anchor='w')
        label.pack(side='left', padx=12)
        return label

    def add_plc_row(self):
        cells = self.add_row("PLC")
        self.plc_com_combo, self.plc_baud_combo = self.port_combos(cells)

        self.station_id_entry = tk.Entry(cells[3], width=8, justify='center')
        # A station id is a plain number, so reject anything else as it is typed.
        digits_only = self.station_id_entry.register(
            lambda proposed: proposed == '' or proposed.isdigit())
        self.station_id_entry.configure(validate='key', validatecommand=(digits_only, '%P'))
        self.station_id_entry.pack(side='left', padx=12, pady=7)
        self.all_entries.append(self.station_id_entry)

        self.connect_button = self.action_button(cells[4], "CONNECT", self.connect_to_plc,
                                                 bg=ui.SUCCESS)
        self.connect_button.pack(side='left', padx=(12, 4))
        self.test_button = self.action_button(cells[4], "TEST", self.read_plc_data)
        self.test_button.pack(side='left', padx=4)

        self.plc_status = self.status_label(cells[5], "Not connected")

        # Initially disable test button
        self.test_button.config(state="disabled")

    def add_loadcell_row(self, number):
        row = DeviceRow()
        row.loadcell_num = f"{number:02d}"
        cells = self.add_row(f"Loadcell {row.loadcell_num} (L{number})")
        row.com_combo, row.baud_combo = self.port_combos(cells)
        self.blank_cell(cells[3])

        connect_button = self.action_button(
            cells[4], "CONNECT", bg=ui.SUCCESS,
            command=lambda: self.connect_loadcell(row, row.com_combo, row.baud_combo,
                                                  row.test_button))
        connect_button.pack(side='left', padx=(12, 4))
        row.test_button = self.action_button(cells[4], "TEST",
                                             lambda: self.test_loadcell(row))
        row.test_button.pack(side='left', padx=4)
        # Initially disable test button
        row.test_button.config(state="disabled")

        row.status = self.status_label(cells[5], "Not connected")
        self.loadcell_rows.append(row)

    def add_lvdt_row(self):
        """Port, baud, connect and the four live readings the LVDT streams."""
        cells = self.add_row("LVDT")
        self.lvdt_com_combo, self.lvdt_baud_combo = self.port_combos(cells)
        self.blank_cell(cells[3])

        self.lvdt_connect_button = self.action_button(cells[4], "CONNECT", self.connect_lvdt,
                                                      bg=ui.SUCCESS)
        self.lvdt_connect_button.pack(side='left', padx=(12, 4))
        self.lvdt_stop_button = self.action_button(cells[4], "STOP", self.disconnect_lvdt,
                                                   bg=ui.DANGER)
        self.lvdt_stop_button.config(state='disabled')
        self.lvdt_stop_button.pack(side='left', padx=4)

        self.lvdt_value_entries = {}
        for name in ('P01', 'P02', 'P03', 'P04'):
            tk.Label(cells[5], text=f"{name}:", bg=cells[5]['bg'], fg=ui.TEXT,
                     font=(ui.FONT_FAMILY, 10, 'bold')).pack(side='left', padx=(12, 2))
            entry = tk.Entry(cells[5], width=7, justify='center', state='readonly')
            entry.pack(side='left')
            self.lvdt_value_entries[name] = entry

        self.lvdt_status_label = self.status_label(cells[5], "Not connected")

    def add_camera_row(self, camera_num):
        """A camera only needs the port and baud rate it is wired on."""
        cells = self.add_row(f"Camera {camera_num:02d}")
        self.camera_combos[camera_num] = self.port_combos(cells)
        self.blank_cell(cells[3])
        self.blank_cell(cells[4])

    def create_options(self, parent):
        """The PLC register read and the Modbus TCP link, under the table."""
        box = tk.Frame(parent, bg=ui.SURFACE,
                       highlightbackground=ui.BORDER, highlightthickness=1)
        box.pack(fill='x', pady=(10, 0))

        def caption(row, text):
            tk.Label(box, text=text, bg=ui.SURFACE, fg=ui.TEXT, font=ui.FONT_BODY_BOLD,
                     anchor='w', width=16).grid(row=row, column=0, sticky='w',
                                               padx=12, pady=7)

        def field(row, column, text, width):
            tk.Label(box, text=text, bg=ui.SURFACE, fg=ui.TEXT_MUTED,
                     font=ui.FONT_SMALL).grid(row=row, column=column, sticky='e',
                                              padx=(12, 4))
            entry = tk.Entry(box, width=width)
            entry.grid(row=row, column=column + 1, sticky='w')
            self.all_entries.append(entry)
            return entry

        # PLC register read
        caption(0, "PLC Register Read")
        self.reg_address_entry = field(0, 1, "Address (e.g. D0001)", 15)
        self.points_entry = field(0, 3, "Points", 6)
        self.points_entry.insert(0, "1")  # Default to 1 point
        self.read_button = self.action_button(box, "READ", self.read_holding_registers)
        self.read_button.grid(row=0, column=5, sticky='w', padx=(12, 4))
        # Initially disable read button
        self.read_button.config(state="disabled")

        # Modbus TCP
        caption(1, "Modbus TCP")
        self.ip_entry = field(1, 1, "IP Address", 15)
        self.port_entry = field(1, 3, "Port", 6)
        actions = tk.Frame(box, bg=ui.SURFACE)
        actions.grid(row=1, column=5, sticky='w', padx=(12, 0))
        self.connect_tcp_button = self.action_button(actions, "CONNECT",
                                                     self.connect_to_modbus_tcp, bg=ui.SUCCESS)
        self.connect_tcp_button.pack(side='left', padx=(0, 4))
        self.test_tcp_button = self.action_button(actions, "TEST", self.read_modbus_tcp_data)
        self.test_tcp_button.pack(side='left', padx=4)
        # Initially disable test button
        self.test_tcp_button.config(state="disabled")
        self.tcp_status = tk.Label(box, text="Not connected", bg=ui.SURFACE,
                                   fg=ui.TEXT_MUTED, font=ui.FONT_SMALL)
        self.tcp_status.grid(row=1, column=6, sticky='w', padx=12)

    def create_log(self, parent):
        """What the devices sent back, for every test on the page."""
        box = tk.Frame(parent, bg=ui.SURFACE,
                       highlightbackground=ui.BORDER, highlightthickness=1)
        box.pack(fill='both', expand=True, pady=(10, 0))

        caption = tk.Frame(box, bg=ui.SUBTLE)
        caption.pack(fill='x')
        tk.Label(caption, text="Rx String", bg=ui.SUBTLE, fg=ui.TEXT,
                 font=ui.FONT_BODY_BOLD, padx=12, pady=5).pack(side='left')
        self.action_button(caption, "CLEAR", lambda: self.log_text.delete("1.0", tk.END),
                           bg=ui.SILVER, width=8).pack(side='right', padx=6, pady=3)

        scrollbar = ttk.Scrollbar(box, orient='vertical')
        scrollbar.pack(side='right', fill='y')
        self.log_text = tk.Text(box, height=6, font=('Consolas', 10), bd=0,
                                bg=ui.SURFACE, fg=ui.TEXT, padx=10, pady=6,
                                yscrollcommand=scrollbar.set)
        self.log_text.pack(fill='both', expand=True)
        scrollbar.config(command=self.log_text.yview)

    def connect_to_plc(self):
        """Connect to PLC using Modbus RTU"""
        port = self.plc_com_combo.get()
        baudrate = int(self.plc_baud_combo.get())
        slave_id = self.station_id_entry.get().strip()

        try:
            if port == "No Ports":
                raise ValueError("No COM ports available.")
            if not slave_id.isdigit():
                raise ValueError("Station ID must be a valid number.")

            slave_id = int(slave_id)

            # Check if there's an existing connection
            if self.modbus_client and self.modbus_client.is_socket_open():
                try:
                    # Test if the existing connection is still working
                    test_response = self.modbus_client.read_coils(
                        address=0,
                        count=1,
                        device_id=slave_id
                    )
                    if not test_response.isError():
                        messagebox.showinfo("Connection Status", "Already connected to PLC!")
                        self.test_button.config(state="normal")
                        self.read_button.config(state="normal")
                        self.plc_status.config(text=f"Connected on {port}", fg=ui.SUCCESS)
                        return True
                except:
                    # If test fails, close the existing connection
                    try:
                        self.modbus_client.close()
                    except:
                        pass

            # Initialize Modbus client with RTU settings
            self.modbus_client = ModbusSerialClient(
                port=port,
                baudrate=baudrate,
                timeout=1,
                stopbits=1,
                bytesize=8,
                parity='N'
            )

            # Try to connect with retries
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    if self.modbus_client.connect():
                        # Test connection by reading a coil
                        test_response = self.modbus_client.read_coils(
                            address=0,
                            count=1,
                            device_id=slave_id
                        )
                        
                        if not test_response.isError():
                            messagebox.showinfo("Connection Status", "Connected to PLC!")
                            self.test_button.config(state="normal")
                            self.read_button.config(state="normal")
                            self.plc_status.config(text=f"Connected on {port}", fg=ui.SUCCESS)
                            return True
                    
                    if attempt < max_retries - 1:
                        time.sleep(1)  # Wait before retrying
                except Exception as e:
                    if attempt < max_retries - 1:
                        time.sleep(1)
                        continue
                    raise e

            # If we get here, connection failed after all retries
            if self.modbus_client:
                self.modbus_client.close()
            messagebox.showerror("Connection Status", "Failed to connect to PLC after multiple attempts.")
            return False
            
        except ValueError as ve:
            messagebox.showerror("Input Error", str(ve))
            return False
        except Exception as e:
            messagebox.showerror("Error", f"Connection Error: {str(e)}")
            if self.modbus_client:
                self.modbus_client.close()
            return False

    def read_plc_data(self):
        """Read data from PLC registers and coils"""
        if self.modbus_client is None or not self.modbus_client.is_socket_open():
            messagebox.showerror("Error", "Not connected to PLC.")
            return

        try:
            slave_id = self.station_id_entry.get().strip()
            
            # Validate inputs
            if not slave_id:
                messagebox.showerror("Error", "Station ID is mandatory!")
                return

            slave_id = int(slave_id)
            
            # Clear the text box
            self.rx_text.delete("1.0", tk.END)
            
            process_status_file = data_files.path(data_files.PROCESS_STATUS)
            input_sensors_file = data_files.path(data_files.INPUT_SENSORS)
            program_selection_file = data_files.path(data_files.PROGRAM_SELECTION_IN_PLC)
            
            # Initialize arrays
            process_status_array = []
            input_sensors_array = []
            program_selection_array = []
            
            # Read and process each file
            for file_path, array_name in [
                (process_status_file, "Process Status"),
                (input_sensors_file, "Input Sensors"),
                (program_selection_file, "Program Selection")
            ]:
                try:
                    if not os.path.exists(file_path):
                        self.rx_text.insert(tk.END, f"Warning: {os.path.basename(file_path)} not found at {file_path}\n")
                        continue
                        
                    with open(file_path, 'r') as f:
                        content = f.read().strip()
                        if not content:
                            self.rx_text.insert(tk.END, f"Warning: {os.path.basename(file_path)} is empty\n")
                            continue
                            
                        # Split by comma and clean the addresses
                        addresses = [addr.strip() for addr in content.split(',') if addr.strip()]
                        
                        if not addresses:
                            self.rx_text.insert(tk.END, f"Warning: No valid addresses found in {os.path.basename(file_path)}\n")
                            continue
                            
                        # Store addresses in appropriate array and read coils
                        if "ProcessStatus" in file_path:
                            self._read_coils(addresses, "Process Status", slave_id)
                        elif "InputSensors" in file_path:
                            self._read_coils(addresses, "Input Sensors", slave_id)
                        elif "ProgramSelection" in file_path:
                            self._read_coils(addresses, "Program Selection", slave_id)
                            
                except Exception as e:
                    self.rx_text.insert(tk.END, f"Error reading {os.path.basename(file_path)}: {str(e)}\n")

            # After reading data, save the values
            self.save_device_values()

        except ValueError as ve:
            messagebox.showerror("Error", "Invalid Station ID")
        except Exception as e:
            messagebox.showerror("Read Error", str(e))

    def _read_coils(self, addresses, section_name, slave_id):
        """Helper method to read coils and display results"""
        if not addresses:
            return
        
        # Add section header
        self.rx_text.insert(tk.END, f"\n{section_name}:\n")
        
        # If addresses is a string, split it into a list
        if isinstance(addresses, str):
            addresses = [addr.strip() for addr in addresses.split(',')]
        
        for address in addresses:
            try:
                # Clean up the address string
                address = address.strip()
                if not address:
                    continue
                    
                # Extract the hex part based on whether it starts with M or P
                if address.startswith('M'):
                    hex_part = address[1:]  # Remove 'M'
                elif address.startswith('P'):
                    hex_part = address[1:]  # Remove 'P'
                else:
                    self.rx_text.insert(tk.END, f"{address} --> Invalid address format (must start with M or P)\n")
                    continue
                
                # Word in decimal, bit in hex: see plc_address.py
                try:
                    coil_address = bit_address(address)
                except ValueError:
                    self.rx_text.insert(tk.END, f"{address} --> Invalid hex value: {hex_part}\n")
                    continue

                # Read 1 coil from the PLC
                response = self.modbus_client.read_coils(
                    address=coil_address,
                    count=1,
                    device_id=slave_id
                )

                if getattr(response, 'isError', lambda: True)():
                    self.rx_text.insert(tk.END, f"{address} --> Error reading coil\n")
                    continue

                # Display result
                status = "ON" if response.bits[0] else "OFF"
                self.rx_text.insert(tk.END, f"{address} --> {status}\n")

            except Exception as e:
                self.rx_text.insert(tk.END, f"{address} --> Error: {str(e)}\n")

    def test_loadcell(self, frame):
        """Test loadcell communication and save data"""
        try:
            if frame not in self.loadcell_ports or not self.loadcell_ports[frame].is_open:
                loadcell_num = frame.loadcell_num
                messagebox.showerror("Connection Error", 
                                   f"Loadcell {loadcell_num} is not connected!")
                return
            
            self.log_text.delete("1.0", tk.END)
            loadcell_num = frame.loadcell_num
            
            try:
                # Clear buffers
                self.loadcell_ports[frame].reset_input_buffer()
                self.loadcell_ports[frame].reset_output_buffer()
                
                # Set timeout
                self.loadcell_ports[frame].timeout = 0.5
                
                # Send command
                command = f"ID{loadcell_num}P".encode()
                self.loadcell_ports[frame].write(command)
                self.log_text.insert(tk.END, f"Sent command: ID{loadcell_num}P\n")
                
                # Update GUI
                self.root.update()
                
                # Read response
                response = self.loadcell_ports[frame].readline()
                
                if response:
                    decoded_response = response.decode('utf-8', errors='replace').strip()
                    self.log_text.insert(tk.END, f"Response: {decoded_response}\n")
                    
                    # Parse and save data
                    try:
                        parts = decoded_response.split(',')
                        if len(parts) > 1:
                            value = parts[1]
                            self.log_text.insert(tk.END, f"Parsed value: {value}\n")
                            
                            # Save to environment variable
                            self.save_loadcell_data(loadcell_num, value)
                            
                    except IndexError:
                        self.log_text.insert(tk.END, "Could not parse value\n")
                else:
                    self.log_text.insert(tk.END, "No response received\n")
                    
            except Exception as e:
                self.log_text.insert(tk.END, f"Communication error: {str(e)}\n")
                
            finally:
                self.loadcell_ports[frame].timeout = 1
                
            # After reading data, save the values
            self.save_device_values()
            
        except Exception as e:
            self.log_text.insert(tk.END, f"Error: {str(e)}\n")
        
        self.root.update()

    def connect_loadcell(self, frame, com_combo, baud_combo, test_button):
        """Connect to loadcell using serial communication"""
        try:
            port = com_combo.get()
            baudrate = int(baud_combo.get())
            loadcell_num = frame.loadcell_num
            
            # Check if port is "No Ports Available"
            if port == "No Ports Available":
                messagebox.showerror("Connection Error", 
                                   f"No COM ports available for Loadcell {loadcell_num}!")
                return
            
            # Check if port still exists
            available_ports = [port.device for port in serial.tools.list_ports.comports()]
            if port not in available_ports:
                messagebox.showerror("Connection Error", 
                                   f"COM Port {port} is no longer available!")
                return
            
            # Close existing connection if any
            if frame in self.loadcell_ports and self.loadcell_ports[frame].is_open:
                self.loadcell_ports[frame].close()
            
            # Create new serial connection with timeout
            ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=0.5,  # Shorter initial timeout
                write_timeout=0.5  # Add write timeout
            )
            
            if ser.is_open:
                self.loadcell_ports[frame] = ser
                test_button.config(state="normal")
                frame.status.config(text=f"Connected on {port}", fg=ui.SUCCESS)
                messagebox.showinfo("Success", f"Connected to Loadcell {loadcell_num}")
            else:
                raise serial.SerialException("Failed to open port")
                
        except ValueError as ve:
            messagebox.showerror("Error", f"Invalid baudrate: {str(ve)}")
        except serial.SerialException as se:
            messagebox.showerror("Error", f"Serial port error: {str(se)}")
        except Exception as e:
            messagebox.showerror("Error", f"Unexpected error: {str(e)}")

    def parse_lvdt_data(self, line):
        """Pull the readings out of one streamed line.

        The device sends a comma separated record whose second field says how
        many readings follow - either two or four. Returns a dict keyed P01..P04,
        or None when the line does not carry a usable record.
        """
        if not line:
            return None

        tokens = [token.strip() for token in line.strip().split(',')]
        if len(tokens) < 2:
            return None

        try:
            count = int(tokens[1])
        except ValueError:
            return None

        if count not in (2, 4) or len(tokens) < count + 2:
            return None

        names = ('P01', 'P02', 'P03', 'P04')[:count]
        readings = {}
        for index, name in enumerate(names):
            raw = tokens[2 + index]
            if index == count - 1:
                # The last field carries trailing characters from the frame.
                raw = raw[:5]
            try:
                value = float(raw)
            except ValueError:
                # A reading that will not parse counts as zero rather than
                # throwing away the rest of the record.
                value = 0.0
            # Readings arrive scaled by 100.
            readings[name] = value / 100 if value else 0.0

        return readings

    def show_lvdt_data(self, readings):
        """Write the parsed readings into the four boxes."""
        for name, entry in self.lvdt_value_entries.items():
            entry.config(state='normal')
            entry.delete(0, tk.END)
            if name in readings:
                entry.insert(0, f"{readings[name]:g}")
            entry.config(state='readonly')

    def connect_lvdt(self):
        """Open the LVDT port and start reading its stream."""
        try:
            port = self.lvdt_com_combo.get()
            baud = self.lvdt_baud_combo.get()
            if not port or not baud:
                messagebox.showwarning("LVDT", "Please select a COM port and BAUD rate first!")
                return

            if self.lvdt_port and self.lvdt_port.is_open:
                self.disconnect_lvdt()

            self.lvdt_port = serial.Serial(port=port, baudrate=int(baud), timeout=1)
            self.lvdt_running = True
            self.lvdt_status_label.config(text=f"Connected on {port}", fg=ui.SUCCESS)
            self.lvdt_connect_button.config(state='disabled')
            self.lvdt_stop_button.config(state='normal')

            self.lvdt_thread = threading.Thread(target=self.read_lvdt_stream, daemon=True)
            self.lvdt_thread.start()

        except Exception as e:
            messagebox.showerror("LVDT", f"Could not open the LVDT port: {e}")
            self.lvdt_status_label.config(text="Not connected", fg=ui.TEXT_MUTED)

    def read_lvdt_stream(self):
        """Read lines off the LVDT port until asked to stop."""
        while self.lvdt_running and self.lvdt_port and self.lvdt_port.is_open:
            try:
                line = self.lvdt_port.readline().decode('utf-8', errors='ignore')
                if not line:
                    continue

                readings = self.parse_lvdt_data(line)
                if readings:
                    # Tk widgets are only safe to touch from the main thread.
                    self.root.after(0, self.show_lvdt_data, readings)
                else:
                    self.root.after(0, self.lvdt_status_label.config,
                                    {'text': 'Improper received string...', 'fg': ui.DANGER})
            except Exception as e:
                print(f"LVDT read error: {e}")
                break

    def disconnect_lvdt(self):
        """Stop reading and close the LVDT port."""
        self.lvdt_running = False
        try:
            if self.lvdt_port and self.lvdt_port.is_open:
                self.lvdt_port.close()
        except Exception as e:
            print(f"Error closing LVDT port: {e}")

        self.lvdt_port = None
        try:
            self.lvdt_status_label.config(text="Not connected", fg=ui.TEXT_MUTED)
            self.lvdt_connect_button.config(state='normal')
            self.lvdt_stop_button.config(state='disabled')
        except Exception:
            pass

    def connect_to_modbus_tcp(self):
        """Connect to PLC using Modbus TCP"""
        ip_address = self.ip_entry.get().strip()
        port = self.port_entry.get().strip()

        try:
            if not ip_address:
                raise ValueError("IP Address is required.")
            if not port.isdigit():
                raise ValueError("Port must be a valid number.")

            port = int(port)

            # Create a Modbus TCP client
            self.modbus_tcp_client = ModbusTcpClient(ip_address, port=port)

            if self.modbus_tcp_client.connect():
                messagebox.showinfo("Connection Status", "Connected to PLC via Modbus TCP!")
                self.test_tcp_button.config(state="normal")
                self.tcp_status.config(text=f"Connected to {ip_address}:{port}", fg=ui.SUCCESS)
            else:
                self.modbus_tcp_client.close()
                messagebox.showerror("Connection Status", "Failed to connect to PLC via Modbus TCP.")
        except ValueError as ve:
            messagebox.showerror("Input Error", str(ve))
        except Exception as e:
            messagebox.showerror("Error", f"Connection Error: {str(e)}")

    def read_modbus_tcp_data(self):
        """Read data from PLC using Modbus TCP"""
        if self.modbus_tcp_client is None or not self.modbus_tcp_client.is_socket_open():
            messagebox.showerror("Error", "Not connected to PLC via Modbus TCP.")
            return

        try:
            # Clear the text box
            self.rx_tcp_text.delete("1.0", tk.END)
            
            # Read holding register (D register) at address 0 (D0000)
            response = self.modbus_tcp_client.read_holding_registers(0, 1, device_id=1)  # Adjust unit ID if needed

            if not response.isError():
                value = response.registers[0]  # Read the integer value
                self.rx_tcp_text.insert(tk.END, f"Value in D0000: {value}\n")
            else:
                self.rx_tcp_text.insert(tk.END, f"Error reading from PLC: {response}\n")

        except Exception as e:
            self.rx_tcp_text.insert(tk.END, f"Error: {str(e)}\n")

    def save_settings(self):
        """Save settings to environment variables and disable editing"""
        try:
            # Validate all required fields are filled
            if not self._validate_settings():
                messagebox.showerror("Validation Error", "Please fill in all required fields!")
                return

            # Save PLC settings
            config.set('PLC_COM_PORT', self.plc_com_combo.get())
            config.set('PLC_BAUD_RATE', self.plc_baud_combo.get())
            config.set('PLC_STATION_ID', self.station_id_entry.get())

            # Save Register Address and Points settings
            config.set('PLC_REG_ADDRESS', self.reg_address_entry.get())
            config.set('PLC_POINTS_TO_READ', self.points_entry.get())

            # Save Loadcell settings
            for row in self.loadcell_rows:
                config.set(f'LOADCELL_{row.loadcell_num}_COM_PORT', row.com_combo.get())
                config.set(f'LOADCELL_{row.loadcell_num}_BAUD_RATE', row.baud_combo.get())

            # Save LVDT settings
            config.set('LVDT_COM_PORT', self.lvdt_com_combo.get())
            config.set('LVDT_BAUD_RATE', self.lvdt_baud_combo.get())

            # Save Camera settings
            for camera_num, (com_combo, baud_combo) in self.camera_combos.items():
                config.set(f'CAMERA_{camera_num:02d}_COM_PORT', com_combo.get())
                config.set(f'CAMERA_{camera_num:02d}_BAUD_RATE', baud_combo.get())

            # Save Modbus TCP settings
            config.set('MODBUS_TCP_IP', self.ip_entry.get())
            config.set('MODBUS_TCP_PORT', self.port_entry.get())

            # Disable all inputs
            self._freeze_all_inputs()

            # Update button states
            self.control_buttons["SAVE"].config(state="disabled")
            self.control_buttons["EDIT"].config(state="normal")

            messagebox.showinfo("Success", "Settings saved successfully!")

        except Exception as e:
            messagebox.showerror("Save Error", f"Failed to save settings!\nError: {str(e)}")

    def _validate_settings(self):
        """Validate that at least one device has all fields filled"""
        # Check if PLC row is complete
        if all([self.plc_com_combo.get(), self.plc_baud_combo.get(), self.station_id_entry.get()]):
            return True

        # Check if any Loadcell row is complete
        for row in self.loadcell_rows:
            if all([row.com_combo.get(), row.baud_combo.get()]):
                return True

        # Check if Modbus TCP is complete
        if all([self.ip_entry.get(), self.port_entry.get()]):
            return True

        return False

    def _freeze_all_inputs(self):
        """Helper method to disable all input fields"""
        for combo in self.all_comboboxes:
            combo.config(state="disabled")
        for entry in self.all_entries:
            entry.config(state="disabled")

    def enable_editing(self):
        """Enable editing of all input fields"""
        for combo in self.all_comboboxes:
            combo.config(state="readonly")
        for entry in self.all_entries:
            entry.config(state="normal")

        # Update button states
        self.control_buttons["SAVE"].config(state="normal")
        self.control_buttons["EDIT"].config(state="disabled")

        messagebox.showinfo("Edit Mode", "Settings are now editable")

    def reset_settings(self):
        """Reset all settings and clear environment variables"""
        try:
            # Ask for confirmation
            if not messagebox.askyesno("Confirm Reset",
                                     "Are you sure you want to reset all settings?"):
                return

            # Settings to clear
            env_vars = [
                'PLC_COM_PORT', 'PLC_BAUD_RATE', 'PLC_STATION_ID',
                'MODBUS_TCP_IP', 'MODBUS_TCP_PORT',
                'LVDT_COM_PORT', 'LVDT_BAUD_RATE'
            ]

            # Add camera settings
            for i in range(1, self.CAMERA_COUNT + 1):
                env_vars.extend([
                    f'CAMERA_{i:02d}_COM_PORT',
                    f'CAMERA_{i:02d}_BAUD_RATE'
                ])

            # Add loadcell environment variables
            for i in range(1, self.LOADCELL_COUNT + 1):
                loadcell_num = f"{i:02d}"
                env_vars.extend([
                    f'LOADCELL_{loadcell_num}_COM_PORT',
                    f'LOADCELL_{loadcell_num}_BAUD_RATE'
                ])

            # Clear each setting
            for var in env_vars:
                config.set(var, '')

            # Reset all inputs to default values
            self._reset_to_defaults()

            # Enable editing
            self.enable_editing()

            # Close any existing connections
            self.cleanup()

            messagebox.showinfo("Reset Complete", "All settings have been reset to default values")

        except Exception as e:
            messagebox.showerror("Reset Error", f"Error during reset: {str(e)}")

    def _reset_to_defaults(self):
        """Helper method to reset all inputs to default values"""
        try:
            # Clear saved values from the settings file
            config.set('PLC_RX_DATA', '')
            for i in range(1, self.LOADCELL_COUNT + 1):
                config.set(f'LOADCELL_{i:02d}_RX_DATA', '')
            config.set('PLC_REG_ADDRESS', '')
            config.set('PLC_POINTS_TO_READ', '1')

            # Pick up ports plugged in since the page opened
            self.available_ports = [port.device for port in serial.tools.list_ports.comports()]

            # Empty every port and baud rate box, and every typed setting
            for combo in self.all_comboboxes:
                combo.config(state="readonly")
                combo.set("")
            for entry in self.all_entries:
                entry.config(state="normal")
                entry.delete(0, tk.END)
            for row in self.loadcell_rows:
                row.com_combo['values'] = self.available_ports or [""]
            self.points_entry.insert(0, "1")  # Reset to default of 1

            self.log_text.delete("1.0", tk.END)

        except Exception as e:
            print(f"Error resetting to defaults: {str(e)}")

    def load_saved_settings(self):
        """Load settings from environment variables"""
        try:
            # Load PLC settings
            plc_port = config.get('PLC_COM_PORT', '')
            plc_baud = config.get('PLC_BAUD_RATE', '')
            plc_station_id = config.get('PLC_STATION_ID', '')

            self.plc_com_combo.set(plc_port)
            self.plc_baud_combo.set(plc_baud)
            self.station_id_entry.delete(0, tk.END)
            self.station_id_entry.insert(0, plc_station_id)

            # Load Register Address and Points settings if they exist
            reg_address = config.get('PLC_REG_ADDRESS', '')
            points_to_read = config.get('PLC_POINTS_TO_READ', '1')

            self.reg_address_entry.delete(0, tk.END)
            self.reg_address_entry.insert(0, reg_address)

            self.points_entry.delete(0, tk.END)
            self.points_entry.insert(0, points_to_read)

            # Load Loadcell settings
            for row in self.loadcell_rows:
                row.com_combo.set(config.get(f'LOADCELL_{row.loadcell_num}_COM_PORT', ''))
                row.baud_combo.set(config.get(f'LOADCELL_{row.loadcell_num}_BAUD_RATE', ''))

            # Restore LVDT settings
            lvdt_port = config.get('LVDT_COM_PORT', '')
            lvdt_baud = config.get('LVDT_BAUD_RATE', '')
            if lvdt_port:
                self.lvdt_com_combo.set(lvdt_port)
            if lvdt_baud:
                self.lvdt_baud_combo.set(lvdt_baud)

            # Restore Camera settings
            for camera_num, (com_combo, baud_combo) in self.camera_combos.items():
                saved_port = config.get(f'CAMERA_{camera_num:02d}_COM_PORT', '')
                saved_baud = config.get(f'CAMERA_{camera_num:02d}_BAUD_RATE', '')
                if saved_port:
                    com_combo.set(saved_port)
                if saved_baud:
                    baud_combo.set(saved_baud)

            # Load Modbus TCP settings
            modbus_ip = config.get('MODBUS_TCP_IP', '')
            modbus_port = config.get('MODBUS_TCP_PORT', '')

            self.ip_entry.delete(0, tk.END)
            self.ip_entry.insert(0, modbus_ip)
            self.port_entry.delete(0, tk.END)
            self.port_entry.insert(0, modbus_port)

            # If we have saved settings, freeze the inputs
            if any([plc_port, plc_baud, plc_station_id, modbus_ip, modbus_port]):
                self._freeze_all_inputs()
                self.control_buttons["SAVE"].config(state="disabled")
                self.control_buttons["EDIT"].config(state="normal")

        except Exception as e:
            messagebox.showerror("Load Error", f"Failed to load settings!\nError: {str(e)}")

    def initialize_loadcell_env(self):
        """Initialize the in-memory loadcell buffers if they don't exist"""
        try:
            for i in range(1, self.LOADCELL_COUNT + 1):
                self.loadcell_data.setdefault(i, [])
        except Exception as e:
            print(f"Error initializing environment variables: {str(e)}")

    def save_loadcell_data(self, loadcell_num, value):
        """Save loadcell data to the in-memory buffer"""
        try:
            # Get existing data
            existing_data = self.loadcell_data.get(loadcell_num, [])
            
            # Create new data entry
            new_entry = {
                'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'value': value
            }
            
            # Add new entry and keep only last 100 readings
            existing_data.append(new_entry)
            if len(existing_data) > 100:  # Limit to last 100 readings
                existing_data = existing_data[-100:]
            
            # Save back to the in-memory buffer
            self.loadcell_data[loadcell_num] = existing_data
            
        except Exception as e:
            print(f"Error saving loadcell data: {str(e)}")

    def load_loadcell_data(self, frame):
        """Load previous loadcell data from the in-memory buffer"""
        try:
            loadcell_num = frame.loadcell_num

            # Get data from the in-memory buffer
            data = self.loadcell_data.get(loadcell_num, [])
            
            # Clear current display
            self.log_text.delete("1.0", tk.END)
            
            if data:
                # Display last 5 entries
                self.log_text.insert(tk.END, "Previous readings:\n")
                for entry in data[-5:]:
                    self.log_text.insert(tk.END, 
                                       f"{entry['timestamp']}: {entry['value']}\n")
            else:
                self.log_text.insert(tk.END, "No previous readings available\n")
            
        except Exception as e:
            self.log_text.insert(tk.END, f"Error loading previous data: {str(e)}\n")

    def save_device_values(self):
        """Keep what the devices sent back, for the next time the page opens"""
        try:
            config.set('PLC_RX_DATA', self.log_text.get("1.0", tk.END).strip())
        except Exception as e:
            print(f"Error saving device values: {str(e)}")

    def load_device_values(self):
        """Show what the devices sent back last time"""
        try:
            rx_data = config.get('PLC_RX_DATA', '')
            if rx_data:
                self.log_text.delete("1.0", tk.END)
                self.log_text.insert(tk.END, rx_data)
        except Exception as e:
            print(f"Error loading device values: {str(e)}")

    def cleanup(self):
        """Close all connections and save values before closing"""
        try:
            # Save all current values
            self.save_device_values()
            
            # Close all connections
            if hasattr(self, 'modbus_client') and self.modbus_client:
                self.modbus_client.close()
            
            if hasattr(self, 'modbus_tcp_client') and self.modbus_tcp_client:
                self.modbus_tcp_client.close()
                
            # Stop the LVDT stream and release its port
            self.disconnect_lvdt()
            
            # Close loadcell connections
            for frame, ser in self.loadcell_ports.items():
                if ser and ser.is_open:
                    ser.close()
                
        except Exception as e:
            print(f"Error during cleanup: {str(e)}")

    def load_plc_options(self):
        """Load PLC address options from plc_register.txt"""
        try:
            file_path = data_files.path(data_files.PLC_ON_REGISTER)
            
            with open(file_path, 'r') as file:
                # Read content and split by commas
                content = file.read().strip()
                options = [opt.strip() for opt in content.split(',') if opt.strip()]
                print(f"Loaded PLC options: {options}")  # Debug print
                return options
        except FileNotFoundError:
            print(f"Warning: PLC_on_register.txt not found at {file_path}")
            return []
        except Exception as e:
            print(f"Error reading PLC options: {str(e)}")
            return []

    def load_barcode_options(self):
        """Load barcode options from BarcodePrintFileNames.txt"""
        try:
            file_path = data_files.path(data_files.BARCODE_PRINT_FILE_NAMES)
            
            with open(file_path, 'r') as file:
                # Read content and split by commas
                content = file.read().strip()
                options = [opt.strip() for opt in content.split(',') if opt.strip()]
                print(f"Loaded barcode options: {options}")  # Debug print
                return options
        except FileNotFoundError:
            print(f"Warning: BarcodePrintFileNames.txt not found at {file_path}")
            return []
        except Exception as e:
            print(f"Error reading barcode options: {str(e)}")
            return []

    def read_holding_registers(self):
        """Read holding registers from PLC and interpret as double datatype values"""
        if self.modbus_client is None or not self.modbus_client.is_socket_open():
            messagebox.showerror("Error", "Not connected to PLC.")
            return

        try:
            slave_id = self.station_id_entry.get().strip()
            register_address = self.reg_address_entry.get().strip()
            points_to_read = self.points_entry.get().strip()
            
            # Validate inputs
            if not slave_id:
                messagebox.showerror("Error", "Station ID is mandatory!")
                return
            
            if not register_address:
                messagebox.showerror("Error", "Register address is mandatory!")
                return
            
            if not points_to_read or not points_to_read.isdigit():
                messagebox.showerror("Error", "Number of points must be a valid integer!")
                return

            slave_id = int(slave_id)
            points_to_read = int(points_to_read)
            
            # Validate points_to_read range
            if points_to_read < 1 or points_to_read > 125:  # Modbus limits for holding registers
                messagebox.showerror("Error", "Number of points must be between 1 and 125!")
                return
            
            # Clear the text box
            self.rx_text.delete("1.0", tk.END)
            
            # Process the register address
            try:
                # Handle D-prefixed addresses (e.g., D0001) by extracting the numeric part
                if register_address.startswith('D'):
                    # Remove 'D' prefix and convert to integer
                    addr = int(register_address[1:])
                elif register_address.startswith('0x'):
                    addr = int(register_address, 16)
                else:
                    # Try to parse as a direct integer
                    addr = int(register_address)
                
                # Add section header
                self.rx_text.insert(tk.END, f"Reading {points_to_read} Holding Register(s) starting at address {register_address}:\n\n")
                
                # Read holding registers
                response = self.modbus_client.read_holding_registers(
                    address=addr,
                    count=points_to_read,
                    device_id=slave_id
                )
                
                if response.isError():
                    self.rx_text.insert(tk.END, f"Error reading registers at address {register_address}\n")
                    return
                
                # Display raw register values
                self.rx_text.insert(tk.END, "Raw Register Values:\n")
                for i, reg_value in enumerate(response.registers):
                    self.rx_text.insert(tk.END, f"Register {addr + i}: {reg_value} (0x{reg_value:04X})\n")
                
                self.rx_text.insert(tk.END, "\n")
                
                # Process registers as doubles if we have at least 2 registers
                if len(response.registers) >= 2:
                    import struct
                    self.rx_text.insert(tk.END, "Interpreting as Double Values:\n")
                    
                    # Process each pair of registers as a double
                    for i in range(0, len(response.registers) - 1, 2):
                        reg1 = response.registers[i]
                        reg2 = response.registers[i + 1]
                        
                        self.rx_text.insert(tk.END, f"\nRegisters {addr + i} & {addr + i + 1} [{reg1}, {reg2}]:\n")
                        
                        # Try different byte orders for maximum compatibility
                        try:
                            # Standard 32-bit IEEE float format (big endian)
                            register_bytes = struct.pack('>HH', reg1, reg2)
                            float_value = struct.unpack('>f', register_bytes)[0]
                            self.rx_text.insert(tk.END, f"  Big-Endian (>f): {float_value:.6f}\n")
                        except Exception as e:
                            self.rx_text.insert(tk.END, f"  Big-Endian error: {str(e)}\n")
                        
                        try:
                            # Little endian format
                            register_bytes = struct.pack('<HH', reg1, reg2)
                            float_value = struct.unpack('<f', register_bytes)[0]
                            self.rx_text.insert(tk.END, f"  Little-Endian (<f): {float_value:.6f}\n")
                        except Exception as e:
                            self.rx_text.insert(tk.END, f"  Little-Endian error: {str(e)}\n")
                        
                        try:
                            # Swapped bytes format
                            register_bytes = struct.pack('>HH', reg2, reg1)
                            float_value = struct.unpack('>f', register_bytes)[0]
                            self.rx_text.insert(tk.END, f"  Swapped registers (>f): {float_value:.6f}\n")
                        except Exception as e:
                            self.rx_text.insert(tk.END, f"  Swapped registers error: {str(e)}\n")
                        
                        try:
                            # Swapped bytes with little endian
                            register_bytes = struct.pack('<HH', reg2, reg1)
                            float_value = struct.unpack('<f', register_bytes)[0]
                            self.rx_text.insert(tk.END, f"  Swapped registers (<f): {float_value:.6f}\n")
                        except Exception as e:
                            self.rx_text.insert(tk.END, f"  Swapped registers little-endian error: {str(e)}\n")
                else:
                    self.rx_text.insert(tk.END, "Need at least 2 registers to interpret as double value.\n")
                
            except ValueError as ve:
                self.rx_text.insert(tk.END, f"Invalid address format: {str(ve)}\n")
            except Exception as e:
                self.rx_text.insert(tk.END, f"Error reading register: {str(e)}\n")
            
            # Save the display values
            self.save_device_values()
            
        except ValueError as ve:
            messagebox.showerror("Error", f"Invalid input: {str(ve)}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read holding registers: {str(e)}")

def main():
    root = tk.Tk()
    app = ComPortSettings(root)
    
    # Add cleanup on window close
    root.protocol("WM_DELETE_WINDOW", lambda: [app.cleanup(), root.destroy()])
    
    root.mainloop()

if __name__ == "__main__":
    main()