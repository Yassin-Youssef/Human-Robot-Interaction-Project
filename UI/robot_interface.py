"""Robot interface for the HRI trust study UI.

Both classes have the same four methods. Each method blocks until finished.
FakeRobot prints and sleeps. RosRobot drives the real robot through hri_study_description.
"""
import os
import sys
import time

BLOCKS = [
    "red cuboid", "red cylinder", "red hexagon",
    "blue cuboid", "blue cylinder", "blue hexagon",
    "green cuboid", "green cylinder", "green hexagon",
]
BOWLS = ("red", "blue")

DEFAULT_DELAYS = {"pick": 3.0, "place_decision": 3.0, "point": 3.0, "home": 3.0}


def check_block(block_name):
    if block_name not in BLOCKS:
        raise ValueError("Unknown block %r, expected one of %s" % (block_name, BLOCKS))


def check_bowl(bowl):
    if bowl not in BOWLS:
        raise ValueError("Unknown bowl %r, expected 'red' or 'blue'" % (bowl,))


class FakeRobot(object):
    """No hardware. Prints what it would do and sleeps for the configured delays."""

    def __init__(self, delays=None):
        self.delays = dict(DEFAULT_DELAYS)
        self.delays.update(delays or {})

    def _do(self, step, text):
        print("[FakeRobot] %s" % text)
        time.sleep(float(self.delays.get(step, 0)))
        print("[FakeRobot] %s done" % step)

    def pick(self, block_name):
        check_block(block_name)
        self._do("pick", "pick '%s' from its slot" % block_name)

    def place_decision(self):
        self._do("place_decision", "place the block in the decision area")

    def point(self, bowl):
        check_bowl(bowl)
        self._do("point", "point to the %s bowl and hold the pose" % bowl)

    def home(self):
        self._do("home", "return to home pose")


ROBOT_PKG_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "src", "hri_study_description", "src")


def _import_robot_package():
    """hri_study_description from the sourced catkin workspace, else from ~/ros/src."""
    try:
        import hri_study_description  # noqa: F401
    except ImportError:
        sys.path.insert(0, os.path.abspath(ROBOT_PKG_SRC))


class RosRobot(object):
    """Real robot: thin adapter to hri_study_description/robot.py (StudyRobot).

    Create it in the main thread before tkinter starts (hri_ui.py does): it starts the
    ROS node and MoveIt there. The four calls then run in the UI worker thread, block until
    the robot has stopped, and raise on any failure.

    dry_run=True (hri_ui.py --ros-dry-run): never imports rospy or MoveIt and sends
    nothing. It only prints what each call would do (slot, coordinates, steps) and sleeps
    for the fake robot delays.
    """

    def __init__(self, config=None, dry_run=False):
        self.config = config or {}
        self.dry_run = dry_run
        self.delays = dict(DEFAULT_DELAYS)
        self.delays.update(self.config.get("fake_robot") or {})
        _import_robot_package()
        from hri_study_description.layout import Layout
        self.layout = Layout()
        for name in BLOCKS:        # every UI block name needs exactly one slot
            self.layout.block(name)
        self._robot = None
        if dry_run:
            print("[RosRobot] ROS dry run: nothing is sent to the robot.")
            return
        import rospy
        from hri_study_description.robot import StudyRobot
        if not rospy.core.is_initialized():
            # Main thread. disable_signals: the UI handles Ctrl+C and Escape itself.
            rospy.init_node("hri_ui", anonymous=True, disable_signals=True)
        self._robot = StudyRobot()

    def _dry(self, step, text):
        print("[RosRobot dry run] %s" % text)
        time.sleep(float(self.delays.get(step, 0)))

    def pick(self, block_name):
        check_block(block_name)
        if self.dry_run:
            b = self.layout.block(block_name)
            return self._dry("pick", "pick '%s' from slot %s (x %.3f, y %.3f): open gripper, move above, "
                                     "straight down, grasp, straight up" % (block_name, b.id, b.x, b.y))
        self._robot.pick(block_name)

    def place_decision(self):
        if self.dry_run:
            p = self.layout.place("decision")
            return self._dry("place_decision", "place on 'decision' (x %.3f, y %.3f): move above, straight "
                                               "down, let go, straight up, home" % (p["x"], p["y"]))
        self._robot.place_decision()

    def point(self, bowl):
        """The cue: push the block on the decision spot toward the bowl, then home.
        Returns once the arm is back home (the UI starts the decision clock then)."""
        check_bowl(bowl)
        if self.dry_run:
            from hri_study_description.layout import load_config
            g = self.layout.push_plan(bowl, load_config("pick.yaml")["push"])
            return self._dry("point", "push toward %s: block %.1f cm from x %.3f y %.3f to x %.3f y %.3f, "
                                      "gripper %.1f cm at %.1f cm/s (%.1f s), then home"
                                      % (bowl, g["length"] * 100, g["decision"][0], g["decision"][1],
                                         g["block_end"][0], g["block_end"][1], g["travel"] * 100,
                                         g["travel"] / g["seconds"] * 100, g["seconds"]))
        self._robot.push(bowl)

    def home(self):
        if self.dry_run:
            return self._dry("home", "home: publish {group: arm, target: home} on /moveit_by_name")
        self._robot.home()
