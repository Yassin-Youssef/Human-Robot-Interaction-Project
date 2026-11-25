# HRI study: do people trust a robot's hint more than a screen's hint?

HRI course project, group 2 (Yassin Baher, David Rusia, Saba Khaburzania, Ana Saldadze).

A Franka Panda robot hands the participant a block. The participant has to sort it into the red or the
blue bowl, following sorting rules they read at the start of the round. Before they decide they get a
hint about which bowl to use. Sometimes the hint is wrong on purpose. We compare two groups:

| Version | Participant IDs | The hint is given by |
|---|---|---|
| **A: screen** | A01, A02, ... | text on the screen: "I suggest the red bowl." |
| **B: robot** | B01, B02, ... | the robot pushing the block toward a bowl (no text) |

The robot picks and places the block in both versions, so the only difference is where the hint comes from.
We measure how often participants follow the hint (especially the wrong ones), how often they are
correct, and how long they take to decide. Trust is asked on paper after each round.

## Session flow

| Step | What happens | Time |
|---|---|---|
| Consent | Consent form, short instructions | |
| Practice | 3 rules, 3 blocks, no time limit, all hints correct, no feedback | |
| Round 1 | 4 rules shown for **40 s** (odd IDs) or **20 s** (even IDs), then hidden; 6 blocks, 2 wrong hints (blocks 3 and 6) | |
| Questionnaire 1 | Paper questionnaire | |
| Round 2 | 4 new rules shown for **20 s** (odd IDs) or **40 s** (even IDs), then hidden; 6 blocks, 2 wrong hints (blocks 2 and 5) | |
| Questionnaire 2 | Paper questionnaire | |
| Debrief | "Thank you for taking part in this experiment." Enter (if the participant wants) shows a table of every block: hint, correct bowl, choice, right or wrong | |

Per block: the robot picks the block from its slot and places it on the decision spot between the bowls.
Version A: the hint appears on the screen. Version B: the robot pushes the block toward a bowl and returns
home. The participant puts the block in a bowl and presses Enter. The decision time runs from the moment
the hint is fully given until Enter.

Every participant gets the same rules, the same block order and the same wrong hints. All of it is in
[UI/study.yaml](UI/study.yaml). The only thing that alternates is the reading time, to counterbalance it:

| Participants | Round 1 reading time | Round 2 reading time |
|---|---|---|
| odd: A01/B01, A03/B03, ... | 40 s | 20 s |
| even: A02/B02, A04/B04, ... | 20 s | 40 s |

Set in [UI/config.yaml](UI/config.yaml) (`swap_reading_times_for_even`). The UI prints the reading times
at start, and the experimenter window shows the current one.

## Repository layout

```
UI/
  hri_ui.py              participant screen + experimenter window (the program you run)
  robot_interface.py     connects the UI to the robot (FakeRobot for tests, RosRobot for the real one)
  study.yaml             rules, correct bowls, block order, wrong hints
  config.yaml            screen, fonts, reading times, debrief text
  tests/                 mock tests, no ROS or robot needed
  data/                  results, one file per participant (see "Data")
    pilot/               pilot sessions (A01, B02 of 2026-11-18)
    old/                 test runs, not for analysis
src/hri_study_description/   ROS package for the robot
  launch/robot.launch    the robot bringup with our blocks (use instead of "Launch ROS")
  config/blocks.yaml     block slots, decision spot and bowl positions on the table
  config/pick.yaml       speed, gripper, push settings
  src/.../robot.py       pick, place, push, home (MoveIt)
  scripts/pick_block.py  command line test of single robot moves
HRI_analysis/            analysis scripts (written for an older design, see "Known issues")
Context/                 original study design documents
```

## Before each session

1. **Table.** Put the 9 blocks on their taped slots (layout below), the red bowl on the participant's
   left and the blue bowl on the right, nothing on the decision X.
2. **Robot.** Unlock the arm in Desk, release the stop button, then in terminal 1:
   ```
   cd ~/ros && source devel/setup.bash
   roslaunch hri_study_description robot.launch
   ```
3. **Reset the robot error state, always**, with the "reset franka error" button in the script panel
   (or `~/scripts/reset-franka-error`). Then press "Move Home".
4. **UI.** In terminal 2:
   ```
   cd ~/ros/UI
   python3 hri_ui.py --participant B01      # robot version
   python3 hri_ui.py --participant A01      # screen version
   ```
   The fullscreen window is the participant's, the small one is the experimenter's. It must not be
   visible to the participant.

Block layout (slots seen from above, robot at the back):

| | x 0.085 | x 0.225 | x 0.37 |
|---|---|---|---|
| y 0.56 | s1 red cuboid | s2 green hexagon | s3 red hexagon |
| y 0.66 | s4 blue hexagon | s5 blue cylinder | s6 green cuboid |
| y 0.76 | s7 green cylinder | s8 red cylinder | s9 blue cuboid |

All blocks are 4.5 cm. Positions are in meters from the table corner next to the robot ([blocks.yaml](src/hri_study_description/config/blocks.yaml)).

## Running a session (experimenter)

| Key or button | Does |
|---|---|
| **Space** / Continue | start, and go on after each "Please wait" screen |
| **Enter** (participant) | starts the round early on the rules screen, next block, done deciding |
| **Red bowl / Blue bowl** | click what the participant chose. The next block only starts after a click. "Undo choice" clears it |
| **Skip round** | skip the rest of the current round and go straight to the next one (asks first) |
| **Escape** / Quit | stop the UI (asks first) |

Keys work in both windows. Enter and Space are ignored while the robot moves.

- **Between rounds:** put the used blocks back on their slots, hand out the questionnaire, then press Space.
- **Resume after a crash or robot error:** quit, fix the robot, then restart at the block that failed,
  for example `python3 hri_ui.py --participant B03 --start-level round2 --start-question 4`.
  Earlier blocks in the participant's file are kept.
- **Starting a participant again from the beginning** moves their earlier file to `data/old/`. Nothing is deleted.

## Data

One file per participant in `UI/data/`, as `<ID>_<date>_<time>.csv` plus the same table as `.xlsx`. It
is rewritten after every choice, so a crash loses nothing. One row per answered block:

| Column | Meaning |
|---|---|
| Participant, Version | e.g. B03, robot |
| Round, Block number | practice / 1 / 2, and 1 to 6 |
| Block | e.g. green cuboid |
| Correct bowl, Hinted bowl, Hint correct | what the rules say, what the hint said, whether they match |
| Chosen bowl, Followed hint | what the participant did, whether it matches the hint |
| Decision time (s) | hint fully given until Enter |
| Reading time (s) | how long the rules of that round were shown (40 or 20; empty for practice) |

Practice rows are in the file too; leave them out of the analysis. A block skipped before a choice is
not saved. Questionnaire answers are on paper.

## Testing without the robot

```
cd ~/ros/UI
python3 -m unittest discover -s tests             # 39 mock tests
python3 hri_ui.py --participant B01 --dry-run     # fake robot, no ROS; saves DRYRUN_ files
python3 hri_ui.py --participant B01 --ros-dry-run # real robot code, prints each move, sends nothing
```

Single robot moves (robot.launch running, e-stop in hand):

```
rosrun hri_study_description pick_block.py --block "red cuboid" --dry-run   # plan only
rosrun hri_study_description pick_block.py --block "red cuboid"             # pick + place on the X
rosrun hri_study_description pick_block.py --push red                       # push cue
rosrun hri_study_description pick_block.py --home
```

## Changing the study

- Rules, correct bowls, block order, wrong hints: [UI/study.yaml](UI/study.yaml). The UI checks it at start
  and stops with a clear message if something does not fit.
- Reading times, debrief text (`debrief_text`), fonts, participant monitor: [UI/config.yaml](UI/config.yaml).
- Block, bowl or decision spot positions: [blocks.yaml](src/hri_study_description/config/blocks.yaml),
  then restart `robot.launch`. Robot speed (now 20 %) and push: [pick.yaml](src/hri_study_description/config/pick.yaml).

## Troubleshooting

| Problem | Fix |
|---|---|
| `ROBOT ERROR ... execution failed (controller error or stop button?)` | The robot is in its error state (often from startup). Release the stop button, press "reset franka error", check with `rostopic echo -n1 /franka_state_controller/franka_states/robot_mode` (must be 1 or 2, not 4), then resume with `--start-level/--start-question`. |
| Participant window on the wrong monitor | Set `participant_monitor_x_offset` in `config.yaml` (e.g. 1920). |
| `Unknown participant` | IDs are A or B plus a number: A01, B12. |
| Lab changed `/opt/ros/cu` | `robot.launch` is a copy of the lab's `hardware.launch` (2026-11-03). Compare and update it. |

## Known issues

- `HRI_analysis/` expects the older design (easy/medium/hard levels, robot and screen in one participant)
  and cannot read the current data files yet.
- `UI/README_UI.md` and `UI/INTEGRATION.md` describe older versions of the design; this README is the current one.
