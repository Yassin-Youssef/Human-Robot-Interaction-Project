"""Makes the pre-generated trial sequence for every participant (design v3: practice + 2 levels).
Run: python make_sequences.py     (writes sequences.csv and statements.json)
The robot program, the screen UI and the data sheet all read these files.

Design v3: same blocks and statements for everyone, advice always correct (advice = correct
bowl). The advice source per block is fixed below; for a random half of the participants robot
and screen are swapped in every row (practice included). Statements apply only within their set.
Block order: P01 gets the order below; every other participant gets Level 1 and Level 2 in a
random order (practice stays fixed), never more than MAX_RUN advices in a row from one source.
"""
import csv
import json
import random

SEED = 2026          # change it to get a different (but reproducible) draw
N = 20               # number of participants (use an even number)
MAX_RUN = 2          # max advices in a row from the same source (after the swap)

# Statements shown together (in this order) before each part, then hidden.
STATEMENTS = {
    "practice": [
        "Red blocks go in the blue bowl.",
        "Green blocks go in the red bowl.",
        "Blue blocks go in the red bowl.",
    ],
    "easy": [   # Level 1
        "Red cubes and red cylinders go in the red bowl.",
        "Green cubes and blue cubes go in the blue bowl.",
        "Hexagons go in the red bowl.",
        "Green cylinders and blue cylinders go in the blue bowl.",
    ],
    "hard": [   # Level 2
        "Red hexagons and red cylinders go in the blue bowl.",
        "Green cylinders and green hexagons go in the red bowl.",
        "Cubes go in the blue bowl.",
        "Blue cylinders and blue hexagons go in the red bowl.",
    ],
}

# Fixed for everyone, same order every session:
# (block, correct bowl, number of the deciding statement, advice source before the swap).
# Block names as the robot knows them: the statements say "cube", the block type is "cuboid".
KEY = {
    "practice": [("green cuboid", "red", 2, "screen"), ("red cylinder", "blue", 1, "robot"),
                 ("blue hexagon", "red", 3, "screen")],
    "easy": [("green hexagon", "red", 3, "robot"), ("green cylinder", "blue", 4, "screen"),
             ("blue cylinder", "blue", 4, "screen"), ("red cuboid", "red", 1, "robot"),
             ("blue hexagon", "red", 3, "robot"), ("green cuboid", "blue", 2, "screen")],
    "hard": [("blue cylinder", "red", 4, "screen"), ("red hexagon", "blue", 1, "robot"),
             ("blue cuboid", "blue", 3, "robot"), ("green cylinder", "red", 2, "screen"),
             ("red cylinder", "blue", 1, "screen"), ("blue hexagon", "red", 4, "robot")],
}
OTHER = {"robot": "screen", "screen": "robot"}


def ordered(items, i, swap, rng, lvl):
    """Block order for participant i: as listed for P01 and the practice, else a random
    order whose advice sources never repeat more than MAX_RUN times in a row."""
    if i == 0 or lvl == "practice":
        return list(items)
    while True:
        order = rng.sample(items, len(items))
        sources = [OTHER[it[3]] if swap else it[3] for it in order]
        if not any(len(set(sources[k:k + MAX_RUN + 1])) == 1 for k in range(len(sources) - MAX_RUN)):
            return order


def main():
    rng = random.Random(SEED)
    swapped = set(rng.sample(range(N), N // 2))        # exactly half get robot and screen swapped
    rated_first = ["robot"] * (N // 2) + ["screen"] * (N - N // 2)
    rng.shuffle(rated_first)                            # trust question order, for the data sheet only
    rows = []
    for i in range(N):
        pid = f"P{i + 1:02d}"
        for lvl, items in KEY.items():
            items = ordered(items, i, i in swapped, rng, lvl)
            for q, (block, correct, sidx, source) in enumerate(items, start=1):
                rows.append({"participant": pid, "level": lvl, "question": q,
                             "statement_no": sidx, "statement": STATEMENTS[lvl][sidx - 1],
                             "block": block, "source": OTHER[source] if i in swapped else source,
                             "advice": correct, "correct": correct, "advice_correct": "yes",
                             "rated_first": "" if lvl == "practice" else rated_first[i],
                             "chosen": "", "time_s": ""})
    with open("sequences.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open("statements.json", "w", encoding="utf-8") as f:
        json.dump(STATEMENTS, f, indent=2)
    print(f"Wrote {len(rows)} rows for {N} participants to sequences.csv and statements.json")
    print("Swapped (robot <-> screen):", " ".join(f"P{i + 1:02d}" for i in sorted(swapped)))


if __name__ == "__main__":
    main()
