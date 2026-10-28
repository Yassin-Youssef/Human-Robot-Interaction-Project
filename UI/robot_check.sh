#!/usr/bin/env bash
# READ-ONLY check of the robot computer for the HRI study.
# Nothing here moves the robot, starts or stops nodes, loads controllers or sends goals.
# It only lists and reads. Run it in a terminal where ROS is already sourced and the robot
# driver is running as usual:
#     bash robot_check.sh 2>&1 | tee robot_check_output.txt
# If you see "bad interpreter" or "$'\r': command not found", fix line endings first:
#     sed -i 's/\r$//' robot_check.sh

T=10   # seconds before a command is given up (so nothing hangs)

run() {
    echo
    echo "================================================================"
    echo "\$ $*"
    echo "----------------------------------------------------------------"
    timeout "$T" bash -c "$*" 2>&1
    rc=$?
    if [ $rc -eq 124 ]; then echo "(timed out after ${T}s)"; elif [ $rc -ne 0 ]; then echo "(exit code $rc)"; fi
}

echo "robot_check.sh  $(date)"
run 'lsb_release -a'
run 'echo "ROS_DISTRO=$ROS_DISTRO  ROS_MASTER_URI=$ROS_MASTER_URI  ROS_IP=$ROS_IP  ROS_HOSTNAME=$ROS_HOSTNAME"'
run 'rosversion -d'

# Requested commands
run 'rostopic list'
run 'rosnode list'
run 'rosservice list'
run 'rostopic echo -n1 /joint_states'
run 'rosparam list | grep controller'
run 'rosnode list | grep -i moveit'
run 'python3 --version'
run 'python3 -c "import tkinter, yaml; print(\"tkinter\", tkinter.TkVersion, \"yaml\", yaml.__version__)"'

# Extra read-only checks (help pick the controller, joint names, limits and gripper)
run 'rostopic list | grep -i -E "follow_joint_trajectory|trajectory|command"'
run 'rostopic list | grep -i -E "grip|hand|finger|tool"'
run 'rosservice list | grep -i -E "grip|hand|finger|tool"'
run 'rostopic info /joint_states'
run 'rosparam list | grep -i -E "joint|limit|gripper|robot_description"'
run 'rosparam get /robot_description | grep -E "<joint |<limit " | head -60'
run 'python3 -c "import rospy, actionlib, control_msgs.msg, trajectory_msgs.msg; print(\"rospy actionlib control_msgs trajectory_msgs: ok\")"'
run 'python3 -c "import moveit_commander; print(\"moveit_commander: ok\")"'
# Lists controllers only (read-only service, changes nothing). Skipped if the service does not exist.
run 'rosservice list | grep -q "/controller_manager/list_controllers" && rosservice call /controller_manager/list_controllers || echo "no /controller_manager/list_controllers service"'

echo
echo "Done. Paste the whole output (or robot_check_output.txt) back to Claude."
