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

```
# terminal 1: start the robot the usual way (existing launch files, controllers, gripper)
# terminal 2, in the UI folder:
python3 hri_ui.py --participant P07 --dry-run       # fake robot, no ROS
python3 hri_ui.py --participant P07 --ros-dry-run   # real adapter, nothing sent
python3 hri_ui.py --participant P07                 # real robot
```
Resume after an interruption: `--start-level hard --start-question 4`. Participant monitor: set
`participant_monitor_x_offset` in `config.yaml`. Keys: participant Enter, experimenter Space (continue) and Escape (quit); all keys work in both windows.
The UI times each decision (advice fully given to Enter). On robot trials the clock starts when `point()` returns,
so `point()` must return only once the arm holds the pose.

## 5. Pilot checklist

- [ ] `python3 -c "import tkinter, yaml"` works on the robot computer; `--dry-run` runs a full session.
- [ ] Participant window on the participant monitor, experimenter window not visible to the participant.
- [ ] `--ros-dry-run` full session: every call prints the expected target, nothing moves.
- [ ] Reduced speed, e-stop in hand: `home()` once, then `pick` + `place_decision` for each of the 9 blocks, one at a time.
- [ ] `point("red")` and `point("blue")` each once, followed by `home()`. From the participant's seat, the pointed bowl is unambiguous.
- [ ] Full session at reduced speed with a group member as participant; Enter is ignored while the arm moves.
- [ ] Same at study speed; durations are the same on every trial; no slot is hit or missed.
- [ ] Block reload between levels works; resume with `--start-question` tested once.
- [ ] A failed call (for example a stopped driver) shows "ROBOT ERROR" and the UI stops.
