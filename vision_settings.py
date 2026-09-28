"""Vision settings: choose the camera, teach parts and try them out.

Built like the other consoles - handed a window or a panel to fill - so
the main console can open it in its page area. Everything it changes goes
through vision_engine, the same code the Test console judges parts with,
so what passes here passes on the line.
"""

import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import cv2
import numpy as np
from PIL import Image, ImageTk

import db
import ui
from vision_engine import (
    VisionController,
    load_camera_config,
    save_camera_config,
    save_vision_config,
)
from vision_engine import camera
from vision_engine.vision_controller import DEFAULT_MATCH_THRESHOLD

CAMERA_SOURCES = ('cam1', 'cam2')
RESOLUTIONS = ('640x480', '800x600', '1280x720', '1920x1080')

# How often the live view is redrawn, and how often a background test is
# checked for its result.
PREVIEW_MS = 40
POLL_MS = 100

# Boxes drawn over the picture.
BOX_TAUGHT = ui.SKY
BOX_DRAWING = ui.WARNING
BOX_OK = ui.LAMP_PASS
BOX_NG = ui.LAMP_FAIL

# A box smaller than this on either side is refused by the engine.
MIN_BOX = 10


class VisionSettings:
    """Camera, inspection defaults, teaching and testing on one page."""

    def __init__(self, root):
        self.root = root
        ui.apply(root)
        self.root.title("EOL Tester - Vision Settings")
        self.root.protocol('WM_DELETE_WINDOW', self.on_close)

        self.controller = VisionController()

        self.stream = None          # live camera stream while the view is on
        self.preview_job = None
        self.poll_job = None
        self.test_thread = None
        self.test_result = None

        # What the picture shows: 'idle', 'live', 'reference' or 'result'.
        self.mode = 'idle'
        self.photo = None
        # (scale, x offset, y offset, image width, image height) of the
        # picture as last drawn, to turn mouse positions into pixels.
        self.view = None
        self.drag_start = None

        # Reference images being taught: {'image': frame, 'roi': dict or None}
        self.references = []

        ui.page_header(root, "Vision Settings")
        ui.footer_bar(root)

        body = tk.Frame(root, bg=ui.APP_BG)
        body.pack(fill='both', expand=True)
        body.grid_columnconfigure(0, weight=2, uniform='vision')
        body.grid_columnconfigure(1, weight=3, uniform='vision')
        body.grid_rowconfigure(0, weight=1)

        left = ui.scrollable(body)
        left.grid(row=0, column=0, sticky='nsew')
        self.build_camera_card(left.body)
        self.build_inspection_card(left.body)
        self.build_parts_card(left.body)
        tk.Frame(left.body, bg=ui.APP_BG, height=ui.PAD_LARGE).pack(fill='x')

        right = tk.Frame(body, bg=ui.APP_BG)
        right.grid(row=0, column=1, sticky='nsew')
        # Teaching is packed first, from the foot, so a short screen takes
        # its height from the picture rather than pushing the card off.
        self.build_teach_card(right)
        self.build_view_card(right)

        self.load_part_numbers()
        self.refresh_parts()
        self.show_message("Start the live view, or add a saved image, to teach a part.",
                          'idle')

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def card(self, parent, title, icon=None, expand=False, side='top'):
        """A titled rounded card. Returns the frame to fill."""
        card = ui.ctk_card(parent)
        card.pack(side=side, fill='both' if expand else 'x', expand=expand,
                  padx=ui.PAD_LARGE,
                  pady=(ui.PAD_LARGE, ui.PAD_LARGE if side == 'bottom' else 0))
        ui.ctk_card_header(card, title, icon=icon)

        inner = tk.Frame(card, bg=ui.SURFACE)
        inner.pack(fill='both', expand=True, padx=ui.PAD_LARGE, pady=ui.PAD_LARGE)
        return inner

    def label(self, parent, text, row, column=0):
        tk.Label(parent, text=text, bg=ui.SURFACE, fg=ui.TEXT,
                 font=ui.FONT_BODY_BOLD, anchor='e').grid(
                     row=row, column=column, sticky='e',
                     padx=(0, ui.PAD), pady=ui.PAD)

    def build_camera_card(self, parent):
        form = self.card(parent, "CAMERA", icon='camera')
        form.grid_columnconfigure(1, weight=1)

        self.label(form, "SOURCE:", 0)
        self.source_var = tk.StringVar(
            value=self.controller.config.get('camera_source', CAMERA_SOURCES[0]))
        source = ttk.Combobox(form, textvariable=self.source_var, state='readonly',
                              values=CAMERA_SOURCES, width=18)
        source.grid(row=0, column=1, sticky='w', pady=ui.PAD)
        source.bind('<<ComboboxSelected>>', lambda event: self.load_camera_fields())

        self.label(form, "DEVICE:", 1)
        self.device_var = tk.StringVar()
        self.device_box = ttk.Combobox(form, textvariable=self.device_var, width=18)
        self.device_box.grid(row=1, column=1, sticky='w', pady=ui.PAD)
        ui.ctk_button(form, "Find", icon='refresh', kind='neutral', width=90,
                      command=self.find_devices).grid(row=1, column=2, padx=(ui.PAD, 0))

        self.label(form, "RESOLUTION:", 2)
        self.resolution_var = tk.StringVar()
        ttk.Combobox(form, textvariable=self.resolution_var, values=RESOLUTIONS,
                     width=18).grid(row=2, column=1, sticky='w', pady=ui.PAD)

        ui.ctk_button(form, "Save Camera", icon='check', kind='success',
                      command=self.save_camera).grid(
                          row=3, column=0, columnspan=3, sticky='e', pady=(ui.PAD, 0))

        self.load_camera_fields()

    def build_inspection_card(self, parent):
        form = self.card(parent, "INSPECTION", icon='gear')
        form.grid_columnconfigure(1, weight=1)

        self.enabled_var = tk.BooleanVar(
            value=bool(self.controller.config.get('vision_enabled', True)))
        ttk.Checkbutton(form, text="Check parts with the camera during testing",
                        variable=self.enabled_var).grid(
                            row=0, column=0, columnspan=2, sticky='w', pady=ui.PAD)

        self.label(form, "DEFAULT THRESHOLD:", 1)
        self.threshold_var = tk.StringVar(value="{:.2f}".format(
            self.controller.config.get('match_threshold', DEFAULT_MATCH_THRESHOLD)))
        ttk.Spinbox(form, textvariable=self.threshold_var, from_=0.30, to=0.99,
                    increment=0.01, format='%.2f', width=8).grid(
                        row=1, column=1, sticky='w', pady=ui.PAD)

        tk.Label(form, bg=ui.SURFACE, fg=ui.TEXT_MUTED, font=ui.FONT_SMALL,
                 justify='left', anchor='w', wraplength=380,
                 text="A part passes when its match score reaches the threshold. "
                      "Each taught part keeps its own threshold; this one is "
                      "used for parts taught from now on.").grid(
                          row=2, column=0, columnspan=2, sticky='w')

        ui.ctk_button(form, "Save Inspection", icon='check', kind='success',
                      command=self.save_inspection).grid(
                          row=3, column=0, columnspan=2, sticky='e', pady=(ui.PAD, 0))

    def build_parts_card(self, parent):
        box = self.card(parent, "TAUGHT PARTS", icon='list')

        columns = ('part', 'refs', 'threshold', 'created')
        self.parts_tree = ttk.Treeview(box, columns=columns, show='headings',
                                       height=7, selectmode='browse')
        for column, heading, width in (('part', "PART NUMBER", 150),
                                       ('refs', "REFS", 50),
                                       ('threshold', "THRESHOLD", 90),
                                       ('created', "TAUGHT ON", 150)):
            self.parts_tree.heading(column, text=heading)
            self.parts_tree.column(column, width=width,
                                   anchor='w' if column == 'part' else 'center')
        self.parts_tree.pack(fill='x')
        self.parts_tree.bind('<<TreeviewSelect>>', lambda event: self.part_selected())

        row = tk.Frame(box, bg=ui.SURFACE)
        row.pack(fill='x', pady=(ui.PAD, 0))

        tk.Label(row, text="THRESHOLD:", bg=ui.SURFACE, fg=ui.TEXT,
                 font=ui.FONT_BODY_BOLD).pack(side='left')
        self.part_threshold_var = tk.StringVar()
        ttk.Spinbox(row, textvariable=self.part_threshold_var, from_=0.30, to=0.99,
                    increment=0.01, format='%.2f', width=6).pack(
                        side='left', padx=ui.PAD)
        ui.ctk_button(row, "Set", kind='neutral', width=60,
                      command=self.set_part_threshold).pack(side='left')

        buttons = tk.Frame(box, bg=ui.SURFACE)
        buttons.pack(fill='x', pady=(ui.PAD, 0))
        ui.ctk_button(buttons, "Test", icon='play', kind='primary', width=110,
                      command=self.test_part).pack(side='left')
        ui.ctk_button(buttons, "Delete", icon='alert', kind='danger', width=110,
                      command=self.delete_part).pack(side='right')

    def build_view_card(self, parent):
        box = self.card(parent, "LIVE VIEW", icon='camera', expand=True)

        self.canvas = tk.Canvas(box, bg=ui.CHART_SURFACE, highlightthickness=1,
                                highlightbackground=ui.BORDER, cursor='crosshair')
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda event: self.redraw())
        self.canvas.bind('<ButtonPress-1>', self.box_start)
        self.canvas.bind('<B1-Motion>', self.box_drag)
        self.canvas.bind('<ButtonRelease-1>', self.box_end)

        self.banner = ui.StatusBanner(box)
        self.banner.pack(fill='x', pady=(ui.PAD, 0))

        buttons = tk.Frame(box, bg=ui.SURFACE)
        buttons.pack(fill='x', pady=(ui.PAD, 0))
        self.live_button = ui.ctk_button(buttons, "Start Live View", icon='play',
                                         kind='primary', width=170,
                                         command=self.toggle_preview)
        self.live_button.pack(side='left')
        ui.ctk_button(buttons, "Capture Reference", icon='camera', kind='neutral',
                      width=170, command=self.capture_reference).pack(
                          side='left', padx=ui.PAD)
        ui.ctk_button(buttons, "Add Image File", icon='document', kind='neutral',
                      width=160, command=self.add_image_file).pack(side='left')

    def build_teach_card(self, parent):
        box = self.card(parent, "TEACH A PART", icon='box', side='bottom')
        box.grid_columnconfigure(1, weight=1)

        self.label(box, "PART NUMBER:", 0)
        self.part_var = tk.StringVar()
        self.part_box = ttk.Combobox(box, textvariable=self.part_var, width=24)
        self.part_box.grid(row=0, column=1, sticky='w', pady=ui.PAD)

        self.label(box, "REFERENCES:", 1)
        self.reference_list = ttk.Treeview(box, columns=('size', 'box'),
                                           show='tree headings', height=4,
                                           selectmode='browse')
        self.reference_list.heading('#0', text="IMAGE")
        self.reference_list.heading('size', text="SIZE")
        self.reference_list.heading('box', text="BOX")
        self.reference_list.column('#0', width=120)
        self.reference_list.column('size', width=100, anchor='center')
        self.reference_list.column('box', width=100, anchor='center')
        self.reference_list.tag_configure('boxed', foreground=ui.SUCCESS)
        self.reference_list.tag_configure('unboxed', foreground=ui.DANGER)
        self.reference_list.grid(row=1, column=1, sticky='ew', pady=ui.PAD)
        self.reference_list.bind('<<TreeviewSelect>>',
                                 lambda event: self.reference_selected())

        side = tk.Frame(box, bg=ui.SURFACE)
        side.grid(row=1, column=2, sticky='n', padx=(ui.PAD, 0), pady=ui.PAD)
        ui.ctk_button(side, "Remove", kind='neutral', width=90,
                      command=self.remove_reference).pack(fill='x')
        ui.ctk_button(side, "Clear", kind='neutral', width=90,
                      command=self.clear_references).pack(fill='x', pady=(ui.PAD, 0))

        tk.Label(box, bg=ui.SURFACE, fg=ui.TEXT_MUTED, font=ui.FONT_SMALL,
                 anchor='w', justify='left',
                 text="Pick a reference, then drag a box around the part on the "
                      "picture. Every reference needs its own box.").grid(
                          row=2, column=0, columnspan=3, sticky='w')

        ui.ctk_button(box, "Save Model", icon='check', kind='success', width=150,
                      command=self.save_model).grid(
                          row=3, column=0, columnspan=3, sticky='e', pady=(ui.PAD, 0))

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def show_message(self, text, level='info'):
        self.banner.show(text, level)

    # ------------------------------------------------------------------
    # Camera
    # ------------------------------------------------------------------

    def load_camera_fields(self):
        index, width, height = load_camera_config(self.source_var.get())
        self.device_var.set('' if index < 0 else str(index))
        self.resolution_var.set(f"{width}x{height}")

    def camera_fields(self):
        """(index, width, height) as typed, or None after saying what is wrong."""
        try:
            index = int(self.device_var.get())
        except ValueError:
            self.show_message("Choose a camera device first - press Find to list them.",
                              'warning')
            return None

        try:
            width, height = (int(part) for part in
                             self.resolution_var.get().lower().split('x'))
        except ValueError:
            self.show_message("Resolution must look like 640x480.", 'warning')
            return None

        if index < 0 or width <= 0 or height <= 0:
            self.show_message("Device and resolution must be positive numbers.",
                              'warning')
            return None
        return index, width, height

    def find_devices(self):
        self.show_message("Looking for cameras...", 'info')
        self.root.update_idletasks()
        found = camera.probe()
        self.device_box['values'] = [str(index) for index in found]
        if not found:
            self.show_message("No camera found. Check that it is plugged in.",
                              'danger')
            return
        if self.device_var.get() not in self.device_box['values']:
            self.device_var.set(str(found[0]))
        self.show_message("Found camera device(s): {}.".format(
            ", ".join(str(index) for index in found)), 'success')

    def save_camera(self):
        fields = self.camera_fields()
        if fields is None:
            return

        source = self.source_var.get()
        try:
            save_camera_config(source, *fields)
            self.controller.config['camera_source'] = source
            save_vision_config(self.controller.config)
        except OSError as e:
            messagebox.showerror("Vision Settings", f"Could not save the camera:\n\n{e}")
            return

        self.controller.reload_config()
        # A running view keeps the device and size it was opened with.
        if self.stream is not None:
            self.stop_preview()
            self.start_preview()
        self.show_message(f"Camera saved: {source} is device {fields[0]} "
                          f"at {fields[1]}x{fields[2]}.", 'success')

    def toggle_preview(self):
        if self.stream is None:
            self.start_preview()
        elif self.mode != 'live':
            # A reference or a test result is on show over the running view.
            self.clear_reference_selection()
            self.mode = 'live'
            self.live_button.configure(text="Stop Live View")
            self.show_message("Live view on.", 'success')
        else:
            self.stop_preview()
            self.mode = 'idle'
            self.redraw()
            self.show_message("Live view stopped.", 'idle')

    def start_preview(self):
        fields = self.camera_fields()
        if fields is None:
            return

        stream = camera.acquire(*fields)
        if stream is None or not stream.wait_until_open(timeout=5.0):
            if stream is not None:
                stream.release()
            self.show_message(f"Camera device {fields[0]} could not be opened.",
                              'danger')
            return

        self.stream = stream
        self.mode = 'live'
        self.clear_reference_selection()
        self.live_button.configure(text="Stop Live View",
                                   image=ui.icon_image('power', ui.TEXT_ON_ACCENT, 20))
        self.show_message("Live view on.", 'success')
        self.preview_tick()

    def stop_preview(self):
        if self.preview_job is not None:
            self.root.after_cancel(self.preview_job)
            self.preview_job = None
        if self.stream is not None:
            self.stream.release()
            self.stream = None
        if self.live_button.winfo_exists():
            self.live_button.configure(text="Start Live View",
                                       image=ui.icon_image('play', ui.TEXT_ON_ACCENT, 20))

    def preview_tick(self):
        self.preview_job = None
        if self.stream is None:
            return

        if not self.stream.is_alive():
            self.stop_preview()
            self.mode = 'idle'
            self.redraw()
            self.show_message("The camera stopped sending pictures.", 'danger')
            return

        if self.mode == 'live':
            frame = self.stream.latest()
            if frame is not None:
                self.draw(frame)

        self.preview_job = self.root.after(PREVIEW_MS, self.preview_tick)

    # ------------------------------------------------------------------
    # Picture
    # ------------------------------------------------------------------

    def draw(self, frame, boxes=()):
        """Fit a BGR frame to the canvas and draw (box, colour) pairs over it."""
        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)
        image_h, image_w = frame.shape[:2]
        scale = min(width / image_w, height / image_h)
        shown_w = max(int(image_w * scale), 1)
        shown_h = max(int(image_h * scale), 1)
        left = (width - shown_w) // 2
        top = (height - shown_h) // 2

        if frame.ndim == 2:
            rgb = cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
        else:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        picture = Image.fromarray(rgb).resize((shown_w, shown_h))
        self.photo = ImageTk.PhotoImage(picture)

        self.canvas.delete('all')
        self.canvas.create_image(left, top, image=self.photo, anchor='nw')
        self.view = (scale, left, top, image_w, image_h)

        for box, colour in boxes:
            self.draw_box(box, colour)

    def draw_box(self, box, colour, tag=None):
        scale, left, top, _, _ = self.view
        x, y, w, h = box
        self.canvas.create_rectangle(left + x * scale, top + y * scale,
                                     left + (x + w) * scale, top + (y + h) * scale,
                                     outline=colour, width=3, tags=tag)

    def redraw(self):
        """Draw again whatever the picture is meant to be showing."""
        if self.mode == 'reference':
            self.show_reference()
        elif self.mode == 'result' and self.test_result is not None:
            self.show_result(self.test_result)
        elif self.mode == 'live' and self.stream is not None:
            frame = self.stream.latest()
            if frame is not None:
                self.draw(frame)
        else:
            self.view = None
            self.canvas.delete('all')
            self.canvas.create_text(
                self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2,
                text="No picture", fill=ui.CHART_TEXT, font=ui.FONT_SECTION)

    def to_image(self, event):
        """Canvas position to a pixel of the picture shown, kept inside it."""
        scale, left, top, image_w, image_h = self.view
        x = int(round((event.x - left) / scale))
        y = int(round((event.y - top) / scale))
        return min(max(x, 0), image_w), min(max(y, 0), image_h)

    # ------------------------------------------------------------------
    # References and boxes
    # ------------------------------------------------------------------

    def refresh_reference_list(self, select=None):
        self.reference_list.delete(*self.reference_list.get_children())
        for index, reference in enumerate(self.references):
            image_h, image_w = reference['image'].shape[:2]
            boxed = bool(reference['roi'])
            self.reference_list.insert(
                '', 'end', iid=str(index), text=f"Reference {index + 1}",
                values=(f"{image_w}x{image_h}", "set" if boxed else "not set"),
                tags=('boxed' if boxed else 'unboxed',))
        if select is not None and 0 <= select < len(self.references):
            self.reference_list.selection_set(str(select))
            self.reference_list.see(str(select))

    def clear_reference_selection(self):
        self.reference_list.selection_remove(*self.reference_list.selection())

    def selected_reference(self):
        selection = self.reference_list.selection()
        return int(selection[0]) if selection else None

    def add_reference(self, image):
        self.references.append({'image': image, 'roi': None})
        index = len(self.references) - 1
        self.refresh_reference_list(select=index)
        self.reference_selected()

    def capture_reference(self):
        if self.stream is None:
            self.show_message("Start the live view first, then capture.", 'warning')
            return
        frame = self.stream.latest()
        if frame is None:
            self.show_message("The camera has not sent a picture yet.", 'warning')
            return
        self.add_reference(frame)

    def add_image_file(self):
        paths = filedialog.askopenfilenames(
            parent=self.root, title="Add reference images",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")])
        for path in paths:
            # imdecode rather than imread, which cannot open paths that are
            # not plain ASCII on Windows.
            image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                self.show_message(f"Not an image: {os.path.basename(path)}", 'danger')
                continue
            self.add_reference(image)

    def reference_selected(self):
        if self.selected_reference() is None:
            return
        self.mode = 'reference'
        if self.stream is not None:
            self.live_button.configure(text="Back to Live View")
        self.show_reference()
        self.show_message("Drag a box around the part on this reference.", 'info')

    def show_reference(self):
        index = self.selected_reference()
        if index is None:
            self.mode = 'live' if self.stream is not None else 'idle'
            self.redraw()
            return
        reference = self.references[index]
        roi = reference['roi']
        boxes = ()
        if roi:
            boxes = (((roi['x'], roi['y'], roi['width'], roi['height']), BOX_TAUGHT),)
        self.draw(reference['image'], boxes)

    def remove_reference(self):
        index = self.selected_reference()
        if index is None:
            return
        del self.references[index]
        self.refresh_reference_list(select=min(index, len(self.references) - 1))
        self.show_reference()

    def clear_references(self):
        if self.references and not messagebox.askyesno(
                "Vision Settings", "Remove every reference image?", parent=self.root):
            return
        self.references = []
        self.refresh_reference_list()
        self.show_reference()

    def box_start(self, event):
        if self.mode != 'reference' or self.view is None:
            return
        self.drag_start = self.to_image(event)

    def box_drag(self, event):
        if self.drag_start is None:
            return
        x0, y0 = self.drag_start
        x1, y1 = self.to_image(event)
        self.canvas.delete('drawing')
        self.draw_box((min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)),
                      BOX_DRAWING, tag='drawing')

    def box_end(self, event):
        if self.drag_start is None:
            return
        x0, y0 = self.drag_start
        x1, y1 = self.to_image(event)
        self.drag_start = None

        index = self.selected_reference()
        if index is None:
            return

        roi = {'x': min(x0, x1), 'y': min(y0, y1),
               'width': abs(x1 - x0), 'height': abs(y1 - y0)}
        if roi['width'] < MIN_BOX or roi['height'] < MIN_BOX:
            self.show_reference()
            self.show_message("That box is too small - drag a larger one.", 'warning')
            return

        self.references[index]['roi'] = roi
        self.refresh_reference_list(select=index)
        self.show_reference()
        self.show_message(f"Box set on reference {index + 1}: "
                          f"{roi['width']}x{roi['height']} pixels.", 'success')

    # ------------------------------------------------------------------
    # Settings, models and tests
    # ------------------------------------------------------------------

    def read_threshold(self, variable):
        try:
            value = float(variable.get())
        except ValueError:
            value = -1
        if not 0.0 < value < 1.0:
            self.show_message("A threshold is a number between 0 and 1, such as 0.75.",
                              'warning')
            return None
        return round(value, 2)

    def save_inspection(self):
        threshold = self.read_threshold(self.threshold_var)
        if threshold is None:
            return
        self.controller.config['vision_enabled'] = bool(self.enabled_var.get())
        self.controller.config['match_threshold'] = threshold
        try:
            save_vision_config(self.controller.config)
        except OSError as e:
            messagebox.showerror("Vision Settings",
                                 f"Could not save the inspection settings:\n\n{e}")
            return
        self.show_message("Inspection settings saved.", 'success')

    def load_part_numbers(self):
        """Part numbers from the model master, plus any already taught."""
        parts = set(self.controller.get_mapped_parts())
        try:
            conn = db.connect()
            cursor = conn.cursor()
            cursor.execute("SELECT MM_PART_NUMBER FROM TBL_MODEL_MASTER "
                           "ORDER BY MM_PART_NUMBER")
            parts.update(row[0] for row in cursor.fetchall() if row[0])
            cursor.close()
            conn.close()
        except Exception as e:
            # Teaching still works without the database: a part number can
            # be typed in.
            print(f"Vision Settings: could not read part numbers: {e}")
        self.part_box['values'] = sorted(parts)

    def refresh_parts(self, select=None):
        self.parts_tree.delete(*self.parts_tree.get_children())
        for part in sorted(self.controller.get_mapped_parts()):
            info = self.controller.model_info(part)
            if info is None:
                values = (part, '-', '-', "model file missing")
            else:
                values = (part, info['references'],
                          "{:.2f}".format(info['threshold']),
                          str(info['created']).replace('T', ' '))
            self.parts_tree.insert('', 'end', iid=part, values=values)
        if select is not None and self.parts_tree.exists(select):
            self.parts_tree.selection_set(select)
            self.parts_tree.see(select)

    def selected_part(self):
        selection = self.parts_tree.selection()
        return selection[0] if selection else None

    def part_selected(self):
        part = self.selected_part()
        if part is None:
            return
        info = self.controller.model_info(part)
        if info is not None:
            self.part_threshold_var.set("{:.2f}".format(info['threshold']))

    def save_model(self):
        part = self.part_var.get().strip()
        if not part:
            self.show_message("Enter the part number to teach.", 'warning')
            return
        if not self.references:
            self.show_message("Add at least one reference image first.", 'warning')
            return

        missing = [str(n) for n, reference in enumerate(self.references, start=1)
                   if not reference['roi']]
        if missing:
            self.show_message("Draw a box on reference {} first.".format(
                ", ".join(missing)), 'warning')
            return

        if self.controller.has_model(part) and not messagebox.askyesno(
                "Vision Settings",
                f"{part} is already taught. Replace its model?", parent=self.root):
            return

        threshold = self.read_threshold(self.threshold_var)
        if threshold is None:
            return

        try:
            self.controller.build_and_save_model(
                part, [reference['image'] for reference in self.references],
                [reference['roi'] for reference in self.references],
                match_threshold=threshold)
        except (ValueError, OSError) as e:
            self.show_message(f"Model not saved: {e}", 'danger')
            return

        self.references = []
        self.refresh_reference_list()
        self.load_part_numbers()
        self.refresh_parts(select=part)
        self.mode = 'live' if self.stream is not None else 'idle'
        self.redraw()
        self.show_message(f"{part} taught and saved.", 'success')

    def set_part_threshold(self):
        part = self.selected_part()
        if part is None:
            self.show_message("Choose a taught part first.", 'warning')
            return
        threshold = self.read_threshold(self.part_threshold_var)
        if threshold is None:
            return
        try:
            self.controller.set_model_threshold(part, threshold)
        except (ValueError, OSError, KeyError) as e:
            self.show_message(f"Threshold not changed: {e}", 'danger')
            return
        self.refresh_parts(select=part)
        self.show_message(f"{part} now passes at {threshold:.2f}.", 'success')

    def delete_part(self):
        part = self.selected_part()
        if part is None:
            self.show_message("Choose a taught part first.", 'warning')
            return
        if not messagebox.askyesno(
                "Vision Settings",
                f"Delete the vision model for {part}?\n\n"
                "It will no longer be checked by the camera until it is taught again.",
                parent=self.root):
            return
        try:
            self.controller.delete_model(part)
        except OSError as e:
            self.show_message(f"Model not deleted: {e}", 'danger')
            return
        self.refresh_parts()
        self.show_message(f"Vision model for {part} deleted.", 'success')

    def test_part(self):
        """Judge a picture against the selected part, as the Test console would."""
        part = self.selected_part()
        if part is None:
            self.show_message("Choose a taught part to test.", 'warning')
            return
        if self.test_thread is not None:
            return

        # The live picture when there is one; otherwise the engine captures
        # from the configured camera itself, exactly as on the line.
        frame = self.stream.latest() if self.stream is not None else None
        # Settings saved earlier on this page must be the ones judged with.
        self.controller.reload_config()

        self.test_result = None
        self.show_message(f"Testing {part}...", 'info')

        def work():
            self.test_result = self.controller.inspect(part, frame=frame)

        self.test_thread = threading.Thread(target=work, daemon=True)
        self.test_thread.start()
        self.poll_job = self.root.after(POLL_MS, self.poll_test)

    def poll_test(self):
        self.poll_job = None
        if self.test_thread is None:
            return
        if self.test_thread.is_alive():
            self.poll_job = self.root.after(POLL_MS, self.poll_test)
            return

        self.test_thread = None
        result = self.test_result
        if result is None:
            return

        if result.judgement == 'ERROR':
            self.show_message(f"{result.part_number}: {result.error}", 'danger')
            return

        self.clear_reference_selection()
        self.mode = 'result'
        self.show_result(result)
        self.show_message(
            "{}  {}  -  score {:.2f}, needs {:.2f}  ({} ms)".format(
                result.part_number, result.judgement, result.match_score,
                result.threshold, result.processing_time_ms),
            'success' if result.ok else 'danger')
        if self.stream is not None:
            self.live_button.configure(text="Back to Live View")

    def show_result(self, result):
        if result.frame is None:
            return
        boxes = ()
        if result.match_box:
            boxes = ((result.match_box, BOX_OK if result.ok else BOX_NG),)
        self.draw(result.frame, boxes)

    # ------------------------------------------------------------------
    # Closing
    # ------------------------------------------------------------------

    def cleanup(self):
        for job in (self.poll_job, self.preview_job):
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
        self.poll_job = None
        self.preview_job = None
        if self.stream is not None:
            self.stream.release()
            self.stream = None

    def on_close(self):
        self.cleanup()
        self.root.destroy()


def main():
    root = tk.Tk()
    root.state('zoomed')
    VisionSettings(root)
    root.mainloop()


if __name__ == "__main__":
    main()
