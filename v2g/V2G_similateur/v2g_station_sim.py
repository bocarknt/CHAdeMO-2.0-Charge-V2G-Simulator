"""
CHAdeMO 2.0 V2G — Station Simulator (EVSE)
============================================
Séquence synchronisée :
  rx MSG100  → débloque Phase 0
  [click]    → TX MSG108  [auto TX MSG109 après rx MSG101]
  rx MSG200  → débloque Phase 1
  [click]    → TX MSG208
  rx MSG201  → débloque Phase 2
  [click]    → TX MSG209
  rx MSG102  → débloque Phase 3
  [click]    → active _discharging : chaque MSG102 reçu → TX MSG109+MSG208
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import threading, serial, serial.tools.list_ports
from datetime import datetime

BG         = "#050B18"
PANEL      = "#0D1526"
BORDER     = "#1A2540"
ACCENT     = "#06B6D4"
TEXT       = "#E2E8F0"
TEXT_MUTED = "#4A5568"
SUCCESS    = "#10B981"
DANGER     = "#EF4444"
WARNING    = "#F59E0B"
BLUE       = "#3B82F6"
PURPLE     = "#8B5CF6"

STATES = ["IDLE","PLUG_DETECTED","HANDSHAKE","V2G_NEGOTIATE","V2G_SEQUENCE","DISCHARGING","STOPPING","FAULT"]
STATE_COLORS = {
    "IDLE": TEXT_MUTED, "PLUG_DETECTED": BLUE, "HANDSHAKE": ACCENT,
    "V2G_NEGOTIATE": PURPLE, "V2G_SEQUENCE": WARNING,
    "DISCHARGING": SUCCESS, "STOPPING": WARNING, "FAULT": DANGER,
}

class SerialThread(threading.Thread):
    def __init__(self, port, baud, on_rx, on_err):
        super().__init__(daemon=True)
        self.port=port; self.baud=baud; self.on_rx=on_rx; self.on_err=on_err
        self.running=False; self.ser=None

    def run(self):
        self.running = True
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=1)
            while self.running:
                if self.ser.in_waiting:
                    line = self.ser.readline().decode("utf-8", errors="replace").strip()
                    if line: self.on_rx(line)
        except serial.SerialException as e:
            self.on_err(str(e))
        finally:
            if self.ser and self.ser.is_open: self.ser.close()

    def send(self, msg):
        if self.ser and self.ser.is_open:
            self.ser.write((msg + "\n").encode("utf-8"))

    def stop(self): self.running = False


def slider_row(parent, label, unit, lo, hi, init, res=1, color=ACCENT):
    var = tk.DoubleVar(value=init)
    row = tk.Frame(parent, bg=PANEL); row.pack(fill="x", padx=6, pady=2)
    tk.Label(row, text=label, bg=PANEL, fg=TEXT_MUTED,
             font=("Courier",8), width=22, anchor="w").pack(side="left")
    def _c(_=None): var.set(round(var.get(),1))
    tk.Scale(row, variable=var, from_=lo, to=hi, resolution=res,
             orient="horizontal", bg=PANEL, fg=TEXT, highlightthickness=0,
             troughcolor=BORDER, activebackground=color,
             length=110, showvalue=False, command=_c).pack(side="left", padx=(4,6))
    tk.Label(row, textvariable=var, bg=PANEL, fg=color,
             font=("Courier",9,"bold"), width=5, anchor="e").pack(side="left")
    tk.Label(row, text=unit, bg=PANEL, fg=TEXT_MUTED,
             font=("Courier",8), width=5, anchor="w").pack(side="left", padx=(3,0))
    return var

def card(parent, key, label, unit, row, col, color, store):
    var = tk.StringVar(value="--"); store[key] = var
    f = tk.Frame(parent, bg=BG, bd=1, relief="solid", padx=4, pady=4)
    f.grid(row=row, column=col, padx=3, pady=3, sticky="nsew")
    parent.grid_columnconfigure(col, weight=1)
    tk.Label(f, text=label.upper(), bg=BG, fg=TEXT_MUTED, font=("Courier",7)).pack(anchor="w")
    tk.Label(f, textvariable=var, bg=BG, fg=color, font=("Courier",12,"bold")).pack(anchor="w")
    tk.Label(f, text=unit, bg=BG, fg=TEXT_MUTED, font=("Courier",7)).pack(anchor="w")
    return var


class StationSim:
    def __init__(self, root):
        self.root = root
        root.title("CHAdeMO 2.0 V2G — Station Simulator (EVSE)")
        root.configure(bg=BG); root.minsize(1000, 640)

        self.ser       = None
        self.connected = False
        self._loop_job = None
        self._cards    = {}
        self._slabels  = {}
        self._state_name = "IDLE"

        # Flags de synchronisation
        self._waiting_for_msg101 = False  # True après TX MSG108
        self._sequence_step = 0  # 0=idle 1=ph0done 2=ph1done 3=ph2done 4=discharging
        self._msg109_pending     = None   # MSG109 à envoyer après rx MSG101
        self._discharging        = False  # True quand Phase 3 active

        # Données véhicule reçues
        self._veh_max_v    = 0
        self._veh_min_v    = 0
        self._veh_min_i    = 0
        self._veh_soc      = 0
        self._veh_max_di   = 0
        self._veh_min_dv   = 0
        self._veh_min_soc  = 0
        self._veh_seq      = 0
        self._veh_dis_t    = 0
        self._veh_energy   = 0
        self._veh_chg_en   = 0
        self._veh_stop     = 0
        self._remaining_dis = 0

        self._build_ui()
        self._refresh_ports()
        root.after(3000, self._auto_refresh)

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        hdr = tk.Frame(self.root, bg=PANEL, height=40)
        hdr.pack(fill="x", padx=6, pady=(6,3)); hdr.pack_propagate(False)
        tk.Label(hdr, text="V2G  CHAdeMO 2.0 — STATION SIMULATOR (EVSE)",
                 bg=PANEL, fg=ACCENT, font=("Courier",12,"bold")).pack(side="left", padx=12)
        self.badge = tk.Label(hdr, text="● OFFLINE", bg=PANEL, fg=DANGER, font=("Courier",9,"bold"))
        self.badge.pack(side="right", padx=12)

        cb = tk.Frame(self.root, bg=BG); cb.pack(fill="x", padx=6, pady=(0,4))
        tk.Label(cb, text="PORT", bg=BG, fg=TEXT_MUTED, font=("Courier",9)).pack(side="left")
        self.port_var = tk.StringVar()
        self.port_cb  = ttk.Combobox(cb, textvariable=self.port_var, width=18, state="readonly")
        self.port_cb.pack(side="left", padx=(4,12))
        tk.Label(cb, text="BAUD", bg=BG, fg=TEXT_MUTED, font=("Courier",9)).pack(side="left")
        self.baud_var = tk.StringVar(value="115200")
        ttk.Combobox(cb, textvariable=self.baud_var, width=9, state="readonly",
                     values=["9600","19200","57600","115200","230400"]).pack(side="left", padx=(4,12))
        self.conn_btn = tk.Button(cb, text="CONNECT", width=10, bg=ACCENT, fg=BG,
                 font=("Courier",9,"bold"), relief="flat", cursor="hand2",
                 command=self._toggle_conn)
        self.conn_btn.pack(side="left")

        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True, padx=6, pady=(0,6))
        self._build_left(main)
        self._build_right(main)

    def _scrollable(self, parent, side, width=None):
        outer = tk.Frame(parent, bg=BG, **({"width": width} if width else {}))
        outer.pack(side=side, fill="y" if width else "both",
                   expand=(width is None), padx=(0,4) if side=="left" else 0)
        if width: outer.pack_propagate(False)
        cv = tk.Canvas(outer, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y"); cv.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(cv, bg=BG)
        win   = cv.create_window((0,0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>",    lambda e: cv.itemconfig(win, width=e.width))
        cv.bind_all("<MouseWheel>", lambda e: cv.yview_scroll(int(-1*(e.delta/120)), "units"))
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
        f = tk.LabelFrame(parent, text=" EVSE V2G STATE MACHINE ",
                bg=PANEL, fg=TEXT_MUTED, font=("Courier",8,"bold"), bd=1, relief="solid")
        f.pack(fill="x", pady=(0,4))
        for s in STATES:
            row = tk.Frame(f, bg=PANEL); row.pack(fill="x", padx=4, pady=1)
            dot = tk.Label(row, text="●", bg=PANEL, fg=TEXT_MUTED, font=("Courier",8))
            dot.pack(side="left")
            lbl = tk.Label(row, text=f"  {s}", bg=PANEL, fg=TEXT_MUTED,
                    font=("Courier",8), anchor="w", width=22)
            lbl.pack(side="left")
            self._slabels[s] = (dot, lbl)
        self._set_state("IDLE")

    def _build_tx_charge(self, parent):
        f = tk.LabelFrame(parent, text=" TX CHARGE — MSG108 / MSG109 ",
                bg=PANEL, fg=BLUE, font=("Courier",8,"bold"), bd=1, relief="solid")
        f.pack(fill="x", pady=(0,3))
        self.v_weld   = slider_row(f, "Welding Detection",  "",      0,   1,   1, color=BLUE)
        self.v_av     = slider_row(f, "Avail. Voltage",     "Vdc", 200, 500, 400, color=BLUE)
        self.v_ai     = slider_row(f, "Avail. Current",     "A",     0, 120,  80, color=BLUE)
        self.v_thr    = slider_row(f, "Threshold Voltage",  "Vdc", 200, 490, 380, color=BLUE)
        fr = tk.Frame(f, bg=PANEL); fr.pack(fill="x", padx=6, pady=(2,4))
        self.v_dis_ok = tk.BooleanVar(value=True)
        tk.Checkbutton(fr, text="  DISCHARGE COMPATIBLE (V2G)", variable=self.v_dis_ok,
                       bg=PANEL, fg=SUCCESS, selectcolor=BG,
                       activebackground=PANEL, font=("Courier",8,"bold")).pack(side="left")

    def _build_tx_discharge(self, parent):
        f = tk.LabelFrame(parent, text=" TX V2G DISCHARGE — MSG208 / MSG209 ",
                bg=PANEL, fg=PURPLE, font=("Courier",8,"bold"), bd=1, relief="solid")
        f.pack(fill="x", pady=(0,3))
        self.v_iv  = slider_row(f, "Input Voltage",      "Vdc", 200, 500, 400, color=PURPLE)
        self.v_ii  = slider_row(f, "Input Current Max",  "A",     0, 100,  50, color=PURPLE)
        self.v_lt  = slider_row(f, "Lower Threshold V",  "Vdc", 200, 400, 280, color=PURPLE)
        self.v_seq = slider_row(f, "Sequence Number",    "",      1,   2,   1, color=PURPLE)
        fr = tk.Frame(f, bg=PANEL); fr.pack(fill="x", padx=6, pady=(2,4))
        self.v_evse_stop = tk.BooleanVar(value=False)
        tk.Checkbutton(fr, text="  EVSE STOP CONTROL", variable=self.v_evse_stop,
                       bg=PANEL, fg=DANGER, selectcolor=BG,
                       activebackground=PANEL, font=("Courier",8,"bold")).pack(side="left")

    def _build_buttons(self, parent):
        f = tk.LabelFrame(parent, text=" V2G SEQUENCE ",
                bg=PANEL, fg=TEXT_MUTED, font=("Courier",8,"bold"), bd=1, relief="solid")
        f.pack(fill="x", pady=(0,3))
        self.phase_lbl = tk.Label(f, text="● Waiting for vehicle...",
                bg=PANEL, fg=TEXT_MUTED, font=("Courier",9,"bold"))
        self.phase_lbl.pack(anchor="w", padx=6, pady=(4,2))

        def mk(txt, cmd):
            b = tk.Button(f, text=txt, bg=BORDER, fg=TEXT_MUTED,
                    font=("Courier",8,"bold"), relief="flat", cursor="hand2",
                    state="disabled", command=cmd)
            b.pack(fill="x", padx=6, pady=2); return b

        self.btn0 = mk("1  PHASE 0 — TX MSG108  [auto TX MSG109 on MSG101]", self._ph0)
        self.btn1 = mk("2  PHASE 1 — TX MSG208  [auto unlock on MSG201]",    self._ph1)
        self.btn2 = mk("3  PHASE 2 — TX MSG209  [auto unlock on MSG102]",    self._ph2)
        self.btn3 = mk("4  PHASE 3 — Start discharge loop",                  self._toggle_ph3)
        self.loop_lbl = tk.Label(f, text="  LOOP STOPPED",
                bg=PANEL, fg=DANGER, font=("Courier",9,"bold"))
        self.loop_lbl.pack(anchor="w", padx=6, pady=(0,4))

    def _build_rx_cards(self, parent):
        c = self._cards

        f100 = tk.LabelFrame(parent, text=" RX MSG100 — VEHICLE CHARGE CAPABILITIES ",
                bg=PANEL, fg=BLUE, font=("Courier",8,"bold"), bd=1, relief="solid")
        f100.pack(fill="x", pady=(0,3))
        for k,l,u,col in [("min_i","Min Charge I","A",0),("min_v","Min V","Vdc",1),
                           ("max_v","Max V","Vdc",2),("soc0","SOC","%",3)]:
            card(f100, k, l, u, 0, col, BLUE, c)
        self.compat_lbl = tk.Label(f100, text="● Waiting vehicle...",
                bg=PANEL, fg=TEXT_MUTED, font=("Courier",8,"bold"))
        self.compat_lbl.grid(row=1, column=0, columnspan=4, sticky="w", padx=6, pady=(2,4))

        f101 = tk.LabelFrame(parent, text=" RX MSG101 — BATTERY INFO ",
                bg=PANEL, fg=TEXT_MUTED, font=("Courier",8,"bold"), bd=1, relief="solid")
        f101.pack(fill="x", pady=(0,3))
        for k,l,u,col in [("mt10","Max T x10s","",0),("mt1m","Max T min","min",1),
                           ("est","Est. Time","min",2),("cap","Capacity","x0.1kWh",3)]:
            card(f101, k, l, u, 0, col, TEXT_MUTED, c)

        f102 = tk.LabelFrame(parent, text=" RX MSG102 — VEHICLE CHARGE CONTROL ",
                bg=PANEL, fg=ACCENT, font=("Courier",8,"bold"), bd=1, relief="solid")
        f102.pack(fill="x", pady=(0,3))
        for k,l,u,col in [("tgt_v","Target V","Vdc",0),("max_ci","Max Chg I","A",1),
                           ("soc1","SOC","%",2)]:
            card(f102, k, l, u, 0, col, ACCENT, c)
        ind = tk.Frame(f102, bg=PANEL)
        ind.grid(row=1, column=0, columnspan=3, sticky="ew", padx=3, pady=(0,3))
        self.chg_en_lbl = tk.Label(ind, text="  CHG_EN:NO  ",
                bg=PANEL, fg=TEXT_MUTED, font=("Courier",8,"bold"))
        self.chg_en_lbl.pack(side="left", padx=6)
        self.stop_lbl = tk.Label(ind, text="  STOP:NO  ",
                bg=PANEL, fg=SUCCESS, font=("Courier",8,"bold"))
        self.stop_lbl.pack(side="left")

        f200 = tk.LabelFrame(parent, text=" RX MSG200 — VEHICLE DISCHARGE CAPABILITIES ",
                bg=PANEL, fg=PURPLE, font=("Courier",8,"bold"), bd=1, relief="solid")
        f200.pack(fill="x", pady=(0,3))
        for k,l,u,col in [("max_di","Max Dis I","A",0),("min_dv","Min Dis V","Vdc",1),
                           ("min_soc","Min SOC","%",2),("max_soc","Max SOC","%",3)]:
            card(f200, k, l, u, 0, col, PURPLE, c)

        f201 = tk.LabelFrame(parent, text=" RX MSG201 — VEHICLE V2G SEQUENCE ",
                bg=PANEL, fg=WARNING, font=("Courier",8,"bold"), bd=1, relief="solid")
        f201.pack(fill="x", pady=(0,3))
        for k,l,u,col in [("v_seq","Seq Num","",0),("dis_t","Dis Time","min",1),
                           ("energy","Energy","x0.1kWh",2)]:
            card(f201, k, l, u, 0, col, WARNING, c)
        self.seq_lbl = tk.Label(f201, text="● Waiting sequence...",
                bg=PANEL, fg=TEXT_MUTED, font=("Courier",8,"bold"))
        self.seq_lbl.grid(row=1, column=0, columnspan=4, sticky="w", padx=6, pady=(2,4))

    def _build_log(self, parent):
        fl = tk.LabelFrame(parent, text=" MESSAGE LOG ",
                bg=PANEL, fg=TEXT_MUTED, font=("Courier",8,"bold"), bd=1, relief="solid")
        fl.pack(fill="both", expand=True)
        self.log = scrolledtext.ScrolledText(fl, bg=BG, fg=TEXT,
                font=("Courier",9), state="disabled", relief="flat")
        self.log.pack(fill="both", expand=True, padx=6, pady=6)
        for tag, col in [("ts",TEXT_MUTED),("TX",ACCENT),("RX",BLUE),("LOOP",TEXT_MUTED),
                         ("INFO",TEXT_MUTED),("OK",SUCCESS),("ERROR",DANGER),
                         ("STATE",PURPLE),("msg",TEXT)]:
            self.log.tag_config(tag, foreground=col)
        tk.Button(fl, text="Clear", bg=BORDER, fg=TEXT, font=("Courier",8),
                  relief="flat", cursor="hand2",
                  command=lambda: [self.log.config(state="normal"),
                                   self.log.delete("1.0","end"),
                                   self.log.config(state="disabled")]
                  ).pack(anchor="e", padx=6, pady=(0,6))

    # ── State machine ─────────────────────────────────────────────────────────
    def _set_state(self, state):
        state = state.upper().strip()
        if state not in STATES: return
        self._state_name = state
        for s,(dot,lbl) in self._slabels.items():
            if s == state:
                dot.config(fg=STATE_COLORS[s])
                lbl.config(fg=STATE_COLORS[s], font=("Courier",9,"bold"))
            else:
                dot.config(fg=TEXT_MUTED)
                lbl.config(fg=TEXT_MUTED, font=("Courier",9))
        self._log("STATE", f"-> {state}")

    # ── Phases ────────────────────────────────────────────────────────────────
    def _ph0(self):
        """TX MSG108. MSG109 envoyé automatiquement après rx MSG101."""
        if not self.ser: return
        dis_ok = 1 if self.v_dis_ok.get() else 0
        self._msg109_pending     = f"MSG109:0,0,1,{dis_ok},0"
        self._waiting_for_msg101 = True
        msg = (f"MSG108:{int(self.v_weld.get())},{int(self.v_av.get())},"
               f"{int(self.v_ai.get())},{int(self.v_thr.get())}")
        self.ser.send(msg); self._log("TX", msg)
        self._sequence_step = 1
        self._set_state("HANDSHAKE")
        self.phase_lbl.config(text="● MSG108 sent — waiting MSG101...", fg=ACCENT)
        self.btn0.config(state="disabled", bg=BORDER, fg=TEXT_MUTED)

    def _ph1(self):
        """TX MSG208. Phase 2 débloquée après rx MSG201."""
        if not self.ser: return
        ii_offset = 0xFF - int(self.v_ii.get())
        msg = (f"MSG208:255,{int(self.v_iv.get())},"
               f"{ii_offset},{int(self.v_lt.get())}")
        self.ser.send(msg); self._log("TX", msg)
        self._sequence_step = 2
        self.phase_lbl.config(text="● MSG208 sent — waiting MSG201...", fg=PURPLE)
        self._set_state("V2G_NEGOTIATE")
        self.btn1.config(state="disabled", bg=BORDER, fg=TEXT_MUTED)

    def _ph2(self):
        """TX MSG209. Phase 3 débloquée après rx MSG102."""
        if not self.ser: return
        seq = int(self.v_seq.get())
        msg = f"MSG209:{seq},{self._veh_dis_t}"
        self.ser.send(msg); self._log("TX", msg)
        self._sequence_step = 3
        self._remaining_dis = self._veh_dis_t
        self.phase_lbl.config(text="● MSG209 sent — waiting MSG102...", fg=WARNING)
        self._set_state("V2G_SEQUENCE")
        self.btn2.config(state="disabled", bg=BORDER, fg=TEXT_MUTED)

    def _toggle_ph3(self):
        if not self._discharging: self._start_loop()
        else: self._stop_loop()

    def _start_loop(self):
        self._discharging = True
        self._sequence_step = 4
        self.btn3.config(text="  STOP DISCHARGE LOOP", bg=DANGER, fg=BG)
        self.loop_lbl.config(text="  LOOP RUNNING", fg=SUCCESS)
        self.phase_lbl.config(text="● DISCHARGING — V2G active", fg=SUCCESS)
        self._set_state("DISCHARGING")

    def _stop_loop(self):
        self._discharging = False
        if self._loop_job: self.root.after_cancel(self._loop_job); self._loop_job = None
        self.btn3.config(text="4  PHASE 3 — Start discharge loop",
                         bg=BORDER, fg=TEXT_MUTED, state="disabled")
        self.loop_lbl.config(text="  LOOP STOPPED", fg=DANGER)
        self._set_state("STOPPING")

    def _send_discharge_response(self):
        """Envoie MSG109 + MSG208 en réponse à chaque MSG102 reçu en Phase 3."""
        if not self.ser: return
        evse_stop = 1 if self.v_evse_stop.get() else 0
        dis_ok    = 1 if self.v_dis_ok.get() else 0
        rem = max(0, int(self._remaining_dis))

        msg109 = (f"MSG109:{int(self.v_av.get())},{self._veh_max_di},"
                  f"{evse_stop},{dis_ok},{rem}")
        self.ser.send(msg109); self._log("LOOP", msg109)

        dis_i_off = max(0, 0xFF - self._veh_max_di)
        ii_off    = 0xFF - int(self.v_ii.get())
        msg208 = (f"MSG208:{dis_i_off},{int(self.v_iv.get())},"
                  f"{ii_off},{int(self.v_lt.get())}")
        self.ser.send(msg208)

        if self._remaining_dis > 0:
            self._remaining_dis -= 0.1 / 60

        if evse_stop:
            self._log("INFO", "EVSE stop — ending discharge")
            self._stop_loop()
            self.phase_lbl.config(text="● DISCHARGE STOPPED", fg=WARNING)

    # ── Réception UART ────────────────────────────────────────────────────────
    def _on_rx(self, line):
        self.root.after(0, self._process, line)

    def _process(self, line):
        self._log("RX", line)

        if line.startswith("RESET"):
            self._reset(); return

        # MSG100 → affiche, compat check, débloque Phase 0
        if line.startswith("MSG100:"):
            p = line.split(":",1)[1].split(",")
            if len(p) >= 4:
                self._veh_min_i = int(p[0]); self._veh_min_v = int(p[1])
                self._veh_max_v = int(p[2]); self._veh_soc   = int(p[3])
                self._cards["min_i"].set(p[0]); self._cards["min_v"].set(p[1])
                self._cards["max_v"].set(p[2]); self._cards["soc0"].set(p[3])
            av = int(self.v_av.get())
            if av >= self._veh_max_v:
                self.compat_lbl.config(
                    text=f"● COMPATIBLE: avail={av}V >= max={self._veh_max_v}V", fg=SUCCESS)
                self._log("OK", f"Vehicle compatible — max_v={self._veh_max_v}V")
            else:
                self.compat_lbl.config(
                    text=f"● INCOMPATIBLE: avail={av}V < max={self._veh_max_v}V", fg=DANGER)
                self._log("ERROR", "Voltage incompatibility")
            if self.btn0.cget("state") == "disabled":
                self.btn0.config(state="normal", bg=BLUE, fg=BG,
                    text="1  PHASE 0 — TX MSG108  [auto TX MSG109 on MSG101]")
                self.phase_lbl.config(text="● MSG100 received — click Phase 0", fg=BLUE)
                self._set_state("PLUG_DETECTED")

        # MSG101 → affiche, envoie MSG109 automatiquement
        elif line.startswith("MSG101:"):
            p = line.split(":",1)[1].split(",")
            if len(p) >= 4:
                self._cards["mt10"].set(p[0]); self._cards["mt1m"].set(p[1])
                self._cards["est"].set(p[2]);  self._cards["cap"].set(p[3])
            if self._waiting_for_msg101 and self._msg109_pending:
                self._waiting_for_msg101 = False
                msg109 = self._msg109_pending
                self._msg109_pending = None
                self.ser.send(msg109); self._log("TX", msg109)
                self.phase_lbl.config(text="● MSG109 sent — waiting MSG200...", fg=ACCENT)
                self._log("OK", "Handshake complete — waiting MSG200 for V2G")

        # MSG102 → affiche
        # Si _discharging : répond MSG109+MSG208
        # Sinon si Phase 3 désactivée : débloque Phase 3
        elif line.startswith("MSG102:"):
            self._log("INFO", f"MSG102 handler — discharging={self._discharging} btn3={self.btn3.cget('state')} state={self._state_name}")
            p = line.split(":",1)[1].split(",")
            if len(p) >= 5:
                self._veh_chg_en = int(p[3]); self._veh_stop = int(p[4])
                self._cards["tgt_v"].set(p[0]); self._cards["max_ci"].set(p[1])
                self._cards["soc1"].set(p[2])
                self.chg_en_lbl.config(
                    text=f"  CHG_EN:{'YES' if self._veh_chg_en else 'NO'}  ",
                    fg=SUCCESS if self._veh_chg_en else TEXT_MUTED)
                self.stop_lbl.config(
                    text=f"  STOP:{'YES' if self._veh_stop else 'NO'}  ",
                    fg=DANGER if self._veh_stop else SUCCESS)

            if self._discharging:
                # Phase 3 active : répondre immédiatement
                self._send_discharge_response()
                # Vérifier arrêt véhicule
                if self._veh_stop or not self._veh_chg_en:
                    self._log("INFO", "Vehicle stop request — ending discharge")
                    self._stop_loop()
                    self.phase_lbl.config(text="● DISCHARGE COMPLETE", fg=SUCCESS)
            elif self.btn3.cget("state") == "disabled":
                # Premier MSG102 reçu → débloque Phase 3
                self.btn3.config(state="normal", bg=SUCCESS, fg=BG,
                    text="4  PHASE 3 — Start discharge loop")
                self.phase_lbl.config(text="● MSG102 received — click Phase 3", fg=SUCCESS)
                self._log("OK", "MSG102 received — Phase 3 unlocked")

        # MSG200 → affiche, débloque Phase 1
        elif line.startswith("MSG200:"):
            p = line.split(":",1)[1].split(",")
            if len(p) >= 4:
                self._veh_max_di  = int(p[0]); self._veh_min_dv = int(p[1])
                self._veh_min_soc = int(p[2])
                self._cards["max_di"].set(p[0]); self._cards["min_dv"].set(p[1])
                self._cards["min_soc"].set(p[2]); self._cards["max_soc"].set(p[3])
            # Ne débloquer Phase 1 que si on est encore en step 1 (après Phase 0)
            if self._sequence_step == 1 and self.btn1.cget("state") == "disabled":
                self.btn1.config(state="normal", bg=PURPLE, fg=BG,
                    text="2  PHASE 1 — TX MSG208  [auto unlock on MSG201]")
                self.phase_lbl.config(text="● MSG200 received — click Phase 1", fg=PURPLE)
                self._log("OK", f"MSG200: max_dis={self._veh_max_di}A min_v={self._veh_min_dv}V")
                self._set_state("V2G_NEGOTIATE")

        # MSG201 → affiche, vérifie séquence, débloque Phase 2
        elif line.startswith("MSG201:"):
            p = line.split(":",1)[1].split(",")
            if len(p) >= 3:
                self._veh_seq    = int(p[0]); self._veh_dis_t  = int(p[1])
                self._veh_energy = int(p[2])
                self._cards["v_seq"].set(p[0]); self._cards["dis_t"].set(p[1])
                self._cards["energy"].set(p[2])
            my_seq = int(self.v_seq.get())
            if self._veh_seq == my_seq:
                self.seq_lbl.config(
                    text=f"● SEQ MATCH: {self._veh_seq:#04x}  Energy={self._veh_energy*0.1:.1f}kWh",
                    fg=SUCCESS)
                self.btn2.config(state="normal", bg=WARNING, fg=BG,
                    text="3  PHASE 2 — TX MSG209  [auto unlock on MSG102]")
                self.phase_lbl.config(text="● Sequence match — click Phase 2", fg=WARNING)
                self._log("OK", f"Sequence {self._veh_seq:#04x} match — Phase 2 unlocked")
            else:
                self.seq_lbl.config(
                    text=f"● SEQ MISMATCH: vehicle={self._veh_seq:#04x} evse={my_seq:#04x}",
                    fg=DANGER)
                self._log("ERROR", "Sequence mismatch")

        elif line.startswith("STATE:"):
            self._log("STATE", f"Vehicle -> {line.split(':',1)[1].strip()}")

    # ── Connexion ─────────────────────────────────────────────────────────────
    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_cb["values"] = ports if ports else ["No ports found"]
        if ports and self.port_var.get() not in ports: self.port_var.set(ports[0])

    def _auto_refresh(self):
        self._refresh_ports(); self.root.after(3000, self._auto_refresh)

    def _toggle_conn(self):
        if self.connected: self._disconnect()
        else: self._connect()

    def _connect(self):
        port = self.port_var.get()
        if not port or port == "No ports found": self._log("ERROR","No port"); return
        baud = int(self.baud_var.get())
        self.ser = SerialThread(port, baud, self._on_rx,
                                lambda e: self.root.after(0, self._log, "ERROR", e))
        self.ser.start(); self.connected = True
        self._log("INFO", f"Connected {port} @ {baud}")
        self.badge.config(text="● ONLINE", fg=SUCCESS)
        self.conn_btn.config(text="DISCONNECT", bg=DANGER)
        self.phase_lbl.config(text="● Connected — waiting MSG100...", fg=ACCENT)
        self._set_state("PLUG_DETECTED")
        self._waiting_for_msg101 = False
        self._discharging = False

    def _disconnect(self):
        if self._loop_job: self.root.after_cancel(self._loop_job); self._loop_job = None
        if self.ser: self.ser.stop(); self.ser = None
        self.connected = False; self._discharging = False; self._sequence_step = 0
        self.badge.config(text="● OFFLINE", fg=DANGER)
        self.conn_btn.config(text="CONNECT", bg=ACCENT)
        for b in [self.btn0, self.btn1, self.btn2, self.btn3]:
            b.config(state="disabled", bg=BORDER, fg=TEXT_MUTED)
        self._set_state("IDLE"); self._log("INFO", "Disconnected")

    def _reset(self):
        if self._loop_job: self.root.after_cancel(self._loop_job); self._loop_job = None
        self._discharging = False; self._waiting_for_msg101 = False
        self._sequence_step = 0
        self.v_evse_stop.set(False)
        for b in [self.btn0, self.btn1, self.btn2, self.btn3]:
            b.config(state="disabled", bg=BORDER, fg=TEXT_MUTED)
        self.phase_lbl.config(text="● RESET — waiting MSG100...", fg=WARNING)
        self._set_state("IDLE"); self._log("INFO", "Reset")

    # ── Log ───────────────────────────────────────────────────────────────────
    def _log(self, cat, msg):
        if not hasattr(self, "log") or self.log is None: return
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log.config(state="normal")
        self.log.insert("end", f"[{ts}] ","ts")
        self.log.insert("end", f"[{cat}] ", cat)
        self.log.insert("end", f"{msg}\n","msg")
        self.log.config(state="disabled"); self.log.see("end")


root = tk.Tk()
app  = StationSim(root)
root.mainloop()