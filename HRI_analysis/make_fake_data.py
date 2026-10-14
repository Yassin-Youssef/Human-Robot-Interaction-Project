"""
Fake data generator for the HRI trust study (for testing analyze.py and for the example results).

Usage:
    python make_fake_data.py                       -> FAKE_example_data.xlsx (40 participants, small real effects)
    python make_fake_data.py --null --n 40         -> no true effect anywhere
    python make_fake_data.py --n 60 --seed 3 --out my_fake.xlsx

As a module: generate(n, seed, null=False, **overrides) returns three DataFrames (data, trust, final)
in exactly the layout of the Excel sheets.
"""
import argparse

import numpy as np
import pandas as pd

LEVELS = ["easy", "medium", "hard"]
SOURCES = ["robot", "screen"]
BOWLS = ["red", "blue"]

# True effects used to make the fake data (all zero in null mode).
EFFECTS = {
    "know_prob": {"easy": 0.92, "medium": 0.75, "hard": 0.55},   # chance the participant knows the answer
    "follow_base": 0.60,           # chance to follow advice when not knowing the answer
    "robot_follow_bonus": 0.08,    # extra chance to follow the robot (H1 behavior)
    "hard_follow_bonus": 0.10,     # extra chance to follow in the hard level (H4)
    "after_error_drop": 0.15,      # chance to follow drops right after a wrong advice (H2)
    "person_sd": 0.5,              # personal differences in how much people follow (logit scale)
    "trust_base": 3.0,
    "trust_robot_bonus": 0.35,     # robot is trusted more (H1 trust)
    "trust_follow_link": 2.0,      # trust goes up with following (points per 100% followed)
    "trust_fooled_penalty": 0.6,   # trust goes down after following the source's wrong advice
    "trust_accuracy_link": 0.5,    # trust goes up with own correctness on that source's questions
    "trust_person_sd": 0.5,
    "trust_noise_sd": 0.6,
    "time_by_level": {"easy": 3.0, "medium": 5.0, "hard": 8.0},
}
NULL_EFFECTS = {
    "know_prob": {"easy": 0.75, "medium": 0.75, "hard": 0.75},
    "robot_follow_bonus": 0.0, "hard_follow_bonus": 0.0, "after_error_drop": 0.0,
    "trust_robot_bonus": 0.0, "trust_follow_link": 0.0, "trust_fooled_penalty": 0.0, "trust_accuracy_link": 0.0,
    "time_by_level": {"easy": 5.0, "medium": 5.0, "hard": 5.0},
}
REASONS = {
    "robot": ["The robot pointing felt more certain.", "It moved, so it seemed to know.",
              "The robot was right when I was unsure."],
    "screen": ["Text is clearer than a gesture.", "The screen never hesitated.", "I could read exactly what it meant."],
    "equal": ["Both gave the same advice.", "I could not tell them apart.", "Both were wrong once."],
}


def _answer_key(rng):
    return {lvl: list(rng.permutation(["red"] * 3 + ["blue"] * 3)) for lvl in LEVELS}


def _plan_level(rng, n_q=6):
    """3 robot + 3 screen in random order, one wrong advice per source, never Q1, never back to back."""
    while True:
        src = list(rng.permutation(["robot"] * 3 + ["screen"] * 3))
        pairs = [(a, b) for a in range(1, n_q) for b in range(a + 2, n_q)      # 0-based, Q2..Q6 -> keep to Q2..Q5
                 if src[a] != src[b] and b <= n_q - 2]
        if pairs:
            a, b = pairs[rng.integers(len(pairs))]
            return src, {a, b}


def generate(n=40, seed=1, null=False, **overrides):
    rng = np.random.default_rng(seed)
    E = {**EFFECTS, **(NULL_EFFECTS if null else {}), **overrides}
    key = _answer_key(np.random.default_rng(12345))      # same answer key for everyone
    rows, trows, frows = [], [], []
    for i in range(1, n + 1):
        pid = f"P{i:02d}"
        person_follow = rng.normal(0, E["person_sd"])
        person_trust = rng.normal(0, E["trust_person_sd"])
        robot_first = (i % 2 == 1)
        diffs = []
        for lvl in LEVELS:
            src, wrong_at = _plan_level(rng)
            cell = {s: {"follow": [], "fooled": [], "acc": []} for s in SOURCES}
            prev_wrong = False
            for q in range(6):
                correct = key[lvl][q]
                other = "blue" if correct == "red" else "red"
                advice = other if q in wrong_at else correct
                knows = rng.random() < E["know_prob"][lvl]
                p_follow = E["follow_base"] + (E["hard_follow_bonus"] if lvl == "hard" else 0) \
                    + (E["robot_follow_bonus"] if src[q] == "robot" else 0) - (E["after_error_drop"] if prev_wrong else 0)
                p_follow = 1 / (1 + np.exp(-(np.log(np.clip(p_follow, 0.02, 0.98) / (1 - np.clip(p_follow, 0.02, 0.98)))
                                             + person_follow)))
                if knows:
                    chosen = correct
                elif rng.random() < p_follow:
                    chosen = advice
                else:
                    chosen = BOWLS[rng.integers(2)]
                base_t = E["time_by_level"][lvl] * (0.7 if knows else 1.0)
                t = round(float(base_t * np.exp(rng.normal(0, 0.3))), 1)
                rows.append({"participant": pid, "level": lvl, "question": q + 1, "source": src[q], "advice": advice,
                             "correct": correct, "chosen": chosen, "time_s": max(t, 0.5)})
                c = cell[src[q]]
                c["follow"].append(chosen == advice)
                c["acc"].append(chosen == correct)
                if advice != correct:
                    c["fooled"].append(chosen == advice)
                prev_wrong = advice != correct
            rt = {}
            for s in SOURCES:
                c = cell[s]
                fr, acc = np.mean(c["follow"]), np.mean(c["acc"])
                fooled = float(np.mean(c["fooled"])) if c["fooled"] else 0.0
                mu = (E["trust_base"] + person_trust + (E["trust_robot_bonus"] if s == "robot" else 0)
                      + E["trust_follow_link"] * (fr - 0.6) - E["trust_fooled_penalty"] * fooled
                      + E["trust_accuracy_link"] * (acc - 0.75))
                rt[s] = int(np.clip(round(mu + rng.normal(0, E["trust_noise_sd"])), 1, 5))
            diffs.append(rt["robot"] - rt["screen"])
            trows.append({"participant": pid, "level": lvl, "trust_robot": rt["robot"], "trust_screen": rt["screen"],
                          "rated_first": "robot" if robot_first else "screen"})
        if null:
            choice = ["robot", "screen", "equal"][rng.integers(3)]
        else:
            m = np.mean(diffs) + rng.normal(0, 0.3)
            choice = "robot" if m > 0.25 else "screen" if m < -0.25 else "equal"
        frows.append({"participant": pid, "more_trusted": choice,
                      "reason": REASONS[choice][rng.integers(3)]})
    return pd.DataFrame(rows), pd.DataFrame(trows), pd.DataFrame(frows)


def main():
    ap = argparse.ArgumentParser(description="Fake HRI data generator")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--null", action="store_true", help="no true effect anywhere")
    ap.add_argument("--out", default="FAKE_example_data.xlsx")
    args = ap.parse_args()
    from make_template import build_workbook
    d, t, f = generate(args.n, args.seed, args.null)
    build_workbook(args.out, d, t, f)
    print(f"Wrote {args.out}: {args.n} participants, {len(d)} Data rows")


if __name__ == "__main__":
    main()
