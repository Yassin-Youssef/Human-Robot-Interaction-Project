#!/bin/bash
# Show the robot, table, blocks, bowls and decision X in RViz. No robot needed.
# Runs its own ROS master on port 11312, so it never touches the real robot.
source /opt/ros/one/setup.bash
source /opt/ros/cu/setup.bash
source "$HOME/ros/devel/setup.bash"
export ROS_MASTER_URI=http://localhost:11312
exec roslaunch -p 11312 hri_study_description view.launch
