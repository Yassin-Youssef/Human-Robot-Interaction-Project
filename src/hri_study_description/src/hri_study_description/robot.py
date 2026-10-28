"""Robot motions for the HRI study (Franka Panda, MoveIt, ROS 1).

StudyRobot has the calls the UI needs. Each call blocks until the robot has stopped and
raises RobotError on any failure:
  pick(name)        steps 1-5: open, move above the block, straight down, grasp, lift
  place_decision()  steps 6-9: move above the decision spot, straight down, let go,
                    straight up, then home()
  push(bowl)        the cue: from home push the placed block toward the "red" or "blue"
                    bowl, then home. Red and blue are exact mirror images.
  home()            the lab's "Move Home" (script panel): moveit_by_name, target "home"
  up()              recovery: straight up from wherever the arm is

Every call plans all its moves first (nothing moves if planning fails), then runs them.
With confirm: true in pick.yaml, it waits for Enter in the terminal before each move
(type q + Enter to stop). With dry_run=True it only plans and prints, nothing moves.

The caller must call rospy.init_node() first (in the main thread).
Settings: config/pick.yaml. Blocks and place spots: config/blocks.yaml.
"""
import math
import sys
import time

import actionlib
import moveit_commander
import rospy
import tf2_geometry_msgs  # noqa: F401  (lets tf2 transform PoseStamped)
import tf2_ros
from geometry_msgs.msg import Pose, PoseStamped
from moveit_msgs.msg import (AttachedCollisionObject, CollisionObject, DisplayTrajectory,
                             MoveItErrorCodes, PositionIKRequest, RobotState)
from moveit_msgs.srv import GetPositionIK, GetStateValidity, GetStateValidityRequest
from shape_msgs.msg import SolidPrimitive

from .layout import Layout, load_config


class RobotError(RuntimeError):
    pass


def down_quaternion(yaw):
    """(x, y, z, w) for the gripper pointing straight down, turned by yaw.

    ROBOT-SPECIFIC: this is Rz(yaw) * Rx(pi). Rx(pi) turns panda_hand_tcp so that its z
    axis (the approach direction) points down. The Franka fingers close along the TCP
    y axis, which is then perpendicular to yaw.
    """
    return (math.cos(yaw / 2.0), math.sin(yaw / 2.0), 0.0, 0.0)


def joint_distance(a, b):
    return sum(abs(x - y) for x, y in zip(a, b))


def primitive(block, padding=0.0):
    shape = block.collision_shape(padding)
    if shape[0] == "box":
        return SolidPrimitive(type=SolidPrimitive.BOX, dimensions=shape[1])
    # SolidPrimitive cylinder dimensions are [height, radius].
    return SolidPrimitive(type=SolidPrimitive.CYLINDER, dimensions=[shape[1], shape[2]])


class StudyRobot(object):
    def __init__(self, dry_run=False, sim=False, confirm=None, place="decision"):
        self.cfg = load_config("pick.yaml")
        self.layout = Layout()
        self.place = self.layout.place(place)
        self.dry_run, self.sim = dry_run, sim
        self.confirm = self.cfg.get("confirm", True) if confirm is None else confirm
        self.held = None          # (block, finger width, gripper yaw) between pick and place
        self.placed = None        # block last put on the decision spot (for the push)
        self._virtual = None      # dry run: planned arm joints instead of the real ones
        c = self.cfg

        moveit_commander.roscpp_initialize(sys.argv[:1])
        self.robot = moveit_commander.RobotCommander()
        self.arm = moveit_commander.MoveGroupCommander(c["arm_group"])
        self.eef = c["eef_link"]
        self.arm.set_end_effector_link(self.eef)
        self.arm.set_max_velocity_scaling_factor(c["velocity_scaling"])
        self.arm.set_max_acceleration_scaling_factor(c["acceleration_scaling"])
        self.arm.set_planning_time(c["planning_time"])
        self.arm.set_planning_pipeline_id("ompl")   # free moves: collision-avoiding planner
        self.arm.set_planner_id("")                  # the pipeline's default planner
        # Straight moves: same joints, but an IK solver that searches locally from the
        # previous posture (line_group in pick.yaml), with the Pilz LIN planner.
        self.line_arm = moveit_commander.MoveGroupCommander(c["line_group"])
        self.line_arm.set_end_effector_link(self.eef)
        self.line_arm.set_max_velocity_scaling_factor(c["velocity_scaling"])
        self.line_arm.set_max_acceleration_scaling_factor(c["acceleration_scaling"])
        self.line_arm.set_planning_pipeline_id("pilz_industrial_motion_planner")
        self.line_arm.set_planner_id("LIN")
        self.frame = self.arm.get_planning_frame()
        self.joints = self.arm.get_active_joints()
        self.scene = moveit_commander.PlanningSceneInterface(synchronous=True)

        self.tf = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf)
        for srv in ("/compute_ik", "/check_state_validity"):
            rospy.wait_for_service(srv, 10.0)
        self.ik_srv = rospy.ServiceProxy("/compute_ik", GetPositionIK)
        self.valid_srv = rospy.ServiceProxy("/check_state_validity", GetStateValidity)
        # RViz (MotionPlanning display) plays trajectories published here.
        self.display = rospy.Publisher("/move_group/display_planned_path", DisplayTrajectory,
                                       queue_size=1, latch=True)
        self._setup_gripper()
        self._setup_home()

    # ==== the calls ========================================================================
    def pick(self, name):
        """Steps 1-5: take block `name` ("red cuboid" or slot id) from its slot and lift it."""
        if self.held is not None:
            raise RobotError("pick(%r): already holding %s" % (name, self.held[0].name))
        try:
            b = self.layout.block(name)
        except ValueError as e:
            raise RobotError(str(e))
        c = self.cfg
        open_width = c["gripper"]["open_width"]
        grasp_z = self.grasp_height(b)

        self.reset_scene()
        options = [(yaw, w) for yaw, w in b.grasp_options() if w <= open_width - 0.01]

        def lines(q, yaw, width):   # the straight moves from posture q, or RobotError
            held = self.held_block(b, width)
            down = self.plan_line(self.state(q, open_width),
                                  self.table_pose(b.x, b.y, grasp_z, yaw, down=True), "straight down")
            self.check(down, open_width, "straight down")
            up = self.plan_line(self.state(self.end_joints(down), width, held),
                                self.table_pose(b.x, b.y, grasp_z + c["lift_height"], yaw, down=True),
                                "lift")
            self.check(up, width, "lift", held)
            return down, up

        # Plan every move before anything moves.
        self.remove_obstacle(b)   # the fingers must go around this block
        try:
            q_above, yaw, width, (down, up) = self.posture_with_lines(
                b.x, b.y, grasp_z + c["approach_height"], options, open_width, None, lines, b.label)
        finally:
            self.add_obstacle(b)  # stays an obstacle until the arm is above it
        start = self.state(self.current_joints(), open_width)
        move_above = self.plan_free(start, q_above, "move above %s" % b.name)
        self.check(move_above, open_width, "move above the block", skip_start=True)
        held = self.held_block(b, width)
        self.show([move_above, down, up])
        self.report("pick %s" % b.label, [("open gripper to %.1f cm" % (open_width * 100), None),
                                          ("move above the block", move_above),
                                          ("straight down", down),
                                          ("grasp at %.1f cm" % (width * 100), None),
                                          ("straight up", up)])
        if self.dry_run:
            self._virtual = self.end_joints(up)
            self.held = (b, width, yaw)
            return

        self.ask("open gripper to %.1f cm" % (open_width * 100))
        self.gripper_open(open_width)
        self.execute(move_above, "move above %s" % b.name)
        self.remove_obstacle(b)
        self.execute(down, "straight down")
        self.ask("grasp %s" % b.name)
        self.gripper_grasp(width)
        self.scene.attach_object(held, touch_links=c["touch_links"])
        self.held = (b, width, yaw)
        self.execute(up, "lift %s" % b.name)

    def place_decision(self):
        """Steps 6-9: put the held block on the decision spot, let go, move up, then home()."""
        if self.held is None:
            raise RobotError("place_decision(): no block in the gripper (call pick first)")
        b, width, _ = self.held
        c, p = self.cfg, self.place
        open_width = c["gripper"]["open_width"]
        place_z = self.grasp_height(b) + c["place_clearance"]
        held = self.held_block(b, width)

        # Any 90 degree gripper turn keeps a cube square to the table (60 for a hexagon).
        step = math.pi / 3 if b.shape == "hexagon" else math.pi / 2
        options = [(p.get("yaw", 0.0) + k * step, None) for k in range(int(round(2 * math.pi / step)))]

        def lines(q, yaw, _):   # the straight moves from posture q, or RobotError
            lower = self.plan_line(self.state(q, width, held),
                                   self.table_pose(p["x"], p["y"], place_z, yaw, down=True),
                                   "straight down to the place spot")
            self.check(lower, width, "straight down to the place spot", held)
            leave = self.plan_line(self.state(self.end_joints(lower), open_width),
                                   self.table_pose(p["x"], p["y"], place_z + c["lift_height"], yaw, down=True),
                                   "straight up from the place spot")
            self.check(leave, open_width, "straight up from the place spot")
            return lower, leave

        q_place, yaw, _, (lower, leave) = self.posture_with_lines(
            p["x"], p["y"], place_z + c["approach_height"], options, width, held, lines,
            "the place spot '%s'" % p["id"])
        to_place = self.plan_free(self.state(self.current_joints(), width, held), q_place,
                                  "move above the place spot")
        self.check(to_place, width, "move above the place spot", held)
        self.show([to_place, lower, leave])
        self.report("place %s on '%s'" % (b.label, p["id"]),
                    [("move above the place spot", to_place), ("straight down", lower),
                     ("open gripper to %.1f cm (let go)" % (open_width * 100), None),
                     ("straight up", leave), ("home (moveit_by_name)", None)])
        if self.dry_run:
            self._virtual = self.end_joints(leave)
            self.held = None
            self.placed = b
            self.home()
            return

        self.execute(to_place, "move above the place spot")
        self.execute(lower, "straight down to the place spot")
        self.ask("open gripper (let go of %s)" % b.name)
        self.gripper_open(open_width)
        self.remove_obstacle(b)   # the block stands on the place spot now, not in the plan
        self.held = None
        self.placed = b
        self.execute(leave, "straight up from the place spot")
        self.home()

    def push(self, bowl):
        """The cue: push the block on the decision spot toward the `bowl` ("red"/"blue") bowl.

        From home: close the gripper, move above the decision spot, straight sideways
        start_offset away from the target bowl, straight down, push toward the bowl at
        push.speed, straight up, home(). Red and blue are mirror images (same distances,
        heights, speed); only the move home starts from the other side.
        """
        if self.held is not None:
            raise RobotError("push(%r): still holding %s" % (bowl, self.held[0].name))
        c, pc = self.cfg, self.cfg["push"]
        half = self.placed.half_width() if self.placed is not None else None
        try:
            g = self.layout.push_plan(bowl, pc, half)
        except ValueError as e:
            raise RobotError(str(e))
        low_z = pc["fingertip_height"] + c["fingertip_length"]   # TCP height during the push
        if pc["fingertip_height"] < c["min_table_clearance"]:
            raise RobotError("push.fingertip_height is below min_table_clearance")
        high_z = low_z + c["approach_height"]
        closed, yaw = pc["closed_width"], g["yaw"]
        dx, dy = g["decision"]
        sx, sy = g["start"]
        ex, ey = g["tcp_end"]
        self.add_bowls()
        # Max straight-line speed (Pilz cartesian limit) -> scaling that gives push.speed.
        max_vel = rospy.get_param("/robot_description_planning/cartesian_limits/max_trans_vel", 1.0)
        slow = pc["speed"] / max_vel

        def lines(q, yaw, _):
            side = self.plan_line(self.state(q, closed), self.table_pose(sx, sy, high_z, yaw, down=True),
                                  "sideways to the push start")
            self.check(side, closed, "sideways to the push start")
            down = self.plan_line(self.state(self.end_joints(side), closed),
                                  self.table_pose(sx, sy, low_z, yaw, down=True), "down to the push start")
            self.check(down, closed, "down to the push start")
            push = self.plan_line(self.state(self.end_joints(down), closed),
                                  self.table_pose(ex, ey, low_z, yaw, down=True),
                                  "push toward %s" % bowl, scaling=slow)
            self.check(push, closed, "push toward %s" % bowl)
            up = self.plan_line(self.state(self.end_joints(push), closed),
                                self.table_pose(ex, ey, high_z, yaw, down=True), "straight up")
            self.check(up, closed, "straight up")
            return side, down, push, up

        q_above, yaw, _, (side, down, push, up) = self.posture_with_lines(
            dx, dy, high_z, [(yaw, None), (yaw + math.pi, None)], closed, None, lines,
            "the decision spot")
        to_above = self.plan_free(self.state(self.current_joints(), closed), q_above,
                                  "move above the decision spot")
        self.check(to_above, closed, "move above the decision spot", skip_start=True)
        self.show([to_above, side, down, push, up])
        self.report("push toward %s: block %.1f cm, gripper %.1f cm at %.1f cm/s"
                    % (bowl, g["length"] * 100, g["travel"] * 100, pc["speed"] * 100),
                    [("close gripper", None), ("move above the decision spot", to_above),
                     ("sideways %.0f cm" % (pc["start_offset"] * 100), side),
                     ("down, fingertips %.0f cm above the table" % (pc["fingertip_height"] * 100), down),
                     ("push toward %s" % bowl, push), ("straight up", up), ("home (moveit_by_name)", None)])
        if self.dry_run:
            self._virtual = self.end_joints(up)
            self.home()
            return

        self.ask("close gripper")
        self.gripper_open(closed)
        self.execute(to_above, "move above the decision spot")
        self.execute(side, "sideways to the push start")
        self.execute(down, "down to the push start")
        self.execute(push, "push toward %s" % bowl)
        self.execute(up, "straight up")
        self.placed = None
        self.home()

    def home(self):
        """The lab's "Move Home": publish {group, target} on /moveit_by_name, as the script
        panel button does, then wait until the arm is at the SRDF "home" joints."""
        h = self.cfg["home"]
        if self.dry_run:
            print("[dry run] home: publish {group: %s, target: %s} on /moveit_by_name"
                  % (h["group"], h["target"]))
            self._virtual = None
            return
        if self.at_home():
            rospy.loginfo("home: already there")
            return
        self.ask("home (moveit_by_name)")
        if self.sim:
            self.arm.set_named_target(h["target"])
            ok = self.arm.go(wait=True)
            self.arm.stop()
            if not ok:
                raise RobotError("home: MoveIt could not reach '%s'" % h["target"])
            return
        if self.home_pub.get_num_connections() == 0:
            raise RobotError("home: no moveit_by_name node listening (is robot.launch running?)")
        from moveit_by_name.msg import Command
        self.home_pub.publish(Command(group=h["group"], target=h["target"]))
        deadline = time.time() + h["timeout"]
        while time.time() < deadline and not rospy.is_shutdown():
            rospy.sleep(0.2)
            if self.at_home():
                rospy.sleep(0.5)   # let the arm settle
                return
        raise RobotError("home: arm not at '%s' after %.0f s (moveit_by_name failed or stopped)"
                         % (h["target"], h["timeout"]))

    def up(self, name=None):
        """Recovery: move straight up lift_height from wherever the arm is now."""
        c = self.cfg
        b = self.layout.block(name) if name else None
        fingers = 2.0 * self.robot.get_current_variable_values()[c["finger_joint"]]
        target = self.arm.get_current_pose(self.eef)
        target.pose.position.z += c["lift_height"]
        if b is not None:
            self.remove_obstacle(b)   # the fingers may still be around this block
        try:
            traj = self.plan_line(self.state(self.arm.get_current_joint_values(), fingers),
                                  target, "straight up")
            self.check(traj, fingers, "straight up", skip_start=True)
            self.show([traj])
            self.report("recovery", [("straight up %.0f cm" % (c["lift_height"] * 100), traj)])
            if not self.dry_run:
                self.execute(traj, "straight up")
        finally:
            if b is not None:
                self.add_obstacle(b)

    # ==== helpers ==========================================================================
    def ask(self, what):
        """Print the next move. With confirm: true also wait for Enter (q + Enter stops)."""
        if self.dry_run:
            return
        if not self.confirm:
            print("Next: %s" % what)
            return
        answer = input("Next: %s. Enter to move, q + Enter to stop: " % what)
        if answer.strip().lower() == "q":
            raise RobotError("stopped by the experimenter before: %s" % what)

    def report(self, title, steps):
        print("\n%s (%.0f%% speed)%s:" % (title, self.cfg["velocity_scaling"] * 100,
                                         ", DRY RUN, nothing moves" if self.dry_run else ""))
        for i, (text, traj) in enumerate(steps, 1):
            secs = "" if traj is None else "%5.1f s" % traj.joint_trajectory.points[-1].time_from_start.to_sec()
            print("  %d. %-34s %s" % (i, text, secs))

    def current_joints(self):
        if self.dry_run and self._virtual is not None:
            return list(self._virtual)
        return list(self.arm.get_current_joint_values())

    def at_home(self):
        tol = self.cfg["home"]["tolerance"]
        now = self.arm.get_current_joint_values()
        return max(abs(a - b) for a, b in zip(now, self.home_joints)) < tol

    def _setup_home(self):
        h = self.cfg["home"]
        named = self.arm.get_named_target_values(h["target"])   # SRDF group_state "home"
        if not named:
            raise RobotError("no named target '%s' in the SRDF" % h["target"])
        self.home_joints = [named[j] for j in self.joints]
        if not self.sim and not self.dry_run:
            from moveit_by_name.msg import Command
            self.home_pub = rospy.Publisher("/moveit_by_name", Command, queue_size=1)
            deadline = time.time() + 3.0
            while self.home_pub.get_num_connections() == 0 and time.time() < deadline:
                rospy.sleep(0.1)

    # ---- gripper ---------------------------------------------------------------------------
    def _setup_gripper(self):
        if self.dry_run:
            return
        if self.sim:
            # Simulation: the fake controller of the MoveIt "gripper" group.
            self.gripper = moveit_commander.MoveGroupCommander(self.cfg["gripper_group"])
            return
        # ROBOT-SPECIFIC: the real Franka hand is driven by the franka_gripper actions,
        # which hardware.launch starts. MoveIt has no controller for it.
        from franka_gripper.msg import GraspAction, MoveAction
        self.move_client = actionlib.SimpleActionClient("/franka_gripper/move", MoveAction)
        self.grasp_client = actionlib.SimpleActionClient("/franka_gripper/grasp", GraspAction)
        for client in (self.move_client, self.grasp_client):
            if not client.wait_for_server(rospy.Duration(5.0)):
                raise RobotError("franka_gripper actions not available. Is robot.launch running?")

    def gripper_open(self, width):
        if self.sim:
            return self._sim_gripper(width)
        from franka_gripper.msg import MoveGoal
        goal = MoveGoal(width=width, speed=self.cfg["gripper"]["speed"])
        self._gripper_action(self.move_client, goal, "open gripper to %.1f cm" % (width * 100))

    def gripper_grasp(self, width):
        if self.sim:
            return self._sim_gripper(width)
        from franka_gripper.msg import GraspEpsilon, GraspGoal
        g = self.cfg["gripper"]
        goal = GraspGoal(width=width, speed=g["speed"], force=g["force"],
                         epsilon=GraspEpsilon(inner=g["epsilon"], outer=g["epsilon"]))
        # The fingers close until they touch the block. The action fails if the final
        # width is not width +- epsilon (for example when nothing is between the fingers).
        self._gripper_action(self.grasp_client, goal, "grasp at %.1f cm" % (width * 100))

    def _gripper_action(self, client, goal, what):
        client.send_goal(goal)
        if not client.wait_for_result(rospy.Duration(self.cfg["gripper"]["timeout"])):
            client.cancel_goal()
            raise RobotError("%s: timed out" % what)
        result = client.get_result()
        if result is None or not result.success:
            raise RobotError("%s failed: %s" % (what, getattr(result, "error", "no result")))
        rospy.loginfo("%s: ok", what)

    def _sim_gripper(self, width):
        # ROBOT-SPECIFIC: each finger joint moves half of the total opening.
        self.gripper.set_joint_value_target({self.cfg["finger_joint"]: width / 2.0})
        if not self.gripper.go(wait=True):
            raise RobotError("simulated gripper move to %.1f cm failed" % (width * 100))
        self.gripper.stop()

    # ---- poses and planning scene -----------------------------------------------------------
    def table_pose(self, x, y, z, yaw=0.0, down=False):
        """Pose given in table_corner, returned in the planning frame."""
        ps = PoseStamped()
        ps.header.frame_id = self.cfg["table_frame"]
        ps.pose.position.x, ps.pose.position.y, ps.pose.position.z = x, y, z
        q = down_quaternion(yaw) if down else (0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))
        o = ps.pose.orientation
        o.x, o.y, o.z, o.w = q
        try:
            return self.tf.transform(ps, self.frame, rospy.Duration(2.0))
        except Exception as e:
            raise RobotError("no '%s' frame. Is robot.launch running? (%s)" % (self.cfg["table_frame"], e))

    @staticmethod
    def object_id(block):
        return "obj_" + block.key     # id in the MoveIt planning scene

    def add_obstacle(self, block):
        pose = self.table_pose(block.x, block.y, block.height / 2.0, block.yaw)
        shape = block.collision_shape(self.cfg["obstacle_padding"])
        if shape[0] == "box":
            self.scene.add_box(self.object_id(block), pose, tuple(shape[1]))
        else:
            self.scene.add_cylinder(self.object_id(block), pose, shape[1], shape[2])

    def remove_obstacle(self, block):
        oid = self.object_id(block)
        if self.scene.get_attached_objects([oid]):
            self.scene.remove_attached_object(self.eef, oid)
        self.scene.remove_world_object(oid)

    def add_bowls(self):
        """Both bowls as obstacles: a cylinder around the oval bowl, push.bowl_margin bigger."""
        size, margin = self.layout.bowl_size, self.cfg["push"]["bowl_margin"]
        if not size:
            return
        radius = max(size["size_x"], size["size_y"]) / 2 + margin
        height = size["height"] + margin
        for color, b in self.layout.bowls.items():
            self.scene.add_cylinder("obj_bowl_" + color, self.table_pose(b["x"], b["y"], height / 2),
                                    height, radius)

    def reset_scene(self):
        """Every slot block becomes an obstacle, slightly bigger than the real block."""
        self.add_bowls()
        for b in self.layout.blocks:
            if self.held is not None and b is self.held[0]:
                continue
            self.remove_obstacle(b)
            self.add_obstacle(b)

    def grasp_height(self, block):
        """TCP height above the table for the grasp: pads centered on the block, but the
        fingertips never closer than min_table_clearance to the table."""
        c = self.cfg
        return max(block.height / 2.0 + c["grasp_z_offset"],
                   c["fingertip_length"] + c["min_table_clearance"])

    def held_block(self, block, width):
        """The grasped block as a MoveIt object fixed to the TCP."""
        aco = AttachedCollisionObject()
        aco.link_name = self.eef
        aco.touch_links = self.cfg["touch_links"]
        aco.object.id = self.object_id(block)
        aco.object.header.frame_id = self.eef
        aco.object.operation = CollisionObject.ADD
        aco.object.pose.orientation.w = 1.0
        # ROBOT-SPECIFIC: the TCP z axis points down into the block, so the block center is
        # (grasp height - half the block height) along +z. The shapes are symmetric under
        # the gripper turns used, so no extra rotation is needed.
        p = Pose()
        p.position.z = self.grasp_height(block) - block.height / 2.0
        p.orientation.w = 1.0
        aco.object.primitives = [primitive(block)]
        aco.object.primitive_poses = [p]
        return aco

    def state(self, arm_joints, finger_width, held=None):
        """Robot state for planning: arm joints, finger opening, optionally a held block."""
        rs = RobotState()
        rs.is_diff = True   # everything not set here comes from the current planning scene
        rs.joint_state.name = list(self.joints) + [self.cfg["finger_joint"]]
        rs.joint_state.position = list(arm_joints) + [finger_width / 2.0]
        if held is not None:
            rs.attached_collision_objects = [held]
        return rs

    # ---- planning -------------------------------------------------------------------------
    def ik_seed(self, x, y):
        """Start posture for the IK search: ik_seed with joint 1 turned toward (x, y)."""
        ps = PoseStamped()
        ps.header.frame_id = self.cfg["table_frame"]
        ps.pose.position.x, ps.pose.position.y = x, y
        ps.pose.orientation.w = 1.0
        try:
            p = self.tf.transform(ps, self.cfg["base_frame"], rospy.Duration(2.0)).pose.position
        except Exception as e:
            raise RobotError("no '%s' frame (%s)" % (self.cfg["base_frame"], e))
        seed = list(self.cfg["ik_seed"])
        seed[0] = math.atan2(p.y, p.x)   # ROBOT-SPECIFIC: joint 1 turns the whole arm about the base
        return seed

    def posture_with_lines(self, x, y, tcp_z, options, finger_width, held, lines, what):
        """Arm posture above (x, y) from which the straight moves can be planned.

        The IK search is partly random and close to the robot base many postures cannot go
        straight down (the folded wrist touches its own safety shapes at the bottom). So
        collect several different postures (posture_tries rounds over all gripper turns),
        try them closest to the start posture first, and keep the first one for which
        lines(q, yaw, value) plans. Returns (q, yaw, value, lines result).
        """
        seed = self.ik_seed(x, y)
        candidates = []
        for _ in range(self.cfg.get("posture_tries", 5)):
            for yaw, value in options:
                q = self.ik(self.table_pose(x, y, tcp_z, yaw, down=True), seed, finger_width, held)
                if q is not None and all(joint_distance(q, other[0]) > 0.05 for other in candidates):
                    candidates.append((q, yaw, value))
        if not candidates:
            raise RobotError("no collision-free arm pose above %s (out of reach?)" % what)
        candidates.sort(key=lambda cand: joint_distance(cand[0], seed))
        error = None
        for q, yaw, value in candidates:
            try:
                return q, yaw, value, lines(q, yaw, value)
            except RobotError as e:
                error = e
        raise RobotError("%s: none of %d arm postures can do the straight moves. Last error: %s"
                         % (what, len(candidates), error))

    def ik(self, pose, seed, finger_width, held=None):
        """Collision-free arm joints for the TCP pose, starting the search from seed."""
        req = PositionIKRequest()
        req.group_name = self.cfg["arm_group"]
        req.robot_state = self.state(seed, finger_width, held)
        req.avoid_collisions = True
        req.ik_link_name = self.eef
        req.pose_stamped = pose
        req.timeout = rospy.Duration(1.0)
        res = self.ik_srv(req)
        if res.error_code.val != MoveItErrorCodes.SUCCESS:
            return None
        js = res.solution.joint_state
        return [js.position[js.name.index(j)] for j in self.joints]

    def plan_free(self, start, joints, what):
        """Collision-free move to arm joints (OMPL). Avoids every obstacle in the scene."""
        self.arm.set_start_state(start)
        self.arm.set_joint_value_target(joints)
        return self._plan(self.arm, what)

    def plan_line(self, start, pose, what, scaling=None):
        """Straight line of the TCP to pose (Pilz LIN), with a smooth speed profile.
        scaling: other speed and acceleration scaling for this move only (the push)."""
        g = self.line_arm
        g.set_start_state(start)
        g.set_pose_target(pose, self.eef)
        if scaling is not None:
            g.set_max_velocity_scaling_factor(scaling)
            g.set_max_acceleration_scaling_factor(scaling)
        try:
            traj = self._plan(g, what)
        finally:
            if scaling is not None:
                g.set_max_velocity_scaling_factor(self.cfg["velocity_scaling"])
                g.set_max_acceleration_scaling_factor(self.cfg["acceleration_scaling"])
        self.check_smooth(traj, what)
        return traj

    def _plan(self, group, what):
        try:
            ok, traj, _, error = group.plan()
        finally:
            group.clear_pose_targets()
            group.set_start_state_to_current_state()
        if not ok or not traj.joint_trajectory.points:
            raise RobotError("%s: no plan found (MoveIt error code %d)" % (what, error.val))
        return traj

    def check_smooth(self, traj, what):
        """Refuse a straight move whose joints zig-zag: the arm would shake."""
        pts = [self.joints_of(traj, p) for p in traj.joint_trajectory.points]
        travel = sum(joint_distance(p, q) for p, q in zip(pts, pts[1:]))
        direct = joint_distance(pts[0], pts[-1])
        if travel > self.cfg["max_joint_travel_ratio"] * direct + 0.02:
            raise RobotError("%s: the joints would zig-zag (%.2f rad of travel for %.2f rad "
                             "direct), so the arm would shake. Not moving." % (what, travel, direct))

    def check(self, traj, finger_width, what, held=None, skip_start=False):
        """Every point of the path must be free of collisions (blocks, table, robot).

        skip_start: do not check the first point, which is where the arm is now. The lab's
        folded "home" pose counts as self-colliding in MoveIt's padded safety model
        (the *_sc links), although the arm is fine there.
        """
        points = traj.joint_trajectory.points
        for pt in (points[1:] if skip_start else points):
            req = GetStateValidityRequest(
                robot_state=self.state(self.joints_of(traj, pt), finger_width, held),
                group_name=self.cfg["arm_group"])
            res = self.valid_srv(req)
            if not res.valid:
                pairs = ", ".join("%s/%s" % (c.contact_body_1, c.contact_body_2) for c in res.contacts)
                raise RobotError("%s: path would collide at %.1f s (%s)"
                                 % (what, pt.time_from_start.to_sec(), pairs or "no details"))

    def joints_of(self, traj, point):
        names = traj.joint_trajectory.joint_names
        return [point.positions[names.index(j)] for j in self.joints]

    def end_joints(self, traj):
        return self.joints_of(traj, traj.joint_trajectory.points[-1])

    def show(self, trajs):
        msg = DisplayTrajectory()   # model_id can stay empty: RViz uses its loaded robot
        msg.trajectory_start = self.robot.get_current_state()
        msg.trajectory = trajs
        self.display.publish(msg)

    def execute(self, traj, what):
        self.ask(what)
        rospy.loginfo("moving: %s", what)
        result = self.arm.execute(traj, wait=True)
        ok = result if isinstance(result, bool) else result.val == MoveItErrorCodes.SUCCESS
        self.arm.stop()
        if not ok:
            raise RobotError("%s: execution failed (controller error or stop button?)" % what)
        error = max(abs(a - b) for a, b in zip(self.arm.get_current_joint_values(), self.end_joints(traj)))
        if error > self.cfg["joint_tolerance"]:
            raise RobotError("%s: the robot stopped %.3f rad away from the planned end" % (what, error))
