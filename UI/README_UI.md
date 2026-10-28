# README: participant UI for the HRI robot trust study

Give this file to Claude Code in the project folder and say: "Follow README_UI.md to build the UI. Show me a short plan first, then build." This file is the single source of truth for the UI. If it conflicts with older files or summaries, THIS FILE WINS.

## 0. Rules for Claude Code

- Show a short plan first and wait for approval before building. List every assumption at the end.
- NEVER run anything that moves the real robot, starts robot controllers or sends goals. Only write code and config, and inspect with read-only commands. The humans run everything, one step at a time.
- Everything must work in `--dry-run` mode with NO ROS and NO robot (fake robot).
- Keep code simple, one main file, comments short. Do not use em dashes in any text written for the user.
- Do not change a decided design point below. If something looks wrong, say so and ask.

## 1. The study in short

Within-subjects HRI study (about 20 participants). Does advice from a physical robot arm (it points to a bowl) get trusted and followed more than identical advice shown as text on a screen? Participants sort blocks into a red or a blue bowl following sorting rules they memorized. For each block the advice comes from the robot OR from the screen. The advice is always correct, but participants are told it might be wrong (the debrief at the end explains). Trust is asked out loud by the group after each level. The participant only places blocks into bowls and presses Enter.

## 2. Design facts the UI must follow

- 3 shapes (cuboid, cylinder, hexagon) x 3 colors (red, blue, green) = 9 block types. Two bowls: red and blue.
- 2 levels, always in this order: easy, then hard. 6 questions per level, 12 in total. No medium level.
- Before each level, ALL 6 sorting statements are shown together on one screen. The participant reads at their own pace and presses Enter to start. After that the statements are hidden for good and never shown again in that level (memory task).
- One block per question. The robot places it in the decision area and the participant must decide which bowl.
- Per level, 3 robot advices and 3 screen advices in random order (max 2 in a row from the same source). The order comes from sequences.csv, never random at run time.
- Advice always equals the correct bowl. No score and no feedback after a question.
- ALL DATA IS ENTERED BY HAND by the group (times, choices, trust ratings, final question) into an Excel sheet. The UI does NOT log, time or save anything about the participant's behavior and has no keys for it.

## 3. Files

Inputs (already in the folder, do not edit by hand):
- `sequences.csv`: one row per participant and question. Columns: participant, level, question, statement_no, statement, block, source (robot or screen), advice (red or blue), correct, advice_correct, rated_first (robot or screen: which source is rated first in the trust questions), chosen, time_s (the last two stay empty, the group fills them in Excel).
- `statements.json`: the 6 statements per level, in display order: {"easy": [...], "hard": [...]}.
- `make_sequences.py`: generates the two files above (N = 20, seed 2026). Do not change the design inside it.

To create:
- `hri_ui.py`: main program, participant screen plus a small read-only experimenter window.
- `robot_interface.py`: class `FakeRobot` (works now) and class `RosRobot` (stub with TODO, to be filled next session by the robot code). Same four methods in both: `pick(block_name)`, `place_decision()`, `point(bowl)` with bowl "red" or "blue", and `home()`. Each method blocks until finished. `FakeRobot` just prints and sleeps.
- `config.yaml`: fullscreen true/false, fake robot delays (seconds), optional maximum reading time for the statements screen (default none), font sizes.
- `README_UI.md`: this file. Short usage notes can be added at the end, nothing else rewritten.

Tech: Python 3, tkinter (standard library) for both windows. Use `rospy` only inside `RosRobot` and import it lazily so dry-run needs no ROS. If ROS 1 Noetic uses Python 3 on the robot computer, tkinter is available there; mention it if not.

## 4. How to run

```
python hri_ui.py --participant P07 --dry-run      # no robot, fake robot
python hri_ui.py --participant P07                # real robot interface
```
Options: `--sequences sequences.csv`, `--config config.yaml`, `--start-level easy|hard`, `--start-question N` (to resume after an interruption). Stop with Escape in the experimenter window (confirm first). Unknown participant: print the valid IDs and exit.

## 5. Participant screen (second monitor or the screen facing the participant)

Fullscreen, large sans-serif text, high contrast, plain neutral background. One neutral style for everything. No colors on the words "RED" and "BLUE" (write them as plain words), no icons, no sounds, no animation.

Flow:

1. **Waiting screen.** Text: "Please wait". Stays until the experimenter presses Space.
2. **Statements screen (once per level).** Title "Sorting rules", then the 6 statements as a numbered list, in the order from statements.json, plus a line "Press Enter when you are ready". Enter starts the level. If a maximum reading time is set in the config, show a countdown and continue automatically at zero. After Enter the statements are never shown again.
3. **Per question, repeated 6 times per level.** The screen shows a question counter ("Block 3 of 6") at the top, a status line in the middle and a fixed advice region below it. Steps:
   1. Idle: "Press Enter for the next block". Enter starts the question. (First question of a level starts right after the statements screen, no second Enter.)
   2. Status: "Robot presenting the block to you". The UI calls `pick(block)`, then `place_decision()`.
   3. Advice. Same position and same text style on both sources, so only the wording differs:
      - screen trial: "Screen recommends: RED" or "Screen recommends: BLUE" (from the advice column). The robot does nothing here (it stays at home).
      - robot trial: "Robot giving advice". The UI calls `point(advice)`, then the arm holds the pose. The advice region never shows RED or BLUE on robot trials.
   4. The advice stays on screen (or the arm keeps pointing) until the participant presses Enter. Enter then calls `home()` if the arm was pointing and starts the next question. Enter works at any time, also during the status line (it just skips ahead after the robot call finishes; ignore Enter while a robot call is running).
   5. After question 6 Enter leads to the end of the level.
4. **Level complete.** Text "Level complete. Please wait." The experimenter reloads the blocks, asks the trust ratings out loud and presses Space to continue (to the statements screen of the next level).
5. **After the hard level.** "Thank you. Please wait." On Space show the debrief screen (see below).
6. **Debrief screen.** Placeholder text from `config.yaml` (the group will write the final text): that some advice might not have been reliable although it was always correct, thanks, and a request not to discuss the study with other participants.

## 6. Experimenter window (read-only, separate small window)

Shows, always up to date: participant ID, level, question number, source (robot or screen), the block to be in the slot, the correct bowl, the advice, `rated_first` (which source to ask about first in the trust questions), the current step ("waiting", "statements", "robot presenting", "advice shown", "level complete"), and a reminder text when a level ends: "Reload the blocks, ask the trust ratings out loud (rated first: robot or screen), then press Space".

Keys in the experimenter window: Space = continue (waiting screen, level change, debrief); Escape = quit with confirmation. Nothing else. NO logging, NO R/B/X/U keys, NO trust form, NO Excel export, NO timing. Nothing is written to disk.

The experimenter window is a convenience so the group knows what to expect and can fill Excel rows. It must never be visible to the participant (separate window on the experimenter's monitor; a note in this README on how to place windows).

## 7. Robot interface (to be completed next session)

`robot_interface.py` defines the four methods above. `FakeRobot` prints what it would do and sleeps for the configured delays (default: 3 s placing, 3 s pointing). `RosRobot` is a stub that raises NotImplementedError with a clear message. A later task fills it in: poses from `poses.yaml` (recorded with `record_pose.py`), fixed durations, ROS 1. Claude Code must not implement motion commands now.

Block names used by `pick`: "red cuboid", "red cylinder", "red hexagon", "blue cuboid", "blue cylinder", "blue hexagon", "green cuboid", "green cylinder", "green hexagon". A config file will map each name to a slot (9 slots) later.

## 8. Questions the UI uses (for testing, same as in sequences.csv)

Easy statements: Hexagons go in the blue bowl. / Red and blue cylinders go in the red bowl. / Green cylinders go in the blue bowl. / Red cuboids go in the red bowl. / Blue cuboids go in the red bowl. / Green cuboids go in the blue bowl.
Easy questions (block, correct bowl): blue cylinder, red / red hexagon, blue / green cuboid, blue / red cuboid, red / green cylinder, blue / blue cuboid, red.

Hard statements: Hexagons that are NOT blue go in the blue bowl. / Blue hexagons go in the red bowl. / Cylinders that are NOT green go in the red bowl. / Green cylinders go in the blue bowl. / Cuboids that are NOT red go in the red bowl. / Red cuboids go in the blue bowl.
Hard questions: green hexagon, blue / red cylinder, red / green cuboid, red / green cylinder, blue / blue hexagon, red / red cuboid, blue.

## 9. Tests Claude Code must run (dry-run, no hardware)

- All 20 participants load without error; an unknown ID prints the valid IDs.
- A full dry-run walk through 12 questions for one participant with scripted key presses: statements screen appears 2 times (once per level), hides after Enter, the advice text appears only on screen trials, robot trials call `point()` with the right bowl, `home()` is called after each robot trial, and nothing is written to disk.
- Enter is ignored while a fake robot call runs; Enter works at any other time.
- `--start-level hard` and `--start-question 4` resume correctly.
- Screenshot or text dump of each screen type for review.

## 10. Out of scope (separate tasks)

Practice trials, consent form text, final debrief text, the Excel data-entry template, the statistical analysis, and the real ROS robot node.

## 11. Open points (ask, do not guess)

- Which monitor is the participant monitor (window placement).
- The final debrief text.
- Whether a maximum reading time for the statements is wanted.

## Usage notes (added after the build)

- Install once: `pip install pyyaml` (on ROS Noetic usually already there as `python3-yaml`). tkinter is in the standard library; on Ubuntu it may need `sudo apt install python3-tk`.
- Code is Python 3.8 compatible (ROS Noetic).
- Window placement: the small window is the experimenter window, the fullscreen one is the participant window. Set `participant_monitor_x_offset` in `config.yaml` to the left edge of the participant monitor (for example `1920` if it is to the right of a 1920 px main monitor). Keep the experimenter window on the experimenter monitor, never on the participant screen. Test the placement with `--dry-run` before each session.
- Keys (changed on request, overrides section 6): Enter, Space and Escape work in both windows, so the experimenter can continue or quit even when the fullscreen participant window has the focus. The waiting and level-end screens show a small line "Experimenter: press Space to ...".
- Decision time (added on request, overrides "NO timing" in sections 2 and 6): stopwatch from the moment the advice is fully given (screen text shown, or the arm holding the pointing pose) to the participant's Enter. Shown live in the experimenter window, all times listed there, and printed in the terminal after each question and as a summary at the debrief or on Escape. Nothing is saved to disk; copy the times into Excel.
- Resume after an interruption: `--start-level hard --start-question 4`. Resuming at question 2 to 6 skips the statements screen and shows "Press Enter for the next block".
- Without `--dry-run` the UI uses `RosRobot`, which is still a stub: it stops at the first robot call with a clear error in the experimenter window. Quit with Escape.
- Between questions there are two Enters: one after the decision (the arm goes home on robot trials) and one on "Press Enter for the next block". Only the first question of a level starts directly from the statements screen.
- Friendly look (added on request, overrides "one neutral style, no icons, no colors on RED and BLUE,
  no animation" in section 5): lavender background with the content on a white rounded card, Quicksand
  rounded font, purple headings, progress dots with "Level 1 · Block 3 of 6" (levels shown as 1 and 2
  instead of easy and hard), a moving robot drawing while the robot presents the block or returns home,
  and short fade-ins. Screen trials show a red or blue bowl in the advised color and "Screen recommends:"
  with the word RED or BLUE in that color. Robot trials show a purple robot-arm drawing and "Robot giving
  advice" (no bowl color; the robot's push shows the bowl). Note for the group: the colored bowl and word
  make the screen advice more eye-catching than the robot cue, which can affect the robot vs screen
  comparison. On screen trials the decision clock starts after the 0.25 s fade. `animations: false` in
  `config.yaml` restores plain text.
