"""
CHAdeMO 2.0 V2G — Battery Simulator
=====================================
Phase 0 : click → TX MSG100 | rx MSG108 → auto TX MSG101 | rx MSG109 → unlock Phase 1
Phase 1 : click → TX MSG200 | rx MSG208 → unlock Phase 2
Phase 2 : click → TX MSG201 | rx MSG209 → unlock Phase 3
Phase 3 : click → boucle 100ms  TX MSG102+MSG200 / rx MSG109+MSG208
          SOC décroît — durée max 3 min simulées — arrêt propre — bouton RESET
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import threading, serial, serial.tools.list_ports
from datetime import datetime

BG         = "#0A0E1A"
PANEL      = "#111827"
BORDER     = "#1F2937"
ACCENT     = "#F59E0B"
TEXT       = "#F3F4F6"
TEXT_MUTED = "#6B7280"
SUCCESS    = "#10B981"
DANGER     = "#EF4444"
WARNING    = "#F59E0B"
BLUE       = "#3B82F6"
PURPLE     = "#8B5CF6"
CYAN       = "#06B6D4"

STATES = ["IDLE","HANDSHAKE","V2G_NEGOTIATE","V2G_SEQUENCE","DISCHARGING","STOPPING","FAULT"]
STATE_COLORS = {
    "IDLE": TEXT_MUTED, "HANDSHAKE": BLUE, "V2G_NEGOTIATE": PURPLE,
    "V2G_SEQUENCE": CYAN, "DISCHARGING": SUCCESS, "STOPPING": WARNING, "FAULT": DANGER,
}

MAX_DISCHARGE_TICKS = 3 * 60 * 10   # 3 min × 60 s × 10 ticks/s = 1800 ticks


class SerialThread(threading.Thread):
    def __init__(self, port, baud, on_rx, on_err):
        super().__init__(daemon=True)
        self.port = port; self.baud = baud
        self.on_rx = on_rx; self.on_err = on_err
        self.running = False; self.ser = None

    def run(self):
        self.running = True
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=1)
            while self.running:
                if self.ser.in_waiting:
                    line = self.ser.readline().decode("utf-8", errors="replace").strip()
                    if line:
                        self.on_rx(line)
        except serial.SerialException as e:
            self.on_err(str(e))
        finally:
            if self.ser and self.ser.is_open:
                self.ser.close()

    def send(self, msg):
        if self.ser and self.ser.is_open:
            self.ser.write((msg + "\n").encode("utf-8"))

    def stop(self):
        self.running = False


def slider_row(parent, label, unit, lo, hi, init, res=1, color=ACCENT):
    var = tk.DoubleVar(value=init)
    row = tk.Frame(parent, bg=PANEL); row.pack(fill="x", padx=6, pady=2)
    tk.Label(row, text=label, bg=PANEL, fg=TEXT_MUTED,
             font=("Courier", 8), width=22, anchor="w").pack(side="left")
    def _c(_=None): var.set(round(var.get(), 1))
    tk.Scale(row, variable=var, from_=lo, to=hi, resolution=res,
             orient="horizontal", bg=PANEL, fg=TEXT, highlightthickness=0,
             troughcolor=BORDER, activebackground=color,
             length=110, showvalue=False, command=_c).pack(side="left", padx=(4, 6))
    tk.Label(row, textvariable=var, bg=PANEL, fg=color,
             font=("Courier", 9, "bold"), width=5, anchor="e").pack(side="left")
    tk.Label(row, text=unit, bg=PANEL, fg=TEXT_MUTED,
             font=("Courier", 8), width=5, anchor="w").pack(side="left", padx=(3, 0))
    return var


def card(parent, key, label, unit, row, col, color, store):
    var = tk.StringVar(value="--"); store[key] = var
    f = tk.Frame(parent, bg=BG, bd=1, relief="solid", padx=4, pady=4)
    f.grid(row=row, column=col, padx=3, pady=3, sticky="nsew")
    parent.grid_columnconfigure(col, weight=1)
    tk.Label(f, text=label.upper(), bg=BG, fg=TEXT_MUTED, font=("Courier", 7)).pack(anchor="w")
    tk.Label(f, textvariable=var, bg=BG, fg=color, font=("Courier", 12, "bold")).pack(anchor="w")
    tk.Label(f, text=unit, bg=BG, fg=TEXT_MUTED, font=("Courier", 7)).pack(anchor="w")
    return var


class BatterySim:
    def __init__(self, root):
        self.root = root
        root.title("CHAdeMO 2.0 V2G — Battery Simulator")
        root.configure(bg=BG); root.minsize(1000, 640)

        self.ser        = None
        self.connected  = False
        self._loop_job  = None
        self._cards     = {}
        self._slabels   = {}

        # Synchronisation
        self._waiting_for_msg108 = False
        self._msg109_count       = 0
        self._discharging        = False
        self._tick_count         = 0   # compteur de ticks pour le timeout 3 min
        self._initial_soc        = 80  # SOC de départ mémorisé pour le reset

        self._build_ui()
        self._refresh_ports()
        root.after(3000, self._auto_refresh)

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        hdr = tk.Frame(self.root, bg=PANEL, height=40)
        hdr.pack(fill="x", padx=6, pady=(6, 3)); hdr.pack_propagate(False)
        tk.Label(hdr, text="V2G  CHAdeMO 2.0 — BATTERY SIMULATOR",
                 bg=PANEL, fg=ACCENT, font=("Courier", 12, "bold")).pack(side="left", padx=12)
        self.badge = tk.Label(hdr, text="● OFFLINE", bg=PANEL, fg=DANGER,
                              font=("Courier", 9, "bold"))
        self.badge.pack(side="right", padx=12)

        cb = tk.Frame(self.root, bg=BG); cb.pack(fill="x", padx=6, pady=(0, 4))
        tk.Label(cb, text="PORT", bg=BG, fg=TEXT_MUTED, font=("Courier", 9)).pack(side="left")
        self.port_var = tk.StringVar()
        self.port_cb  = ttk.Combobox(cb, textvariable=self.port_var, width=18, state="readonly")
        self.port_cb.pack(side="left", padx=(4, 12))
        tk.Label(cb, text="BAUD", bg=BG, fg=TEXT_MUTED, font=("Courier", 9)).pack(side="left")
        self.baud_var = tk.StringVar(value="115200")
        ttk.Combobox(cb, textvariable=self.baud_var, width=9, state="readonly",
                     values=["9600", "19200", "57600", "115200", "230400"]).pack(side="left", padx=(4, 12))
        self.conn_btn = tk.Button(cb, text="CONNECT", width=10, bg=ACCENT, fg=BG,
                                  font=("Courier", 9, "bold"), relief="flat", cursor="hand2",
                                  command=self._toggle_conn)
        self.conn_btn.pack(side="left")

        # Bouton RESET visible en permanence
        self.reset_btn = tk.Button(cb, text="RESET", width=8, bg=BORDER, fg=TEXT_MUTED,
                                   font=("Courier", 9, "bold"), relief="flat", cursor="hand2",
                                   command=self._do_reset, state="disabled")
        self.reset_btn.pack(side="left", padx=(12, 0))

        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self._build_left(main)
        self._build_right(main)

    def _scrollable(self, parent, side, width=None):
        outer = tk.Frame(parent, bg=BG, **({"width": width} if width else {}))
        outer.pack(side=side, fill="y" if width else "both",
                   expand=(width is None), padx=(0, 4) if side == "left" else 0)
        if width: outer.pack_propagate(False)
        cv  = tk.Canvas(outer, bg=BG, highlightthickness=0)
        sb  = ttk.Scrollbar(outer, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y"); cv.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(cv, bg=BG)
        win   = cv.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>",    lambda e: cv.itemconfig(win, width=e.width))
        cv.bind_all("<MouseWheel>", lambda e: cv.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        return inner

    def _build_left(self, parent):
        lf = self._scrollable(parent, "left", 410)
        self._build_states(lf)
        self._build_tx_charge(lf)
        self._build_tx_discharge(lf)
        self._build_buttons(lf)

    def _build_right(self, parent):
        rf = self._scrollable(parent, "left")
        self._build_rx_cards(rf)
        self._build_log(rf)

    def _build_states(self, parent):
        f = tk.LabelFrame(parent, text=" V2G STATE MACHINE ",
                          bg=PANEL, fg=TEXT_MUTED, font=("Courier", 8, "bold"), bd=1, relief="solid")
        f.pack(fill="x", pady=(0, 4))
        for s in STATES:
            row = tk.Frame(f, bg=PANEL); row.pack(fill="x", padx=4, pady=1)
            dot = tk.Label(row, text="●", bg=PANEL, fg=TEXT_MUTED, font=("Courier", 8))
            dot.pack(side="left")
            lbl = tk.Label(row, text=f"  {s}", bg=PANEL, fg=TEXT_MUTED,
                           font=("Courier", 8), anchor="w", width=22)
            lbl.pack(side="left")
            self._slabels[s] = (dot, lbl)
        self._set_state("IDLE")

    def _build_tx_charge(self, parent):
        f = tk.LabelFrame(parent, text=" TX CHARGE — MSG100 / MSG101 / MSG102 ",
                          bg=PANEL, fg=BLUE, font=("Courier", 8, "bold"), bd=1, relief="solid")
        f.pack(fill="x", pady=(0, 3))
        self.v_min_i = slider_row(f, "Min Charge Current",  "A",         0,  20,   5, color=BLUE)
        self.v_min_v = slider_row(f, "Min Battery Voltage", "Vdc",     200, 500, 280, color=BLUE)
        self.v_max_v = slider_row(f, "Max Battery Voltage", "Vdc",     200, 500, 400, color=BLUE)
        self.v_cap   = slider_row(f, "Battery Capacity",    "x0.1kWh",  10, 500, 400, color=BLUE)
        self.v_soc   = slider_row(f, "State of Charge",     "%",         0, 100,  80, color=BLUE)
        self.v_tgt_v = slider_row(f, "Target Charge V",     "Vdc",     200, 500, 400, color=BLUE)
        self.v_chg_i = slider_row(f, "Max Charge Current",  "A",         0, 100,   0, color=BLUE)
        self.v_soc.trace_add("write", lambda *_: self._update_soc_display())

    def _build_tx_discharge(self, parent):
        f = tk.LabelFrame(parent, text=" TX V2G DISCHARGE — MSG200 / MSG201 ",
                          bg=PANEL, fg=PURPLE, font=("Courier", 8, "bold"), bd=1, relief="solid")
        f.pack(fill="x", pady=(0, 3))
        self.v_dis_i  = slider_row(f, "Max Discharge I",   "A",     0, 100,  50, color=PURPLE)
        self.v_min_dv = slider_row(f, "Min Discharge V",   "Vdc", 200, 400, 300, color=PURPLE)
        self.v_min_soc= slider_row(f, "Min SOC",           "%",     0,  50,  20, color=PURPLE)
        self.v_max_soc= slider_row(f, "Max SOC",           "%",    50, 100,  90, color=PURPLE)
        self.v_seq    = slider_row(f, "Sequence Number",   "",      1,   2,   1, color=PURPLE)
        fr = tk.Frame(f, bg=PANEL); fr.pack(fill="x", padx=6, pady=(2, 0))
        self.v_stop   = tk.BooleanVar(value=False)
        tk.Checkbutton(fr, text="  NORMAL STOP REQUEST", variable=self.v_stop,
                       bg=PANEL, fg=DANGER, selectcolor=BG, activebackground=PANEL,
                       font=("Courier", 8, "bold")).pack(side="left")
        fr2 = tk.Frame(f, bg=PANEL); fr2.pack(fill="x", padx=6, pady=(0, 4))
        self.v_chg_en = tk.BooleanVar(value=True)
        tk.Checkbutton(fr2, text="  VEHICLE CHARGING ENABLED", variable=self.v_chg_en,
                       bg=PANEL, fg=SUCCESS, selectcolor=BG, activebackground=PANEL,
                       font=("Courier", 8, "bold")).pack(side="left")

    def _build_buttons(self, parent):
        f = tk.LabelFrame(parent, text=" V2G SEQUENCE ",
                          bg=PANEL, fg=TEXT_MUTED, font=("Courier", 8, "bold"), bd=1, relief="solid")
        f.pack(fill="x", pady=(0, 3))
        self.phase_lbl = tk.Label(f, text="● NOT STARTED", bg=PANEL, fg=TEXT_MUTED,
                                  font=("Courier", 9, "bold"))
        self.phase_lbl.pack(anchor="w", padx=6, pady=(4, 2))

        def mk(txt, cmd):
            b = tk.Button(f, text=txt, bg=BORDER, fg=TEXT_MUTED,
                          font=("Courier", 8, "bold"), relief="flat", cursor="hand2",
                          state="disabled", command=cmd)
            b.pack(fill="x", padx=6, pady=2)
            return b

        self.btn0 = mk("1  PHASE 0 — TX MSG100", self._ph0)
        self.btn1 = mk("2  PHASE 1 — TX MSG200", self._ph1)
        self.btn2 = mk("3  PHASE 2 — TX MSG201", self._ph2)
        self.btn3 = mk("4  PHASE 3 — Start discharge loop", self._toggle_ph3)

        # Barre de progression temps + SOC
        prog_f = tk.Frame(f, bg=PANEL); prog_f.pack(fill="x", padx=6, pady=(4, 2))
        tk.Label(prog_f, text="SOC:", bg=PANEL, fg=TEXT_MUTED, font=("Courier", 9)).pack(side="left")
        self.soc_lbl = tk.Label(prog_f, text="80%", bg=PANEL, fg=SUCCESS,
                                font=("Courier", 14, "bold"))
        self.soc_lbl.pack(side="left", padx=8)
        self.time_lbl = tk.Label(prog_f, text="", bg=PANEL, fg=TEXT_MUTED,
                                 font=("Courier", 9))
        self.time_lbl.pack(side="right", padx=6)

        # Barre de progression décharge
        self.progress_var = tk.DoubleVar(value=0)
        self.progress = ttk.Progressbar(f, variable=self.progress_var,
                                        maximum=MAX_DISCHARGE_TICKS, length=200)
        self.progress.pack(fill="x", padx=6, pady=(0, 6))

    def _build_rx_cards(self, parent):
        c = self._cards

        f108 = tk.LabelFrame(parent, text=" RX MSG108 — EVSE CHARGE CAPABILITIES ",
                             bg=PANEL, fg=SUCCESS, font=("Courier", 8, "bold"), bd=1, relief="solid")
        f108.pack(fill="x", pady=(0, 3))
        for k, l, u, col in [("weld", "Welding", "", 0), ("av", "Avail V", "Vdc", 1),
                              ("ai", "Avail I", "A", 2), ("thr", "Threshold", "Vdc", 3)]:
            card(f108, k, l, u, 0, col, SUCCESS, c)

        f109 = tk.LabelFrame(parent, text=" RX MSG109 — EVSE STATUS ",
                             bg=PANEL, fg=CYAN, font=("Courier", 8, "bold"), bd=1, relief="solid")
        f109.pack(fill="x", pady=(0, 3))
        for k, l, u, col in [("pv", "Present V", "Vdc", 0), ("pi", "Present I", "A", 1),
                              ("dc", "Dis.Compat", "", 2), ("rm", "Remaining", "min", 3)]:
            card(f109, k, l, u, 0, col, CYAN, c)
        self.evse_lbl = tk.Label(f109, text="● EVSE: STANDBY", bg=PANEL, fg=TEXT_MUTED,
                                 font=("Courier", 8, "bold"))
        self.evse_lbl.grid(row=1, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 4))

        f208 = tk.LabelFrame(parent, text=" RX MSG208 — EVSE DISCHARGE CAPABILITIES ",
                             bg=PANEL, fg=PURPLE, font=("Courier", 8, "bold"), bd=1, relief="solid")
        f208.pack(fill="x", pady=(0, 3))
        for k, l, u, col in [("di", "Discharge I", "A", 0), ("iv", "Input V", "Vdc", 1),
                              ("ii", "Input I", "A", 2), ("lt", "Lower Thr", "Vdc", 3)]:
            card(f208, k, l, u, 0, col, PURPLE, c)

        f209 = tk.LabelFrame(parent, text=" RX MSG209 — EVSE V2G SEQUENCE ",
                             bg=PANEL, fg=WARNING, font=("Courier", 8, "bold"), bd=1, relief="solid")
        f209.pack(fill="x", pady=(0, 3))
        for k, l, u, col in [("sn", "Seq Num", "", 0), ("rd", "Remaining", "min", 1)]:
            card(f209, k, l, u, 0, col, WARNING, c)
        self.seq_lbl = tk.Label(f209, text="● Waiting sequence...", bg=PANEL, fg=TEXT_MUTED,
                                font=("Courier", 8, "bold"))
        self.seq_lbl.grid(row=1, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 4))

    def _build_log(self, parent):
        fl = tk.LabelFrame(parent, text=" MESSAGE LOG ",
                           bg=PANEL, fg=TEXT_MUTED, font=("Courier", 8, "bold"), bd=1, relief="solid")
        fl.pack(fill="both", expand=True)
        self.log = scrolledtext.ScrolledText(fl, bg=BG, fg=TEXT,
                                             font=("Courier", 9), state="disabled", relief="flat")
        self.log.pack(fill="both", expand=True, padx=6, pady=6)
        for tag, col in [("ts", TEXT_MUTED), ("TX", ACCENT), ("RX", CYAN), ("LOOP", TEXT_MUTED),
                         ("INFO", TEXT_MUTED), ("OK", SUCCESS), ("ERROR", DANGER),
                         ("STATE", PURPLE), ("msg", TEXT)]:
            self.log.tag_config(tag, foreground=col)
        tk.Button(fl, text="Clear", bg=BORDER, fg=TEXT, font=("Courier", 8),
                  relief="flat", cursor="hand2",
                  command=lambda: [self.log.config(state="normal"),
                                   self.log.delete("1.0", "end"),
                                   self.log.config(state="disabled")]
                  ).pack(anchor="e", padx=6, pady=(0, 6))

    # ── State machine ─────────────────────────────────────────────────────────
    def _set_state(self, state):
        state = state.upper().strip()
        if state not in STATES: return
        for s, (dot, lbl) in self._slabels.items():
            if s == state:
                dot.config(fg=STATE_COLORS[s])
                lbl.config(fg=STATE_COLORS[s], font=("Courier", 9, "bold"))
            else:
                dot.config(fg=TEXT_MUTED)
                lbl.config(fg=TEXT_MUTED, font=("Courier", 9))
        self._log("STATE", f"-> {state}")

    def _update_soc_display(self):
        soc = int(self.v_soc.get())
        col = SUCCESS if soc >= 50 else (WARNING if soc >= 20 else DANGER)
        self.soc_lbl.config(text=f"{soc}%", fg=col)

    # ── Phases ────────────────────────────────────────────────────────────────
    def _ph0(self):
        if not self.ser: return
        self._initial_soc = self.v_soc.get()  # mémorise SOC initial pour reset
        self._msg101_pending     = f"MSG101:255,30,20,{int(self.v_cap.get())}"
        self._waiting_for_msg108 = True
        self._msg109_count       = 0
        msg = f"MSG100:{int(self.v_min_i.get())},{int(self.v_min_v.get())},{int(self.v_max_v.get())},100"
        self.ser.send(msg); self._log("TX", msg)
        self.phase_lbl.config(text="● MSG100 sent — waiting MSG108...", fg=BLUE)
        self._set_state("HANDSHAKE")
        self.btn0.config(state="disabled", bg=BORDER, fg=TEXT_MUTED)

    def _ph1(self):
        if not self.ser: return
        msg = (f"MSG200:{int(self.v_dis_i.get())},{int(self.v_min_dv.get())},"
               f"{int(self.v_min_soc.get())},{int(self.v_max_soc.get())}")
        self.ser.send(msg); self._log("TX", msg)
        self.phase_lbl.config(text="● MSG200 sent — waiting MSG208...", fg=PURPLE)
        self._set_state("V2G_NEGOTIATE")
        self.btn1.config(state="disabled", bg=BORDER, fg=TEXT_MUTED)

    def _ph2(self):
        if not self.ser: return
        seq     = int(self.v_seq.get())
        soc     = self.v_soc.get()
        min_soc = self.v_min_soc.get()
        dis_i   = max(1, self.v_dis_i.get())
        energy  = int((soc - min_soc) * self.v_cap.get() / 100)
        dis_t   = int(energy * 60 / dis_i) if energy > 0 else 0
        msg = f"MSG201:{seq},{dis_t},{energy}"
        self.ser.send(msg); self._log("TX", msg)
        self.phase_lbl.config(text="● MSG201 sent — waiting MSG209...", fg=CYAN)
        self._set_state("V2G_SEQUENCE")
        self.btn2.config(state="disabled", bg=BORDER, fg=TEXT_MUTED)

    def _toggle_ph3(self):
        if not self._discharging:
            self._start_loop()
        else:
            self._request_stop()

    def _start_loop(self):
        self._discharging  = True
        self._tick_count   = 0
        self.v_stop.set(False)
        self.v_chg_en.set(True)
        self.btn3.config(text="  STOP DISCHARGE", bg=DANGER, fg=BG)
        self.phase_lbl.config(text="● DISCHARGING — V2G active", fg=SUCCESS)
        self._set_state("DISCHARGING")
        self._tick()

    def _request_stop(self):
        """Demande un arrêt propre : envoie stop_flag=1 au prochain tick."""
        self.v_stop.set(True)
        self._log("INFO", "Stop requested")

    def _stop_loop(self, reason="STOPPED"):
        """Arrêt propre : envoie MSG102 avec stop=1 chg_en=0, puis nettoie."""
        if self._loop_job:
            self.root.after_cancel(self._loop_job)
            self._loop_job = None
        self._discharging = False

        # Envoi final stop
        if self.ser:
            soc = self.v_soc.get()
            msg102 = f"MSG102:{int(self.v_tgt_v.get())},{int(self.v_chg_i.get())},{int(soc)},0,1"
            self.ser.send(msg102)
            msg200 = f"MSG200:0,{int(self.v_min_dv.get())},{int(self.v_min_soc.get())},{int(self.v_max_soc.get())}"
            self.ser.send(msg200)
            self._log("TX", f"STOP — {msg102}")

        self.btn3.config(text="4  PHASE 3 — Start discharge loop",
                         bg=BORDER, fg=TEXT_MUTED, state="disabled")
        self.phase_lbl.config(text=f"● DISCHARGE {reason}", fg=WARNING)
        self.time_lbl.config(text="")
        self._set_state("STOPPING")
        self._log("INFO", f"Discharge {reason} — SOC final: {int(self.v_soc.get())}%")

        # Active le bouton RESET
        self.reset_btn.config(state="normal", bg=WARNING, fg=BG)

    def _tick(self):
        if not self.ser or not self._discharging:
            return

        soc     = self.v_soc.get()
        stop    = 1 if self.v_stop.get() else 0
        chg_en  = 1 if self.v_chg_en.get() else 0
        dis_i   = int(self.v_dis_i.get())
        min_soc = int(self.v_min_soc.get())

        # TX MSG102
        msg102 = (f"MSG102:{int(self.v_tgt_v.get())},{int(self.v_chg_i.get())},"
                  f"{int(soc)},{chg_en},{stop}")
        self.ser.send(msg102)
        self._log("LOOP", msg102)

        # TX MSG200
        msg200 = (f"MSG200:{dis_i},{int(self.v_min_dv.get())},"
                  f"{min_soc},{int(self.v_max_soc.get())}")
        self.ser.send(msg200)

        # Incrément compteur et affichage temps
        self._tick_count += 1
        elapsed_s  = self._tick_count * 0.1
        remaining  = max(0, MAX_DISCHARGE_TICKS - self._tick_count)
        rem_s      = remaining * 0.1
        self.time_lbl.config(
            text=f"  {int(elapsed_s//60):02d}:{int(elapsed_s%60):02d} / 03:00  "
                 f"({int(rem_s//60):02d}:{int(rem_s%60):02d} rem)")
        self.progress_var.set(self._tick_count)

        # ── Conditions d'arrêt ──────────────────────────────────────────────
        # 1. Stop demandé manuellement
        if stop or not chg_en:
            self._stop_loop("COMPLETE")
            return

        # 2. SOC minimum atteint
        if soc <= min_soc:
            self._log("INFO", f"SOC min {min_soc}% atteint")
            self._stop_loop("SOC MIN")
            return

        # 3. Timeout 3 minutes simulées
        if self._tick_count >= MAX_DISCHARGE_TICKS:
            self._log("INFO", "Durée max 3 min atteinte")
            self._stop_loop("TIMEOUT")
            return

        # ── Décroissance SOC ────────────────────────────────────────────────
        # Formule : delta = (I/C) * dt * acceleration
        # dt = 0.1s (tick 100ms), acceleration x600 pour simulation 3 min
        # SOC passe de 80% à 20% en 3 min avec dis_i=50A, cap=400
        # Verification : (50/400) * (100/3600) * 0.1 * 600 = 0.208 %/tick
        # => 60% / 0.208 = ~288 ticks = ~29s reel = 3 min simules
        delta = (dis_i / max(1, self.v_cap.get())) * (100.0 / 3600.0) * 0.1 * 600
        self.v_soc.set(round(max(0.0, soc - delta), 1))

        self._loop_job = self.root.after(100, self._tick)

    # ── RX UART ───────────────────────────────────────────────────────────────
    def _on_rx(self, line):
        self.root.after(0, self._process, line)

    def _process(self, line):
        # Ignorer les messages DBG du firmware
        if line.startswith("DBG:"):
            self._log("INFO", f"[FW] {line}")
            return

        self._log("RX", line)

        if line.startswith("RESET"):
            self._do_reset(); return

        if line.startswith("MSG108:"):
            p = line.split(":", 1)[1].split(",")
            if len(p) >= 4:
                self._cards["weld"].set(p[0]); self._cards["av"].set(p[1])
                self._cards["ai"].set(p[2]);   self._cards["thr"].set(p[3])
            if self._waiting_for_msg108:
                self._waiting_for_msg108 = False
                msg101 = getattr(self, "_msg101_pending", None)
                if msg101:
                    self._msg101_pending = None
                    self.ser.send(msg101); self._log("TX", msg101)
                    self.phase_lbl.config(text="● MSG101 sent — waiting MSG109...", fg=BLUE)

        elif line.startswith("MSG109:"):
            p = line.split(":", 1)[1].split(",")
            if len(p) >= 5:
                self._cards["pv"].set(p[1] if len(p) > 1 else "--")
                self._cards["pi"].set(p[2] if len(p) > 2 else "--")
                dc = int(p[3]) if len(p) > 3 else 0
                self._cards["dc"].set(str(dc))
                self._cards["rm"].set(p[4] if len(p) > 4 else "--")
                status = int(p[4]) if len(p) > 4 else 0
                self.evse_lbl.config(
                    text=f"● EVSE: {'ACTIVE' if (status & 1) else 'STANDBY'}",
                    fg=SUCCESS if (status & 1) else TEXT_MUTED)
            self._msg109_count += 1
            if self._msg109_count == 1:
                dc = int(p[3]) if len(p) > 3 else 0
                if dc == 1:
                    self.btn1.config(state="normal", bg=PURPLE, fg=BG,
                                     text="2  PHASE 1 — TX MSG200")
                    self.phase_lbl.config(text="● EVSE V2G compatible — click Phase 1", fg=PURPLE)
                    self._log("OK", "EVSE V2G compatible — Phase 1 unlocked")
                else:
                    self.phase_lbl.config(text="● EVSE NOT V2G compatible", fg=DANGER)
                    self._log("ERROR", "discharge_compat=0")

            # En décharge : arrêt si EVSE demande stop
            # MSG109 format : protocol,pres_v,pres_i,dis_compat,status,rem_10s,rem_1min
            # stop_control = bit5 du status byte (p[4])
            if self._discharging and len(p) > 4:
                status_byte = int(p[4])
                evse_stop_ctrl = (status_byte >> 5) & 0x01
                if evse_stop_ctrl:
                    self._log("INFO", "EVSE stop_control=1 — arrêt décharge")
                    self.v_stop.set(True)

        elif line.startswith("MSG208:"):
            p = line.split(":", 1)[1].split(",")
            if len(p) >= 4:
                self._cards["di"].set(p[0]); self._cards["iv"].set(p[1])
                self._cards["ii"].set(p[2]); self._cards["lt"].set(p[3])
            # Débloquer Phase 2 uniquement pendant la négociation V2G
            # (_msg109_count == 1 signifie qu'on est juste après le handshake)
            if self._msg109_count == 1 and self.btn2.cget("state") == "disabled":
                self.btn2.config(state="normal", bg=CYAN, fg=BG,
                                 text="3  PHASE 2 — TX MSG201")
                self.phase_lbl.config(text="● MSG208 received — click Phase 2", fg=CYAN)
                self._log("OK", "MSG208 received — Phase 2 unlocked")

        elif line.startswith("MSG209:"):
            p = line.split(":", 1)[1].split(",")
            if len(p) >= 2:
                evse_seq = int(p[0]); my_seq = int(self.v_seq.get())
                self._cards["sn"].set(p[0]); self._cards["rd"].set(p[1])
                if evse_seq == my_seq:
                    self.seq_lbl.config(text=f"● SEQ MATCH: {evse_seq:#04x}", fg=SUCCESS)
                    self.btn3.config(state="normal", bg=SUCCESS, fg=BG,
                                     text="4  PHASE 3 — Start discharge loop")
                    self.phase_lbl.config(text="● Sequence agreed — click Phase 3", fg=SUCCESS)
                    self._log("OK", f"Sequence {evse_seq:#04x} agreed — Phase 3 unlocked")
                else:
                    self.seq_lbl.config(
                        text=f"● SEQ MISMATCH: got {evse_seq:#04x} exp {my_seq:#04x}", fg=DANGER)
                    self._log("ERROR", f"Sequence mismatch")

        elif line.startswith("STATE:"):
            s = line.split(":", 1)[1].strip()
            self._log("STATE", f"EVSE -> {s}")
            if s in ("IDLE", "STOPPING") and self._discharging:
                self._stop_loop("EVSE STOP")

    # ── Connexion ─────────────────────────────────────────────────────────────
    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_cb["values"] = ports if ports else ["No ports found"]
        if ports and self.port_var.get() not in ports:
            self.port_var.set(ports[0])

    def _auto_refresh(self):
        self._refresh_ports(); self.root.after(3000, self._auto_refresh)

    def _toggle_conn(self):
        if self.connected: self._disconnect()
        else: self._connect()

    def _connect(self):
        port = self.port_var.get()
        if not port or port == "No ports found":
            self._log("ERROR", "No port"); return
        baud = int(self.baud_var.get())
        self.ser = SerialThread(port, baud, self._on_rx,
                                lambda e: self.root.after(0, self._log, "ERROR", e))
        self.ser.start(); self.connected = True
        self._log("INFO", f"Connected {port} @ {baud}")
        self.badge.config(text="● ONLINE", fg=SUCCESS)
        self.conn_btn.config(text="DISCONNECT", bg=DANGER)
        self.btn0.config(state="normal", bg=BLUE, fg=BG, text="1  PHASE 0 — TX MSG100")
        self.reset_btn.config(state="normal", bg=WARNING, fg=BG)
        self.phase_lbl.config(text="● Connected — click Phase 0", fg=BLUE)
        self._waiting_for_msg108 = False; self._msg109_count = 0
        self._discharging = False; self._tick_count = 0

    def _disconnect(self):
        if self._loop_job: self.root.after_cancel(self._loop_job); self._loop_job = None
        if self.ser: self.ser.stop(); self.ser = None
        self.connected = False; self._discharging = False
        self.badge.config(text="● OFFLINE", fg=DANGER)
        self.conn_btn.config(text="CONNECT", bg=ACCENT)
        for b in [self.btn0, self.btn1, self.btn2, self.btn3]:
            b.config(state="disabled", bg=BORDER, fg=TEXT_MUTED)
        self.reset_btn.config(state="disabled", bg=BORDER, fg=TEXT_MUTED)
        self._set_state("IDLE"); self._log("INFO", "Disconnected")

    def _do_reset(self):
        """Remet le système dans ses conditions initiales."""
        # 1. Stoppe la boucle si active
        if self._loop_job:
            self.root.after_cancel(self._loop_job); self._loop_job = None
        self._discharging = False

        # 2. Remet les flags
        self._waiting_for_msg108 = False
        self._msg109_count       = 0
        self._tick_count         = 0
        self.v_stop.set(False)
        self.v_chg_en.set(True)

        # 3. Remet le SOC à sa valeur initiale
        self.v_soc.set(self._initial_soc)
        self.progress_var.set(0)
        self.time_lbl.config(text="")

        # 4. Remet les boutons
        for b in [self.btn0, self.btn1, self.btn2, self.btn3]:
            b.config(state="disabled", bg=BORDER, fg=TEXT_MUTED)
        if self.connected:
            self.btn0.config(state="normal", bg=BLUE, fg=BG, text="1  PHASE 0 — TX MSG100")
        self.reset_btn.config(bg=BORDER, fg=TEXT_MUTED,
                              state="normal" if self.connected else "disabled")

        # 5. Remet les cartes RX à --
        for v in self._cards.values():
            v.set("--")
        self.evse_lbl.config(text="● EVSE: STANDBY", fg=TEXT_MUTED)
        self.seq_lbl.config(text="● Waiting sequence...", fg=TEXT_MUTED)

        self.phase_lbl.config(text="● RESET — click Phase 0", fg=WARNING)
        self._set_state("IDLE")
        self._log("INFO", "=== RESET — système réinitialisé ===")

    # ── Log ───────────────────────────────────────────────────────────────────
    def _log(self, cat, msg):
        if not hasattr(self, "log") or self.log is None: return
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log.config(state="normal")
        self.log.insert("end", f"[{ts}] ", "ts")
        self.log.insert("end", f"[{cat}] ", cat)
        self.log.insert("end", f"{msg}\n", "msg")
        self.log.config(state="disabled"); self.log.see("end")


root = tk.Tk()
app  = BatterySim(root)
root.mainloop()
