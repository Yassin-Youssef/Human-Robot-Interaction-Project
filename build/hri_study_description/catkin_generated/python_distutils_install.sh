#!/bin/sh

if [ -n "$DESTDIR" ] ; then
    case $DESTDIR in
        /*) # ok
            ;;
        *)
            /bin/echo "DESTDIR argument must be absolute... "
            /bin/echo "otherwise python's distutils will bork things."
            exit 1
    esac
fi

echo_and_run() { echo "+ $@" ; "$@" ; }

echo_and_run cd "/home/hri26-group2/ros/src/hri_study_description"

# ensure that Python install destination exists
echo_and_run mkdir -p "$DESTDIR/home/hri26-group2/ros/install/lib/python3/dist-packages"

# Note that PYTHONPATH is pulled from the environment to support installing
# into one location when some dependencies were installed in another
# location, #123.
echo_and_run /usr/bin/env \
    PYTHONPATH="/home/hri26-group2/ros/install/lib/python3/dist-packages:/home/hri26-group2/ros/build/hri_study_description/lib/python3/dist-packages:$PYTHONPATH" \
    CATKIN_BINARY_DIR="/home/hri26-group2/ros/build/hri_study_description" \
    "/usr/bin/python3" \
    "/home/hri26-group2/ros/src/hri_study_description/setup.py" \
    egg_info --egg-base /home/hri26-group2/ros/build/hri_study_description \
    build --build-base "/home/hri26-group2/ros/build/hri_study_description" \
    install \
    --root="${DESTDIR-/}" \
    --install-layout=deb --prefix="/home/hri26-group2/ros/install" --install-scripts="/home/hri26-group2/ros/install/bin"
