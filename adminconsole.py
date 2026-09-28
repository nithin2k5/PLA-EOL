import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox, filedialog
import mysql.connector
import customtkinter as ctk
import os
import json
from datetime import datetime, timedelta
import csv
import auth
import config
import db
import ui


# The employee form: (field, label). Every one of them is required.
FIELDS = (
    ("name", "FULL NAME"),
    ("number", "EMPLOYEE NUMBER"),
    ("password", "PASSWORD"),
    ("mobile", "MOBILE NUMBER"),
    ("designation", "DESIGNATION"),
    ("department", "DEPARTMENT"),
)

# Employee list columns: heading, width, whether it takes spare width.
LIST_COLUMNS = (
    ("FULL NAME", 170, True),
    ("EMPLOYEE NO", 150, False),
    ("DESIGNATION", 140, False),
    ("DEPARTMENT", 140, False),
    ("MOBILE", 125, False),
    ("STATUS", 90, False),
)


class AdminConsole:
    def __init__(self, root):
        self.root = root
        ui.apply(root)

        # Initialize backup paths and machine ID from the settings file
        self.backup_paths = {
            'primary': config.get('PRIMARY_BACKUP_PATH', ''),
            'secondary': config.get('SECONDARY_BACKUP_PATH', '')
        }
        self.machine_id = config.get('MACHINE_ID', '')
        self.set_title()

        # Database configuration
        self.db_config = db.get_config()

        # Initialize database
        self.init_database()

        self.root.configure(bg=ui.APP_BG)

        # The employee number of the record in the form, or None while the
        # form is for a new employee.
        self.editing = None
        # Every employee as loaded, for the list's search box to filter.
        self.employees = []

        self.setup_ui()
        self.load_records()
        self.clear_entries()

    def set_title(self):
        title = "ADMIN CONSOLE"
        if self.machine_id:
            title += f" - Machine ID: {self.machine_id}"
        self.root.title(title)

    def init_database(self):
        """Create the database and any missing tables."""
        if not db.init_database():
            messagebox.showerror("Database Error", "Failed to initialize database")

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def setup_ui(self):
        # The pink title bar and footer every console carries
        ui.page_header(self.root, "Admin")
        ui.footer_bar(self.root)

        body = tk.Frame(self.root, bg=ui.APP_BG)
        body.pack(fill=tk.BOTH, expand=True, padx=ui.PAD_LARGE, pady=ui.PAD_LARGE)
        body.grid_columnconfigure(0, weight=2, uniform='admin')
        body.grid_columnconfigure(1, weight=3, uniform='admin')
        body.grid_rowconfigure(0, weight=1)

        # The left column scrolls, so a short screen can still reach the form.
        left = ui.scrollable(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, ui.PAD_LARGE))
        # The form first: employees change far more often than the machine
        # setup, which is done once.
        self.create_employee_form(left.body)
        self.create_machine_card(left.body)

        self.create_employee_list(body)

    def card(self, parent, title, icon):
        """A titled rounded card. Returns (card, frame to fill)."""
        card = ui.ctk_card(parent)
        header = ui.ctk_card_header(card, title, icon=icon)
        inner = tk.Frame(card, bg=ui.SURFACE)
        inner.pack(fill=tk.BOTH, expand=True, padx=ui.PAD_LARGE, pady=ui.PAD_LARGE)
        card.header = header
        return card, inner

    @staticmethod
    def field_label(parent, text, row, column=0, columnspan=1):
        tk.Label(parent, text=text, bg=ui.SURFACE, fg=ui.TEXT_MUTED,
                 font=(ui.FONT_FAMILY, 10, 'bold'), anchor='w').grid(
                     row=row, column=column, columnspan=columnspan,
                     sticky='w', pady=(ui.PAD, 2))

    def create_machine_card(self, parent):
        """Machine ID, the two archive folders, and the employee backup."""
        card, box = self.card(parent, "MACHINE", 'gear')
        card.pack(fill=tk.X, padx=(0, ui.PAD), pady=(ui.PAD_LARGE, ui.PAD_LARGE))
        box.grid_columnconfigure(0, weight=1)

        self.field_label(box, "MACHINE ID", 0)
        self.machine_id_var = tk.StringVar(value=self.machine_id)
        self.machine_id_entry = tk.Entry(box, textvariable=self.machine_id_var,
                                         font=ui.FONT_BODY)
        self.machine_id_entry.grid(row=1, column=0, sticky='ew', ipady=3)
        ui.ctk_button(box, "Save ID", icon='check', kind='primary', width=110,
                      command=self.save_machine_id).grid(
                          row=1, column=1, padx=(ui.PAD, 0))

        self.path_vars = {}
        self.path_marks = {}
        for offset, (key, title) in enumerate((('primary', "PRIMARY ARCHIVE FOLDER"),
                                               ('secondary', "SECONDARY ARCHIVE FOLDER"))):
            row = 2 + offset * 2
            self.field_label(box, title, row)

            well = tk.Frame(box, bg=ui.SURFACE, highlightthickness=1,
                            highlightbackground=ui.BORDER)
            well.grid(row=row + 1, column=0, sticky='ew')
            mark = tk.Label(well, bg=ui.SURFACE, font=ui.FONT_BODY_BOLD, width=2)
            mark.pack(side=tk.LEFT, padx=(4, 0))
            var = tk.StringVar()
            # A label rather than an entry: the path is only ever chosen
            # with Browse. It is cut from the left when too long, since the
            # folder names at the end are the ones that tell two apart.
            path = tk.Label(well, bg=ui.SURFACE, fg=ui.TEXT,
                            font=ui.FONT_SMALL, anchor='w', width=1)
            path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 4), ipady=4)
            path.bind('<Button-1>', lambda e, k=key: self.browse_backup_path(k))
            path.bind('<Configure>', lambda e, lbl=path, v=var: self.fit_path(lbl, v))
            var.trace_add('write', lambda *a, lbl=path, v=var: self.fit_path(lbl, v))

            ui.ctk_button(box, "Browse", icon='document', kind='neutral', width=110,
                          command=lambda k=key: self.browse_backup_path(k)).grid(
                              row=row + 1, column=1, padx=(ui.PAD, 0))

            self.path_vars[key] = var
            self.path_marks[key] = mark
            self.show_path(key)

        tk.Label(box, bg=ui.SURFACE, fg=ui.TEXT_MUTED, font=ui.FONT_SMALL,
                 anchor='w', justify='left', wraplength=420,
                 text="Old test results are archived to both folders. Testing "
                      "stays disabled until the Machine ID and both folders "
                      "are set.").grid(row=6, column=0, columnspan=2, sticky='w',
                                       pady=(ui.PAD, 0))

        ui.ctk_button(box, "Back Up Employee List", icon='download', kind='success',
                      command=self.create_backup).grid(
                          row=7, column=0, columnspan=2, sticky='e',
                          pady=(ui.PAD_LARGE, 0))

    @staticmethod
    def fit_path(label, var):
        """Show as much of the end of the path as the label has room for."""
        text = var.get()
        font = tkfont.Font(font=label.cget('font'))
        room = label.winfo_width() - 8
        if room > 20 and font.measure(text) > room:
            while text and font.measure("…" + text) > room:
                text = text[1:]
            text = "…" + text
        label.config(text=text)

    def show_path(self, key):
        """Show a folder, marked by whether it is there."""
        path = self.backup_paths.get(key, '')
        mark = self.path_marks[key]
        if not path:
            self.path_vars[key].set("Not set - press Browse")
            mark.config(text="!", fg=ui.WARNING_HOVER)
        elif os.path.isdir(path):
            self.path_vars[key].set(path)
            mark.config(text="✓", fg=ui.SUCCESS)
        else:
            self.path_vars[key].set(path + "   (folder not found)")
            mark.config(text="✕", fg=ui.DANGER)

    def create_employee_form(self, parent):
        card, box = self.card(parent, "EMPLOYEE", 'shield')
        card.pack(fill=tk.X, padx=(0, ui.PAD))
        box.grid_columnconfigure(0, weight=1, uniform='form')
        box.grid_columnconfigure(1, weight=1, uniform='form')

        # Whether the form is adding someone or changing a record.
        self.mode_label = ctk.CTkLabel(card.header, text="", fg_color=ui.NAVY,
                                       text_color=ui.ACCENT_SOFT, font=ui.FONT_SMALL)
        self.mode_label.pack(side='right', padx=ui.PAD_LARGE)

        self.entries = {}
        for index, (key, title) in enumerate(FIELDS):
            row, column = divmod(index, 2)
            self.field_label(box, title, row * 2, column)
            entry = tk.Entry(box, font=ui.FONT_BODY,
                             show="•" if key == 'password' else "")
            entry.grid(row=row * 2 + 1, column=column, sticky='ew', ipady=3,
                       padx=(0, ui.PAD) if column == 0 else (ui.PAD, 0))
            self.entries[key] = entry

        self.password_hint = tk.Label(box, text="", bg=ui.SURFACE, fg=ui.TEXT_MUTED,
                                      font=ui.FONT_SMALL, anchor='w')
        self.password_hint.grid(row=6, column=0, columnspan=2, sticky='w',
                                pady=(ui.PAD, 0))

        self.active_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="Active - can sign in to Settings, Vision and Admin",
                        variable=self.active_var).grid(
                            row=7, column=0, columnspan=2, sticky='w',
                            pady=(ui.PAD, 0))

        actions = tk.Frame(box, bg=ui.SURFACE)
        actions.grid(row=8, column=0, columnspan=2, sticky='ew', pady=(ui.PAD_LARGE, 0))

        self.add_button = ui.ctk_button(actions, "Add Employee", icon='check',
                                        kind='success', width=150,
                                        command=self.add_record)
        self.save_button = ui.ctk_button(actions, "Save Changes", icon='check',
                                         kind='primary', width=150,
                                         command=self.save_record)
        self.delete_button = ui.ctk_button(actions, "Delete", icon='alert',
                                           kind='danger', width=110,
                                           command=self.delete_record)
        self.clear_button = ui.ctk_button(actions, "New / Clear", icon='refresh',
                                          kind='neutral', width=130,
                                          command=self.clear_entries)
        self.clear_button.pack(side=tk.RIGHT)

        self.banner = ui.StatusBanner(box)
        self.banner.grid(row=9, column=0, columnspan=2, sticky='ew',
                         pady=(ui.PAD_LARGE, 0))

    def create_employee_list(self, parent):
        card, box = self.card(parent, "EMPLOYEES", 'list')
        card.grid(row=0, column=1, sticky="nsew")

        self.count_label = ctk.CTkLabel(card.header, text="", fg_color=ui.NAVY,
                                        text_color=ui.ACCENT_SOFT, font=ui.FONT_SMALL)
        self.count_label.pack(side='right', padx=ui.PAD_LARGE)

        search_row = tk.Frame(box, bg=ui.SURFACE)
        search_row.pack(fill=tk.X, pady=(0, ui.PAD))
        tk.Label(search_row, text="SEARCH", bg=ui.SURFACE, fg=ui.TEXT_MUTED,
                 font=(ui.FONT_FAMILY, 10, 'bold')).pack(side=tk.LEFT, padx=(0, ui.PAD))
        self.search_var = tk.StringVar()
        self.search_var.trace_add('write', lambda *a: self.show_employees())
        tk.Entry(search_row, textvariable=self.search_var, font=ui.FONT_BODY).pack(
            side=tk.LEFT, fill=tk.X, expand=True, ipady=3)

        tree_frame = tk.Frame(box, bg=ui.SURFACE)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        columns = [heading for heading, _, _ in LIST_COLUMNS]
        self.tree = ttk.Treeview(tree_frame, columns=columns, show='headings',
                                 selectmode='browse')
        for heading, width, stretch in LIST_COLUMNS:
            self.tree.heading(heading, text=heading)
            self.tree.column(heading, width=width, minwidth=width, stretch=stretch,
                             anchor='w' if stretch else 'center')
        self.tree.tag_configure('inactive', foreground=ui.TEXT_MUTED)

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        sideways = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=sideways.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scrollbar.grid(row=0, column=1, sticky='ns')
        sideways.grid(row=1, column=0, sticky='ew')

        tk.Label(box, bg=ui.SURFACE, fg=ui.TEXT_MUTED, font=ui.FONT_SMALL, anchor='w',
                 text="Click an employee to change or delete them.").pack(
                     fill=tk.X, pady=(ui.PAD, 0))

        self.tree.bind('<<TreeviewSelect>>', self.on_tree_select)

    # ------------------------------------------------------------------
    # Employee list
    # ------------------------------------------------------------------

    def load_records(self):
        """Load every employee, then show those matching the search box."""
        try:
            conn = mysql.connector.connect(**self.db_config)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT EMPLOYEE_FULL_NAME, EMPLOYEE_NUMBER, DESIGNATION,
                       DEPARTMENT, MOBILE_NUMBER, IS_ACTIVE
                FROM EMPLOYEE_INFO
                ORDER BY ID DESC
            """)
            self.employees = cursor.fetchall()
            cursor.close()
            conn.close()
        except mysql.connector.Error as err:
            self.employees = []
            messagebox.showerror("Database Error", f"Failed to load records: {err}")
        self.show_employees()

    def show_employees(self):
        wanted = self.search_var.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        shown = 0
        for name, number, designation, department, mobile, active in self.employees:
            values = [name, number, designation, department, mobile]
            if wanted and not any(wanted in str(v or '').lower() for v in values):
                continue
            # The employee number is the row's id, so it is read back exactly
            # as stored - a list value would turn "0123" into the number 123.
            self.tree.insert('', 'end', iid=number,
                             values=[v or '' for v in values] +
                                    ["Active" if active else "Inactive"],
                             tags=() if active else ('inactive',))
            shown += 1

        total = len(self.employees)
        active = sum(1 for e in self.employees if e[5])
        self.count_label.configure(
            text=f"{total} employees  ·  {active} active" if shown == total
            else f"{shown} of {total} shown")

        if self.editing is not None and self.tree.exists(self.editing):
            self.tree.selection_set(self.editing)

    def on_tree_select(self, event=None):
        """Load the selected employee into the form."""
        selected = self.tree.selection()
        if not selected or selected[0] == self.editing:
            return
        emp_number = selected[0]
        try:
            conn = mysql.connector.connect(**self.db_config)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT EMPLOYEE_FULL_NAME, EMPLOYEE_NUMBER, MOBILE_NUMBER,
                       DESIGNATION, DEPARTMENT, IS_ACTIVE
                FROM EMPLOYEE_INFO
                WHERE EMPLOYEE_NUMBER = %s
            """, (emp_number,))
            record = cursor.fetchone()
            cursor.close()
            conn.close()
        except mysql.connector.Error as err:
            self.banner.show(f"Could not load the employee: {err}", 'danger')
            return
        if not record:
            return

        name, number, mobile, designation, department, active = record
        for key, value in (('name', name), ('number', number), ('password', ''),
                           ('mobile', mobile), ('designation', designation),
                           ('department', department)):
            self.entries[key].delete(0, tk.END)
            self.entries[key].insert(0, value or '')
        self.active_var.set(bool(active))
        self.set_mode(number)
        self.banner.show(f"Editing {name}. Change the details and press Save Changes.",
                         'info')

    # ------------------------------------------------------------------
    # Employee form
    # ------------------------------------------------------------------

    def set_mode(self, emp_number):
        """Switch the form between adding (None) and editing an employee."""
        self.editing = emp_number
        self.add_button.pack_forget()
        self.save_button.pack_forget()
        self.delete_button.pack_forget()
        if emp_number is None:
            self.mode_label.configure(text="NEW EMPLOYEE")
            self.password_hint.config(text="Set the password they will sign in with.")
            self.add_button.pack(side=tk.LEFT)
        else:
            self.mode_label.configure(text=f"EDITING {emp_number}")
            self.password_hint.config(text="Leave the password blank to keep the current one.")
            self.save_button.pack(side=tk.LEFT)
            self.delete_button.pack(side=tk.LEFT, padx=(ui.PAD, 0))

    def form_values(self, need_password):
        """The form's values, or None after pointing at the first one missing."""
        values = {key: entry.get().strip() for key, entry in self.entries.items()}
        for key, title in FIELDS:
            if key == 'password' and not need_password:
                continue
            if not values[key]:
                self.banner.show(f"Please enter the {title.lower()}.", 'warning')
                self.entries[key].focus_set()
                return None
        return values

    def clear_entries(self):
        """Empty the form, ready for a new employee."""
        for entry in self.entries.values():
            entry.delete(0, tk.END)
        self.active_var.set(True)
        self.set_mode(None)
        if self.tree.selection():
            self.tree.selection_remove(self.tree.selection())
        self.banner.show("Fill in the form to add an employee, or pick one from the list.",
                         'idle')
        self.entries['name'].focus_set()

    def add_record(self):
        """Add a new employee record"""
        values = self.form_values(need_password=True)
        if values is None:
            return

        machine_id = self.machine_id_var.get().strip()
        if not machine_id:
            self.banner.show("Set and save the Machine ID first.", 'warning')
            self.machine_id_entry.focus_set()
            return

        try:
            conn = mysql.connector.connect(**self.db_config)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO EMPLOYEE_INFO (
                    EMPLOYEE_FULL_NAME, EMPLOYEE_NUMBER, PASSWORD,
                    DESIGNATION, DEPARTMENT, MOBILE_NUMBER, MACHINE_ID, IS_ACTIVE
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                values['name'], values['number'],
                auth.compute_hash(values['password']),
                values['designation'], values['department'], values['mobile'],
                machine_id, bool(self.active_var.get()),
            ))
            conn.commit()
            cursor.close()
            conn.close()
        except mysql.connector.Error as err:
            if err.errno == 1062:  # Duplicate entry error
                self.banner.show(f"Employee number {values['number']} already exists.",
                                 'danger')
                self.entries['number'].focus_set()
            else:
                messagebox.showerror("Database Error", f"Failed to add employee: {err}")
            return

        self.load_records()
        self.clear_entries()
        self.banner.show(f"{values['name']} added.", 'success')

    def save_record(self):
        """Save changes to the employee in the form."""
        if self.editing is None:
            return
        values = self.form_values(need_password=False)
        if values is None:
            return

        # The password column is only touched when a new one was typed.
        columns = ["EMPLOYEE_FULL_NAME = %s", "EMPLOYEE_NUMBER = %s",
                   "DESIGNATION = %s", "DEPARTMENT = %s", "MOBILE_NUMBER = %s",
                   "IS_ACTIVE = %s"]
        parameters = [values['name'], values['number'], values['designation'],
                      values['department'], values['mobile'],
                      bool(self.active_var.get())]
        if values['password']:
            columns.append("PASSWORD = %s")
            parameters.append(auth.compute_hash(values['password']))
        parameters.append(self.editing)

        try:
            conn = mysql.connector.connect(**self.db_config)
            cursor = conn.cursor()
            cursor.execute("UPDATE EMPLOYEE_INFO SET " + ", ".join(columns) +
                           " WHERE EMPLOYEE_NUMBER = %s", tuple(parameters))
            conn.commit()
            cursor.close()
            conn.close()
        except mysql.connector.Error as err:
            if err.errno == 1062:
                self.banner.show(f"Employee number {values['number']} already exists.",
                                 'danger')
            else:
                messagebox.showerror("Database Error", f"Failed to update record: {err}")
            return

        self.editing = values['number']
        self.load_records()
        self.set_mode(values['number'])
        self.entries['password'].delete(0, tk.END)
        self.banner.show(f"{values['name']} saved.", 'success')

    def delete_record(self):
        """Delete the employee in the form."""
        if self.editing is None:
            return
        name = self.entries['name'].get().strip() or self.editing
        if not messagebox.askyesno(
                "Delete Employee",
                f"Delete {name} ({self.editing})?\n\n"
                "To stop someone signing in but keep their record, untick "
                "Active and save instead.", parent=self.root):
            return

        try:
            conn = mysql.connector.connect(**self.db_config)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM EMPLOYEE_INFO WHERE EMPLOYEE_NUMBER = %s",
                           (self.editing,))
            conn.commit()
            cursor.close()
            conn.close()
        except mysql.connector.Error as err:
            messagebox.showerror("Database Error", f"Failed to delete record: {err}")
            return

        self.editing = None
        self.load_records()
        self.clear_entries()
        self.banner.show(f"{name} deleted.", 'success')

    # ------------------------------------------------------------------
    # Machine and archive folders
    # ------------------------------------------------------------------

    def browse_backup_path(self, path_type):
        """Open folder selection dialog and update backup path"""
        try:
            current_path = self.backup_paths.get(path_type, '')
            initial_dir = current_path if current_path and os.path.isdir(current_path) \
                else os.path.expanduser('~')

            folder_path = filedialog.askdirectory(
                parent=self.root,
                title=f"Select {path_type.title()} Archive Folder",
                initialdir=initial_dir
            )
            if not folder_path:
                return

            self.backup_paths[path_type] = folder_path
            config.set(f'{path_type.upper()}_BACKUP_PATH', folder_path)
            self.show_path(path_type)
            self.banner.show(f"{path_type.title()} archive folder set.", 'success')

        except Exception as e:
            messagebox.showerror("Error", f"Failed to set {path_type} backup path:\n{e}")

    def create_backup(self):
        """Create backup of last 3 months data"""
        conn = None
        try:
            primary_path = self.backup_paths.get('primary') or None
            secondary_path = self.backup_paths.get('secondary') or None

            if not primary_path and not secondary_path:
                messagebox.showerror("Error", "Please select at least one valid backup location!")
                return

            # Validate that paths exist
            if primary_path and not os.path.exists(primary_path):
                messagebox.showerror("Error", f"Primary backup path does not exist: {primary_path}")
                return
            if secondary_path and not os.path.exists(secondary_path):
                messagebox.showerror("Error", f"Secondary backup path does not exist: {secondary_path}")
                return

            # Calculate date range
            end_date = datetime.now()
            start_date = end_date - timedelta(days=90)  # 3 months

            conn = mysql.connector.connect(**self.db_config)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    EMPLOYEE_FULL_NAME,
                    EMPLOYEE_NUMBER,
                    DESIGNATION,
                    DEPARTMENT,
                    MOBILE_NUMBER,
                    MACHINE_ID,
                    IS_ACTIVE,
                    CREATED_DATE
                FROM EMPLOYEE_INFO
                WHERE CREATED_DATE BETWEEN %s AND %s
            """, (start_date, end_date))
            data = cursor.fetchall()
            cursor.close()

            if not data:
                messagebox.showinfo("Info", "No data found for the last 3 months")
                return

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"employee_backup_{timestamp}.csv"
            headers = [
                "Employee Name",
                "Employee Number",
                "Designation",
                "Department",
                "Mobile Number",
                "Machine ID",
                "Status",
                "Created Date"
            ]

            def save_backup(path):
                if path:
                    full_path = os.path.join(path, filename)
                    with open(full_path, 'w', newline='') as f:
                        writer = csv.writer(f)
                        writer.writerow(headers)
                        for row in data:
                            writer.writerow(row)
                    return full_path
                return None

            primary_file = save_backup(primary_path)
            secondary_file = save_backup(secondary_path)

            backup_info = {
                'timestamp': timestamp,
                'date_range': {
                    'start': start_date.strftime("%Y-%m-%d"),
                    'end': end_date.strftime("%Y-%m-%d")
                },
                'record_count': len(data),
                'primary_location': primary_file,
                'secondary_location': secondary_file
            }

            if primary_path:
                info_file = os.path.join(primary_path, f"backup_info_{timestamp}.json")
                with open(info_file, 'w') as f:
                    json.dump(backup_info, f, indent=4)

            messagebox.showinfo("Success",
                                f"Backup created successfully!\n"
                                f"Records backed up: {len(data)}\n"
                                f"Date range: {start_date.date()} to {end_date.date()}")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to create backup: {str(e)}")
        finally:
            if conn is not None:
                conn.close()

    def save_machine_id(self):
        """Save the Machine ID and give it to employees that have none."""
        try:
            machine_id = self.machine_id_var.get().strip()
            if not machine_id:
                self.banner.show("Please enter a Machine ID.", 'warning')
                self.machine_id_entry.focus_set()
                return

            # Save to the settings file
            config.set('MACHINE_ID', machine_id)
            self.machine_id = machine_id

            conn = mysql.connector.connect(**self.db_config)
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE EMPLOYEE_INFO
                SET MACHINE_ID = %s
                WHERE MACHINE_ID IS NULL OR MACHINE_ID = ''
            """, (machine_id,))
            conn.commit()
            cursor.close()
            conn.close()

            self.set_title()
            self.load_records()
            self.banner.show(f"Machine ID saved: {machine_id}", 'success')

        except Exception as e:
            messagebox.showerror("Error", f"Failed to save Machine ID: {str(e)}")

    def cleanup(self):
        """Cleanup function called when closing the application"""
        try:
            # Save backup paths and machine ID to the settings file
            if self.backup_paths.get('primary'):
                config.set('PRIMARY_BACKUP_PATH', self.backup_paths['primary'])
            if self.backup_paths.get('secondary'):
                config.set('SECONDARY_BACKUP_PATH', self.backup_paths['secondary'])
            if self.machine_id_var.get().strip():
                config.set('MACHINE_ID', self.machine_id_var.get().strip())

        except Exception as e:
            print(f"Error during cleanup: {str(e)}")


def main():
    root = tk.Tk()
    app = AdminConsole(root)
    root.mainloop()


if __name__ == "__main__":
    main()
