"""
CHAdeMO Station Simulator
=========================
Simulateur du côté borne de recharge pour le protocole CHAdeMO.

Rôle :
    - Reçoit MSG100 (capacités véhicule), MSG101 (info batterie),
      MSG102 (contrôle de charge) via UART depuis le station MC.
    - Envoie MSG108 (capacités borne) et MSG109 (statut borne)
      vers le station MC via UART.

Séquence :
    Connexion  → attente MSG100
    MSG100 reçu → vérification compatibilité → Phase 1 débloquée
    MSG101 reçu → Phase 2 débloquée
    Phase 1     → envoie MSG108 + MSG109
    Phase 2     → envoie MSG109 (output_enable=1)
    MSG102 reçu → Phase 3 débloquée
    Phase 3     → boucle MSG109 à 100 ms
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import serial
import serial.tools.list_ports
from datetime import datetime

# ── Palette de couleurs ───────────────────────────────────────────────────────
BG         = "#0D1117"
PANEL      = "#161B22"
BORDER     = "#30363D"
ACCENT     = "#58A6FF"
TEXT       = "#E6EDF3"
TEXT_MUTED = "#8B949E"
SUCCESS    = "#3FB950"
DANGER     = "#F85149"
WARNING    = "#E3B341"
BLUE       = "#79C0FF"

# ── États CHAdeMO ─────────────────────────────────────────────────────────────
STATES = [
    "IDLE", "PLUG_DETECTED", "INSULATION_TEST",
    "PRE_CHARGE", "CHARGING", "CHARGE_COMPLETE", "FAULT",
]

STATE_COLOR = {
    "IDLE":             TEXT_MUTED,
    "PLUG_DETECTED":    "#79C0FF",
    "INSULATION_TEST":  WARNING,
    "PRE_CHARGE":       "#56D364",
    "CHARGING":         SUCCESS,
    "CHARGE_COMPLETE":  "#58A6FF",
    "FAULT":            DANGER,
}

STATE_DESC = {
    "IDLE":             "Station ready, waiting for vehicle.",
    "PLUG_DETECTED":    "Plug inserted. Handshake initiated.",
    "INSULATION_TEST":  "Running insulation resistance test.",
    "PRE_CHARGE":       "Pre-charging output capacitors.",
    "CHARGING":         "Active DC charging in progress.",
    "CHARGE_COMPLETE":  "Target SOC reached. Session ending.",
    "FAULT":            "Fault detected. Check the log.",
}


# ── Thread série ──────────────────────────────────────────────────────────────
class SerialThread(threading.Thread):
    """Thread de communication série non-bloquant.

    Lit les lignes entrantes et appelle ``on_receive`` pour chacune.
    Appelle ``on_error`` en cas d'exception série.
    """

    def __init__(self, port, baud, on_receive, on_error):
        super().__init__(daemon=True)
        self.port       = port
        self.baud       = baud
        self.on_receive = on_receive
        self.on_error   = on_error
        self.running    = False
        self.ser        = None

    def run(self):
        self.running = True
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=1)
            while self.running:
                if self.ser.in_waiting:
                    line = self.ser.readline().decode("utf-8", errors="replace").strip()
                    if line:
                        self.on_receive(line)
        except serial.SerialException as e:
            self.on_error(str(e))
        finally:
            if self.ser and self.ser.is_open:
                self.ser.close()

    def send(self, text):
        """Envoie une ligne terminée par '\\n' via le port série."""
        if self.ser and self.ser.is_open:
            self.ser.write((text + "\n").encode("utf-8"))

    def stop(self):
        """Arrête la boucle de lecture."""
        self.running = False


# ── Widgets réutilisables ─────────────────────────────────────────────────────
def slider_row(parent, label, unit, lo, hi, initial, res=1):
    """Crée une ligne slider modifiable par l'utilisateur.

    :param parent:  Widget parent.
    :param label:   Libellé affiché.
    :param unit:    Unité affichée à droite.
    :param lo:      Valeur minimale.
    :param hi:      Valeur maximale.
    :param initial: Valeur initiale.
    :param res:     Résolution (défaut 1).
    :return:        ``tk.DoubleVar`` lié au slider.
    """
    var = tk.DoubleVar(value=initial)
    row = tk.Frame(parent, bg=PANEL)
    row.pack(fill="x", padx=6, pady=2)
    tk.Label(row, text=label, bg=PANEL, fg=TEXT_MUTED,
             font=("Courier", 8), width=18, anchor="w").pack(side="left")

    def _clamp(_=None):
        var.set(round(var.get(), 1))

    tk.Scale(row, variable=var, from_=lo, to=hi, resolution=res,
             orient="horizontal", bg=PANEL, fg=TEXT, highlightthickness=0,
             troughcolor=BORDER, activebackground=ACCENT,
             length=120, showvalue=False, command=_clamp).pack(side="left", padx=(4, 6))
    tk.Label(row, textvariable=var, bg=PANEL, fg=ACCENT,
             font=("Courier", 9, "bold"), width=5, anchor="e").pack(side="left")
    tk.Label(row, text=unit, bg=PANEL, fg=TEXT_MUTED,
             font=("Courier", 8), width=5, anchor="w").pack(side="left", padx=(3, 0))
    return var


def value_row(parent, label, unit, init="—", color=BLUE):
    """Crée une ligne en lecture seule pour afficher des valeurs calculées/reçues.

    :param parent:  Widget parent.
    :param label:   Libellé affiché.
    :param unit:    Unité affichée à droite.
    :param init:    Valeur initiale.
    :param color:   Couleur de la valeur.
    :return:        ``tk.StringVar`` lié au label.
    """
    var = tk.StringVar(value=str(init))
    row = tk.Frame(parent, bg=PANEL)
    row.pack(fill="x", padx=6, pady=2)
    tk.Label(row, text=label, bg=PANEL, fg=TEXT_MUTED,
             font=("Courier", 8), width=18, anchor="w").pack(side="left")
    tk.Label(row, textvariable=var, bg=PANEL, fg=color,
             font=("Courier", 9, "bold"), width=7, anchor="e").pack(side="left", padx=(8, 4))
    tk.Label(row, text=unit, bg=PANEL, fg=TEXT_MUTED,
             font=("Courier", 8), width=5, anchor="w").pack(side="left", padx=(3, 0))
    return var


# ── Application principale ────────────────────────────────────────────────────
class StationApp:
    """Interface graphique du simulateur borne CHAdeMO."""

    def __init__(self, root):
        self.root = root
        root.title("CHAdeMO Station Simulator")
        root.configure(bg=BG)
        root.minsize(900, 580)

        self.serial_thread      = None
        self.connected          = False
        self._loop_job          = None
        self.state_labels       = {}
        self.current_state      = "IDLE"
        self._veh_min_current   = 0   # reçu via MSG100
        self._veh_min_voltage   = 0   # reçu via MSG100
        self._veh_max_voltage   = 0   # reçu via MSG100
        self._last_current_req  = 0.0 # mis à jour par MSG102
        self._last_target_v     = 0   # mis à jour par MSG102
        self._last_stop_req     = 0   # mis à jour par MSG102
        self._remaining_minutes = 0   # mis à jour par MSG101

        self._build_ui()
        self._refresh_ports()
        root.after(3000, self._auto_refresh_ports)

    # ── Construction UI ───────────────────────────────────────────────────────
    def _build_ui(self):
        """Construit l'interface complète (header, barre connexion, panneaux)."""
        hdr = tk.Frame(self.root, bg=PANEL, height=36)
        hdr.pack(fill="x", padx=6, pady=(6, 3))
        hdr.pack_propagate(False)
        tk.Label(hdr, text="⚡  CHAdeMO STATION SIMULATOR",
                 bg=PANEL, fg=ACCENT, font=("Courier", 11, "bold")).pack(side="left", padx=10)
        self.status_badge = tk.Label(hdr, text="● DISCONNECTED",
                 bg=PANEL, fg=DANGER, font=("Courier", 9, "bold"))
        self.status_badge.pack(side="right", padx=10)

        cbar = tk.Frame(self.root, bg=BG)
        cbar.pack(fill="x", padx=6, pady=(0, 4))
        tk.Label(cbar, text="PORT", bg=BG, fg=TEXT_MUTED, font=("Courier", 9)).pack(side="left")
        self.port_var   = tk.StringVar()
        self.port_combo = ttk.Combobox(cbar, textvariable=self.port_var, width=18, state="readonly")
        self.port_combo.pack(side="left", padx=(4, 12))
        tk.Label(cbar, text="BAUD", bg=BG, fg=TEXT_MUTED, font=("Courier", 9)).pack(side="left")
        self.baud_var = tk.StringVar(value="115200")
        ttk.Combobox(cbar, textvariable=self.baud_var, width=9, state="readonly",
                     values=["9600", "19200", "57600", "115200", "230400"]).pack(side="left", padx=(4, 12))
        self.connect_btn = tk.Button(cbar, text="CONNECT", width=10,
                 bg=ACCENT, fg=BG, font=("Courier", 9, "bold"),
                 relief="flat", cursor="hand2", command=self._toggle_connection)
        self.connect_btn.pack(side="left")

        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        left = tk.Frame(main, bg=BG, width=360)
        left.pack(side="left", fill="y", padx=(0, 4))
        left.pack_propagate(False)

        right_outer = tk.Frame(main, bg=BG)
        right_outer.pack(side="left", fill="both", expand=True)

        # Panneau droit scrollable
        canvas    = tk.Canvas(right_outer, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(right_outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        right        = tk.Frame(canvas, bg=BG)
        right_window = canvas.create_window((0, 0), window=right, anchor="nw")
        right.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(right_window, width=e.width))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        # Panneau gauche scrollable
        lc  = tk.Canvas(left, bg=BG, highlightthickness=0)
        lsb = ttk.Scrollbar(left, orient="vertical", command=lc.yview)
        lc.configure(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y")
        lc.pack(side="left", fill="both", expand=True)
        lframe = tk.Frame(lc, bg=BG)
        lw     = lc.create_window((0, 0), window=lframe, anchor="nw")
        lframe.bind("<Configure>", lambda e: lc.configure(scrollregion=lc.bbox("all")))
        lc.bind("<Configure>", lambda e: lc.itemconfig(lw, width=e.width))

        self._build_left_panel(lframe)
        self._build_telemetry(right)
        self._build_log(right)

    def _build_left_panel(self, parent):
        """Construit le panneau gauche : machine d'états, sliders TX, boutons séquence."""
        self._build_state_machine(parent)

        # MSG108 — capacités borne (user-editable)
        f108 = tk.LabelFrame(parent, text=" TX: 0x108  STATION CAPABILITIES ",
                 bg=PANEL, fg=BLUE, font=("Courier", 8, "bold"), bd=1, relief="solid")
        f108.pack(fill="x", pady=(0, 3))
        self.v_welding   = slider_row(f108, "Welding Detection", "",    0,   1,   0)
        self.v_avail_v   = slider_row(f108, "Avail. Voltage",    "Vdc", 200, 500, 400)
        self.v_avail_i   = slider_row(f108, "Avail. Current",    "A",   0,  120,  80)
        self.v_threshold = slider_row(f108, "Threshold Voltage", "Vdc", 200, 500, 380)
        ic = tk.Frame(f108, bg=PANEL); ic.pack(fill="x", padx=6, pady=(2, 4))
        self.incompat_var = tk.BooleanVar(value=False)
        self.incompat_chk = tk.Checkbutton(ic,
                 text="  FORCE INCOMPATIBLE (MSG109 incompat=1)",
                 variable=self.incompat_var, bg=PANEL, fg=DANGER, selectcolor=BG,
                 activebackground=PANEL, font=("Courier", 8, "bold"), state="disabled")
        self.incompat_chk.pack(side="left")

        # MSG109 — statut borne (read-only, calculé/miroir)
        f109 = tk.LabelFrame(parent, text=" TX: 0x109  STATION STATUS ",
                 bg=PANEL, fg=BLUE, font=("Courier", 8, "bold"), bd=1, relief="solid")
        f109.pack(fill="x", pady=(0, 3))
        self.d_out_v       = value_row(f109, "Output Voltage",  "Vdc", "0", ACCENT)
        self.d_out_i       = value_row(f109, "Output Current",  "A",   "0", ACCENT)
        self.d_out_enable  = value_row(f109, "Output Enable",   "",    "0", ACCENT)
        self.d_remain      = value_row(f109, "Remaining Time",  "min", "0", ACCENT)
        self.d_incompat_tx = value_row(f109, "Incompat field",  "",    "0", ACCENT)

        # Boutons de séquence
        fseq = tk.LabelFrame(parent, text=" CHARGING SEQUENCE ",
                 bg=PANEL, fg=TEXT_MUTED, font=("Courier", 8, "bold"), bd=1, relief="solid")
        fseq.pack(fill="x", pady=(0, 3))
        self.phase_label = tk.Label(fseq, text="● PHASE: NOT STARTED",
                 bg=PANEL, fg=TEXT_MUTED, font=("Courier", 9, "bold"))
        self.phase_label.pack(anchor="w", padx=6, pady=(4, 2))
        self.phase1_btn = tk.Button(fseq,
                 text="① PHASE 1 — Send Station Caps (0x108+0x109)  🔒",
                 bg=BORDER, fg=TEXT_MUTED, font=("Courier", 8, "bold"),
                 relief="flat", cursor="hand2", state="disabled",
                 command=self._send_phase1)
        self.phase1_btn.pack(fill="x", padx=6, pady=2)
        self.phase2_btn = tk.Button(fseq,
                 text="② PHASE 2 — Send Confirm (0x109)  🔒",
                 bg=BORDER, fg=TEXT_MUTED, font=("Courier", 8, "bold"),
                 relief="flat", cursor="hand2", state="disabled",
                 command=self._send_phase2)
        self.phase2_btn.pack(fill="x", padx=6, pady=2)
        self.phase3_btn = tk.Button(fseq,
                 text="③ PHASE 3 — Start Output Loop (0x109)  🔒",
                 bg=BORDER, fg=TEXT_MUTED, font=("Courier", 8, "bold"),
                 relief="flat", cursor="hand2", state="disabled",
                 command=self._toggle_phase3)
        self.phase3_btn.pack(fill="x", padx=6, pady=2)
        self.running_label = tk.Label(fseq, text="  ◉ LOOP STOPPED",
                 bg=PANEL, fg=DANGER, font=("Courier", 9, "bold"))
        self.running_label.pack(anchor="w", padx=6, pady=(0, 4))

    def _build_state_machine(self, parent):
        """Construit le panneau machine d'états CHAdeMO."""
        frame = tk.LabelFrame(parent, text=" PROTOCOL STATE MACHINE ",
                 bg=PANEL, fg=TEXT_MUTED, font=("Courier", 8, "bold"), bd=1, relief="solid")
        frame.pack(fill="x", pady=(0, 4))
        for state in STATES:
            row = tk.Frame(frame, bg=PANEL)
            row.pack(fill="x", padx=4, pady=1)
            dot = tk.Label(row, text="●", bg=PANEL, fg=TEXT_MUTED, font=("Courier", 8))
            dot.pack(side="left")
            lbl = tk.Label(row, text=f"  {state}", bg=PANEL, fg=TEXT_MUTED,
                     font=("Courier", 8), anchor="w", width=20)
            lbl.pack(side="left")
            self.state_labels[state] = (dot, lbl)
        self.state_desc = tk.Label(parent, text=STATE_DESC["IDLE"],
                 bg=PANEL, fg=WARNING, font=("Courier", 8),
                 wraplength=320, justify="left", anchor="w", padx=6, pady=4)
        self.state_desc.pack(fill="x", pady=(0, 4))
        self._set_state("IDLE")

    def _set_state(self, state):
        """Applique une transition d'état dans la machine d'états.

        :param state: Nom de l'état cible (voir ``STATES``).
        """
        state = state.upper().strip()
        if state not in STATES:
            self._log("ERROR", f"Unknown state: {state}"); return
        for s, (dot, lbl) in self.state_labels.items():
            if s == state:
                color = STATE_COLOR[s]
                dot.config(fg=color)
                lbl.config(fg=color, font=("Courier", 9, "bold"))
            else:
                dot.config(fg=TEXT_MUTED)
                lbl.config(fg=TEXT_MUTED, font=("Courier", 9))
        self.state_desc.config(text=f"▶️  {STATE_DESC.get(state, '')}")
        self.current_state = state
        self.root.after(0, self._log, "STATE", f"Transition → {state}")

    # ── Panneau droit : télémétrie RX ─────────────────────────────────────────
    def _build_telemetry(self, parent):
        """Construit les cartes de télémétrie RX (MSG100, MSG101, MSG102)."""
        self.tele_vars = {}

        # MSG100 — capacités véhicule
        f100 = tk.LabelFrame(parent, text=" 0x100  VEHICLE CAPABILITIES  (received) ",
                 bg=PANEL, fg="#79C0FF", font=("Courier", 8, "bold"), bd=1, relief="solid")
        f100.pack(fill="x", pady=(0, 3))
        for key, label, unit, col in [
            ("min_current", "Min Charge Current", "A",    0),
            ("min_voltage", "Min Battery Voltage", "Vdc", 1),
            ("max_voltage", "Max Battery Voltage", "Vdc", 2),
            ("charge_rate", "Charged Rate Const.", "",    3),
        ]:
            self._make_card(f100, key, label, unit, 0, col, "#79C0FF")
        self.compat_label = tk.Label(f100, text="● Waiting for vehicle…",
                 bg=PANEL, fg=TEXT_MUTED, font=("Courier", 8, "bold"))
        self.compat_label.grid(row=1, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 4))

        # MSG101 — info batterie
        f101 = tk.LabelFrame(parent, text=" 0x101  BATTERY INFO  (received) ",
                 bg=PANEL, fg=TEXT_MUTED, font=("Courier", 8, "bold"), bd=1, relief="solid")
        f101.pack(fill="x", pady=(0, 3))
        for key, label, unit, col in [
            ("max_time_10s",  "Max Time",  "x10s",    0),
            ("max_time_1min", "Max Time",  "x1min",   1),
            ("est_time_1min", "Est. Time", "min",      2),
            ("capacity",      "Capacity",  "x0.1kWh", 3),
        ]:
            self._make_card(f101, key, label, unit, 0, col, TEXT_MUTED)

        # MSG102 — statut live véhicule
        f102 = tk.LabelFrame(parent, text=" 0x102  VEHICLE LIVE STATUS  (received) ",
                 bg=PANEL, fg=WARNING, font=("Courier", 8, "bold"), bd=1, relief="solid")
        f102.pack(fill="x", pady=(0, 3))
        for key, label, unit, col in [
            ("target_voltage", "Target Voltage",  "Vdc", 0),
            ("current_req",    "Current Request", "A",   1),
            ("soc",            "State of Charge", "%",   2),
        ]:
            self._make_card(f102, key, label, unit, 0, col, WARNING)
        ind = tk.Frame(f102, bg=PANEL)
        ind.grid(row=1, column=0, columnspan=3, sticky="ew", padx=3, pady=(0, 3))
        self.fault_ind = tk.Label(ind, text="  FAULT:NO  ", bg=PANEL, fg=SUCCESS,
                 font=("Courier", 8, "bold"))
        self.fault_ind.pack(side="left", padx=(6, 6))
        self.chg_ind = tk.Label(ind, text="  CHG:NO  ", bg=PANEL, fg=TEXT_MUTED,
                 font=("Courier", 8, "bold"))
        self.chg_ind.pack(side="left", padx=(0, 6))
        self.stop_ind = tk.Label(ind, text="  STOP:NO  ", bg=PANEL, fg=SUCCESS,
                 font=("Courier", 8, "bold"))
        self.stop_ind.pack(side="left")

        # Aliases courts
        self.r_min_current = self.tele_vars["min_current"]
        self.r_min_voltage = self.tele_vars["min_voltage"]
        self.r_max_voltage = self.tele_vars["max_voltage"]
        self.r_charge_rate = self.tele_vars["charge_rate"]
        self.r_max_t10s    = self.tele_vars["max_time_10s"]
        self.r_max_t1min   = self.tele_vars["max_time_1min"]
        self.r_est_t1min   = self.tele_vars["est_time_1min"]
        self.r_cap         = self.tele_vars["capacity"]
        self.r_target_v    = self.tele_vars["target_voltage"]
        self.r_cur_req     = self.tele_vars["current_req"]
        self.r_soc         = self.tele_vars["soc"]
        self.r_fault       = tk.StringVar(value="—")
        self.r_chg_req     = tk.StringVar(value="—")
        self.r_stop        = tk.StringVar(value="—")

    def _make_card(self, parent, key, label, unit, row, col, color=None):
        """Crée une carte de télémétrie en lecture seule.

        :param parent:  Widget parent (LabelFrame).
        :param key:     Clé dans ``self.tele_vars``.
        :param label:   Libellé de la carte.
        :param unit:    Unité affichée.
        :param row:     Ligne dans la grille.
        :param col:     Colonne dans la grille.
        :param color:   Couleur de la valeur (défaut ACCENT).
        """
        fg   = color or ACCENT
        card = tk.Frame(parent, bg=BG, bd=1, relief="solid", padx=4, pady=4)
        card.grid(row=row, column=col, padx=3, pady=3, sticky="nsew")
        parent.grid_columnconfigure(col, weight=1)
        tk.Label(card, text=label.upper(), bg=BG, fg=TEXT_MUTED,
                 font=("Courier", 8)).pack(anchor="w")
        var = tk.StringVar(value="—")
        self.tele_vars[key] = var
        tk.Label(card, textvariable=var, bg=BG, fg=fg,
                 font=("Courier", 13, "bold")).pack(anchor="w")
        tk.Label(card, text=unit, bg=BG, fg=TEXT_MUTED,
                 font=("Courier", 8)).pack(anchor="w")

    def _build_log(self, parent):
        """Construit le panneau de log (panneau droit, bas)."""
        flog = tk.LabelFrame(parent, text=" MESSAGE LOG ",
                 bg=PANEL, fg=TEXT_MUTED, font=("Courier", 8, "bold"), bd=1, relief="solid")
        flog.pack(fill="both", expand=True)
        self.log = scrolledtext.ScrolledText(flog, bg=BG, fg=TEXT,
                 font=("Courier", 9), state="disabled", relief="flat")
        self.log.pack(fill="both", expand=True, padx=6, pady=6)
        for tag, col in [("ts", TEXT_MUTED), ("TX", ACCENT), ("RX", WARNING),
                          ("LOOP", TEXT_MUTED), ("INFO", TEXT_MUTED),
                          ("OK", SUCCESS), ("ERROR", DANGER), ("msg", TEXT),
                          ("STATE", BLUE)]:
            self.log.tag_config(tag, foreground=col)
        tk.Button(flog, text="Clear Log", bg=BORDER, fg=TEXT,
                  font=("Courier", 8), relief="flat", cursor="hand2",
                  command=self._clear_log).pack(anchor="e", padx=6, pady=(0, 6))

    # ── Vérification compatibilité ────────────────────────────────────────────
    def _check_compatibility(self):
        """Vérifie si les capacités de la borne couvrent les besoins du véhicule.

        :return: True si compatible, False sinon.
        """
        av  = self.v_avail_v.get()
        ai  = self.v_avail_i.get()
        thv = self.v_threshold.get()
        return (av  >= self._veh_max_voltage and
                ai  >= self._veh_min_current and
                thv <= self._veh_max_voltage)

    # ── Actions de séquence ───────────────────────────────────────────────────
    def _send_phase1(self):
        """Envoie MSG108 (capacités borne) et MSG109 initial via UART."""
        if not self.serial_thread:
            self._log("ERROR", "Not connected"); return
        weld     = int(self.v_welding.get())
        av       = int(self.v_avail_v.get())
        ai       = int(self.v_avail_i.get())
        thv      = int(self.v_threshold.get())
        incompat = 1 if self.incompat_var.get() else 0
        msg108   = f"MSG108:{weld},{av},{ai},{thv}"
        msg109   = f"MSG109:0,0,0,0,0,0,{incompat}"
        self.serial_thread.send(msg108); self._log("TX", msg108)
        self.serial_thread.send(msg109); self._log("TX", msg109)
        self.d_incompat_tx.set(str(incompat))
        self.phase_label.config(text="● PHASE 1 SENT — Waiting for battery…", fg=BLUE)
        self.phase1_btn.config(state="disabled", fg=TEXT_MUTED, bg=BORDER)
        self.incompat_chk.config(state="disabled")
        if incompat == 1:
            self._log("INFO", "Incompatibility flagged — resetting after delay")
            self.root.after(2000, self._do_reset)

    def _send_phase2(self):
        """Envoie MSG109 de confirmation (output_enable=1) via UART."""
        if not self.serial_thread:
            self._log("ERROR", "Not connected"); return
        remaining = self._remaining_minutes
        msg109    = f"MSG109:0,0,1,0,{remaining},0"
        self.serial_thread.send(msg109); self._log("TX", msg109)
        self.d_out_v.set("0"); self.d_out_i.set("0")
        self.d_out_enable.set("1"); self.d_remain.set(str(remaining))
        self.d_incompat_tx.set("0")
        self.phase_label.config(text="● PHASE 2 SENT — Waiting for vehicle Phase 3…", fg=WARNING)
        self.phase2_btn.config(state="disabled", fg=TEXT_MUTED, bg=BORDER)
        self._set_state("PRE_CHARGE")

    def _toggle_phase3(self):
        """Démarre ou arrête la boucle d'envoi MSG109 (Phase 3)."""
        if self._loop_job is None:
            self._start_loop()
        else:
            self._stop_loop()

    def _start_loop(self):
        """Démarre la boucle d'envoi MSG109 à 100 ms."""
        self.phase3_btn.config(text="■ STOP OUTPUT LOOP", bg=DANGER, fg=BG)
        self.running_label.config(text="  ◉ LOOP RUNNING", fg=SUCCESS)
        self.phase_label.config(text="● PHASE 3 — STATION OUTPUTTING", fg=SUCCESS)
        self._set_state("CHARGING")
        self._loop_tick()

    def _stop_loop(self):
        """Arrête la boucle d'envoi MSG109 et remet les affichages à zéro."""
        if self._loop_job:
            self.root.after_cancel(self._loop_job)
            self._loop_job = None
        self.phase3_btn.config(text="③ PHASE 3 — Start Output Loop (0x109)  🔒",
                               bg=BORDER, fg=TEXT_MUTED, state="disabled")
        self.running_label.config(text="  ◉ LOOP STOPPED", fg=DANGER)
        for d in [self.d_out_v, self.d_out_i, self.d_out_enable,
                  self.d_remain, self.d_incompat_tx]:
            d.set("0")

    def _loop_tick(self):
        """Un tick de la boucle Phase 3 : envoie MSG109 miroir de MSG102."""
        if not self.serial_thread:
            self._stop_loop(); return
        out_v     = self._last_target_v
        out_i     = round(self._last_current_req, 1)
        remaining = self._remaining_minutes
        msg109    = f"MSG109:{out_v},{out_i},1,0,{remaining},0"
        self.serial_thread.send(msg109); self._log("LOOP", msg109)
        self.d_out_v.set(str(out_v)); self.d_out_i.set(str(out_i))
        self.d_out_enable.set("1"); self.d_remain.set(str(remaining))
        self.d_incompat_tx.set("0")
        if self._last_stop_req == 1:
            self._log("INFO", "Stop request received — ending session")
            self._stop_loop()
            self.phase_label.config(text="● CHARGE COMPLETE — Session ended", fg=SUCCESS)
            self._set_state("CHARGE_COMPLETE")
            return
        self._loop_job = self.root.after(100, self._loop_tick)

    # ── Reset ─────────────────────────────────────────────────────────────────
    def _do_reset(self):
        """Réinitialise tous les états et l'interface pour une nouvelle session."""
        self._stop_loop()
        if self.serial_thread:
            self.serial_thread.send("RESET")
        self._remaining_minutes = 0
        self._last_current_req  = 0.0
        self._last_target_v     = 0
        self._last_stop_req     = 0
        self.phase1_btn.config(state="disabled",
            text="① PHASE 1 — Send Station Caps (0x108+0x109)  🔒", fg=TEXT_MUTED, bg=BORDER)
        self.phase2_btn.config(state="disabled",
            text="② PHASE 2 — Send Confirm (0x109)  🔒", fg=TEXT_MUTED, bg=BORDER)
        self.phase3_btn.config(state="disabled",
            text="③ PHASE 3 — Start Output Loop (0x109)  🔒", fg=TEXT_MUTED, bg=BORDER)
        self.incompat_chk.config(state="disabled")
        self.incompat_var.set(False)
        self.compat_label.config(text="● Reset — waiting for vehicle…", fg=WARNING)
        self.phase_label.config(text="● RESET — Ready for new session", fg=WARNING)
        self._set_state("IDLE")
        for v in [self.r_min_current, self.r_min_voltage, self.r_max_voltage, self.r_charge_rate,
                  self.r_max_t10s, self.r_max_t1min, self.r_est_t1min, self.r_cap,
                  self.r_target_v, self.r_cur_req, self.r_soc,
                  self.r_fault, self.r_chg_req, self.r_stop]:
            v.set("—")
        for d in [self.d_out_v, self.d_out_i, self.d_out_enable,
                  self.d_remain, self.d_incompat_tx]:
            d.set("0")
        self._log("INFO", "Station reset")

    # ── Réception série ───────────────────────────────────────────────────────
    def _on_rx(self, line):
        """Traite une ligne reçue depuis le station MC via UART.

        :param line: Ligne décodée (sans '\\n').
        """
        self.root.after(0, self._log, "RX", line)

        if line.startswith("RESET"):
            self.root.after(0, self._do_reset); return

        if line.startswith("MSG100:"):
            parts = line.split(":", 1)[1].split(",")
            if len(parts) == 4:
                self._veh_min_current = int(parts[0])
                self._veh_min_voltage = int(parts[1])
                self._veh_max_voltage = int(parts[2])
                self.root.after(0, self.r_min_current.set, parts[0])
                self.root.after(0, self.r_min_voltage.set, parts[1])
                self.root.after(0, self.r_max_voltage.set, parts[2])
                self.root.after(0, self.r_charge_rate.set, parts[3])
                self.root.after(0, self._process_msg100)

        elif line.startswith("MSG101:"):
            parts = line.split(":", 1)[1].split(",")
            if len(parts) == 4:
                self._remaining_minutes = int(parts[2])
                self.root.after(0, self.r_max_t10s.set,  parts[0])
                self.root.after(0, self.r_max_t1min.set, parts[1])
                self.root.after(0, self.r_est_t1min.set, parts[2])
                self.root.after(0, self.r_cap.set,        parts[3])
                self.root.after(0, self._unlock_phase2)

        elif line.startswith("MSG102:"):
            parts = line.split(":", 1)[1].split(",")
            if len(parts) == 6:
                self._last_target_v    = int(parts[0])
                self._last_current_req = float(parts[1])
                self._last_stop_req    = int(parts[5])
                self.root.after(0, self.r_target_v.set, parts[0])
                self.root.after(0, self.r_cur_req.set,  parts[1])
                self.root.after(0, self.r_soc.set,      parts[2])
                self.root.after(0, self.r_fault.set,    parts[3])
                self.root.after(0, self.r_chg_req.set,  parts[4])
                self.root.after(0, self.r_stop.set,     parts[5])
                self.root.after(0, self._update_indicators, parts)
                if self._loop_job is None and self.phase3_btn.cget("state") == "disabled":
                    self.root.after(0, self._unlock_phase3)

        elif line.startswith("STATE:"):
            state = line.split(":", 1)[1].strip()
            self.root.after(0, self._set_state, state)

        elif line.startswith("FAULT:"):
            reason = line.split(":", 1)[1].strip()
            self.root.after(0, self._set_state, "FAULT")
            self.root.after(0, self._log, "ERROR", f"Fault reported: {reason}")

    def _process_msg100(self):
        """Vérifie la compatibilité après réception de MSG100 et met à jour l'UI."""
        if self._check_compatibility():
            av = int(self.v_avail_v.get())
            self.compat_label.config(
                text=f"● COMPATIBLE ✓  avail={av}V ≥ max={self._veh_max_voltage}V", fg=SUCCESS)
            self.phase1_btn.config(state="normal",
                text="① PHASE 1 — Send Station Caps (0x108+0x109)", fg=TEXT, bg=BORDER)
            self.incompat_chk.config(state="normal")
            self._log("OK", f"Vehicle compatible — max_v={self._veh_max_voltage} min_i={self._veh_min_current}")
            self._set_state("INSULATION_TEST")
        else:
            av = int(self.v_avail_v.get())
            self.compat_label.config(
                text=f"● INCOMPATIBLE ✗  avail={av}V < max={self._veh_max_voltage}V", fg=DANGER)
            self._log("ERROR", f"Incompatible — avail_v={av} needed≥{self._veh_max_voltage}")

    def _unlock_phase2(self):
        """Déverrouille le bouton Phase 2 après réception de MSG101."""
        self.phase2_btn.config(state="normal",
            text="② PHASE 2 — Send Confirm (0x109)", fg=TEXT, bg=BORDER)
        self.phase_label.config(
            text=f"● MSG101 RECEIVED ✓  est={self._remaining_minutes}min — Click Phase 2",
            fg=SUCCESS)
        self._log("OK", f"MSG101 received — est time {self._remaining_minutes} min")

    def _unlock_phase3(self):
        """Déverrouille le bouton Phase 3 après réception du premier MSG102."""
        self.phase3_btn.config(state="normal",
            text="③ PHASE 3 — Start Output Loop (0x109)", fg=TEXT, bg=SUCCESS)
        self.phase_label.config(text="● PHASE 3 READY — Click to start output", fg=SUCCESS)
        self._log("OK", "MSG102 received — Phase 3 unlocked")

    def _update_indicators(self, parts):
        """Met à jour les indicateurs FAULT / CHG / STOP depuis les champs MSG102.

        :param parts: Liste des champs parsés de MSG102.
        """
        fault = parts[3]; chg = parts[4]; stop = parts[5]
        self.fault_ind.config(text=f"  FAULT:{'YES' if fault == '1' else 'NO'}  ",
                              fg=DANGER if fault == "1" else SUCCESS)
        self.chg_ind.config(text=f"  CHG:{'YES' if chg == '1' else 'NO'}  ",
                            fg=SUCCESS if chg == "1" else TEXT_MUTED)
        self.stop_ind.config(text=f"  STOP:{'YES' if stop == '1' else 'NO'}  ",
                             fg=WARNING if stop == "1" else SUCCESS)

    # ── Gestion connexion ─────────────────────────────────────────────────────
    def _refresh_ports(self):
        """Rafraîchit la liste des ports série disponibles."""
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo["values"] = ports if ports else ["No ports found"]
        if ports and self.port_var.get() not in ports:
            self.port_var.set(ports[0])

    def _auto_refresh_ports(self):
        """Rafraîchissement automatique des ports toutes les 3 secondes."""
        self._refresh_ports()
        self.root.after(3000, self._auto_refresh_ports)

    def _toggle_connection(self):
        """Bascule entre connexion et déconnexion."""
        if self.connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        """Ouvre le port série et démarre le thread de réception."""
        port = self.port_var.get()
        if not port or port == "No ports found":
            self._log("ERROR", "No port"); return
        baud = int(self.baud_var.get())
        self.serial_thread = SerialThread(port, baud, self._on_rx, self._on_error)
        self.serial_thread.start()
        self.connected = True
        self._log("INFO", f"Connected to {port} @ {baud}")
        self.status_badge.config(text="● CONNECTED", fg=SUCCESS)
        self.connect_btn.config(text="DISCONNECT", bg=DANGER)
        self.phase_label.config(text="● PLUG DETECTED — Waiting for vehicle MSG100", fg=BLUE)
        self._set_state("PLUG_DETECTED")

    def _disconnect(self):
        """Ferme la connexion série et remet l'interface à l'état initial."""
        self._stop_loop()
        if self.serial_thread:
            self.serial_thread.stop()
            self.serial_thread = None
        self.connected = False
        self._log("INFO", "Disconnected")
        self.status_badge.config(text="● DISCONNECTED", fg=DANGER)
        self.connect_btn.config(text="CONNECT", bg=ACCENT)
        self.phase1_btn.config(state="disabled",
            text="① PHASE 1 — Send Station Caps (0x108+0x109)  🔒", fg=TEXT_MUTED, bg=BORDER)
        self.phase2_btn.config(state="disabled",
            text="② PHASE 2 — Send Confirm (0x109)  🔒", fg=TEXT_MUTED, bg=BORDER)
        self.phase3_btn.config(state="disabled",
            text="③ PHASE 3 — Start Output Loop (0x109)  🔒", fg=TEXT_MUTED, bg=BORDER)
        self.incompat_chk.config(state="disabled")
        self.phase_label.config(text="● PHASE: NOT STARTED", fg=TEXT_MUTED)
        self._set_state("IDLE")

    def _on_error(self, msg):
        """Affiche une erreur série dans le log."""
        self.root.after(0, self._log, "ERROR", msg)

    # ── Log ───────────────────────────────────────────────────────────────────
    def _log(self, category, message):
        """Ajoute une ligne horodatée dans le panneau de log.

        :param category: Tag de catégorie (TX, RX, OK, ERROR, STATE, …).
        :param message:  Contenu du message.
        """
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log.config(state="normal")
        self.log.insert("end", f"[{ts}] ", "ts")
        self.log.insert("end", f"[{category}] ", category)
        self.log.insert("end", f"{message}\n", "msg")
        self.log.config(state="disabled")
        self.log.see("end")

    def _clear_log(self):
        """Efface tout le contenu du panneau de log."""
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")


# ── Point d'entrée ────────────────────────────────────────────────────────────
root = tk.Tk()
app  = StationApp(root)
root.mainloop()
