"""Pure translation: scheduler ArmCommand -> Isaac Sim command (SPEC §6.3 -> §6.4).

pick/place positions are resolved from positions.json (fixed coordinates).
Kept pure so it is unit-testable without MQTT.
"""

from isaac_common.schemas import ArmCommand, IsaacSimCommand, Waypoint


def translate(cmd: ArmCommand, positions: dict) -> IsaacSimCommand:
    locs = positions.get("locations", {})
    return IsaacSimCommand(
        arm_id=cmd.arm_id,
        action="move",
        pick=Waypoint(key=cmd.from_, pos=locs.get(cmd.from_)),
        place=Waypoint(key=cmd.to, pos=locs.get(cmd.to)),
        product_id=cmd.product_id,
    )
