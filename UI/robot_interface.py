"""Robot interface for the HRI trust study UI.

Both classes have the same four methods. Each method blocks until finished.
FakeRobot works now (prints and sleeps). RosRobot is a stub for the next session.
"""
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


class RosRobot(object):
    """Real robot (ROS 1). Stub only, to be filled in the next session.

    TODO (next session):
    - import rospy here, lazily, so --dry-run never needs ROS
    - init the node, load poses from poses.yaml (recorded with record_pose.py)
    - map each block name to one of the 9 slots (separate config file)
    - fixed durations, each method blocks until the motion is finished
    """

    def __init__(self, config=None):
        self.config = config or {}

    def _not_ready(self, method):
        raise NotImplementedError(
            "RosRobot.%s() is not implemented yet. The ROS motion code is added in a "
            "later session. Use --dry-run to run the UI with the fake robot." % method)

    def pick(self, block_name):
        check_block(block_name)
        self._not_ready("pick")

    def place_decision(self):
        self._not_ready("place_decision")

    def point(self, bowl):
        check_bowl(bowl)
        self._not_ready("point")

    def home(self):
        self._not_ready("home")
