#!/usr/bin/env python3
"""Command line test for the robot calls in hri_study_description/robot.py.

By default: pick(block) then place_decision() (which ends with home()).
With confirm: true in pick.yaml it waits for Enter before every move (q + Enter stops).

Usage:
  rosrun hri_study_description pick_block.py --block "red cuboid" --dry-run   # plan only
  rosrun hri_study_description pick_block.py --block s1                       # name or slot id
  rosrun hri_study_description pick_block.py --block s1 --pick-only           # steps 1-5
  rosrun hri_study_description pick_block.py --home                           # only home()
  rosrun hri_study_description pick_block.py --up --block s1                  # recovery: straight up

Needs robot.launch running. Settings: config/pick.yaml. Blocks: config/blocks.yaml.
"""
import argparse
import os
import sys

import rospy

from hri_study_description.robot import RobotError, StudyRobot


def main():
    ap = argparse.ArgumentParser(description="Test the study robot calls from the command line.")
    ap.add_argument("--block", default="s1", help='block name ("red cuboid") or slot id (default: s1)')
    ap.add_argument("--place", default="decision", help="place spot id from blocks.yaml")
    ap.add_argument("--dry-run", action="store_true", help="plan and show in RViz only, nothing moves")
    ap.add_argument("--sim", action="store_true", help="use with launch/sim.launch (fake controllers)")
    ap.add_argument("--pick-only", action="store_true", help="only pick (steps 1-5)")
    ap.add_argument("--home", action="store_true", help="only home()")
    ap.add_argument("--up", action="store_true", help="recovery: only straight up from where the arm is")
    args = ap.parse_args(rospy.myargv()[1:])
    if args.sim and ":11311" in os.environ.get("ROS_MASTER_URI", ""):
        sys.exit("--sim needs the simulation master: export ROS_MASTER_URI=http://localhost:11312")

    rospy.init_node("pick_block", anonymous=True)
    try:
        robot = StudyRobot(dry_run=args.dry_run, sim=args.sim, place=args.place)
        if args.home:
            robot.home()
        elif args.up:
            robot.up(args.block)
        else:
            robot.pick(args.block)
            if not args.pick_only:
                robot.place_decision()
        print("\nDone%s." % (" (dry run, nothing moved)" if args.dry_run else ""))
    except (RobotError, ValueError) as e:
        rospy.logerr("STOPPED: %s", e)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        sys.exit(1)


if __name__ == "__main__":
    main()
