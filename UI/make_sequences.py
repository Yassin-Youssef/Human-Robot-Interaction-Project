"""Makes the pre-generated trial sequence for every participant (design v2: advice always correct).
Run: python make_sequences.py     (writes sequences.csv and statements.json)
The robot program, the screen UI and the data sheet all read these files."""
import csv
import itertools
import json
import random

SEED = 2026          # change it to get a different (but reproducible) draw
N = 20               # number of participants (use an even number)
MAX_RUN = 2          # max advices in a row from the same source
SRC = {"r": "robot", "s": "screen"}

# Statements shown together (in this order) before each level, then hidden.
STATEMENTS = {
    "easy": [
        "Hexagons go in the blue bowl.",
        "Red and blue cylinders go in the red bowl.",
        "Green cylinders go in the blue bowl.",
        "Red cuboids go in the red bowl.",
        "Blue cuboids go in the red bowl.",
        "Green cuboids go in the blue bowl.",
    ],
    "hard": [
        "Hexagons that are NOT blue go in the blue bowl.",
        "Blue hexagons go in the red bowl.",
        "Cylinders that are NOT green go in the red bowl.",
        "Green cylinders go in the blue bowl.",
        "Cuboids that are NOT red go in the red bowl.",
        "Red cuboids go in the blue bowl.",
    ],
}

# Fixed for everyone, same order every session: (block, correct bowl, number of the deciding statement)
KEY = {
    "easy": [("blue cylinder", "red", 2), ("red hexagon", "blue", 1), ("green cuboid", "blue", 6),
             ("red cuboid", "red", 4), ("green cylinder", "blue", 3), ("blue cuboid", "red", 5)],
    "hard": [("green hexagon", "blue", 1), ("red cylinder", "red", 3), ("green cuboid", "red", 5),
             ("green cylinder", "blue", 4), ("blue hexagon", "red", 2), ("red cuboid", "blue", 6)],
}

SWAP = str.maketrans("rs", "sr")
ORDERS = [o for o in sorted(set("".join(p) for p in itertools.permutations("rrrsss")))
          if not any(len(set(o[i:i + MAX_RUN + 1])) == 1 for i in range(len(o) - MAX_RUN))]


def level_orders(rng):
    """N source orders for one level: every order is used with its mirror image, so each question
    has the robot exactly half of the time across participants."""
    pairs = []
    seen = set()
    for o in ORDERS:
        if o not in seen:
            seen.add(o)
            seen.add(o.translate(SWAP))
            pairs.append((o, o.translate(SWAP)))
    rng.shuffle(pairs)
    out = []
    k = 0
    while len(out) < N:
        out.extend(pairs[k % len(pairs)])
        k += 1
    out = out[:N]
    rng.shuffle(out)
    return out


def main():
    rng = random.Random(SEED)
    plan = {lvl: level_orders(rng) for lvl in KEY}
    rated_first = ["robot"] * (N // 2) + ["screen"] * (N - N // 2)
    rng.shuffle(rated_first)
    rows = []
    for i in range(N):
        pid = f"P{i + 1:02d}"
        for lvl, items in KEY.items():
            order = plan[lvl][i]
            for q, (block, correct, sidx) in enumerate(items, start=1):
                rows.append({"participant": pid, "level": lvl, "question": q,
                             "statement_no": sidx, "statement": STATEMENTS[lvl][sidx - 1],
                             "block": block, "source": SRC[order[q - 1]], "advice": correct,
                             "correct": correct, "advice_correct": "yes", "rated_first": rated_first[i],
                             "chosen": "", "time_s": ""})
    with open("sequences.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open("statements.json", "w", encoding="utf-8") as f:
        json.dump(STATEMENTS, f, indent=2)
    print(f"Wrote {len(rows)} rows for {N} participants to sequences.csv and statements.json")


if __name__ == "__main__":
    main()
