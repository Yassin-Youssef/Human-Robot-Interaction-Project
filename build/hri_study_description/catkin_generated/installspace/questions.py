#!/usr/bin/env python3
"""The 18 experiment questions and the block the robot picks up for each one.

Every question has a level (easy, medium, hard), a number (1 to 6), the new sorting
statement shown on the screen, the block the robot brings, and the correct bowl.
Statements stack within a level and the newest statement that applies wins.

The block is linked to its slot in config/blocks.yaml by color and shape, so the robot
knows where to pick it up. The questions say "cuboid"; blocks.yaml calls that type "cube".

Run it to list the questions with their slots and to check the links:
  rosrun hri_study_description questions.py
  rosrun hri_study_description questions.py --level easy
"""
import argparse
import collections
import os
import sys

import rospkg
import yaml

LEVELS = ("easy", "medium", "hard")

Question = collections.namedtuple("Question", "level number statement color shape correct")

QUESTIONS = [
    # level, Q, new statement, block color, block shape, correct bowl
    Question("easy", 1, "Red and green blocks go in the red bowl, blue blocks in the blue bowl",
             "blue", "cylinder", "blue"),
    Question("easy", 2, "Cylinders go in the blue bowl", "green", "cuboid", "red"),
    Question("easy", 3, "Blue blocks go in the red bowl", "red", "cylinder", "blue"),
    Question("easy", 4, "Green blocks go in the blue bowl", "blue", "cuboid", "red"),
    Question("easy", 5, "Cuboids go in the blue bowl", "green", "cylinder", "blue"),
    Question("easy", 6, "Red blocks go in the red bowl", "red", "cuboid", "red"),

    Question("medium", 1, "Green cuboids and blue cylinders go in the red bowl, everything else in "
             "the blue bowl", "red", "cylinder", "blue"),
    Question("medium", 2, "Red blocks go in the red bowl", "blue", "cuboid", "blue"),
    Question("medium", 3, "Cylinders go in the blue bowl, except green cylinders, which go in the "
             "red bowl", "green", "cuboid", "red"),
    Question("medium", 4, "Blue blocks go in the blue bowl", "red", "cuboid", "red"),
    Question("medium", 5, "Cuboids go in the red bowl, except blue cuboids, which go in the blue "
             "bowl", "blue", "cylinder", "blue"),
    Question("medium", 6, "Red cuboids go in the blue bowl", "green", "cylinder", "red"),

    Question("hard", 1, "Everything that is NOT a green cuboid goes in the red bowl, green cuboids "
             "in the blue bowl", "blue", "cuboid", "red"),
    Question("hard", 2, "Anything that is NOT a cylinder goes in the blue bowl", "red", "cylinder", "red"),
    Question("hard", 3, "Blue blocks that are NOT cuboids go in the blue bowl", "green", "cuboid", "blue"),
    Question("hard", 4, "Blocks that are NOT red go in the red bowl, except cylinders, which go in "
             "the blue bowl", "red", "cuboid", "blue"),
    Question("hard", 5, "Green blocks that are NOT cylinders go in the red bowl", "blue", "cylinder", "blue"),
    Question("hard", 6, "Anything that is NOT blue goes in the red bowl", "green", "cylinder", "red"),
]

# Shape word in the questions -> block type in blocks.yaml.
SHAPE_TO_TYPE = {"cuboid": "cube", "cylinder": "cylinder"}


def questions(level=None):
    """All questions in session order, or only those of one level."""
    return [q for q in QUESTIONS if level is None or q.level == level]


def block_label(q):
    return "%s %s" % (q.color, q.shape)


def load_blocks():
    path = os.path.join(rospkg.RosPack().get_path("hri_study_description"), "config", "blocks.yaml")
    with open(path) as f:
        return yaml.safe_load(f)["blocks"]


def slot_for(q, blocks=None):
    """The blocks.yaml entry (id, name, x, y, ...) of the block this question uses."""
    blocks = load_blocks() if blocks is None else blocks
    matches = [b for b in blocks
               if b["color"] == q.color and b["type"] == SHAPE_TO_TYPE[q.shape]]
    if len(matches) != 1:
        raise ValueError("%s Q%d: %d blocks in blocks.yaml match '%s'"
                         % (q.level, q.number, len(matches), block_label(q)))
    return matches[0]


def check(blocks):
    """Problems with the question set or its links to blocks.yaml (empty list = all fine)."""
    problems = []
    for level in LEVELS:
        qs = questions(level)
        if [q.number for q in qs] != [1, 2, 3, 4, 5, 6]:
            problems.append("%s: questions are not numbered 1 to 6" % level)
        if len({block_label(q) for q in qs}) != 6:
            problems.append("%s: does not use each of the 6 blocks exactly once" % level)
        bowls = [q.correct for q in qs]
        if bowls.count("red") != 3 or bowls.count("blue") != 3:
            problems.append("%s: needs 3 red and 3 blue correct bowls" % level)
        for q in qs:
            try:
                slot_for(q, blocks)
            except ValueError as e:
                problems.append(str(e))
    return problems


def main():
    ap = argparse.ArgumentParser(description="List the experiment questions and the block slot for each.")
    ap.add_argument("--level", choices=LEVELS, help="only this level")
    args = ap.parse_args()

    blocks = load_blocks()
    for level in LEVELS if args.level is None else (args.level,):
        print("\n%s" % level.upper())
        print("  Q  block            slot  x     y      correct  statement")
        for q in questions(level):
            try:
                s = slot_for(q, blocks)
                where = "%-4s  %.3f %.3f" % (s["id"], s["x"], s["y"])
            except ValueError:
                where = "??    -     -    "
            print("  %d  %-15s  %s  %-7s  %s" % (q.number, block_label(q), where, q.correct, q.statement))

    problems = check(blocks)
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print("  " + p)
        sys.exit(1)
    print("\nChecks ok: every level is numbered 1 to 6, uses each block once, has 3 red and 3 blue "
          "answers, and every block has exactly one slot in blocks.yaml.")


if __name__ == "__main__":
    main()
