"""Mock-only tests for the link between the UI and the robot code.

No ROS master, no robot: rospy, moveit_commander, tf2_ros, actionlib and the gripper and
moveit_by_name messages are replaced by stubs before the robot code is imported.
Run from the UI folder:  python3 -m unittest discover -s tests -v
(moveit_msgs, geometry_msgs and shape_msgs are only used as plain message classes.)
"""
import csv
import warnings
import io
import math
import os
import sys
import threading
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

warnings.filterwarnings("ignore", message="The NumPy module was reloaded")
HERE = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.dirname(HERE)
PKG_SRC = os.path.abspath(os.path.join(UI_DIR, "..", "src", "hri_study_description", "src"))
sys.path.insert(0, UI_DIR)
sys.path.insert(0, PKG_SRC)

import robot_interface as ri  # noqa: E402
from hri_study_description.layout import Layout  # noqa: E402

J = ["panda_joint%d" % i for i in range(1, 8)]
HOME = [0.76, -0.2, 0.36, -2.87, 0.15, 2.7, 1.75]


# ---------------------------------------------------------------------------------------
# Stub world: records every robot side effect in order
# ---------------------------------------------------------------------------------------
class World(object):
    def __init__(self):
        self.events = []
        self.joints = list(HOME)
        self.execute_ok = True
        self.plan_ok = True
        self.grasp_ok = True
        self.moveit_by_name_moves = True
        self.plan_log = []      # (group, velocity scaling) per plan
        self.added = []         # planning scene objects added


class Traj(object):
    def __init__(self, start, end):
        pts = []
        for i, q in enumerate((start, end)):
            pts.append(types.SimpleNamespace(positions=list(q),
                                             time_from_start=types.SimpleNamespace(to_sec=lambda i=i: 2.0 * i)))
        self.joint_trajectory = types.SimpleNamespace(joint_names=list(J), points=pts)


class FakeGroup(object):
    def __init__(self, world, name):
        self.w, self.name = world, name
        self.start, self.target = None, None
        self.vel = None

    def set_max_velocity_scaling_factor(self, v):
        self.vel = v

    def __getattr__(self, attr):             # every setter we do not care about
        return lambda *a, **k: None

    def get_planning_frame(self):
        return "table"

    def get_active_joints(self):
        return list(J)

    def get_named_target_values(self, target):
        return dict(zip(J, HOME)) if target == "home" else {}

    def get_current_joint_values(self):
        return list(self.w.joints)

    def set_start_state(self, rs):
        self.start = list(rs.joint_state.position[:7])

    def set_joint_value_target(self, q):
        self.target = list(q)

    def set_pose_target(self, pose, link):
        self.target = [v + 0.01 for v in self.start]   # a small, smooth straight move

    def plan(self):
        self.w.events.append("plan")
        self.w.plan_log.append((self.name, self.vel))
        err = types.SimpleNamespace(val=1 if self.w.plan_ok else -31)
        return self.w.plan_ok, Traj(self.start, self.target), 0.1, err

    def execute(self, traj, wait=True):
        self.w.events.append("execute")
        if self.w.execute_ok:
            self.w.joints = list(traj.joint_trajectory.points[-1].positions)
        return self.w.execute_ok


def make_stubs(world):
    """sys.modules entries for everything that talks to ROS."""
    rospy = types.ModuleType("rospy")
    rospy.core = types.SimpleNamespace(is_initialized=lambda: False)
    rospy.init_node = mock.Mock()
    rospy.wait_for_service = lambda *a, **k: None
    rospy.Duration = lambda *a, **k: a
    rospy.sleep = lambda *a, **k: None
    rospy.is_shutdown = lambda: False
    rospy.loginfo = rospy.logwarn = rospy.logerr = lambda *a, **k: None
    rospy.get_param = lambda name, default=None: default

    def service_proxy(name, srv_type):
        if name == "/compute_ik":
            return lambda req: types.SimpleNamespace(
                error_code=types.SimpleNamespace(val=1),
                solution=types.SimpleNamespace(joint_state=types.SimpleNamespace(name=list(J), position=[0.1] * 7)))
        return lambda req: types.SimpleNamespace(valid=True, contacts=[])
    rospy.ServiceProxy = service_proxy

    class Publisher(object):
        def __init__(self, topic, msg_type, **k):
            self.topic = topic

        def get_num_connections(self):
            return 1

        def publish(self, msg):
            if self.topic == "/moveit_by_name":
                world.events.append("moveit_by_name %s/%s" % (msg.group, msg.target))
                if world.moveit_by_name_moves:
                    world.joints = list(HOME)
    rospy.Publisher = Publisher

    mc = types.ModuleType("moveit_commander")
    mc.roscpp_initialize = lambda *a: None
    mc.MoveGroupCommander = lambda name: FakeGroup(world, name)
    mc.RobotCommander = lambda: types.SimpleNamespace(
        get_current_state=lambda: None,
        get_current_variable_values=lambda: {"panda_finger_joint1": 0.0375})

    class Scene(object):
        def __init__(self, **k):
            pass

        def __getattr__(self, attr):
            return lambda *a, **k: None

        def get_attached_objects(self, ids):
            return {}

        def attach_object(self, obj, touch_links=None):
            world.events.append("attach")

        def add_cylinder(self, name, pose, height, radius):
            world.added.append(name)
    mc.PlanningSceneInterface = Scene

    tf2 = types.ModuleType("tf2_ros")
    tf2.Buffer = lambda: types.SimpleNamespace(transform=lambda ps, frame, timeout=None: ps)
    tf2.TransformListener = lambda buf: None

    al = types.ModuleType("actionlib")

    class Client(object):
        def __init__(self, name, action):
            self.name = name

        def wait_for_server(self, timeout):
            return True

        def send_goal(self, goal):
            world.events.append("gripper " + self.name.split("/")[-1])

        def wait_for_result(self, timeout):
            return True

        def cancel_goal(self):
            pass

        def get_result(self):
            ok = world.grasp_ok or not self.name.endswith("grasp")
            return types.SimpleNamespace(success=ok, error="" if ok else "no block")
    al.SimpleActionClient = Client

    def msg_module(name, classes):
        m = types.ModuleType(name)
        for c in classes:
            setattr(m, c, type(c, (object,), {"__init__": lambda self, **k: self.__dict__.update(k)}))
        return m

    return {
        "rospy": rospy, "moveit_commander": mc, "tf2_ros": tf2,
        "tf2_geometry_msgs": types.ModuleType("tf2_geometry_msgs"), "actionlib": al,
        "franka_gripper": types.ModuleType("franka_gripper"),
        "franka_gripper.msg": msg_module("franka_gripper.msg",
                                         ["GraspAction", "MoveAction", "MoveGoal", "GraspGoal", "GraspEpsilon"]),
        "moveit_by_name": types.ModuleType("moveit_by_name"),
        "moveit_by_name.msg": msg_module("moveit_by_name.msg", ["Command"]),
    }


class StubbedRos(object):
    """Context: stub modules in sys.modules, fresh import of hri_study_description.robot."""

    def __init__(self, world):
        self.patch = mock.patch.dict(sys.modules, make_stubs(world))

    def __enter__(self):
        self.patch.start()
        sys.modules.pop("hri_study_description.robot", None)
        import hri_study_description.robot as robot
        return robot

    def __exit__(self, *exc):
        sys.modules.pop("hri_study_description.robot", None)
        self.patch.stop()


def silent(fn, *a, **k):
    with redirect_stdout(io.StringIO()):
        return fn(*a, **k)


# ---------------------------------------------------------------------------------------
# 1. Names: UI block names, blocks.yaml slots, sequences.csv
# ---------------------------------------------------------------------------------------
class TestNames(unittest.TestCase):
    def test_every_ui_block_has_exactly_one_slot(self):
        layout = Layout()
        slots = [layout.block(name).id for name in ri.BLOCKS]
        self.assertEqual(len(set(slots)), 9)

    def test_every_block_in_sequences_csv_is_known(self):
        with open(os.path.join(UI_DIR, "sequences.csv")) as f:
            used = {row["block"] for row in csv.DictReader(f)}
        self.assertTrue(used <= set(ri.BLOCKS), used - set(ri.BLOCKS))

    def test_hexagon_is_grasped_on_its_flats(self):
        hexagon = Layout().block("red hexagon")
        yaws = [round(math.degrees(y)) for y, w in hexagon.grasp_options()]
        self.assertEqual(yaws, [0, 60, 120, 180, 240, 300])
        self.assertTrue(all(w == hexagon.across_flats for _, w in hexagon.grasp_options()))


# ---------------------------------------------------------------------------------------
# 2. RosRobot adapter (UI side)
# ---------------------------------------------------------------------------------------
class FakeStudyRobot(object):
    calls = []

    def __init__(self):
        FakeStudyRobot.calls = []
        self.fail = None

    def _do(self, call):
        FakeStudyRobot.calls.append(call)
        if self.fail == call.split()[0]:
            raise RuntimeError("robot failed in " + call)

    def pick(self, name):
        self._do("pick " + name)

    def place_decision(self):
        self._do("place_decision")

    def home(self):
        self._do("home")

    def push(self, bowl):
        self._do("push " + bowl)


class TestRosRobotAdapter(unittest.TestCase):
    def make(self):
        world = World()
        stubs = make_stubs(world)
        stubs["hri_study_description.robot"] = types.SimpleNamespace(StudyRobot=FakeStudyRobot)
        self.patch = mock.patch.dict(sys.modules, stubs)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        robot = silent(ri.RosRobot, {"fake_robot": {}})
        return robot, stubs["rospy"]

    def test_init_starts_the_node_without_signal_handlers(self):
        robot, rospy = self.make()
        rospy.init_node.assert_called_once()
        self.assertTrue(rospy.init_node.call_args[1]["disable_signals"])

    def test_calls_are_forwarded_in_order(self):
        robot, _ = self.make()
        robot.pick("red cuboid")
        robot.place_decision()
        robot.home()
        self.assertEqual(FakeStudyRobot.calls, ["pick red cuboid", "place_decision", "home"])

    def test_bad_names_are_rejected_before_the_robot(self):
        robot, _ = self.make()
        with self.assertRaises(ValueError):
            robot.pick("purple cuboid")
        with self.assertRaises(ValueError):
            robot.point("green")
        self.assertEqual(FakeStudyRobot.calls, [])

    def test_point_is_the_push(self):
        robot, _ = self.make()
        robot.point("red")
        robot.point("blue")
        self.assertEqual(FakeStudyRobot.calls, ["push red", "push blue"])

    def test_exceptions_propagate(self):
        robot, _ = self.make()
        robot._robot.fail = "pick"
        with self.assertRaises(RuntimeError):
            robot.pick("blue hexagon")

    def test_calls_work_from_a_worker_thread(self):
        robot, _ = self.make()
        result = {}

        def work():   # like hri_ui.run_robot
            try:
                robot.pick("green cylinder")
                robot.place_decision()
            except Exception as e:
                result["error"] = e
        t = threading.Thread(target=work)
        t.start()
        t.join(5)
        self.assertNotIn("error", result)
        self.assertEqual(FakeStudyRobot.calls, ["pick green cylinder", "place_decision"])


class TestRosDryRun(unittest.TestCase):
    def test_prints_calls_and_never_imports_ros(self):
        for mod in ("rospy", "moveit_commander", "hri_study_description.robot"):
            self.assertNotIn(mod, sys.modules)
        robot = silent(ri.RosRobot, {"fake_robot": {"pick": 0, "place_decision": 0, "home": 0, "point": 0}}, dry_run=True)
        out = io.StringIO()
        with redirect_stdout(out):
            robot.pick("red hexagon")
            robot.place_decision()
            robot.home()
        text = out.getvalue()
        self.assertIn("slot s3", text)
        self.assertIn("decision", text)
        self.assertIn("/moveit_by_name", text)
        out = io.StringIO()
        with redirect_stdout(out):
            robot.point("blue")
        self.assertIn("push toward blue: block 8.3 cm", out.getvalue())
        for mod in ("rospy", "moveit_commander", "hri_study_description.robot"):
            self.assertNotIn(mod, sys.modules)

    def test_dry_run_flags_are_exclusive(self):
        import hri_ui
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            hri_ui.main(["--participant", "P01", "--dry-run", "--ros-dry-run"])


# ---------------------------------------------------------------------------------------
# 3. StudyRobot (robot side) with stubbed ROS and MoveIt
# ---------------------------------------------------------------------------------------
class TestStudyRobot(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.ctx = StubbedRos(self.world)
        self.robot_mod = self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__)

    def make(self, **kw):
        kw.setdefault("confirm", False)
        return silent(self.robot_mod.StudyRobot, **kw)

    def moves(self):
        return [e for e in self.world.events if e != "plan"]

    def test_pick_plans_everything_first_then_moves_in_order(self):
        r = self.make()
        silent(r.pick, "red cuboid")
        ev = self.world.events
        self.assertEqual(ev[:3], ["plan"] * 3)
        self.assertEqual(self.moves(), ["gripper move", "execute", "execute", "gripper grasp", "attach", "execute"])
        self.assertEqual(r.held[0].name, "red cuboid")

    def test_place_decision_lets_go_then_goes_home(self):
        r = self.make()
        silent(r.pick, "blue cylinder")
        self.world.events = []
        silent(r.place_decision)
        self.assertEqual(self.moves(), ["execute", "execute", "gripper move", "execute", "moveit_by_name arm/home"])
        self.assertIsNone(r.held)

    def test_unknown_block_raises_before_any_motion(self):
        r = self.make()
        with self.assertRaises(self.robot_mod.RobotError):
            r.pick("purple cuboid")
        self.assertEqual(self.world.events, [])

    def test_place_without_a_block_raises(self):
        r = self.make()
        with self.assertRaises(self.robot_mod.RobotError):
            r.place_decision()
        self.assertEqual(self.world.events, [])

    def test_planning_failure_moves_nothing(self):
        r = self.make()
        self.world.plan_ok = False
        with self.assertRaises(self.robot_mod.RobotError):
            silent(r.pick, "red cuboid")
        self.assertEqual(self.moves(), [])

    def test_execution_failure_stops_the_call(self):
        r = self.make()
        self.world.execute_ok = False
        with self.assertRaises(self.robot_mod.RobotError):
            silent(r.pick, "red cuboid")
        self.assertEqual(self.moves(), ["gripper move", "execute"])   # no grasp after the failed move

    def test_failed_grasp_raises_and_does_not_lift(self):
        r = self.make()
        self.world.grasp_ok = False
        with self.assertRaises(self.robot_mod.RobotError):
            silent(r.pick, "red cuboid")
        self.assertEqual(self.moves(), ["gripper move", "execute", "execute", "gripper grasp"])
        self.assertIsNone(r.held)

    def test_home_uses_moveit_by_name_and_waits(self):
        r = self.make()
        self.world.joints = [0.0] * 7
        r.home()
        self.assertEqual(self.moves(), ["moveit_by_name arm/home"])
        self.assertTrue(r.at_home())

    def test_home_raises_when_the_arm_does_not_arrive(self):
        r = self.make()
        r.cfg["home"]["timeout"] = 0.05
        self.world.joints = [0.0] * 7
        self.world.moveit_by_name_moves = False
        with self.assertRaises(self.robot_mod.RobotError):
            r.home()

    def test_confirm_q_stops_before_moving(self):
        r = self.make(confirm=True)
        with mock.patch("builtins.input", return_value="q"):
            with self.assertRaises(self.robot_mod.RobotError):
                silent(r.pick, "red cuboid")
        self.assertEqual(self.moves(), [])

    def test_confirm_enter_runs_each_move(self):
        r = self.make(confirm=True)
        with mock.patch("builtins.input", return_value="") as ask:
            silent(r.pick, "red cuboid")
        self.assertEqual(ask.call_count, 5)   # open, above, down, grasp, lift

    def test_pick_tries_another_posture_when_the_way_down_fails(self):
        r = self.make()
        postures = iter([[0.1 * i] * 7 for i in range(1, 100)])
        r.ik = lambda *a, **k: next(postures)        # a different posture on every IK call
        real_plan_line = r.plan_line
        tried = []

        def plan_line(start, pose, what):
            tried.append(what)
            if what == "straight down" and len(tried) == 1:
                raise self.robot_mod.RobotError("straight down: no plan found (MoveIt error code -31)")
            return real_plan_line(start, pose, what)
        r.plan_line = plan_line
        silent(r.pick, "red cylinder")
        self.assertEqual(tried[:3], ["straight down", "straight down", "lift"])   # 2nd posture worked
        self.assertEqual(self.moves(), ["gripper move", "execute", "execute", "gripper grasp", "attach", "execute"])

    def test_pick_fails_cleanly_when_no_posture_works(self):
        r = self.make()
        r.plan_line = mock.Mock(side_effect=self.robot_mod.RobotError("no plan found (-31)"))
        with self.assertRaisesRegex(self.robot_mod.RobotError, "none of .* arm postures"):
            silent(r.pick, "red cylinder")
        self.assertEqual(self.moves(), [])

    def test_dry_run_plans_but_never_moves(self):
        r = self.make(dry_run=True)
        silent(r.pick, "green hexagon")
        silent(r.place_decision)
        self.assertEqual(self.moves(), [])
        self.assertGreater(self.world.events.count("plan"), 0)


# ---------------------------------------------------------------------------------------
# 4. Push cue
# ---------------------------------------------------------------------------------------
class TestPushGeometry(unittest.TestCase):
    def setUp(self):
        from hri_study_description.layout import load_config
        self.layout, self.push = Layout(), load_config("pick.yaml")["push"]

    def test_red_and_blue_are_mirrors(self):
        red = self.layout.push_plan("red", self.push, 0.0225)
        blue = self.layout.push_plan("blue", self.push, 0.0225)
        for key in ("length", "travel", "seconds", "yaw"):
            self.assertAlmostEqual(red[key], blue[key], places=9, msg=key)
        dy = red["decision"][1]
        for key in ("start", "tcp_end", "block_end"):
            self.assertAlmostEqual(red[key][0], blue[key][0], places=9)
            self.assertAlmostEqual(red[key][1] - dy, -(blue[key][1] - dy), places=9, msg=key)

    def test_block_stops_15_cm_from_the_bowl(self):
        for color in ("red", "blue"):
            g = self.layout.push_plan(color, self.push)
            bowl = self.layout.bowl(color)
            self.assertAlmostEqual(math.hypot(bowl["x"] - g["block_end"][0], bowl["y"] - g["block_end"][1]),
                                   self.push["end_distance_to_bowl"], places=9)

    def test_too_short_or_too_long_is_refused(self):
        for end in (0.30, 0.01):    # push of -7 cm and 22 cm
            cfg = dict(self.push, end_distance_to_bowl=end)
            with self.assertRaisesRegex(ValueError, "push toward red would be"):
                self.layout.push_plan("red", cfg)

    def test_unknown_bowl(self):
        with self.assertRaises(ValueError):
            self.layout.push_plan("green", self.push)


class TestPushRobot(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.ctx = StubbedRos(self.world)
        self.robot_mod = self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__)
        self.r = silent(self.robot_mod.StudyRobot, confirm=False)

    def moves(self):
        return [e for e in self.world.events if e != "plan"]

    def test_push_plans_first_then_moves_in_order_and_ends_home(self):
        silent(self.r.push, "red")
        ev = self.world.events
        self.assertEqual(ev[:5], ["plan"] * 5)
        self.assertEqual(self.moves(), ["gripper move"] + ["execute"] * 5 + ["moveit_by_name arm/home"])
        self.assertIn("obj_bowl_red", self.world.added)
        self.assertIn("obj_bowl_blue", self.world.added)

    def test_only_the_push_is_slow(self):
        silent(self.r.push, "blue")
        speeds = [v for g, v in self.world.plan_log]
        slow = self.r.cfg["push"]["speed"] / 1.0      # stub max_trans_vel 1.0
        self.assertEqual(speeds.count(slow), 1)       # one move at push speed
        self.assertEqual(speeds[2], slow)             # sideways, down, PUSH, up, above

    def test_red_and_blue_make_the_same_moves(self):
        silent(self.r.push, "red")
        red = list(self.world.plan_log), self.moves()
        self.world.plan_log, self.world.events = [], []
        silent(self.r.push, "blue")
        self.assertEqual(red, (self.world.plan_log, self.moves()))

    def test_bad_bowl_and_held_block_are_refused(self):
        with self.assertRaises(self.robot_mod.RobotError):
            self.r.push("green")
        self.r.held = (Layout().block("red cuboid"), 0.045, 0.0)
        with self.assertRaises(self.robot_mod.RobotError):
            self.r.push("red")
        self.assertEqual(self.moves(), [])

    def test_failed_push_plan_moves_nothing(self):
        real = self.r.plan_line

        def plan_line(start, pose, what, scaling=None):
            if what.startswith("push"):
                raise self.robot_mod.RobotError("push: no plan found")
            return real(start, pose, what, scaling)
        self.r.plan_line = plan_line
        with self.assertRaises(self.robot_mod.RobotError):
            silent(self.r.push, "red")
        self.assertEqual(self.moves(), [])


if __name__ == "__main__":
    unittest.main()
