#!/usr/bin/env python3
"""Participant UI for the HRI robot trust study (see README_UI.md).

Participant window: fullscreen, the participant only presses Enter.
Experimenter window: read-only, shows the current trial and the decision times.
Space = continue, Escape = quit (both work in either window).
Two versions with the same flow: A01, A02, ... = screen version (the hint is text on the
screen), B01, B02, ... = robot version (the hint is the robot pushing the block). Statements,
correct bowls, block order and wrong hints come from study.yaml.
Decision time = hint fully shown (screen text, or robot back up after the push) until Enter.
Times are shown in the experimenter window and printed in the terminal. The experimenter clicks
the bowl the participant chose; every block is saved to data/<participant>_<date>.csv.

    python hri_ui.py --participant A01 --dry-run
"""
import sys

sys.dont_write_bytecode = True  # write nothing to disk, not even __pycache__

import argparse
import csv
import datetime
import glob
import re
import math
import os
import shutil
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox

import yaml

from robot_interface import BLOCKS, BOWLS, FakeRobot, RosRobot

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")   # one CSV per participant session, kept off GitHub
# Saved columns: the same fields, labels and order as the experimenter window.
# Saved columns, one row per block: (key, header).
DATA_COLUMNS = [("participant", "Participant"), ("version", "Version"), ("round", "Round"),
                ("question", "Block number"), ("block", "Block"), ("correct", "Correct bowl"),
                ("advice", "Hinted bowl"), ("hint_correct", "Hint correct"),
                ("chosen", "Chosen bowl"), ("followed", "Followed hint"), ("time_s", "Decision time (s)")]
# Session order. Ids as in study.yaml; labels, block counts and reading times come from
# `levels` in config.yaml.
LEVELS = ["practice", "round1", "round2"]
ROUND_NAMES = {"practice": "practice", "round1": "1", "round2": "2"}   # "Round" column
DEFAULT_LEVELS = {
    "practice": {"label": "Practice round", "questions": 3, "reading_time_s": None},
    "round1": {"label": "Round 1", "questions": 6, "reading_time_s": 40},
    "round2": {"label": "Round 2", "questions": 6, "reading_time_s": 20},
}
QUESTIONS_PER_LEVEL = 6   # largest level, for --start-question
VERSIONS = {"A": "screen", "B": "robot"}   # first letter of the participant ID

# Friendly theme: soft lavender background, content on a white rounded card.
BG = "#E9E3F7"        # lavender background
CARD = "#FFFFFF"      # the card the content sits on
SHADOW = "#D6CDEE"    # soft shadow under the card
FG = "#2E3440"        # dark gray text
MUTED = "#9AA0AA"     # hints for the experimenter, upcoming progress dots
ACCENT = "#7C5CBF"    # purple: title, progress, robot drawings
BOWL_COLORS = {"red": ("#D64545", "#F6C9C9"), "blue": ("#3B6FD8", "#C9D8F6")}   # (bowl, inside)

DEFAULT_CONFIG = {
    "fullscreen": True,
    "participant_monitor_x_offset": 0,
    "fake_robot": {"pick": 3.0, "place_decision": 3.0, "point": 3.0, "home": 3.0},
    "levels": DEFAULT_LEVELS,
    "font_family": "Helvetica",
    "font_sizes": {"title": 48, "text": 40, "statements": 32, "counter": 28, "hint": 18, "experimenter": 13},
    "debrief_text": "Thank you for taking part.",
    "animations": True,   # icons, loading robot, progress dots, fades; false = plain text only
    "fade_ms": 250,
}

# Participant screen labels, top to bottom: name -> (font size key, relative y, anchor)
LAYOUT = [
    ("counter", "counter", 0.06, "n"),
    ("title", "title", 0.06, "n"),
    ("statements", "statements", 0.20, "n"),
    ("status", "text", 0.40, "center"),
    ("advice", "text", 0.58, "center"),
    ("prompt", "text", 0.86, "center"),
    ("countdown", "counter", 0.94, "center"),
    ("hint", "hint", 0.97, "s"),  # small line for the experimenter on wait screens
]

EXPERIMENTER_FIELDS = [
    ("participant", "Participant"),
    ("version", "Version"),
    ("round", "Round"),
    ("question", "Block number"),
    ("block", "Block"),
    ("correct", "Correct bowl"),
    ("advice", "Hinted bowl"),
    ("chosen", "Chosen bowl"),
    ("step", "Step"),
    ("timer", "Decision time"),
]

STEP_NAMES = {
    "waiting": "waiting",
    "statements": "statements",
    "idle": "waiting for Enter (next block)",
    "presenting": "robot presenting",
    "advice": "hint shown",
    "explain": "practice: correct answer shown",
    "homing": "robot returning home",
    "level_complete": "round complete",
    "debrief": "debrief",
}


def blend(c1, c2, k):
    """Color between c1 (k=0) and c2 (k=1), both "#RRGGBB"."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02X%02X%02X" % tuple(int(round(x + (y - x) * k)) for x, y in zip(a, b))


def format_time_row(row):
    level, question, source, block, advice, seconds = row
    return "%-4s Q%d  %-6s  %-14s  advice %-4s  %6.2f s" % (level, question, source, block, advice, seconds)


def fail(message):
    print("Error: " + message, file=sys.stderr)
    sys.exit(1)


def load_config(path):
    if not os.path.isfile(path):
        fail("config file not found: %s" % path)
    with open(path, encoding="utf-8") as f:
        user = yaml.safe_load(f) or {}
    config = dict(DEFAULT_CONFIG)
    for key, value in user.items():
        if key == "levels" and isinstance(value, dict):
            merged = {lv: dict(spec) for lv, spec in DEFAULT_LEVELS.items()}
            for lv, spec in value.items():
                merged.setdefault(lv, {}).update(spec or {})
            config[key] = merged
        elif isinstance(value, dict) and isinstance(config.get(key), dict):
            merged = dict(config[key])
            merged.update(value)
            config[key] = merged
        else:
            config[key] = value
    return config


def load_study(path, participant, levels=DEFAULT_LEVELS):
    """Read study.yaml and build this participant's trials.

    Returns (trials, statements, sequence number). trials = {level: [trial, ...]} in the
    order presented; a trial is a dict with block, correct, rule, advice, source.
    """
    m = re.match(r"^([AB])(\d+)$", participant)
    if not m or int(m.group(2)) < 1:
        print("Unknown participant '%s'. Use A01, A02, ... (screen version) or B01, B02, ... "
              "(robot version)." % participant)
        sys.exit(1)
    source = VERSIONS[m.group(1)]
    if not os.path.isfile(path):
        fail("study file not found: %s" % path)
    with open(path, encoding="utf-8") as f:
        study = yaml.safe_load(f) or {}
    rounds, sequences = study.get("rounds") or {}, study.get("sequences") or []
    if not sequences:
        fail("%s: no sequences" % path)
    # One sequence = everyone gets it; several = they rotate by participant number.
    seq_no = (int(m.group(2)) - 1) % len(sequences) + 1

    trials, statements = {}, {}
    for level in LEVELS:
        where = "%s, %s" % (os.path.basename(path), level)
        spec = rounds.get(level) or {}
        statements[level] = [str(x) for x in spec.get("statements") or []]
        answers = spec.get("answers") or {}
        if not statements[level]:
            fail("%s: no statements" % where)
        for block, answer in answers.items():
            if block not in BLOCKS:
                fail("%s: unknown block %r in answers" % (where, block))
            if (not isinstance(answer, list) or len(answer) != 2 or answer[0] not in BOWLS
                    or not isinstance(answer[1], int) or not 1 <= answer[1] <= len(statements[level])):
                fail("%s: answer of %r must be [red or blue, statement number]" % (where, block))
        # Check all sequences, not only this participant's, so a typo shows up at once.
        for k, seq in enumerate(sequences, start=1):
            part = (seq or {}).get(level) or {}
            order, wrong = part.get("order") or [], part.get("wrong") or []
            w = "%s, sequence %d" % (where, k)
            n = int(levels[level]["questions"])
            if len(order) != n:
                fail("%s: expected %d blocks, found %d" % (w, n, len(order)))
            for block in order:
                if block not in answers:
                    fail("%s: block %r has no answer in rounds.%s.answers" % (w, block, level))
            if len(set(order)) != len(order):
                fail("%s: a block appears twice" % w)
            if any(not isinstance(x, int) or not 2 <= x <= n for x in wrong) or len(set(wrong)) != len(wrong):
                fail("%s: wrong must be block numbers from 2 to %d, found %r" % (w, n, wrong))
            if k == seq_no:
                trials[level] = []
                for q, block in enumerate(order, start=1):
                    correct, rule = answers[block]
                    other = "blue" if correct == "red" else "red"
                    trials[level].append({"block": block, "correct": correct, "rule": rule,
                                          "advice": other if q in wrong else correct, "source": source})
    return trials, statements, seq_no


class App(object):
    def __init__(self, root, participant, trials, statements, config, robot,
                 start_level=None, start_question=1):
        self.root = root  # the experimenter window
        self.participant = participant
        self.trials = trials
        self.statements = statements
        self.config = config
        self.robot = robot
        self.levels = [lv for lv in LEVELS if lv in trials]
        self.version = VERSIONS[participant[0]]
        if start_level is None or start_level not in self.levels:
            start_level = self.levels[0]
        self.level_index = self.levels.index(start_level)
        self.question = start_question
        self.state = "waiting"
        self.busy = False  # True while a robot call runs; Enter and Space are ignored
        self.pointing = False
        self.robot_error = None
        self.countdown_job = None
        self.remaining = 0
        self.advice_start = None  # stopwatch start (time.monotonic) while advice is shown
        self.times = []  # (level, question, source, block, advice, seconds)
        self.summary_printed = False
        self.records = []         # one dict per block whose advice was shown
        self.pending = None       # record of the current block until the next block starts
        self.dry = isinstance(robot, FakeRobot) or getattr(robot, "dry_run", False)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.data_path = os.path.join(DATA_DIR, "%s%s_%s.csv" % ("DRYRUN_" if self.dry else "", participant, stamp))
        # Started from the beginning: older files of this participant move to data/old/ at the
        # first save. Resumed (--start-level / --start-question): continue in the latest file.
        self.archive_pending = self.level_index == 0 and start_question == 1
        if not self.archive_pending:
            self.resume_from_file(start_question)
        self.last_printed = ""

        self._build_experimenter()
        self._build_participant()

        # All keys work whichever window has the focus (with one monitor the
        # experimenter window is hidden behind the fullscreen participant window).
        for window in (self.root, self.pwin):
            window.bind("<Return>", self.on_enter)
            window.bind("<KP_Enter>", self.on_enter)
            window.bind("<space>", self.on_space)
            window.bind("<Escape>", self.on_escape)
        self.root.protocol("WM_DELETE_WINDOW", self.on_escape)
        self.pwin.protocol("WM_DELETE_WINDOW", self.on_escape)
        # Keyboard focus on the window in front; the experimenter window stays on top so its
        # Continue button can always be clicked (it belongs on the experimenter monitor).
        self.root.attributes("-topmost", True)
        self.root.after(300, self.pwin.focus_force)

        self.show_waiting()

    # ---------- windows ----------

    def font(self, size_key, weight="normal"):
        return (self.config["font_family"], int(self.config["font_sizes"][size_key]), weight)

    def _build_participant(self):
        win = tk.Toplevel(self.root)
        win.title("Participant")
        win.configure(bg=BG)
        x = int(self.config.get("participant_monitor_x_offset") or 0)
        if self.config["fullscreen"]:
            win.geometry("+%d+0" % x)
            win.update_idletasks()
            win.attributes("-fullscreen", True)
            try:
                win.configure(cursor="none")
            except tk.TclError:
                pass
        else:
            win.geometry("1280x800+%d+40" % x)
        self.pwin = win

        # Drawing layer behind the text: progress dots, advice icons, loading robot.
        self.canvas = tk.Canvas(win, bg=BG, highlightthickness=0)
        self.canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.anim_job = None
        self.fade_job = None

        wrap = int(win.winfo_screenwidth() * 0.8)
        self.labels = {}
        for name, size_key, rely, anchor in LAYOUT:
            label = tk.Label(win, text="", bg=CARD, fg=FG, font=self.font(size_key),
                             wraplength=wrap, justify="center")
            label.place(relx=0.5, rely=rely, anchor=anchor)
            label.place_info_saved = dict(relx=0.5, rely=rely, anchor=anchor)
            self.labels[name] = label
        self.labels["statements"].configure(justify="left")
        self.colors = {name: FG for name in self.labels}
        self.colors.update(hint=MUTED, title=ACCENT, counter=ACCENT, countdown=ACCENT)
        for name, label in self.labels.items():
            label.configure(fg=self.colors[name])
        for name in ("title", "counter", "countdown"):
            self.labels[name].configure(font=self.font(dict((n, k) for n, k, _, _ in LAYOUT)[name], "bold"))
        self.advice_text = ""   # screen advice drawn on the canvas (two colors), for screen_text()

    def _build_experimenter(self):
        r = self.root
        r.title("Experimenter")
        r.geometry("+40+40")
        self.exp_values = {}
        for row, (key, name) in enumerate(EXPERIMENTER_FIELDS):
            tk.Label(r, text=name + ":", font=self.font("experimenter"), anchor="w").grid(
                row=row, column=0, sticky="w", padx=(10, 4), pady=1)
            value = tk.Label(r, text="", font=self.font("experimenter", "bold"), anchor="w")
            value.grid(row=row, column=1, sticky="w", padx=(4, 10), pady=1)
            self.exp_values[key] = value
        self.exp_reminder = tk.Label(r, text="", font=self.font("experimenter", "bold"), fg="#B00000",
                                     wraplength=420, justify="left", anchor="w")
        self.exp_reminder.grid(row=len(EXPERIMENTER_FIELDS), column=0, columnspan=2,
                               sticky="w", padx=10, pady=(8, 4))
        buttons = tk.Frame(r)
        buttons.grid(row=len(EXPERIMENTER_FIELDS) + 1, column=0, columnspan=2, sticky="w", padx=10, pady=(4, 4))
        # takefocus=0: the buttons never hold the keyboard focus, so Space is not handled twice.
        self.continue_button = tk.Button(buttons, text="Continue (Space)", font=self.font("experimenter", "bold"),
                                         takefocus=0, command=self.on_space)
        self.continue_button.pack(side="left")
        tk.Button(buttons, text="Quit (Esc)", font=self.font("experimenter"), takefocus=0,
                  command=self.on_escape).pack(side="left", padx=(10, 0))
        # Ends the current part (practice, Round 1 or Round 2) at once; only when the robot is still.
        self.skip_button = tk.Button(buttons, text="Skip round", font=self.font("experimenter"), takefocus=0,
                                     command=self.on_skip)
        self.skip_button.pack(side="left", padx=(10, 0))
        # The bowl the participant chose. Must be clicked before the next block can start.
        choice = tk.Frame(r)
        choice.grid(row=len(EXPERIMENTER_FIELDS) + 4, column=0, columnspan=2, sticky="w", padx=10, pady=(4, 4))
        tk.Label(choice, text="Participant chose:", font=self.font("experimenter", "bold")).pack(side="left")
        self.choice_buttons = []
        for bowl, color in (("red", "#D64545"), ("blue", "#3B6FD8")):
            b = tk.Button(choice, text="%s bowl" % bowl.capitalize(), font=self.font("experimenter", "bold"),
                          fg="white", bg=color, activebackground=color, takefocus=0,
                          command=lambda bowl=bowl: self.on_choice(bowl))
            b.pack(side="left", padx=(8, 0))
            self.choice_buttons.append(b)
        self.undo_button = tk.Button(choice, text="Undo choice", font=self.font("experimenter"), takefocus=0,
                                     command=lambda: self.on_choice(None))
        self.undo_button.pack(side="left", padx=(8, 0))
        tk.Label(r, text="Decision times (advice shown -> Enter):", font=self.font("experimenter", "bold"),
                 anchor="w").grid(row=len(EXPERIMENTER_FIELDS) + 2, column=0, columnspan=2,
                                  sticky="w", padx=10, pady=(8, 0))
        size = int(self.config["font_sizes"]["experimenter"])
        self.exp_times = tk.Label(r, text="(none yet)", font=("Courier", size), anchor="w", justify="left")
        self.exp_times.grid(row=len(EXPERIMENTER_FIELDS) + 3, column=0, columnspan=2,
                            sticky="w", padx=10, pady=(0, 10))

    def set_screen(self, icon=None, progress=False, bowl=None, **texts):
        """Show the given texts on the participant screen; every other label is cleared.

        icon: None, "bowl" (screen advice, needs bowl="red"/"blue"), "robot" (robot advice)
        or "loading" (moving robot arm). progress: draw the progress dots of this level.
        With icon="bowl" the advice text is drawn with the bowl word in the bowl color.
        """
        self.advice_text = ""
        for name, label in self.labels.items():
            label.configure(text=texts.get(name, ""))
        if icon == "bowl" and self.animations():
            self.advice_text = texts.get("advice", "")
            self.labels["advice"].configure(text="")
        # An empty label would still draw a thin strip over the drawings: hide it.
        for label in self.labels.values():
            if label.cget("text"):
                label.place(**label.place_info_saved)
            else:
                label.place_forget()
        self.draw(icon, progress, bowl)
        self.fade_in()
        self.update_experimenter()

    # ---------- drawings and animation ----------

    def animations(self):
        return bool(self.config.get("animations", True))

    def draw(self, icon, progress, bowl=None):
        if self.anim_job is not None:
            self.root.after_cancel(self.anim_job)
            self.anim_job = None
        self.canvas.delete("all")
        self.fade_items = []
        if not self.animations():
            return
        self.pwin.update_idletasks()
        w, h = max(self.canvas.winfo_width(), 400), max(self.canvas.winfo_height(), 300)
        size = h * 0.13
        self.draw_card(w, h)
        if progress:
            self.draw_progress(w, h)
        if icon == "bowl":
            self.draw_bowl(w * 0.5, h * 0.40, size, bowl)
            self.draw_advice_text(w * 0.5, h * 0.58, bowl)
        elif icon == "robot":
            self.draw_robot(w * 0.5, h * 0.40, size, ACCENT, 0.35, 1.7)
        elif icon == "loading":
            self.animate_robot(w * 0.5, h * 0.62, size * 0.9, 0.0)

    def draw_card(self, w, h):
        """White rounded card with a soft shadow, on the lavender background."""
        x0, y0, x1, y1, r = w * 0.06, h * 0.025, w * 0.94, h * 0.98, h * 0.04

        def rounded(dx, dy, color):
            pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
                   x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
            pts = [v + (dx if i % 2 == 0 else dy) for i, v in enumerate(pts)]
            return self.canvas.create_polygon(pts, smooth=True, fill=color, outline=color)
        rounded(0, h * 0.008, SHADOW)
        rounded(0, 0, CARD)

    def draw_advice_text(self, cx, cy, bowl):
        """The hint sentence in dark gray with the bowl word bold in the bowl color, centered."""
        text = self.advice_text
        i = text.find(bowl)
        head, word, tail = text[:i], text[i:i + len(bowl)], text[i + len(bowl):]
        font = tkfont.Font(font=self.font("text"))
        bold = tkfont.Font(font=self.font("text", "bold"))
        x = cx - (font.measure(head) + bold.measure(word) + font.measure(tail)) / 2
        for part, f, color in ((head, font, FG), (word, bold, BOWL_COLORS[bowl][0]), (tail, font, FG)):
            if part:
                item = self.canvas.create_text(x, cy, text=part, anchor="w", font=f, fill=color)
                self.fade_items.append((item, {"fill": color}))
            x += f.measure(part)

    def draw_progress(self, w, h):
        n, r, gap = self.n_questions(), h * 0.011, h * 0.045
        counter = self.labels["counter"]
        y = counter.winfo_y() + counter.winfo_reqheight() + r * 2.5     # just below the counter line
        x0 = w * 0.5 - gap * (n - 1) / 2
        for i in range(n):
            q = i + 1
            fill = ACCENT if q <= self.question else CARD
            outline = ACCENT if q <= self.question else MUTED
            item = self.canvas.create_oval(x0 + i * gap - r, y - r, x0 + i * gap + r, y + r,
                                           fill=fill, outline=outline, width=2)
            self.fade_items.append((item, {"fill": fill, "outline": outline}))

    def draw_bowl(self, cx, cy, s, bowl):
        """Bowl in the advised color: filled body, lighter inside, small foot."""
        main, inside = BOWL_COLORS[bowl]
        body = [(-1.05, 0.0), (-0.98, 0.32), (-0.78, 0.58), (-0.42, 0.74), (0.42, 0.74), (0.78, 0.58),
                (0.98, 0.32), (1.05, 0.0)]
        items = [
            (self.canvas.create_rectangle(cx - s * 0.32, cy + s * 0.72, cx + s * 0.32, cy + s * 0.84,
                                          fill=main, outline=main), {"fill": main, "outline": main}),
            (self.canvas.create_polygon([v for x, y in body for v in (cx + x * s, cy + y * s)], smooth=True,
                                        fill=main, outline=main), {"fill": main, "outline": main}),
            (self.canvas.create_oval(cx - s * 1.05, cy - s * 0.2, cx + s * 1.05, cy + s * 0.2, fill=inside,
                                     outline=main, width=max(3, int(s * 0.05))), {"fill": inside, "outline": main}),
        ]
        self.fade_items += items

    def draw_robot(self, cx, cy, s, color, a1, a2, fade=True):
        """Simple robot arm: base, two links, two-finger gripper. a1, a2 = joint angles (rad)."""
        lw = max(4, int(s * 0.07))
        base = (cx, cy + s * 0.65)
        p1 = (base[0] + s * 0.75 * math.sin(a1), base[1] - s * 0.75 * math.cos(a1))
        p2 = (p1[0] + s * 0.65 * math.sin(a1 + a2), p1[1] - s * 0.65 * math.cos(a1 + a2))
        d = (math.sin(a1 + a2), -math.cos(a1 + a2))           # gripper direction
        n = (-d[1], d[0])                                     # across the gripper
        g = s * 0.14
        items = [
            self.canvas.create_line(cx - s * 0.5, base[1] + s * 0.08, cx + s * 0.5, base[1] + s * 0.08,
                                    fill=color, width=lw * 2, capstyle="round"),
            self.canvas.create_line(base[0], base[1], p1[0], p1[1], fill=color, width=lw, capstyle="round"),
            self.canvas.create_line(p1[0], p1[1], p2[0], p2[1], fill=color, width=lw, capstyle="round"),
            self.canvas.create_line(p2[0] + n[0] * g, p2[1] + n[1] * g, p2[0] + n[0] * g + d[0] * g * 1.3,
                                    p2[1] + n[1] * g + d[1] * g * 1.3, fill=color, width=lw, capstyle="round"),
            self.canvas.create_line(p2[0] - n[0] * g, p2[1] - n[1] * g, p2[0] - n[0] * g + d[0] * g * 1.3,
                                    p2[1] - n[1] * g + d[1] * g * 1.3, fill=color, width=lw, capstyle="round"),
            self.canvas.create_line(p2[0] - n[0] * g, p2[1] - n[1] * g, p2[0] + n[0] * g, p2[1] + n[1] * g,
                                    fill=color, width=lw, capstyle="round"),
        ]
        for x, y in (base, p1):
            items.append(self.canvas.create_oval(x - lw, y - lw, x + lw, y + lw, fill=CARD, outline=color, width=lw // 2 + 1))
        if fade:
            for i in items:   # joints: hollow circles; everything else: lines
                self.fade_items.append((i, {"fill": CARD, "outline": color} if self.canvas.type(i) == "oval"
                                       else {"fill": color}))
        return items

    def animate_robot(self, cx, cy, s, t):
        """Loading animation: the robot arm swings gently while the real robot works."""
        self.canvas.delete("loading")
        items = self.draw_robot(cx, cy, s, ACCENT, 0.45 * math.sin(t), 1.2 + 0.35 * math.sin(t * 1.7), fade=False)
        for i in items:
            self.canvas.addtag_withtag("loading", i)
        self.anim_job = self.root.after(40, self.animate_robot, cx, cy, s, t + 0.08)

    def fade_in(self, step=0):
        """Fade the texts and drawings in from the background color."""
        if self.fade_job is not None and step == 0:
            self.root.after_cancel(self.fade_job)
        self.fade_job = None
        steps = 8 if self.animations() and int(self.config.get("fade_ms", 0)) > 0 else 0
        k = 1.0 if steps == 0 else min(1.0, step / float(steps))
        for name, label in self.labels.items():
            label.configure(fg=blend(CARD, self.colors[name], k))
        for item, colors in getattr(self, "fade_items", []):
            try:
                self.canvas.itemconfigure(item, **{opt: blend(CARD, c, k) for opt, c in colors.items()})
            except tk.TclError:
                pass
        if k < 1.0:
            self.fade_job = self.root.after(int(self.config["fade_ms"]) // steps, self.fade_in, step + 1)

    def fade_ms(self):
        return int(self.config.get("fade_ms", 0)) if self.animations() else 0

    def screen_text(self):
        """Plain text dump of the participant screen (used for testing)."""
        parts = []
        for name, _, _, _ in LAYOUT:
            text = self.labels[name].cget("text") or (self.advice_text if name == "advice" else "")
            if text:
                parts.append("[%s] %s" % (name, text))
        return "\n".join(parts)

    def update_experimenter(self):
        t = self.trial()
        step = STEP_NAMES[self.state]
        if self.busy:
            step += " (robot moving, Enter ignored)"
        if self.robot_error is not None:
            step = "ROBOT ERROR: %s" % self.robot_error
        values = {
            "participant": self.participant,
            "version": self.version,
            "round": "%s (%s)" % (ROUND_NAMES[self.level], self.spec()["label"]),
            "question": "%d of %d" % (self.question, self.n_questions()),
            "block": t["block"],
            "correct": t["correct"],
            "advice": t["advice"] + ("   (WRONG on purpose)" if t["advice"] != t["correct"] else ""),
            "chosen": self.choice_text(),
            "step": step,
            "timer": self.timer_text(),
        }
        for key, value in values.items():
            self.exp_values[key].configure(text=value)
        if self.times:
            self.exp_times.configure(text="\n".join(format_time_row(row) for row in self.times))

        reminder = ""
        if self.state == "waiting":
            reminder = "Press Space to start."
        elif self.state == "level_complete" and self.level == "practice":
            reminder = ("Practice done. Reload the blocks, answer the participant's questions, "
                        "then press Space to start %s" % self.spec(self.levels[self.level_index + 1])["label"])
        elif self.state == "level_complete":
            if self.choice_missing():
                print("[UI] Click the bowl the participant chose first (Red bowl or Blue bowl).")
                return
            self.pending = None
            if self.level_index + 1 < len(self.levels):
                reminder = ("Reload the blocks, give paper questionnaire %s, then press Space"
                            % ROUND_NAMES[self.level])
            else:
                reminder = ("Give paper questionnaire %s, then press Space for the debrief"
                            % ROUND_NAMES[self.level])
        elif self.state == "debrief":
            reminder = "Session finished. Press Escape to quit."
        self.exp_reminder.configure(text=reminder)
        can_continue = self.state in ("waiting", "level_complete") and not self.busy
        self.continue_button.configure(state="normal" if can_continue else "disabled")
        can_choose = self.pending is not None and self.robot_error is None
        for b in self.choice_buttons + [self.undo_button]:
            b.configure(state="normal" if can_choose else "disabled")
        if self.choice_missing() and self.state in ("idle", "level_complete"):
            reminder = "Click the bowl the participant chose first (Red bowl or Blue bowl). " + reminder
            self.exp_reminder.configure(text=reminder)
        can_skip = self.state in ("statements", "idle", "advice", "explain") and not self.busy and self.robot_error is None
        self.skip_button.configure(state="normal" if can_skip else "disabled")
        if can_continue and reminder != self.last_printed:
            print("[UI] " + reminder.replace("press Space", "press Space or click Continue")
                  .replace("Press Space", "Press Space or click Continue"))
        self.last_printed = reminder

    # ---------- state ----------

    @property
    def level(self):
        return self.levels[self.level_index]

    def spec(self, level=None):
        return self.config["levels"][level or self.level]

    def n_questions(self):
        return int(self.spec()["questions"])

    def trial(self):
        return self.trials[self.level][self.question - 1]

    def counter_text(self):
        return "%s  \u00b7  Block %d of %d" % (self.spec()["label"], self.question, self.n_questions())

    # ---------- stopwatch ----------

    def start_stopwatch(self):
        self.pwin.update_idletasks()  # advice is drawn before the clock starts
        self.advice_start = time.monotonic()
        self.update_experimenter()
        self._tick_stopwatch()

    def _tick_stopwatch(self):
        if self.advice_start is None:
            return
        self.exp_values["timer"].configure(text=self.timer_text())
        self.root.after(100, self._tick_stopwatch)

    def stop_stopwatch(self):
        if self.advice_start is None:
            return
        seconds = time.monotonic() - self.advice_start
        self.advice_start = None
        t = self.trial()
        row = (self.level, self.question, t["source"], t["block"], t["advice"], seconds)
        self.times.append(row)
        if self.pending is not None:
            self.pending["time_s"] = round(seconds, 3)
            self.save()
        print("Decision time: " + format_time_row(row))

    def timer_text(self):
        if self.advice_start is not None:
            return "%.1f s (running)" % (time.monotonic() - self.advice_start)
        if self.times:
            return "last: %.2f s" % self.times[-1][5]
        return "-"

    def print_summary(self):
        if self.summary_printed or not self.times:
            return
        self.summary_printed = True
        print("\nDecision times for %s (advice shown -> Enter):" % self.participant)
        for row in self.times:
            print("  " + format_time_row(row))

    # ---------- keys ----------

    def on_enter(self, event=None):
        if self.busy:
            return
        if self.state in ("statements", "idle"):
            if self.choice_missing():
                print("[UI] Click the bowl the participant chose first (Red bowl or Blue bowl).")
                return
            self.pending = None
            self.start_question()
        elif self.state == "advice":
            if self.advice_start is None:
                return   # advice still fading in, the clock has not started yet
            self.stop_stopwatch()
            self.finish_question()
        elif self.state == "explain":
            self.next_question()

    def on_space(self, event=None):
        if self.busy:
            return
        if self.state == "waiting":
            self.start_level()
        elif self.state == "level_complete":
            if self.level_index + 1 < len(self.levels):
                self.level_index += 1
                self.question = 1
                self.start_level()
            else:
                self.show_debrief()

    def choice_missing(self):
        return self.pending is not None and self.pending["chosen"] is None

    def choice_text(self):
        if self.pending is None:
            return "-"
        return self.pending["chosen"] or "(click Red bowl or Blue bowl)"

    def on_choice(self, bowl):
        """bowl = "red"/"blue" from the buttons, None = Undo."""
        if self.pending is None:
            return
        self.pending["chosen"] = bowl
        print("[UI] %s Q%d %s: participant chose %s" % (self.pending["level"], self.pending["question"],
                                                       self.pending["block"], bowl or "(undone)"))
        self.save()
        self.update_experimenter()

    def session_files(self):
        """This participant's data files (dry runs and real sessions are kept apart)."""
        prefix = ("DRYRUN_" if self.dry else "") + self.participant + "_"
        return sorted(p for p in glob.glob(os.path.join(DATA_DIR, prefix + "*"))
                      if os.path.isfile(p) and not p.endswith(".tmp") and ".tmp." not in p)

    def resume_from_file(self, start_question):
        """Keep the blocks before the resume point from the latest file and continue in it."""
        files = [p for p in self.session_files() if p.endswith(".csv")]
        if not files:
            return
        self.data_path = files[-1]
        order = {lv: i for i, lv in enumerate(self.levels)}
        keys = {label: key for key, label in DATA_COLUMNS}
        level_of = {name: lv for lv, name in ROUND_NAMES.items()}
        with open(self.data_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                r = {keys.get(k, k): v for k, v in row.items()}
                if "question" not in r or not r["question"]:
                    continue
                r["question"] = int(str(r["question"]).split()[0])
                r["level"] = level_of.get(r.get("round"))
                if r.get("level") not in order or (order[r["level"]], r["question"]) >= (self.level_index, start_question):
                    continue   # this block is run again now
                r["time_s"] = float(r["time_s"]) if r.get("time_s") else None
                self.records.append(r)
        print("[UI] Resuming: continuing in %s (%d earlier blocks kept)"
              % (os.path.basename(self.data_path), len(self.records)))

    def archive_old_files(self):
        """Move this participant's older files to data/old/ (never deletes anything)."""
        mine = os.path.splitext(self.data_path)[0]
        old = [p for p in self.session_files() if os.path.splitext(p)[0] != mine]
        if not old:
            return
        old_dir = os.path.join(DATA_DIR, "old")
        os.makedirs(old_dir, exist_ok=True)
        for p in old:
            target, n = os.path.join(old_dir, os.path.basename(p)), 1
            while os.path.exists(target):
                root, ext = os.path.splitext(os.path.basename(p))
                target, n = os.path.join(old_dir, "%s_%d%s" % (root, n, ext)), n + 1
            shutil.move(p, target)
        print("[UI] Started %s again: %d older file(s) moved to data/old/" % (self.participant, len(old)))

    def data_rows(self):
        """One row per answered block, values as shown in the experimenter window."""
        rows = []
        for r in self.records:
            if r["chosen"] is None:
                continue
            row = {key: r.get(key, "") for key, _ in DATA_COLUMNS}
            row["question"] = int(r["question"])
            row["hint_correct"] = "yes" if r["advice"] == r["correct"] else "no"
            row["followed"] = "yes" if r["chosen"] == r["advice"] else "no"
            row["time_s"] = "" if r["time_s"] is None else round(r["time_s"], 2)
            rows.append(row)
        return rows

    def save(self):
        """Write every answered block to data/ as CSV and Excel, rewritten after every choice
        through a temporary file, so a crash never leaves a half-written file."""
        rows = self.data_rows()
        if not rows:
            return
        os.makedirs(DATA_DIR, exist_ok=True)
        if self.archive_pending:
            self.archive_pending = False
            self.archive_old_files()
        tmp = self.data_path + ".tmp"
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([label for _, label in DATA_COLUMNS])
            for row in rows:
                w.writerow([row[key] for key, _ in DATA_COLUMNS])
        os.replace(tmp, self.data_path)
        try:
            self.save_excel(rows)
        except Exception as e:   # the CSV is the main record; never stop the session for Excel
            print("[UI] Excel file not written (%s); the CSV is saved." % e, file=sys.stderr)

    def save_excel(self, rows):
        """The same table as the CSV, with a bold header row."""
        from openpyxl import Workbook
        from openpyxl.styles import Font
        wb = Workbook()
        ws = wb.active
        ws.title = "Data"
        ws.append([label for _, label in DATA_COLUMNS])
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for row in rows:
            ws.append([row[key] for key, _ in DATA_COLUMNS])
        for col, (_, label) in enumerate(DATA_COLUMNS):
            ws.column_dimensions[chr(65 + col)].width = max(12, len(label) + 4)
        ws.freeze_panes = "A2"
        path = os.path.splitext(self.data_path)[0] + ".xlsx"
        tmp = path + ".tmp.xlsx"
        wb.save(tmp)
        os.replace(tmp, path)

    def on_skip(self):
        """Skip the rest of the current level: go straight to its end screen."""
        if self.busy or self.state not in ("statements", "idle", "advice", "explain"):
            return
        label = self.spec()["label"]
        if not messagebox.askyesno("Skip", "Skip the rest of %s?" % label, parent=self.root):
            return
        if self.countdown_job is not None:
            self.root.after_cancel(self.countdown_job)
            self.countdown_job = None
        self.advice_start = None   # the current decision is not timed
        if self.choice_missing():   # skipped before a choice: not saved
            self.records.remove(self.pending)
            self.pending = None
        self.pointing = False      # the push cue already ends with the arm at home
        print("[UI] Skipped the rest of %s at block %d of %d" % (label, self.question, self.n_questions()))
        self.show_level_complete()

    def on_escape(self, event=None):
        # Ask on top of the window that got the key, so the dialog is never hidden.
        parent = event.widget.winfo_toplevel() if event is not None else self.root
        if messagebox.askyesno("Quit", "Quit the study UI?", parent=parent):
            self.print_summary()
            self.root.destroy()

    # ---------- screens ----------

    def show_waiting(self):
        self.state = "waiting"
        self.set_screen(status="Please wait", hint="Experimenter: press Space to start")

    def start_level(self):
        # Resuming in the middle of a level skips the statements screen.
        if self.question == 1:
            self.show_statements()
        else:
            self.show_idle()

    def show_statements(self):
        self.state = "statements"
        lines = ["%d. %s" % (i + 1, s) for i, s in enumerate(self.statements[self.level])]
        self.set_screen(title="Sorting rules", statements="\n".join(lines),
                        prompt="Press Enter when you are ready")
        limit = self.spec().get("reading_time_s")   # None = unlimited (Enter only)
        if limit:
            self.remaining = int(limit)
            self._countdown()

    def _countdown(self):
        if self.state != "statements":
            return
        if self.remaining <= 0:
            self.countdown_job = None
            self.start_question()
            return
        label = self.labels["countdown"]
        label.configure(text="Time left: %d s" % self.remaining)
        label.place(**label.place_info_saved)   # it was hidden while empty
        self.remaining -= 1
        self.countdown_job = self.root.after(1000, self._countdown)

    def show_idle(self):
        self.state = "idle"
        self.set_screen(progress=True, counter=self.counter_text(), status="Press Enter for the next block")

    def start_question(self):
        if self.countdown_job is not None:
            self.root.after_cancel(self.countdown_job)
            self.countdown_job = None
        self.state = "presenting"
        self.set_screen(icon="loading", progress=True, counter=self.counter_text(),
                        status="Robot presenting the block to you")
        t = self.trial()
        # Robot trials: the arm stays up after placing and the push starts from there.
        self.run_robot([("pick", t["block"]), ("place_decision", t["source"] == "screen")], self.show_advice)

    def show_advice(self):
        self.state = "advice"
        t = self.trial()
        self.pending = {"participant": self.participant, "version": self.version,
                        "round": ROUND_NAMES[self.level], "level": self.level, "question": self.question,
                        "block": t["block"], "advice": t["advice"],
                        "correct": t["correct"], "chosen": None, "time_s": None}
        self.records.append(self.pending)
        # Both sources get an icon of the same size, style and gray color in the same place.
        if t["source"] == "screen":
            self.set_screen(icon="bowl", bowl=t["advice"], progress=True, counter=self.counter_text(),
                            advice="I suggest the %s bowl." % t["advice"])
            # The stopwatch starts once the advice is fully faded in.
            self.root.after(self.fade_ms(), self.start_stopwatch)
        else:
            # Robot trial: the screen never shows the bowl, the robot gives the cue.
            # The stopwatch starts when point() returns.
            self.set_screen(icon="robot", progress=True, counter=self.counter_text(), advice="Robot giving advice")
            self.pointing = True
            self.run_robot([("point", t["advice"])], self.start_stopwatch)

    def finish_question(self):
        if self.pointing:
            self.state = "homing"
            self.set_screen(icon="loading", progress=True, counter=self.counter_text())
            self.run_robot([("home",)], self.after_home)
        else:
            self.after_answer()

    def after_home(self):
        self.pointing = False
        self.after_answer()

    def after_answer(self):
        if self.level == "practice":
            self.show_explanation()
        else:
            self.next_question()

    def show_explanation(self):
        """Practice only: the correct bowl and the statement that decides it."""
        self.state = "explain"
        t = self.trial()
        self.set_screen(progress=True, counter=self.counter_text(),
                        status="The correct bowl is the %s bowl." % t["correct"],
                        advice="Rule %d: %s" % (t["rule"], self.statements[self.level][t["rule"] - 1]),
                        prompt="Press Enter to continue")

    def next_question(self):
        if self.question < self.n_questions():
            self.question += 1
            self.show_idle()
        else:
            self.show_level_complete()

    def show_level_complete(self):
        self.state = "level_complete"
        if self.level == "practice":
            self.set_screen(status="Practice complete. Please wait.",
                            hint="Experimenter: press Space to start %s"
                                 % self.spec(self.levels[self.level_index + 1])["label"])
        elif self.level_index == len(self.levels) - 1:
            self.set_screen(status="Thank you. Please wait.",
                            hint="Experimenter: press Space for the debrief")
        else:
            self.set_screen(status="Round complete. Please wait.",
                            hint="Experimenter: press Space to start the next round")

    def show_debrief(self):
        self.state = "debrief"
        self.set_screen(status=str(self.config["debrief_text"]).strip())
        self.print_summary()

    # ---------- robot ----------

    def run_robot(self, calls, then):
        """Run robot calls in a thread so the windows stay responsive, then call `then`."""
        self.busy = True
        self.update_experimenter()
        result = {}

        def work():
            try:
                for call in calls:
                    getattr(self.robot, call[0])(*call[1:])
            except Exception as e:
                result["error"] = e

        thread = threading.Thread(target=work)
        thread.daemon = True
        thread.start()
        self._wait_robot(thread, result, then)

    def _wait_robot(self, thread, result, then):
        if thread.is_alive():
            self.root.after(30, self._wait_robot, thread, result, then)
            return
        if "error" in result:
            # Stay busy: the session cannot go on safely. The experimenter quits with Escape.
            self.robot_error = result["error"]
            print("Robot error: %r" % (self.robot_error,), file=sys.stderr)
            self.update_experimenter()
            return
        self.busy = False
        then()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Participant UI for the HRI robot trust study.")
    parser.add_argument("--participant", required=True,
                        help="participant ID: A01, A02, ... (screen version) or B01, B02, ... (robot version)")
    parser.add_argument("--dry-run", action="store_true", help="use the fake robot (no ROS, no robot)")
    parser.add_argument("--ros-dry-run", action="store_true",
                        help="real robot adapter, but only print each call; sends nothing, needs no ROS")
    parser.add_argument("--study", default=os.path.join(HERE, "study.yaml"))
    parser.add_argument("--config", default=os.path.join(HERE, "config.yaml"))
    parser.add_argument("--start-level", choices=LEVELS, default=None,
                        help="round to start at: practice, round1 or round2 (default: practice)")
    parser.add_argument("--start-question", type=int, default=1,
                        help="question to resume at (1-%d)" % QUESTIONS_PER_LEVEL)
    args = parser.parse_args(argv)
    if args.dry_run and args.ros_dry_run:
        parser.error("use --dry-run or --ros-dry-run, not both")
    if not 1 <= args.start_question <= QUESTIONS_PER_LEVEL:
        parser.error("--start-question must be between 1 and %d" % QUESTIONS_PER_LEVEL)

    participant = args.participant.strip().upper()
    config = load_config(args.config)
    trials, statements, seq_no = load_study(args.study, participant, config["levels"])
    print("%s: %s version, sequence %d" % (participant, VERSIONS[participant[0]], seq_no))

    if args.dry_run:
        print("Dry run: fake robot, no ROS.")
        robot = FakeRobot(config["fake_robot"])
    elif args.ros_dry_run:
        robot = RosRobot(config, dry_run=True)
    else:
        robot = RosRobot(config)   # starts ROS and MoveIt here, in the main thread, before tkinter

    root = tk.Tk()
    App(root, participant, trials, statements, config, robot, args.start_level, args.start_question)
    root.mainloop()


if __name__ == "__main__":
    main()
