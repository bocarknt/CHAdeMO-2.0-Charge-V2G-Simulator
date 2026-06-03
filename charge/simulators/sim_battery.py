"""
CHAdeMO Battery Simulator
Design: original compact left-panel + big log.
Change from original: fields that are calculated (not user-chosen)
show as read-only value labels instead of sliders.

SENT:     MSG100, MSG101, MSG102
RECEIVED: MSG108, MSG109
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import threading, serial, serial.tools.list_ports
from datetime import datetime

BG         = "#0D1117"
PANEL      = "#161B22"
BORDER     = "#30363D"
ACCENT     = "#E3B341"
TEXT       = "#E6EDF3"
TEXT_MUTED = "#8B949E"
SUCCESS    = "#3FB950"
DANGER     = "#F85149"
WARNING    = "#E3B341"
BLUE       = "#79C0FF"

# ── Serial thread ─────────────────────────────────────────────────────────────
class SerialThread(threading.Thread):
    def __init__(self, port, baud, on_receive, on_error):
        super().__init__(daemon=True)
        self.port=port; self.baud=baud
        self.on_receive=on_receive; self.on_error=on_error
        self.running=False; self.ser=None

    def run(self):
        self.running=True
        try:
            self.ser=serial.Serial(self.port, self.baud, timeout=1)
            while self.running:
                if self.ser.in_waiting:
                    line=self.ser.readline().decode("utf-8", errors="replace").strip()
                    if line: self.on_receive(line)
        except serial.SerialException as e: self.on_error(str(e))
        finally:
            if self.ser and self.ser.is_open: self.ser.close()

    def send(self, text):
        if self.ser and self.ser.is_open:
            self.ser.write((text+"\n").encode("utf-8"))

    def stop(self): self.running=False

# ── Helpers — all use pack, never grid ───────────────────────────────────────
def slider_row(parent, label, unit, lo, hi, initial, res=1):
    """User-editable slider row."""
    var = tk.DoubleVar(value=initial)
    row = tk.Frame(parent, bg=PANEL)
    row.pack(fill="x", padx=6, pady=2)
    tk.Label(row, text=label, bg=PANEL, fg=TEXT_MUTED,
             font=("Courier", 8), width=18, anchor="w").pack(side="left")
    def _c(_=None): var.set(round(var.get(), 1))
    tk.Scale(row, variable=var, from_=lo, to=hi, resolution=res,
             orient="horizontal", bg=PANEL, fg=TEXT, highlightthickness=0,
             troughcolor=BORDER, activebackground=ACCENT,
             length=120, showvalue=False, command=_c).pack(side="left", padx=(4,6))
    tk.Label(row, textvariable=var, bg=PANEL, fg=ACCENT,
             font=("Courier", 9, "bold"), width=5, anchor="e").pack(side="left")
    tk.Label(row, text=unit, bg=PANEL, fg=TEXT_MUTED,
             font=("Courier", 8), width=5, anchor="w").pack(side="left", padx=(3,0))
    return var

def value_row(parent, label, unit, init="—", color=BLUE):
    """Read-only display row for calculated/received values."""
    var = tk.StringVar(value=str(init))
    row = tk.Frame(parent, bg=PANEL)
    row.pack(fill="x", padx=6, pady=2)
    tk.Label(row, text=label, bg=PANEL, fg=TEXT_MUTED,
             font=("Courier", 8), width=18, anchor="w").pack(side="left")
    tk.Label(row, textvariable=var, bg=PANEL, fg=color,
             font=("Courier", 9, "bold"), width=7, anchor="e").pack(side="left", padx=(8,4))
    tk.Label(row, text=unit, bg=PANEL, fg=TEXT_MUTED,
             font=("Courier", 8), width=5, anchor="w").pack(side="left", padx=(3,0))
    return var

# ── App ───────────────────────────────────────────────────────────────────────
class BatteryApp:
    def __init__(self, root):
        self.root = root
        root.title("CHAdeMO Battery Simulator")
        root.configure(bg=BG)
        root.minsize(900, 580)

        self.serial_thread  = None
        self.connected      = False
        self._loop_job      = None
        self._avail_voltage = 0
        self._avail_current = 0
        self._user_max_current = 0
        self._calc_est_minutes = 0
        self._msg109_count  = 0

        self._build_ui()
        self._refresh_ports()
        root.after(3000, self._auto_refresh_ports)
        root.after(200,  self._refresh_calc_display)  # init display from defaults

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Header
        hdr = tk.Frame(self.root, bg=PANEL, height=36)
        hdr.pack(fill="x", padx=6, pady=(6,3))
        hdr.pack_propagate(False)
        tk.Label(hdr, text="🔋  CHAdeMO BATTERY SIMULATOR",
                 bg=PANEL, fg=ACCENT, font=("Courier", 11, "bold")).pack(side="left", padx=10)
        self.status_badge = tk.Label(hdr, text="● DISCONNECTED",
                 bg=PANEL, fg=DANGER, font=("Courier", 9, "bold"))
        self.status_badge.pack(side="right", padx=10)

        # Connection bar
        cbar = tk.Frame(self.root, bg=BG)
        cbar.pack(fill="x", padx=6, pady=(0,4))
        tk.Label(cbar, text="PORT", bg=BG, fg=TEXT_MUTED, font=("Courier", 9)).pack(side="left")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(cbar, textvariable=self.port_var, width=18, state="readonly")
        self.port_combo.pack(side="left", padx=(4,12))
        tk.Label(cbar, text="BAUD", bg=BG, fg=TEXT_MUTED, font=("Courier", 9)).pack(side="left")
        self.baud_var = tk.StringVar(value="115200")
        ttk.Combobox(cbar, textvariable=self.baud_var, width=9, state="readonly",
                     values=["9600","19200","57600","115200","230400"]).pack(side="left", padx=(4,12))
        self.connect_btn = tk.Button(cbar, text="CONNECT", width=10,
                 bg=ACCENT, fg=BG, font=("Courier", 9, "bold"),
                 relief="flat", cursor="hand2", command=self._toggle_connection)
        self.connect_btn.pack(side="left")

        # Main: left (controls) | right (SOC + charger display + log)
        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True, padx=6, pady=(0,6))

        left = tk.Frame(main, bg=BG, width=360)
        left.pack(side="left", fill="y", padx=(0,4))
        left.pack_propagate(False)

        right_outer = tk.Frame(main, bg=BG)
        right_outer.pack(side="left", fill="both", expand=True)

        # Right side: scrollable canvas
        canvas = tk.Canvas(right_outer, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(right_outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        right = tk.Frame(canvas, bg=BG)
        right_window = canvas.create_window((0, 0), window=right, anchor="nw")

        def _on_frame_configure(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        def _on_canvas_configure(e):
            canvas.itemconfig(right_window, width=e.width)
        right.bind("<Configure>", _on_frame_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        def _on_mousewheel(e):
            canvas.yview_scroll(int(-1*(e.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # Left panel: scrollable
        lc = tk.Canvas(left, bg=BG, highlightthickness=0)
        lsb = ttk.Scrollbar(left, orient="vertical", command=lc.yview)
        lc.configure(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y")
        lc.pack(side="left", fill="both", expand=True)
        lframe = tk.Frame(lc, bg=BG)
        lw = lc.create_window((0,0), window=lframe, anchor="nw")
        lframe.bind("<Configure>", lambda e: lc.configure(scrollregion=lc.bbox("all")))
        lc.bind("<Configure>", lambda e: lc.itemconfig(lw, width=e.width))

        self._build_left_panel(lframe)
        self._build_soc_display(right)
        self._build_charger_display(right)
        self._build_log(right)

    # ── LEFT PANEL ────────────────────────────────────────────────────────────
    def _build_left_panel(self, parent):
        # ── 0x100 — user sets all 4 fields ───────────────────────────────────
        f100 = tk.LabelFrame(parent, text=" TX: 0x100  VEHICLE CAPABILITIES ",
                 bg=PANEL, fg=BLUE, font=("Courier", 8, "bold"), bd=1, relief="solid")
        f100.pack(fill="x", pady=(0,3))
        self.v_min_current  = slider_row(f100, "Min Charge Current", "A",    0,  20,   5)
        self.v_min_voltage  = slider_row(f100, "Min Battery Voltage","Vdc", 200, 500, 280)
        self.v_max_voltage  = slider_row(f100, "Max Battery Voltage","Vdc", 200, 500, 400)
        self.v_charge_rate  = slider_row(f100, "Charge Rate Const.", "",    0,  255,   0)

        # ── 0x101 — capacity user-set; times calculated ───────────────────────
        f101 = tk.LabelFrame(parent, text=" TX: 0x101  BATTERY INFO ",
                 bg=PANEL, fg=BLUE, font=("Courier", 8, "bold"), bd=1, relief="solid")
        f101.pack(fill="x", pady=(0,3))
        self.v_capacity     = slider_row(f101, "Capacity",          "Ah",  10, 200,  40)
        # calculated — read-only
        self.d_max_time_10s  = value_row(f101, "Max time (x10s)",  "",   3,      ACCENT)
        self.d_max_time_1min = value_row(f101, "Max time (x1min)", "",   3,      ACCENT)
        self.d_est_time_1min = value_row(f101, "Est time (x1min)", "",   "—",    ACCENT)
        self.d_cap_kwh       = value_row(f101, "Capacity",    "x0.1kWh", 400,    ACCENT)
        # trace capacity slider to refresh
        self.v_capacity.trace_add("write", lambda *_: self._refresh_calc_display())

        # ── 0x102 — target voltage user-set; SOC live; current_req calculated ─
        f102 = tk.LabelFrame(parent, text=" TX: 0x102  VEHICLE LIVE STATUS ",
                 bg=PANEL, fg=ACCENT, font=("Courier", 8, "bold"), bd=1, relief="solid")
        f102.pack(fill="x", pady=(0,3))
        self.v_target_voltage = slider_row(f102, "Target Voltage", "Vdc", 200, 500, 400)
        self.v_soc            = slider_row(f102, "State of Charge", "%",    0, 100,  42)
        # calculated — read-only
        self.d_current_req    = value_row(f102, "Current Request",  "A",  "—",  ACCENT)
        # checkboxes
        fr = tk.Frame(f102, bg=PANEL); fr.pack(fill="x", padx=6, pady=(2,0))
        self.fault_var = tk.BooleanVar(value=False)
        tk.Checkbutton(fr, text="  FAULT FLAG", variable=self.fault_var,
                       bg=PANEL, fg=DANGER, selectcolor=BG,
                       activebackground=PANEL, font=("Courier", 8, "bold")).pack(side="left")
        sr = tk.Frame(f102, bg=PANEL); sr.pack(fill="x", padx=6, pady=(0,4))
        self.stop_request_var = tk.BooleanVar(value=False)
        tk.Checkbutton(sr, text="  MANUAL STOP REQUEST", variable=self.stop_request_var,
                       bg=PANEL, fg=WARNING, selectcolor=BG,
                       activebackground=PANEL, font=("Courier", 8, "bold")).pack(side="left")
        self.v_soc.trace_add("write", self._on_soc_change)

        # ── Sequence buttons ──────────────────────────────────────────────────
        fseq = tk.LabelFrame(parent, text=" CHARGING SEQUENCE ",
                 bg=PANEL, fg=TEXT_MUTED, font=("Courier", 8, "bold"), bd=1, relief="solid")
        fseq.pack(fill="x", pady=(0,3))
        self.phase_label = tk.Label(fseq, text="● PHASE: NOT STARTED",
                 bg=PANEL, fg=TEXT_MUTED, font=("Courier", 9, "bold"))
        self.phase_label.pack(anchor="w", padx=6, pady=(4,2))
        self.phase1_btn = tk.Button(fseq,
                 text="① PHASE 1 — Send Capabilities (0x100)  🔒",
                 bg=BORDER, fg=TEXT_MUTED, font=("Courier", 8, "bold"),
                 relief="flat", cursor="hand2", state="disabled",
                 command=self._send_phase1)
        self.phase1_btn.pack(fill="x", padx=6, pady=2)
        self.phase2_btn = tk.Button(fseq,
                 text="② PHASE 2 — Send Battery Info (0x101)  🔒",
                 bg=BORDER, fg=TEXT_MUTED, font=("Courier", 8, "bold"),
                 relief="flat", cursor="hand2", state="disabled",
                 command=self._send_phase2)
        self.phase2_btn.pack(fill="x", padx=6, pady=2)
        self.phase3_btn = tk.Button(fseq,
                 text="③ PHASE 3 — Start Charging Loop (0x102)  🔒",
                 bg=BORDER, fg=TEXT_MUTED, font=("Courier", 8, "bold"),
                 relief="flat", cursor="hand2", state="disabled",
                 command=self._toggle_phase3)
        self.phase3_btn.pack(fill="x", padx=6, pady=2)
        self.running_label = tk.Label(fseq, text="  ◉ LOOP STOPPED",
                 bg=PANEL, fg=DANGER, font=("Courier", 9, "bold"))
        self.running_label.pack(anchor="w", padx=6, pady=(0,4))

    # ── SOC display (RIGHT panel, top) ────────────────────────────────────────
    def _build_soc_display(self, parent):
        fsoc = tk.LabelFrame(parent, text=" STATE OF CHARGE ",
                 bg=PANEL, fg=TEXT_MUTED, font=("Courier", 8, "bold"), bd=1, relief="solid")
        fsoc.pack(fill="x", pady=(0,4))
        inner = tk.Frame(fsoc, bg=PANEL); inner.pack(fill="x", padx=8, pady=6)
        self.soc_number = tk.Label(inner, text="42%", bg=PANEL, fg=ACCENT,
                 font=("Courier", 28, "bold"), width=5, anchor="e")
        self.soc_number.pack(side="left")
        rs = tk.Frame(inner, bg=PANEL); rs.pack(side="left", fill="x", expand=True, padx=(16,0))
        self.soc_status = tk.Label(rs, text="IDLE", bg=PANEL, fg=TEXT_MUTED,
                 font=("Courier", 10, "bold"))
        self.soc_status.pack(anchor="w")
        self.soc_canvas = tk.Canvas(rs, height=28, bg=PANEL, highlightthickness=0)
        self.soc_canvas.pack(fill="x", pady=(6,0))
        self.soc_canvas.bind("<Configure>", lambda e: self._redraw_soc_bar())
        self.v_soc.trace_add("write", lambda *_: self._update_soc_display())

    # ── Charger display (RIGHT panel, middle) — RX 0x108 and 0x109 ───────────
    def _build_charger_display(self, parent):
        # ── 0x108 — Charger capabilities ─────────────────────────────────────
        f108 = tk.LabelFrame(parent, text=" RX: 0x108  CHARGER CAPABILITIES  (received) ",
                 bg=PANEL, fg=SUCCESS, font=("Courier", 8, "bold"), bd=1, relief="solid")
        f108.pack(fill="x", pady=(0,3))

        fields_108 = [
            ("welding_id",    "Welding Detect.", "",    0),
            ("avail_voltage", "Avail. Voltage",  "Vdc", 1),
            ("avail_current", "Avail. Current",  "A",   2),
            ("threshold_v",   "Threshold V",     "Vdc", 3),
        ]
        for key, label, unit, col in fields_108:
            self._make_charger_card(f108, key, label, unit, 0, col)

        # ── 0x109 — Charger live output ───────────────────────────────────────
        f109 = tk.LabelFrame(parent, text=" RX: 0x109  CHARGER STATUS  (received) ",
                 bg=PANEL, fg=ACCENT, font=("Courier", 8, "bold"), bd=1, relief="solid")
        f109.pack(fill="x", pady=(0,3))

        fields_109_r0 = [
            ("protocol_num",    "Protocol No.",    "",    0),
            ("present_voltage", "Present Voltage", "Vdc", 1),
            ("present_current", "Present Current", "A",   2),
            ("remaining_10s",   "Remaining x10s",  "",    3),
        ]
        for key, label, unit, col in fields_109_r0:
            self._make_charger_card(f109, key, label, unit, 0, col)

        fields_109_r1 = [
            ("remaining_1min", "Remaining min",  "",  0),
            ("status_fault",   "Status/Fault",   "",  1),
        ]
        for key, label, unit, col in fields_109_r1:
            self._make_charger_card(f109, key, label, unit, 1, col)

        # map shorter-code internal vars to the card vars
        # r_welding, r_avail_v etc. are aliases for charger_vars
        self.r_welding   = self.charger_vars["welding_id"]
        self.r_avail_v   = self.charger_vars["avail_voltage"]
        self.r_avail_i   = self.charger_vars["avail_current"]
        self.r_threshold = self.charger_vars["threshold_v"]
        self.r_incompat  = self.charger_vars["protocol_num"]
        self.r_pres_v    = self.charger_vars["present_voltage"]
        self.r_pres_i    = self.charger_vars["present_current"]
        self.r_out_enable= self.charger_vars["remaining_10s"]
        self.r_remain    = self.charger_vars["remaining_1min"]
        self.r_status    = self.charger_vars["status_fault"]

    def _make_charger_card(self, parent, key, label, unit, row, col):
        """Read-only display card for charger data — same style as reference."""
        if not hasattr(self, 'charger_vars'):
            self.charger_vars = {}
        card = tk.Frame(parent, bg=BG, bd=1, relief="solid", padx=4, pady=3)
        card.grid(row=row, column=col, padx=3, pady=3, sticky="nsew")
        parent.grid_columnconfigure(col, weight=1)

        tk.Label(card, text=label.upper(), bg=BG, fg=TEXT_MUTED,
                 font=("Courier", 8)).pack(anchor="w")

        var = tk.StringVar(value="—")
        self.charger_vars[key] = var

        tk.Label(card, textvariable=var, bg=BG, fg=SUCCESS,
                 font=("Courier", 12, "bold")).pack(anchor="w")

        tk.Label(card, text=unit, bg=BG, fg=TEXT_MUTED,
                 font=("Courier", 8)).pack(anchor="w")

    # ── Log (RIGHT panel, bottom) ─────────────────────────────────────────────
    def _build_log(self, parent):
        flog = tk.LabelFrame(parent, text=" MESSAGE LOG ",
                 bg=PANEL, fg=TEXT_MUTED, font=("Courier", 8, "bold"), bd=1, relief="solid")
        flog.pack(fill="both", expand=True)
        self.log = scrolledtext.ScrolledText(flog, bg=BG, fg=TEXT,
                 font=("Courier", 9), state="disabled", relief="flat")
        self.log.pack(fill="both", expand=True, padx=6, pady=6)
        for tag, col in [("ts", TEXT_MUTED), ("TX", ACCENT), ("RX", BLUE),
                         ("LOOP", TEXT_MUTED), ("INFO", TEXT_MUTED),
                         ("OK", SUCCESS), ("ERROR", DANGER), ("msg", TEXT)]:
            self.log.tag_config(tag, foreground=col)
        tk.Button(flog, text="Clear Log", bg=BORDER, fg=TEXT,
                  font=("Courier", 8), relief="flat", cursor="hand2",
                  command=self._clear_log).pack(anchor="e", padx=6, pady=(0,6))

    # ── SOC display helpers ───────────────────────────────────────────────────
    def _redraw_soc_bar(self):
        c = self.soc_canvas; c.delete("all")
        w = c.winfo_width(); h = c.winfo_height()
        if w < 2: return
        soc = int(self.v_soc.get())
        col = SUCCESS if soc >= 80 else (ACCENT if soc >= 30 else DANGER)
        c.create_rectangle(0, 0, w, h, fill=BORDER, outline="")
        fw = int(w * soc / 100)
        if fw > 0: c.create_rectangle(0, 0, fw, h, fill=col, outline="")
        for p in [25, 50, 75]:
            x = int(w * p / 100)
            c.create_line(x, 0, x, h, fill=BG, width=2)
        c.create_text(w//2, h//2, text=f"{soc}%",
                      fill=BG if soc > 15 else TEXT, font=("Courier", 11, "bold"))

    def _update_soc_display(self):
        soc = int(self.v_soc.get())
        self.soc_number.config(text=f"{soc}%")
        col = SUCCESS if soc >= 80 else (ACCENT if soc >= 30 else DANGER)
        self.soc_number.config(fg=col)
        if self.fault_var.get():   self.soc_status.config(text="FAULT",    fg=DANGER)
        elif self._loop_job:       self.soc_status.config(text="CHARGING", fg=SUCCESS)
        else:                      self.soc_status.config(text="IDLE",     fg=TEXT_MUTED)
        self._redraw_soc_bar()

    def _on_soc_change(self, *_):
        if int(self.v_soc.get()) >= 100:
            self.stop_request_var.set(True)
        self._update_soc_display()

    # ── Calculated values ─────────────────────────────────────────────────────
    def _refresh_calc_display(self):
        """Update read-only display fields from current slider values."""
        cap = max(1, self.v_capacity.get())
        cap_kwh = int(cap * 10)
        self.d_cap_kwh.set(str(cap_kwh))
        self.d_max_time_10s.set("3")
        self.d_max_time_1min.set("3")
        if self._avail_current > 0:
            min_i = max(1, self.v_min_current.get())
            user_i = max(min_i, self._avail_current * 0.8)
            soc = self.v_soc.get()
            energy_rem = cap * (1.0 - soc / 100.0)
            est_min = min(3, int(energy_rem / user_i * 60.0))
            self.d_est_time_1min.set(str(est_min))
            self.d_current_req.set(str(round(user_i * (1.0 - soc / 100.0), 1)))
            self._user_max_current = round(user_i, 1)
        else:
            self.d_est_time_1min.set("—")
            self.d_current_req.set("—")

    def _get_msg101_values(self):
        """Return (max_time_10s, max_time_1min, est_time_1min, cap_kwh)."""
        cap = max(1, self.v_capacity.get())
        cap_kwh = int(cap * 10)
        min_i = max(1, self.v_min_current.get())
        user_i = max(min_i, self._avail_current * 0.8)
        soc = self.v_soc.get()
        energy_rem = cap * (1.0 - soc / 100.0)
        est_min = min(3, int(energy_rem / user_i * 60.0))
        self._calc_est_minutes = est_min
        return 3, 3, est_min, cap_kwh

    # ── Phase actions ─────────────────────────────────────────────────────────
    def _send_phase1(self):
        if not self.serial_thread: self._log("ERROR", "Not connected"); return
        mi  = int(self.v_min_current.get())
        mnv = int(self.v_min_voltage.get())
        mxv = int(self.v_max_voltage.get())
        cr  = int(self.v_charge_rate.get())
        msg = f"MSG100:{mi},{mnv},{mxv},{cr}"
        self.serial_thread.send(msg)
        self._log("TX", msg)
        self.phase_label.config(text="● PHASE 1 SENT — Waiting for station…", fg=BLUE)

    def _send_phase2(self):
        if not self.serial_thread: self._log("ERROR", "Not connected"); return
        t10s, t1min, test, cap = self._get_msg101_values()
        msg = f"MSG101:{t10s},{t1min},{test},{cap}"
        self.serial_thread.send(msg)
        self._log("TX", msg)
        self.phase_label.config(text="● PHASE 2 SENT — Waiting for confirm…", fg=WARNING)
        self.phase2_btn.config(state="disabled", fg=TEXT_MUTED, bg=BORDER)

    def _toggle_phase3(self):
        if self._loop_job is None: self._start_loop()
        else: self._stop_loop()

    def _start_loop(self):
        self.phase3_btn.config(text="■ STOP CHARGING LOOP", bg=DANGER, fg=BG)
        self.running_label.config(text="  ◉ LOOP RUNNING", fg=SUCCESS)
        self.phase_label.config(text="● PHASE 3 — CHARGING ACTIVE", fg=SUCCESS)
        self._update_soc_display()
        self._loop_tick()

    def _stop_loop(self):
        if self._loop_job:
            self.root.after_cancel(self._loop_job)
            self._loop_job = None
        self.phase3_btn.config(text="③ PHASE 3 — Start Charging Loop (0x102)  🔒",
                               bg=BORDER, fg=TEXT_MUTED, state="disabled")
        self.running_label.config(text="  ◉ LOOP STOPPED", fg=DANGER)
        self._update_soc_display()

    def _loop_tick(self):
        if not self.serial_thread: self._stop_loop(); return
        soc      = self.v_soc.get()
        tgt_v    = int(self.v_target_voltage.get())
        fault    = 1 if self.fault_var.get() else 0
        stop_req = 1 if self.stop_request_var.get() else 0
        cur_req  = round(self._user_max_current * (1.0 - soc / 100.0), 1)
        cur_req  = max(0.0, cur_req)
        self.d_current_req.set(str(cur_req))
        msg = f"MSG102:{tgt_v},{cur_req},{int(soc)},{fault},1,{stop_req}"
        self.serial_thread.send(msg)
        self._log("LOOP", msg)
        if stop_req:
            self._stop_loop()
            self.phase_label.config(text="● CHARGING COMPLETE / STOPPED", fg=SUCCESS)
            return
        cap = max(1, self.v_capacity.get())
        delta = (cur_req / cap) * (100.0 / 3600.0) * 0.1 * 500   # 10x for demo speed
        self.v_soc.set(round(min(100.0, soc + delta), 1))
        self._loop_job = self.root.after(100, self._loop_tick)

    # ── Reset ─────────────────────────────────────────────────────────────────
    def _full_reset(self):
        self._stop_loop()
        self._msg109_count = 0
        self._avail_current = 0
        self._avail_voltage = 0
        self._user_max_current = 0
        self.phase1_btn.config(
            state="normal" if self.connected else "disabled",
            text="① PHASE 1 — Send Capabilities (0x100)",
            fg=TEXT if self.connected else TEXT_MUTED, bg=BORDER)
        self.phase2_btn.config(state="disabled",
            text="② PHASE 2 — Send Battery Info (0x101)  🔒", fg=TEXT_MUTED, bg=BORDER)
        self.phase3_btn.config(state="disabled",
            text="③ PHASE 3 — Start Charging Loop (0x102)  🔒", fg=TEXT_MUTED, bg=BORDER)
        self.phase_label.config(text="● RESET — Ready for new session", fg=WARNING)
        for v in [self.r_welding, self.r_avail_v, self.r_avail_i, self.r_threshold,
                  self.r_incompat, self.r_pres_v, self.r_pres_i, self.r_out_enable,
                  self.r_remain, self.r_status]:
            v.set("—")
        self.d_est_time_1min.set("—")
        self.d_current_req.set("—")
        self._log("INFO", "Simulator reset")

    # ── RX ────────────────────────────────────────────────────────────────────
    def _on_rx(self, line):
        self.root.after(0, self._log, "RX", line)
        if line.startswith("RESET"):
            self.root.after(0, self._full_reset); return

        if line.startswith("MSG108:"):
            parts = line.split(":",1)[1].split(",")
            if len(parts) == 4:
                self._avail_voltage = int(parts[1])
                self._avail_current = int(parts[2])
                self.root.after(0, self.r_welding.set,   parts[0])
                self.root.after(0, self.r_avail_v.set,   parts[1])
                self.root.after(0, self.r_avail_i.set,   parts[2])
                self.root.after(0, self.r_threshold.set, parts[3])

        elif line.startswith("MSG109:"):
            parts = line.split(":",1)[1].split(",")
            self._msg109_count += 1

            if self._msg109_count == 1:
                if len(parts) >= 7:
                    incompat = int(parts[6])
                    self.root.after(0, self.r_incompat.set, str(incompat))
                    if incompat == 1:
                        self.root.after(0, self._handle_incompatible)
                    else:
                        self.root.after(0, self._handle_compatible)

            elif self._msg109_count == 2:
                if len(parts) >= 5:
                    remaining = parts[4]
                    self.root.after(0, self.r_remain.set, remaining)
                    if int(remaining) > 0:
                        self.root.after(0, self._unlock_phase3)

            else:
                if len(parts) >= 6:
                    self.root.after(0, self.r_incompat.set,   parts[0])
                    self.root.after(0, self.r_pres_v.set,     parts[1] if len(parts)>1 else "—")
                    self.root.after(0, self.r_pres_i.set,     parts[2] if len(parts)>2 else "—")
                    self.root.after(0, self.r_out_enable.set, parts[3] if len(parts)>3 else "—")
                    self.root.after(0, self.r_status.set,     parts[4] if len(parts)>4 else "—")
                    self.root.after(0, self.r_remain.set,     parts[5] if len(parts)>5 else "—")

    def _handle_incompatible(self):
        self._log("ERROR", "INCOMPATIBLE — station cannot serve this battery. Resetting…")
        self.phase_label.config(text="● INCOMPATIBLE ✗ — Resetting", fg=DANGER)
       
        self.root.after(1500, self._full_reset)

    def _handle_compatible(self):
        self._user_max_current = round(max(self.v_min_current.get(), self._avail_current * 0.8), 1)
        self._refresh_calc_display()
        self.phase2_btn.config(state="normal",
            text="② PHASE 2 — Send Battery Info (0x101)", fg=TEXT, bg=BORDER)
        self.phase_label.config(
            text=f"● COMPATIBLE ✓  I={self._user_max_current}A — Click Phase 2", fg=SUCCESS)
        self._log("OK", f"Compatible — charging current {self._user_max_current}A, est {self._calc_est_minutes} min")

    def _unlock_phase3(self):
        self.phase3_btn.config(state="normal",
            text="③ PHASE 3 — Start Charging Loop (0x102)", fg=TEXT, bg=SUCCESS)
        self.phase_label.config(text="● PHASE 3 READY — Click to start charging", fg=SUCCESS)
        self._log("OK", "Station confirmed — Phase 3 unlocked")

    # ── Connection ────────────────────────────────────────────────────────────
    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo["values"] = ports if ports else ["No ports found"]
        if ports and self.port_var.get() not in ports: self.port_var.set(ports[0])

    def _auto_refresh_ports(self):
        self._refresh_ports(); self.root.after(3000, self._auto_refresh_ports)

    def _toggle_connection(self):
        if self.connected: self._disconnect()
        else: self._connect()

    def _connect(self):
        port = self.port_var.get()
        if not port or port == "No ports found": self._log("ERROR", "No port"); return
        baud = int(self.baud_var.get())
        self.serial_thread = SerialThread(port, baud, self._on_rx, self._on_error)
        self.serial_thread.start()
        self.connected = True
        self._log("INFO", f"Connected to {port} @ {baud}")
        self.status_badge.config(text="● CONNECTED", fg=SUCCESS)
        self.connect_btn.config(text="DISCONNECT", bg=DANGER)
        self.phase1_btn.config(state="normal",
            text="① PHASE 1 — Send Capabilities (0x100)", fg=TEXT, bg=BORDER)
        self.phase_label.config(text="● PLUG DETECTED — Click Phase 1", fg=BLUE)
        self.soc_status.config(text="PLUG DETECTED", fg=BLUE)

    def _disconnect(self):
        self._stop_loop()
        if self.serial_thread: self.serial_thread.stop(); self.serial_thread = None
        self.connected = False; self._msg109_count = 0
        self._log("INFO", "Disconnected")
        self.status_badge.config(text="● DISCONNECTED", fg=DANGER)
        self.connect_btn.config(text="CONNECT", bg=ACCENT)
        self.phase1_btn.config(state="disabled",
            text="① PHASE 1 — Send Capabilities (0x100)  🔒", fg=TEXT_MUTED, bg=BORDER)
        self.phase2_btn.config(state="disabled",
            text="② PHASE 2 — Send Battery Info (0x101)  🔒", fg=TEXT_MUTED, bg=BORDER)
        self.phase3_btn.config(state="disabled",
            text="③ PHASE 3 — Start Charging Loop (0x102)  🔒", fg=TEXT_MUTED, bg=BORDER)
        self.phase_label.config(text="● PHASE: NOT STARTED", fg=TEXT_MUTED)
        self.soc_status.config(text="IDLE", fg=TEXT_MUTED)

    def _on_error(self, msg): self.root.after(0, self._log, "ERROR", msg)

    # ── Log ───────────────────────────────────────────────────────────────────
    def _log(self, category, message):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log.config(state="normal")
        self.log.insert("end", f"[{ts}] ", "ts")
        self.log.insert("end", f"[{category}] ", category)
        self.log.insert("end", f"{message}\n", "msg")
        self.log.config(state="disabled")
        self.log.see("end")

    def _clear_log(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")


root = tk.Tk()
app = BatteryApp(root)
root.mainloop()