"""Table layout from config/blocks.yaml: block slots, block shapes and place spots.

Plain Python and yaml only (no ROS), so the UI's --ros-dry-run can use it without ROS.
"""
import math
import os

import yaml

PKG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_DIR = os.path.join(PKG_DIR, "config")


def load_config(name, config_dir=CONFIG_DIR):
    with open(os.path.join(config_dir, name)) as f:
        return yaml.safe_load(f)


class Block(object):
    """One slot of blocks.yaml: where the block is and its shape."""

    def __init__(self, layout, entry):
        t = layout["block_types"][entry["type"]]
        self.id = entry["id"]
        self.name = entry["name"]                         # e.g. "red cuboid", as the UI says
        self.label = "%s %s" % (self.id, self.name)
        self.key = "%s_%s" % (self.id, self.name.replace(" ", "_"))   # e.g. s1_red_cuboid
        self.color = entry["color"]
        self.x, self.y, self.yaw = entry["x"], entry["y"], entry.get("yaw", 0.0)
        self.shape = t["shape"]
        if self.shape == "box":
            self.size = list(t["size"])                   # x, y, z
            self.height = self.size[2]
        elif self.shape == "cylinder":
            self.diameter, self.height = t["diameter"], t["height"]
        elif self.shape == "hexagon":
            self.across_flats, self.height = t["across_flats"], t["height"]
        else:
            raise ValueError("block type '%s': unknown shape '%s'" % (entry["type"], self.shape))

    def grasp_options(self):
        """(gripper yaw, width between the fingers) for each way to grasp the block.

        Cube: any multiple of 90 degrees. Cylinder: any turn (4 tried). Hexagon: multiples
        of 60 degrees, so the fingers close on two opposite flats.
        """
        if self.shape == "box":
            return [(self.yaw + k * math.pi / 2, self.size[1] if k % 2 == 0 else self.size[0])
                    for k in range(4)]
        if self.shape == "cylinder":
            return [(self.yaw + k * math.pi / 2, self.diameter) for k in range(4)]
        return [(self.yaw + k * math.pi / 3, self.across_flats) for k in range(6)]

    def half_width(self):
        """Half the width the pusher meets (a hexagon at the place spot shows a flat side)."""
        if self.shape == "box":
            return min(self.size[0], self.size[1]) / 2
        if self.shape == "cylinder":
            return self.diameter / 2
        return self.across_flats / 2

    def collision_shape(self, padding=0.0):
        """("box", [x, y, z]) or ("cylinder", height, radius) for MoveIt.

        A hexagon is modeled as the cylinder around its corners (slightly bigger, safe side).
        """
        if self.shape == "box":
            return ("box", [d + 2 * padding for d in self.size])
        if self.shape == "cylinder":
            return ("cylinder", self.height + 2 * padding, self.diameter / 2 + padding)
        corner_radius = self.across_flats / math.sqrt(3)
        return ("cylinder", self.height + 2 * padding, corner_radius + padding)


class Layout(object):
    def __init__(self, config_dir=CONFIG_DIR):
        self.data = load_config("blocks.yaml", config_dir)
        self.blocks = [Block(self.data, b) for b in self.data["blocks"]]
        self.places = {p["id"]: p for p in self.data.get("places", [])}
        self.bowls = {b["color"]: b for b in self.data.get("bowls", [])}
        self.bowl_size = self.data.get("bowl_size")

    def block(self, key):
        """Find a block by its name ("red cuboid") or slot id ("s1")."""
        key = str(key).strip().lower()
        found = [b for b in self.blocks if key in (b.name.lower(), b.id.lower())]
        if len(found) != 1:
            raise ValueError("block '%s': %d matches in blocks.yaml (known: %s)"
                             % (key, len(found), ", ".join(b.name for b in self.blocks)))
        return found[0]

    def place(self, key):
        if key not in self.places:
            raise ValueError("unknown place spot '%s' (known: %s)" % (key, ", ".join(self.places) or "none"))
        return self.places[key]

    def bowl(self, color):
        if color not in self.bowls:
            raise ValueError("unknown bowl '%s' (known: %s)" % (color, ", ".join(self.bowls) or "none"))
        return self.bowls[color]

    def push_plan(self, bowl, push, block_half=None):
        """Geometry of the push toward the `bowl` ("red" or "blue") bowl, table_corner frame.

        The block starts on the decision spot and is pushed on the line toward the bowl
        center until it is push["end_distance_to_bowl"] from it. Raises ValueError if that
        push is shorter than push["min_length"] or longer than push["max_length"].
        block_half: half width of the block in the push direction (default: the widest type).
        """
        d, b = self.place("decision"), self.bowl(bowl)
        dist = math.hypot(b["x"] - d["x"], b["y"] - d["y"])
        ux, uy = (b["x"] - d["x"]) / dist, (b["y"] - d["y"]) / dist
        length = dist - push["end_distance_to_bowl"]
        if not push["min_length"] <= length <= push["max_length"]:
            raise ValueError("push toward %s would be %.1f cm, allowed %.0f to %.0f cm. Check the "
                             "decision spot and bowl positions in blocks.yaml"
                             % (bowl, length * 100, push["min_length"] * 100, push["max_length"] * 100))
        if block_half is None:
            block_half = max(blk.half_width() for blk in self.blocks)
        contact = block_half + push["pusher_half_thickness"]   # TCP to block center at contact
        start = (d["x"] - ux * push["start_offset"], d["y"] - uy * push["start_offset"])
        block_end = (d["x"] + ux * length, d["y"] + uy * length)
        tcp_end = (block_end[0] - ux * contact, block_end[1] - uy * contact)
        # Gripper yaw with the closing axis (TCP y) along the push, so a fingertip's flat
        # face pushes. Folded into [-90, 90) degrees, so red and blue use the same turn.
        yaw = math.atan2(ux, -uy)
        yaw = (yaw + math.pi / 2) % math.pi - math.pi / 2
        travel = push["start_offset"] - contact + length
        return {"bowl": bowl, "direction": (ux, uy), "length": length, "start": start,
                "tcp_end": tcp_end, "block_end": block_end, "yaw": yaw,
                "travel": travel, "seconds": travel / push["speed"],
                "decision": (d["x"], d["y"])}

