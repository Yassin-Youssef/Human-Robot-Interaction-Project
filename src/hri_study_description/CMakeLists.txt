cmake_minimum_required(VERSION 3.0.2)
project(hri_study_description)

find_package(catkin REQUIRED)
catkin_python_setup()
catkin_package()

catkin_install_python(PROGRAMS scripts/pick_block.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION})

install(DIRECTORY config launch rviz urdf DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION})
