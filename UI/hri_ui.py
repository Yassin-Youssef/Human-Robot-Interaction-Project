#!/usr/bin/env python3
"""Participant UI for the HRI robot trust study (see README_UI.md).

Participant window: fullscreen, the participant only presses Enter.
Experimenter window: read-only, shows the current trial and the decision times.
Space = continue, Escape = quit (both work in either window).
Decision time = advice fully shown (screen text, or robot holding the point) until Enter.
Times are shown in the experimenter window and printed in the terminal; nothing is saved to disk.

    python hri_ui.py --participant P07 --dry-run
"""
import sys

sys.dont_write_bytecode = True  # write nothing to disk, not even __pycache__

import argparse
import csv
import json
import math
import os
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox

import yaml

from robot_interface import BLOCKS, BOWLS, FakeRobot, RosRobot

HERE = os.path.dirname(os.path.abspath(__file__))
LEVELS = ["easy", "hard"]
QUESTIONS_PER_LEVEL = 6
SOURCES = ("robot", "screen")
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
    "max_reading_time_s": None,
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
    ("level", "Level"),
    ("question", "Question"),
    ("source", "Source"),
    ("block", "Block in slot"),
    ("correct", "Correct bowl"),
    ("advice", "Advice"),
    ("rated_first", "Rated first"),
    ("step", "Step"),
    ("timer", "Decision time"),
]

STEP_NAMES = {
    "waiting": "waiting",
    "statements": "statements",
    "idle": "waiting for Enter (next block)",
    "presenting": "robot presenting",
    "advice": "advice shown",
    "homing": "robot returning home",
    "level_complete": "level complete",
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
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            merged = dict(config[key])
            merged.update(value)
            config[key] = merged
        else:
            config[key] = value
    return config


def load_trials(path, participant):
    """Return {level: [row, ...]} for one participant, sorted by question."""
    if not os.path.isfile(path):
        fail("sequences file not found: %s" % path)
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    valid_ids = sorted(set(r["participant"] for r in rows))
    mine = [r for r in rows if r["participant"] == participant]
    if not mine:
        print("Unknown participant '%s'. Valid IDs: %s" % (participant, " ".join(valid_ids)))
        sys.exit(1)

    trials = {}
    for level in LEVELS:
        level_rows = sorted([r for r in mine if r["level"] == level], key=lambda r: int(r["question"]))
        questions = [int(r["question"]) for r in level_rows]
        if questions != list(range(1, QUESTIONS_PER_LEVEL + 1)):
            fail("%s %s: expected questions 1-%d, found %s" % (participant, level, QUESTIONS_PER_LEVEL, questions))
        for r in level_rows:
            where = "%s %s question %s" % (participant, level, r["question"])
            if r["block"] not in BLOCKS:
                fail("%s: unknown block %r" % (where, r["block"]))
            if r["source"] not in SOURCES:
                fail("%s: source must be robot or screen, found %r" % (where, r["source"]))
            if r["advice"] not in BOWLS or r["correct"] not in BOWLS:
                fail("%s: advice and correct must be red or blue" % where)
            if r["rated_first"] not in SOURCES:
                fail("%s: rated_first must be robot or screen, found %r" % (where, r["rated_first"]))
        trials[level] = level_rows
    return trials


def load_statements(path, trials):
    if not os.path.isfile(path):
        fail("statements file not found: %s" % path)
    with open(path, encoding="utf-8") as f:
        statements = json.load(f)
    for level in LEVELS:
        if len(statements.get(level, [])) != QUESTIONS_PER_LEVEL:
            fail("statements.json: expected %d statements for %s" % (QUESTIONS_PER_LEVEL, level))
        for r in trials[level]:
            if r["statement"] not in statements[level]:
                fail("sequences and statements.json do not match: %r" % r["statement"])
    return statements


class App(object):
    def __init__(self, root, participant, trials, statements, config, robot,
                 start_level="easy", start_question=1):
        self.root = root  # the experimenter window
        self.participant = participant
        self.trials = trials
        self.statements = statements
        self.config = config
        self.robot = robot
        self.level_index = LEVELS.index(start_level)
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
        self.colors.update(hint=MUTED, title=ACCENT, counter=ACCENT)
        for name, label in self.labels.items():
            label.configure(fg=self.colors[name])
        for name in ("title", "counter"):
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
        """'Screen recommends: ' in dark gray and the bowl word in the bowl color, centered."""
        head, word = self.advice_text.rsplit(" ", 1)
        font = tkfont.Font(font=self.font("text"))
        bold = tkfont.Font(font=self.font("text", "bold"))
        total = font.measure(head + " ") + bold.measure(word)
        x = cx - total / 2
        a = self.canvas.create_text(x, cy, text=head + " ", anchor="w", font=font, fill=FG)
        b = self.canvas.create_text(x + font.measure(head + " "), cy, text=word, anchor="w", font=bold,
                                    fill=BOWL_COLORS[bowl][0])
        self.fade_items += [(a, {"fill": FG}), (b, {"fill": BOWL_COLORS[bowl][0]})]

    def draw_progress(self, w, h):
        n, r, gap = QUESTIONS_PER_LEVEL, h * 0.011, h * 0.045
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
            "level": self.level,
            "question": "%d of %d" % (self.question, QUESTIONS_PER_LEVEL),
            "source": t["source"],
            "block": t["block"],
            "correct": t["correct"],
            "advice": t["advice"],
            "rated_first": t["rated_first"],
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
        elif self.state == "level_complete":
            if self.level_index + 1 < len(LEVELS):
                reminder = ("Reload the blocks, ask the trust ratings out loud (rated first: %s), "
                            "then press Space" % t["rated_first"])
            else:
                reminder = ("Ask the trust ratings out loud (rated first: %s), "
                            "then press Space for the debrief" % t["rated_first"])
        elif self.state == "debrief":
            reminder = "Session finished. Press Escape to quit."
        self.exp_reminder.configure(text=reminder)
        can_continue = self.state in ("waiting", "level_complete") and not self.busy
        self.continue_button.configure(state="normal" if can_continue else "disabled")
        if can_continue and reminder != self.last_printed:
            print("[UI] " + reminder.replace("press Space", "press Space or click Continue")
                  .replace("Press Space", "Press Space or click Continue"))
        self.last_printed = reminder

    # ---------- state ----------

    @property
    def level(self):
        return LEVELS[self.level_index]

    def trial(self):
        return self.trials[self.level][self.question - 1]

    def counter_text(self):
        return "Level %d  \u00b7  Block %d of %d" % (self.level_index + 1, self.question, QUESTIONS_PER_LEVEL)

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
            self.start_question()
        elif self.state == "advice":
            if self.advice_start is None:
                return   # advice still fading in, the clock has not started yet
            self.stop_stopwatch()
            self.finish_question()

    def on_space(self, event=None):
        if self.busy:
            return
        if self.state == "waiting":
            self.start_level()
        elif self.state == "level_complete":
            if self.level_index + 1 < len(LEVELS):
                self.level_index += 1
                self.question = 1
                self.start_level()
            else:
                self.show_debrief()

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
        limit = self.config.get("max_reading_time_s")
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
        self.labels["countdown"].configure(text="Time left: %d s" % self.remaining)
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
        self.run_robot([("pick", self.trial()["block"]), ("place_decision",)], self.show_advice)

    def show_advice(self):
        self.state = "advice"
        t = self.trial()
        # Both sources get an icon of the same size, style and gray color in the same place.
        if t["source"] == "screen":
            self.set_screen(icon="bowl", bowl=t["advice"], progress=True, counter=self.counter_text(),
                            advice="Screen recommends: %s" % t["advice"].upper())
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
            self.next_question()

    def after_home(self):
        self.pointing = False
        self.next_question()

    def next_question(self):
        if self.question < QUESTIONS_PER_LEVEL:
            self.question += 1
            self.show_idle()
        else:
            self.show_level_complete()

    def show_level_complete(self):
        self.state = "level_complete"
        if self.level_index == len(LEVELS) - 1:
            self.set_screen(status="Thank you. Please wait.",
                            hint="Experimenter: press Space for the debrief")
        else:
            self.set_screen(status="Level complete. Please wait.",
                            hint="Experimenter: press Space to start the next level")

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
    parser.add_argument("--participant", required=True, help="participant ID, e.g. P07")
    parser.add_argument("--dry-run", action="store_true", help="use the fake robot (no ROS, no robot)")
    parser.add_argument("--ros-dry-run", action="store_true",
                        help="real robot adapter, but only print each call; sends nothing, needs no ROS")
    parser.add_argument("--sequences", default=os.path.join(HERE, "sequences.csv"))
    parser.add_argument("--config", default=os.path.join(HERE, "config.yaml"))
    parser.add_argument("--start-level", choices=LEVELS, default="easy")
    parser.add_argument("--start-question", type=int, default=1,
                        help="question to resume at (1-%d)" % QUESTIONS_PER_LEVEL)
    args = parser.parse_args(argv)
    if args.dry_run and args.ros_dry_run:
        parser.error("use --dry-run or --ros-dry-run, not both")
    if not 1 <= args.start_question <= QUESTIONS_PER_LEVEL:
        parser.error("--start-question must be between 1 and %d" % QUESTIONS_PER_LEVEL)

    participant = args.participant.strip().upper()
    config = load_config(args.config)
    trials = load_trials(args.sequences, participant)
    statements_path = os.path.join(os.path.dirname(os.path.abspath(args.sequences)), "statements.json")
    statements = load_statements(statements_path, trials)

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
