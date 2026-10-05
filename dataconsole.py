import tkinter as tk
from tkinter import ttk, messagebox
from tkcalendar import DateEntry
import mysql.connector
import customtkinter as ctk

import db
import ui
from datetime import datetime
import csv
import os


# Results columns: heading, width, whether it takes up spare width. The
# readings are short numbers, so they stay narrow and the spec data - the
# one long value - gets whatever the window has left over.
COLUMNS = (
    ("NO", 55, False),
    ("LOT NUMBER", 135, False),
    ("PART NUMBER", 140, False),
    ("L1", 80, False),
    ("L2", 80, False),
    ("L3", 80, False),
    ("L4", 80, False),
    ("P1", 80, False),
    ("P2", 80, False),
    ("P3", 80, False),
    ("P4", 80, False),
    ("CAM2", 80, False),
    ("RESULT", 90, False),
    ("SCAN RESULT", 125, False),
    ("EMPLOYEE CODE", 165, False),
    ("SPEC DATA", 240, True),
    ("CREATED DATE", 185, False),
)

# Where the result sits in a fetched row. The query selects the ID first, in
# the place the NO column takes, so a row lines up with COLUMNS.
RESULT_AT = [heading for heading, _, _ in COLUMNS].index("RESULT")

# Shown in a cell with no value, rather than Python's "None".
BLANK = "—"


class DataConsole:
    def __init__(self, root):
        # Accept parent window instead of creating new one
        self.root = root
        ui.apply(root)
        self.root.title("EOL (END OF LINE) TESTER")

        # Database configuration
        self.db_config = db.get_config()
        self.columns = [heading for heading, _, _ in COLUMNS]
        # The rows on show, as raw values, for the export.
        self.export_rows = []

        ui.page_header(self.root, "Work Data")
        # The footer is packed before the body so it keeps the foot of the window
        ui.footer_bar(self.root)

        body = tk.Frame(self.root, bg=ui.APP_BG)
        body.pack(fill=tk.BOTH, expand=True, padx=ui.PAD_LARGE, pady=ui.PAD_LARGE)

        self.create_filter_bar(body)
        self.create_summary(body)
        self.create_table(body)

        self.load_part_numbers()
        # Open on this month's results rather than an empty table.
        self.root.after(100, lambda: self.search_records(quiet=True))

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def create_filter_bar(self, parent):
        """Every filter and both actions on one line."""
        card = ui.ctk_card(parent)
        card.pack(fill=tk.X)

        bar = tk.Frame(card, bg=ui.SURFACE)
        bar.pack(fill=tk.X, padx=ui.PAD_LARGE, pady=ui.PAD_LARGE)

        def label(text, column):
            tk.Label(bar, text=text, bg=ui.SURFACE, fg=ui.TEXT,
                     font=ui.FONT_BODY_BOLD).grid(
                         row=0, column=column, sticky="e",
                         padx=(ui.PAD_LARGE if column else 0, ui.PAD))

        label("PART NUMBER", 0)
        self.part_combobox = ttk.Combobox(bar, state="readonly", width=13)
        self.part_combobox.grid(row=0, column=1)

        # Start at the beginning of the current month, end today.
        current_date = datetime.now()
        start_of_month = current_date.replace(day=1)

        label("FROM", 2)
        self.start_date_entry = self.date_entry(bar, current_date)
        self.start_date_entry.grid(row=0, column=3)
        self.start_date_entry.set_date(start_of_month)

        label("TO", 4)
        self.end_date_entry = self.date_entry(bar, current_date)
        self.end_date_entry.grid(row=0, column=5)
        self.end_date_entry.set_date(current_date)

        label("RESULT", 6)
        self.result_combobox = ttk.Combobox(bar, state="readonly", width=8,
                                            values=["ALL", "PASS", "NG"])
        self.result_combobox.grid(row=0, column=7)
        self.result_combobox.set("ALL")

        label("STATUS", 8)
        self.status_combobox = ttk.Combobox(bar, state="readonly", width=9,
                                            values=["ACTIVE", "INACTIVE"])
        self.status_combobox.grid(row=0, column=9)
        self.status_combobox.set("ACTIVE")

        # The actions sit at the right-hand end, however wide the window is.
        bar.grid_columnconfigure(10, weight=1)

        actions = tk.Frame(bar, bg=ui.SURFACE)
        self.search_button = ui.ctk_button(actions, text="Search", icon='clipboard',
                                           kind='primary', width=120,
                                           command=self.search_records)
        self.search_button.pack(side=tk.LEFT, padx=(0, ui.PAD))

        self.export_button = ui.ctk_button(actions, text="Export CSV", icon='download',
                                           kind='success', width=130,
                                           command=self.export_to_csv)
        self.export_button.pack(side=tk.LEFT)

        def place_actions(event=None):
            # Beside the filters when they fit, otherwise on a line of their
            # own under them - never cut off the right-hand edge.
            filters = sum(child.winfo_reqwidth() for child in bar.grid_slaves(row=0)
                          if child is not actions) + 9 * ui.PAD_LARGE
            wide = bar.winfo_width() >= filters + actions.winfo_reqwidth() + ui.PAD_LARGE
            where = (dict(row=0, column=11, columnspan=1, sticky="e",
                          padx=(ui.PAD_LARGE, 0), pady=0) if wide else
                     dict(row=1, column=0, columnspan=12, sticky="e",
                          padx=0, pady=(ui.PAD, 0)))
            if actions.grid_info().get("row") != where["row"]:
                actions.grid(**where)

        actions.grid(row=0, column=11, sticky="e", padx=(ui.PAD_LARGE, 0))
        bar.bind("<Configure>", place_actions, add="+")

    def date_entry(self, parent, max_date):
        return DateEntry(parent,
                         width=11,
                         date_pattern='dd-mm-yyyy',
                         background=ui.ACCENT_FILL,
                         foreground=ui.TEXT_ON_ACCENT,
                         borderwidth=0,
                         font=ui.FONT_BODY,
                         showweeknumbers=False,
                         showothermonthdays=True,
                         firstweekday='sunday',
                         maxdate=max_date,
                         selectmode='day',
                         cursor='hand2')

    def create_summary(self, parent):
        """A row of totals for the search on show."""
        row = tk.Frame(parent, bg=ui.APP_BG)
        row.pack(fill=tk.X, pady=(ui.PAD_LARGE, 0))

        self.stats = {}
        for column, (key, title, colour) in enumerate((
                ('total', "RECORDS", ui.ACCENT),
                ('pass', "PASS", ui.SUCCESS),
                ('ng', "NG", ui.DANGER),
                ('rate', "PASS RATE", ui.ACCENT))):
            row.grid_columnconfigure(column, weight=1, uniform='stats')

            tile = ui.ctk_card(row, height=66)
            tile.grid(row=0, column=column, sticky="ew",
                      padx=(0 if column == 0 else ui.PAD, 0))
            tile.pack_propagate(False)

            ctk.CTkLabel(tile, text=title, text_color=ui.TEXT_MUTED,
                         font=(ui.FONT_FAMILY, 11, 'bold')).pack(
                             anchor='w', padx=ui.PAD_LARGE, pady=(ui.PAD, 0))
            value = ctk.CTkLabel(tile, text=BLANK, text_color=colour,
                                 font=(ui.FONT_FAMILY, 22, 'bold'))
            value.pack(anchor='w', padx=ui.PAD_LARGE)
            self.stats[key] = value

    def create_table(self, parent):
        table_card = ui.ctk_card(parent)
        table_card.pack(fill=tk.BOTH, expand=True, pady=(ui.PAD_LARGE, 0))

        header = ui.ctk_card_header(table_card, "RESULTS", icon='list')
        # What the table is showing, so a printout or photo of it says so.
        self.range_label = ctk.CTkLabel(header, text="", fg_color=ui.NAVY,
                                        text_color=ui.ACCENT_SOFT,
                                        font=ui.FONT_SMALL)
        self.range_label.pack(side='right', padx=ui.PAD_LARGE)

        table_frame = tk.Frame(table_card, bg=ui.SURFACE)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=ui.PAD_LARGE,
                         pady=ui.PAD_LARGE)
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        self.result_table = ttk.Treeview(table_frame, columns=self.columns,
                                         show="headings")
        scroll_y = ttk.Scrollbar(table_frame, orient=tk.VERTICAL,
                                 command=self.result_table.yview)
        scroll_x = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL,
                                 command=self.result_table.xview)
        self.result_table.configure(yscrollcommand=scroll_y.set,
                                    xscrollcommand=scroll_x.set)

        self.result_table.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")

        for heading, width, stretch in COLUMNS:
            self.result_table.heading(heading, text=heading)
            self.result_table.column(heading, width=width, minwidth=width,
                                     anchor="w" if stretch else "center",
                                     stretch=stretch)

        # Bands of five in light yellow, as on the Test console's results;
        # a failed part also reads red, so NG rows stand out in a long list.
        self.result_table.tag_configure('band', background=ui.ROW_BAND)
        self.result_table.tag_configure('plain', background=ui.ROW_PLAIN)
        self.result_table.tag_configure('ng', foreground=ui.DANGER)

        # Laid over the table while it has nothing to show.
        self.empty_label = tk.Label(table_frame, bg=ui.SURFACE, fg=ui.TEXT_MUTED,
                                    font=ui.FONT_SECTION,
                                    text="Choose the filters above and press Search.")
        self.empty_label.place(relx=0.5, rely=0.5, anchor="center")

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def load_part_numbers(self):
        """Load part numbers from TBL_MODEL_MASTER into combobox"""
        try:
            conn = mysql.connector.connect(**self.db_config)
            cursor = conn.cursor()

            cursor.execute("SELECT MM_PART_NUMBER FROM TBL_MODEL_MASTER ORDER BY MM_PART_NUMBER")
            part_numbers = [row[0] for row in cursor.fetchall()]

            # Add "ALL" option at the beginning
            part_numbers.insert(0, "ALL")
            self.part_combobox['values'] = part_numbers
            self.part_combobox.set("ALL")

            cursor.close()
            conn.close()

        except mysql.connector.Error as err:
            messagebox.showerror("Database Error", f"Failed to load part numbers: {err}")

    @staticmethod
    def display(value):
        if value is None or value == "":
            return BLANK
        if isinstance(value, datetime):
            return value.strftime('%d-%m-%Y  %H:%M:%S')
        return value

    def search_records(self, quiet=False):
        """Search records based on filters.

        `quiet` is for the search made on opening the page: a database that
        cannot be reached is then left to the empty table to show, rather
        than greeting the operator with an error box.
        """
        self.result_table.delete(*self.result_table.get_children())
        self.export_rows = []

        start = self.start_date_entry.get_date()
        end = self.end_date_entry.get_date()
        part_number = self.part_combobox.get()
        result_filter = self.result_combobox.get()

        conn = None
        try:
            conn = mysql.connector.connect(**self.db_config)
            cursor = conn.cursor()

            # Build query with dynamic filters for TBL_TEST_RESULTS
            query = """
                SELECT
                    ID, LOT_NUMBER, PART_NUMBER, L1, L2, L3, L4, P1, P2, P3, P4,
                    CAM2, RESULT, SCAN_RESULT, EMP_CODE, SPEC_DATA, CREATED_DATE
                FROM TBL_TEST_RESULTS
                WHERE DATE(CREATED_DATE) BETWEEN %s AND %s
            """
            params = [start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d')]

            # Add part number filter if not ALL
            if part_number and part_number != "ALL":
                query += " AND PART_NUMBER = %s"
                params.append(part_number)

            # Add result filter if not ALL
            if result_filter and result_filter != "ALL":
                query += " AND RESULT = %s"
                params.append(result_filter)

            query += " ORDER BY CREATED_DATE DESC"

            cursor.execute(query, tuple(params))
            records = cursor.fetchall()
            cursor.close()

        except mysql.connector.Error as err:
            self.show_summary([])
            self.empty_label.config(text="Could not reach the database.")
            self.empty_label.place(relx=0.5, rely=0.5, anchor="center")
            if not quiet:
                messagebox.showerror("Database Error", f"Search failed: {err}")
            return

        finally:
            if conn is not None and conn.is_connected():
                conn.close()

        for i, record in enumerate(records, 1):
            raw = [i] + list(record)[1:]  # Add row number, skip ID
            self.export_rows.append(raw)
            tags = ['band' if ((i - 1) // 5) % 2 == 0 else 'plain']
            if str(record[RESULT_AT]).upper() == 'NG':
                tags.append('ng')
            self.result_table.insert('', 'end', values=[self.display(v) for v in raw],
                                     tags=tuple(tags))

        self.range_label.configure(text="{} to {}{}".format(
            start.strftime('%d-%m-%Y'), end.strftime('%d-%m-%Y'),
            "" if part_number in ("", "ALL") else "  ·  " + part_number))
        self.show_summary(records)

        if records:
            self.empty_label.place_forget()
        else:
            self.empty_label.config(text="No records match these filters.")
            self.empty_label.place(relx=0.5, rely=0.5, anchor="center")

    def show_summary(self, records):
        total = len(records)
        passed = sum(1 for r in records if str(r[RESULT_AT]).upper() == 'PASS')
        failed = sum(1 for r in records if str(r[RESULT_AT]).upper() == 'NG')
        self.stats['total'].configure(text=str(total))
        self.stats['pass'].configure(text=str(passed))
        self.stats['ng'].configure(text=str(failed))
        self.stats['rate'].configure(
            text="{:.1f} %".format(100.0 * passed / total) if total else BLANK)

    def export_to_csv(self):
        """Export table data to CSV file"""
        try:
            # Check if there's data to export
            if not self.export_rows:
                messagebox.showwarning("No Data", "No data to export. Please search first.")
                return

            # Generate filename with timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"EOL_Data_Export_{timestamp}.csv"

            # Get desktop path
            desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
            filepath = os.path.join(desktop, filename)

            # Write to CSV
            with open(filepath, 'w', newline='', encoding='utf-8') as file:
                writer = csv.writer(file)
                writer.writerow(self.columns)
                for row in self.export_rows:
                    writer.writerow(['' if value is None else value for value in row])

            messagebox.showinfo("Export Successful", f"Data exported to:\n{filepath}")

        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to export data: {e}")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    root = tk.Tk()
    app = DataConsole(root)
    root.mainloop()
