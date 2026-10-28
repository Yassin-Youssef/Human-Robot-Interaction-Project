# INTEGRATION.md: connect the study UI to the real robot

For Claude Code on the robot computer. The real robot code and poses already exist there.
Your job is a thin adapter between the UI and that code. Read this whole file first, and README_UI.md for the study.

## 1. What the UI does

`hri_ui.py` runs the participant screen and a small experimenter window. For every block it calls the robot
through `robot_interface.py`. The UI is finished and tested; do not change its flow.

Contract (both `FakeRobot` and `RosRobot` in `robot_interface.py`):

| Call | Must do | Ends with |
|---|---|---|
| `pick(block_name)` | Take that block from its slot. | Block held in the gripper. |
| `place_decision()` | Put the held block in the decision area in front of the participant, release it, go home. | Arm at home, block in the decision area. |
| `point(bowl)` | `bowl` is `"red"` or `"blue"`. Point clearly at that bowl and hold the pose. | Arm holding the pointing pose. |
| `home()` | Return to the home pose. | Arm at home. |

- Each call **blocks until the move is finished**. Return only when the robot has stopped.
- On any failure **raise an exception**. Never return silently. The UI then shows "ROBOT ERROR" and stops until Escape.
- The UI calls them in this order per question: `pick`, `place_decision`, then on robot trials only
  `point(advice)` and, after the participant presses Enter, `home()`. On screen trials the arm stays at home.
- Keep each call the same duration on every trial (fixed durations), so robot timing does not differ between blocks.
- Calls come from a worker thread, one at a time, never in parallel.
- `hri_ui.py` currently creates `RosRobot()` without arguments when `--dry-run` is not given.

The 9 block names passed to `pick` (exact strings):
`red cuboid`, `red cylinder`, `red hexagon`, `blue cuboid`, `blue cylinder`, `blue hexagon`,
`green cuboid`, `green cylinder`, `green hexagon`.
Each one sits in its own slot. Use the existing slot or pose mapping on this computer. Reject unknown names
(`check_block` and `check_bowl` already exist in `robot_interface.py`).

## 2. Steps for you

1. **Inspect read-only.** List and read the existing robot files: scripts, launch files, pose and config yaml,
   the recording script. Do not run anything that moves the robot or starts controllers.
2. **Run `robot_check.sh`** (read-only: lists topics, nodes, services, controllers, joint states, Python, tkinter):
   `bash robot_check.sh 2>&1 | tee robot_check_output.txt`. If it fails with `$'\r'`, run `sed -i 's/\r$//' robot_check.sh` first.
3. **Report to the user before coding:** which existing code moves the arm and the gripper, how the poses and slots
   are stored, what is missing for the four calls, and your plan. Wait for the user's OK.
4. **Fill in `RosRobot`** as a thin adapter: each method calls the existing code with the existing poses.
   Import `rospy` and the existing modules lazily inside `RosRobot`, so `--dry-run` still needs no ROS.
   Do not write new pose files or a new motion stack unless something is really missing, and then ask first.
5. **Add `--ros-dry-run`** to `hri_ui.py`: uses `RosRobot` but only prints each call and what it would send
   (target poses, durations), sends nothing, and sleeps for the same durations.
6. **Test without motion:** `--dry-run`, then `--ros-dry-run`, then hand over to the user for the steps in section 4.

## 3. Safety rules

- **Never move the robot without the user's explicit go**, given for that specific move. Approval for one move does not cover the next.
- **Reduced speed** for all tests (for example 25 to 50 percent), using the existing speed setting.
- **One test move at a time.** Test each call on its own, for example one `pick` of one block, before any full run.
- **Emergency stop in reach** of a person at every test, and the workspace clear of people and objects that are not part of the setup.
- **`--ros-dry-run`** must print the calls without sending anything. Use it before every first real run.
- The user starts all robot drivers and launch files, and runs every command that can move the robot. You only write code and suggest commands.

## 4. Run everything together

The robot code lives in the catkin package `~/ros/src/hri_study_description`:
`config/blocks.yaml` (9 slots, decision spot), `config/pick.yaml` (speeds, gripper,
`confirm`, `home`), `src/hri_study_description/robot.py` (`StudyRobot`: `pick`,
`place_decision`, `home`), `scripts/pick_block.py` (command line test).
`RosRobot` in `robot_interface.py` forwards the UI calls to `StudyRobot`.

```
# once after changes:   cd ~/ros && catkin build hri_study_description && source devel/setup.bash
# terminal 1: the robot, INSTEAD of the "Launch ROS" button (arm unlocked in Desk first)
roslaunch hri_study_description robot.launch
# terminal 2, in ~/ros/UI:
python3 hri_ui.py --participant P07 --dry-run       # fake robot, no ROS
python3 hri_ui.py --participant P07 --ros-dry-run   # real adapter, prints only, sends nothing, no ROS needed
python3 hri_ui.py --participant P07                 # real robot
```
With `confirm: true` in `pick.yaml` every robot move waits for Enter in terminal 2
(q + Enter stops the call and the UI shows ROBOT ERROR). Watch terminal 2 during tests.
`point(bowl)` is the push cue: from home it closes the gripper, goes above the decision spot,
moves 5 cm sideways away from the target bowl, goes down (fingertips 1 cm above the table),
pushes the block toward the bowl at 2 cm/s until the block center is 15 cm from the bowl center
(8.25 cm push, gripper 10 cm in about 5 s), goes up and home. It returns when the arm is home,
so the UI's decision clock starts then. Red and blue are exact mirror images. Settings: `push`
in `pick.yaml`; the decision spot is midway between the bowls in `blocks.yaml`.
Resume after an interruption: `--start-level hard --start-question 4`. Participant monitor: set
`participant_monitor_x_offset` in `config.yaml`. Keys: participant Enter, experimenter Space (continue) and Escape (quit); all keys work in both windows.
The UI times each decision (advice fully given to Enter). On robot trials the clock starts when `point()` returns.

## 4a. Robot test order (the user runs every step, e-stop in hand, 10 percent speed)

1. **No robot:** `python3 -m unittest discover -s tests -v` in `~/ros/UI` (mock tests, all must pass).
2. **Layout in RViz, no robot:** `ROS_MASTER_URI=http://localhost:11312 roslaunch -p 11312 hri_study_description view.launch`.
   Check the 9 blocks (colors, shapes) and the labels s1_red_cuboid ... s9_green_hexagon.
   Tape the slots at the `blocks.yaml` positions. Measure the real hexagon and replace the TODO size.
3. **Start the robot:** stop `hardware.launch`, start `robot.launch` (terminal 1).
4. **UI without motion:** `python3 hri_ui.py --participant P01 --ros-dry-run`. Every call prints its slot and target.
5. **home():** `rosrun hri_study_description pick_block.py --home`. Same as the script panel "Move Home".
6. **One pick, plan only:** `rosrun hri_study_description pick_block.py --block "red cuboid" --dry-run`. Watch the path in RViz.
7. **One pick and place_decision:** `rosrun hri_study_description pick_block.py --block "red cuboid"`.
   Enter before each move: open, above, down, grasp, lift, above decision spot, down, let go, up, home.
   After a stop with the arm low: `pick_block.py --up --block "red cuboid"`.
8. **All 9 blocks** one at a time like step 7. Put each block back in its slot.
9. **Decision spot moved** (now x 0.52, y 0.6275, midway between the bowls): re-tape the X,
   restart `robot.launch`, and repeat step 7 for one block (place_decision plan only, then real).
10. **Push, plan only:** place a block on the decision X by hand, then
    `rosrun hri_study_description pick_block.py --push red --dry-run` and the same with `blue`.
    In RViz both paths must look like mirror images.
11. **Push for real, one at a time:** `pick_block.py --push red` (Enter before each move), put the
    block back on the X, then `pick_block.py --push blue`. Watch: the fingertips do not touch the
    table, the block slides without tipping, it stops well before the bowl. Do this once for each
    shape (cuboid, cylinder, hexagon), because a cylinder or hexagon may roll or tip.
12. **Full trial from the command line:** `pick_block.py --block "blue cuboid"`, then
    `pick_block.py --push red`.
13. **UI with the real robot:** `python3 hri_ui.py --participant P01`. Robot trials now push.
14. Only after all of this: set `confirm: false` in `pick.yaml`.

## 4b. Cue pretest checklist (3 to 5 people, before the pilot)

- [ ] The person sits in the participant seat; the experimenter runs `--push red` and `--push blue` in a
      random order, 3 times each, without saying which.
- [ ] After each push the person says which bowl the robot indicated. Every answer must be correct.
- [ ] Ask if the push looked the same on both sides (speed, distance, duration). Note any difference.
- [ ] The block never tips, rolls away or reaches a bowl; check with every shape.
- [ ] Time each push from the first motion to the arm at home; red and blue should match within about a second.
- [ ] Note anything the person found unclear or startling (speed, noise, how close the arm comes).

## 5. Pilot checklist

- [ ] `python3 -c "import tkinter, yaml"` works on the robot computer; `--dry-run` runs a full session.
- [ ] Participant window on the participant monitor, experimenter window not visible to the participant.
- [ ] `--ros-dry-run` full session: every call prints the expected target, nothing moves.
- [ ] Reduced speed, e-stop in hand: `home()` once, then `pick` + `place_decision` for each of the 9 blocks, one at a time.
- [ ] `point("red")` and `point("blue")` (push cue) each once. From the participant's seat, the indicated bowl is unambiguous (see 4b).
- [ ] Full session at reduced speed with a group member as participant; Enter is ignored while the arm moves.
- [ ] Same at study speed; durations are the same on every trial; no slot is hit or missed.
- [ ] Block reload between levels works; resume with `--start-question` tested once.
- [ ] A failed call (for example a stopped driver) shows "ROBOT ERROR" and the UI stops.
