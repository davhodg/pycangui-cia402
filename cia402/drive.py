# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The CiA 402 drive profile: the state machine, and the objects around it.

CiA 402 is the one CANopen profile where reading and writing objects is not
enough. Everything else pycangui does is *this object holds that value*, which
a custom pane can already show and change. A drive is different in one
specific way: **it will not do anything until it has been walked through a
state machine**, and the walk is a sequence of writes to one object whose
meaning depends on what the drive answered to the last one.

    Switch on disabled --0x06--> Ready to switch on --0x07--> Switched on
                                                                  |
                                                                0x0F
                                                                  v
                                                          Operation enabled

That is why this is code and not a pane full of fields. The state is not a
value either: it is decoded from overlapping masks of the statusword, where
"Ready to switch on" and "Switched on" differ in one bit while "Fault" is a
different mask altogether. A field showing 0x0637 tells nobody anything.

What is *not* here is anything a custom pane can already do. The targets, the
actual values and the modes are ordinary objects, listed below only so that the
pane can put a unit and a type against them without an EDS -- the profile
defines both, so a drive that has never been given an EDS can still be driven.

Nothing here touches Qt or the bus. It is handed something that can read and
write objects, which is what makes all of it testable without either.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

# --- the objects, and what they are -----------------------------------------------------


@dataclass(frozen=True)
class Object:
    """One object of the profile, with the type CiA 402 says it has.

    Carried here rather than looked up in an EDS on purpose: these indices and
    these types are the profile. A drive with no EDS loaded is still a drive,
    and refusing to talk to one until somebody finds the file would be refusing
    over a thing we already know.
    """

    index: int
    sub: int
    name: str
    fmt: str  # struct format, little endian: CANopen is
    unit: str = ""
    #: "position" or "velocity" for an object in the drive's own units, which
    #: 0x60A8 or 0x60A9 says when the drive has them; ``unit`` is what is
    #: shown until then.
    measures: str = ""

    @property
    def where(self) -> tuple[int, int]:
        return (self.index, self.sub)


CONTROLWORD = Object(0x6040, 0, "Controlword", "<H")
STATUSWORD = Object(0x6041, 0, "Statusword", "<H")
MODE = Object(0x6060, 0, "Mode of operation", "<b")
MODE_DISPLAY = Object(0x6061, 0, "Mode in use", "<b")
ERROR_CODE = Object(0x603F, 0, "Error code", "<H")

#: Position and velocity are in whatever units the drive was set up in --
#: increments, rpm, mm/s -- and not in counts by any rule. The drive says
#: which in 0x60A8 and 0x60A9 when it has them; until then the label claims
#: nothing it does not know.
POSITION_UNITS = "position units"
VELOCITY_UNITS = "velocity units"

POSITION_ACTUAL = Object(0x6064, 0, "Position", "<i", POSITION_UNITS, "position")
VELOCITY_ACTUAL = Object(0x606C, 0, "Velocity", "<i", VELOCITY_UNITS, "velocity")
TORQUE_ACTUAL = Object(0x6077, 0, "Torque", "<h", "per mille of rated")

TARGET_POSITION = Object(0x607A, 0, "Target position", "<i", POSITION_UNITS, "position")
TARGET_VELOCITY = Object(0x60FF, 0, "Target velocity", "<i", VELOCITY_UNITS, "velocity")
#: Velocity mode's own target, which is not 0x60FF: mode 2 ("vl") is the
#: frequency-inverter mode, with 16-bit objects of its own and a speed in
#: rpm unless the drive's vl dimension factor (0x604C) scales it otherwise.
#: Writing 0x60FF to a drive in this mode sets a target it is not using.
VL_TARGET_VELOCITY = Object(0x6042, 0, "vl target velocity", "<h", "rpm")
#: And its own actual speed, in the same units: a drive in velocity mode may
#: not have 0x606C at all, or keep it in counts per second beside this.
VL_VELOCITY_ACTUAL = Object(0x6044, 0, "vl velocity actual value", "<h", "rpm")
TARGET_TORQUE = Object(0x6071, 0, "Target torque", "<h", "per mille of rated")
PROFILE_VELOCITY = Object(0x6081, 0, "Profile velocity", "<I", VELOCITY_UNITS, "velocity")

#: What the drive is asking of itself: the output of its profile generator,
#: before the motor has followed. Beside the actual value it tells a target
#: the drive never took (both still) from a motor that is not following (the
#: demand moving, the actual value not).
POSITION_DEMAND = Object(0x6062, 0, "Position demand", "<i", POSITION_UNITS, "position")
VELOCITY_DEMAND = Object(0x606B, 0, "Velocity demand", "<i", VELOCITY_UNITS, "velocity")
VL_VELOCITY_DEMAND = Object(0x6043, 0, "vl velocity demand", "<h", "rpm")
TORQUE_DEMAND = Object(0x6074, 0, "Torque demand", "<h", "per mille of rated")

#: Which demand each mode has. Homing has none worth reading.
DEMAND_FOR = {
    1: POSITION_DEMAND,
    2: VL_VELOCITY_DEMAND,
    3: VELOCITY_DEMAND,
    4: TORQUE_DEMAND,
    8: POSITION_DEMAND,
    9: VELOCITY_DEMAND,
    10: TORQUE_DEMAND,
}

#: Limits. These are not mode specific in the way a target is: a drive in
#: any mode is held to its maximum torque, and the velocity limits cap what
#: a profile or a controller may ask for. They are written once for a set-up
#: rather than moment to moment, which is why they sit apart from the target.
MAX_TORQUE = Object(0x6072, 0, "Max torque", "<H", "per mille of rated")
MAX_PROFILE_VELOCITY = Object(0x607F, 0, "Max profile velocity", "<I", VELOCITY_UNITS, "velocity")
#: In rpm by the standard, unlike the velocity objects above: this one is
#: about the motor rather than about the profile, and CiA 402 gives it
#: revolutions per minute directly.
MAX_MOTOR_SPEED = Object(0x6080, 0, "Max motor speed", "<I", "rpm")

#: The units the drive's position and velocity objects are in, one byte each:
#: a power of ten, the unit, and for velocity the unit of time below it.
SI_UNIT_POSITION = Object(0x60A8, 0, "SI unit position", "<I")
SI_UNIT_VELOCITY = Object(0x60A9, 0, "SI unit velocity", "<I")
#: Which of them says the units of what, by ``Object.measures``.
SI_UNITS = {"position": SI_UNIT_POSITION, "velocity": SI_UNIT_VELOCITY}

#: The unit codes CiA 402 takes from its table of SI units, as far as a drive
#: is likely to use them.
SI_CODES = {
    0x01: "m",
    0x03: "s",
    0x10: "rad",
    0x41: "°",
    0x47: "min",
    0x48: "h",
    0xB4: "rev",
    0xB5: "inc",
}


def si_unit(value: int) -> str | None:
    """What 0x60A8 or 0x60A9 says, as a unit to show; None if it says nothing.

    Byte 3 is a power of ten, byte 2 the unit and byte 1 the unit of time
    for a velocity. 0x00B44700 is rev per minute, so rpm; 0xFF410000 is
    tenths of a degree. A code not in the table is shown as itself, since
    a label that guesses is the fault being put right here.
    """
    value = int(value) & 0xFFFF_FFFF
    power = struct.unpack("<b", bytes([value >> 24]))[0]
    unit, per = (value >> 16) & 0xFF, (value >> 8) & 0xFF
    if unit == 0:
        return None
    name = SI_CODES.get(unit, f"unit 0x{unit:02X}")
    if (unit, per) == (0xB4, 0x47):
        name = "rpm"
    elif per:
        name = f"{name}/{SI_CODES.get(per, f'0x{per:02X}')}"
    return name if power == 0 else f"{10.0**power:g} {name}"


#: What it takes to put the two speed limits in one unit, where SI_UNIT_VELOCITY
#: does not say rpm: how many increments of the position encoder make how many
#: motor revolutions, and the velocity factor from the drive's velocity units to
#: increments per second. Each is a numerator and a divisor.
ENCODER_INCREMENTS = Object(0x608F, 1, "Encoder increments", "<I")
MOTOR_REVOLUTIONS = Object(0x608F, 2, "Motor revolutions", "<I")
VELOCITY_FACTOR_NUMERATOR = Object(0x6096, 1, "Velocity factor numerator", "<I")
VELOCITY_FACTOR_DIVISOR = Object(0x6096, 2, "Velocity factor divisor", "<I")
#: Read once, when a drive is chosen, beside 0x60A8 and 0x60A9.
SCALING = (
    ENCODER_INCREMENTS,
    MOTOR_REVOLUTIONS,
    VELOCITY_FACTOR_NUMERATOR,
    VELOCITY_FACTOR_DIVISOR,
)


def rpm_per_velocity_unit(si_velocity: int | None, scaling: dict) -> float | None:
    """How many rpm one of the drive's velocity units is; None where it cannot be said.

    0x607F is in the drive's velocity units and 0x6080 in rpm, so comparing
    the two needs this. From 0x60A9 where it names revolutions or radians
    against time. Where it names increments per second, or says nothing --
    and a drive with no factor group counts in increments -- from the encoder
    resolution (0x608F), through the velocity factor (0x6096) where the drive
    has one and 0x60A9 does not already say increments. ``scaling`` is
    whatever of SCALING was read, by ``where``; a zero, or a part missing,
    means no answer rather than a guess.
    """
    per_second = {0x03: 60.0, 0x47: 1.0}  # to "per minute"
    if si_velocity:
        value = int(si_velocity) & 0xFFFF_FFFF
        power = 10.0 ** struct.unpack("<b", bytes([value >> 24]))[0]
        unit, per = (value >> 16) & 0xFF, (value >> 8) & 0xFF
        if per not in per_second:
            return None
        if unit == 0xB4:  # revolutions
            return power * per_second[per]
        if unit == 0x10:  # radians
            return power * per_second[per] / (2 * math.pi)
        if unit != 0xB5:  # increments, worked out below; anything else is not a speed of the motor
            return None
        to_increments_per_s = power * per_second[per] / 60.0
    else:
        numerator = scaling.get(VELOCITY_FACTOR_NUMERATOR.where)
        divisor = scaling.get(VELOCITY_FACTOR_DIVISOR.where)
        to_increments_per_s = numerator / divisor if numerator and divisor else 1.0
    increments = scaling.get(ENCODER_INCREMENTS.where)
    revolutions = scaling.get(MOTOR_REVOLUTIONS.where)
    if not increments or not revolutions:
        return None
    return to_increments_per_s * 60.0 * revolutions / increments


# --- set once for a machine: the Settings tab ------------------------------------------------

ACCELERATION_UNITS = "acceleration units"

SOFTWARE_LIMIT_MIN = Object(0x607D, 1, "Min position limit", "<i", POSITION_UNITS, "position")
SOFTWARE_LIMIT_MAX = Object(0x607D, 2, "Max position limit", "<i", POSITION_UNITS, "position")

PROFILE_ACCELERATION = Object(0x6083, 0, "Profile acceleration", "<I", ACCELERATION_UNITS)
PROFILE_DECELERATION = Object(0x6084, 0, "Profile deceleration", "<I", ACCELERATION_UNITS)
QUICK_STOP_DECELERATION = Object(0x6085, 0, "Quick stop deceleration", "<I", ACCELERATION_UNITS)

HOMING_METHOD = Object(0x6098, 0, "Homing method", "<b")
HOMING_SPEED_SWITCH = Object(0x6099, 1, "Speed to the switch", "<I", VELOCITY_UNITS, "velocity")
HOMING_SPEED_ZERO = Object(0x6099, 2, "Speed to zero", "<I", VELOCITY_UNITS, "velocity")
HOMING_ACCELERATION = Object(0x609A, 0, "Homing acceleration", "<I", ACCELERATION_UNITS)
HOME_OFFSET = Object(0x607C, 0, "Home offset", "<i", POSITION_UNITS, "position")

FOLLOWING_ERROR_WINDOW = Object(
    0x6065, 0, "Following error window", "<I", POSITION_UNITS, "position"
)
POSITION_WINDOW = Object(0x6067, 0, "Position window", "<I", POSITION_UNITS, "position")
#: Bit 7 turns position values round, bit 6 velocity values.
POLARITY = Object(0x607E, 0, "Polarity", "<B")
POLARITY_BITS = ((7, "Position reversed"), (6, "Velocity reversed"))

QUICK_STOP_OPTION = Object(0x605A, 0, "Quick stop", "<h")
SHUTDOWN_OPTION = Object(0x605B, 0, "Shutdown", "<h")
DISABLE_OPTION = Object(0x605C, 0, "Disable operation", "<h")
HALT_OPTION = Object(0x605D, 0, "Halt", "<h")
FAULT_REACTION_OPTION = Object(0x605E, 0, "Fault reaction", "<h")

_COAST = "Power off: the motor coasts"
_RAMPS = {
    1: "the slow-down ramp",
    2: "the quick stop ramp",
    3: "the current limit",
    4: "the voltage limit",
}

#: What each option code makes the drive do, from CiA 402. Negative codes are
#: the maker's own, shown as a number rather than guessed at.
OPTIONS = {
    QUICK_STOP_OPTION.where: {
        0: _COAST,
        **{code: f"Stop on {ramp}, then switch off" for code, ramp in _RAMPS.items()},
        **{code + 4: f"Stop on {ramp}, and stay in quick stop" for code, ramp in _RAMPS.items()},
    },
    SHUTDOWN_OPTION.where: {0: _COAST, 1: "Stop on the slow-down ramp, then switch off"},
    DISABLE_OPTION.where: {0: _COAST, 1: "Stop on the slow-down ramp, then switch off"},
    HALT_OPTION.where: {code: f"Stop on {ramp}" for code, ramp in _RAMPS.items()},
    FAULT_REACTION_OPTION.where: {0: _COAST, **{c: f"Stop on {r}" for c, r in _RAMPS.items()}},
}


def _homing_methods() -> dict[int, str]:
    """The homing methods CiA 402 numbers, by what each homes on."""
    named = {
        1: "Negative limit switch and index pulse",
        2: "Positive limit switch and index pulse",
        17: "Negative limit switch",
        18: "Positive limit switch",
        33: "Index pulse, moving negative",
        34: "Index pulse, moving positive",
        35: "Current position",
        37: "Current position",
    }
    families = (
        (3, "Positive home switch", True),
        (5, "Negative home switch", True),
        (7, "Home switch, positive limit switch side", True),
        (11, "Home switch, negative limit switch side", True),
        (19, "Positive home switch", False),
        (21, "Negative home switch", False),
        (23, "Home switch, positive limit switch side", False),
        (27, "Home switch, negative limit switch side", False),
    )
    for first, what, index in families:
        count = 4 if "side" in what else 2
        for edge in range(count):
            # Which edge of the switch each stops at is in the method number,
            # which the drive's manual uses too.
            named[first + edge] = f"{what}{' and index pulse' if index else ''}"
    return dict(sorted(named.items()))


HOMING_METHODS = _homing_methods()


def choice_name(choices: dict[int, str], value: int) -> str:
    """A code as a line in a list: its number and what it means, or that it is
    the maker's own or not in the standard."""
    if value in choices:
        return f"{value}  {choices[value]}"
    return f"{value}  Manufacturer specific" if value < 0 else f"{value}  Not in CiA 402"


#: Which modes this drive has, as a bit per mode. Bit 0 is profile position,
#: so the bit number is the mode number minus one, and the top sixteen bits
#: are the manufacturer's own.
SUPPORTED_MODES = Object(0x6502, 0, "Supported drive modes", "<I")

#: Mode number -> the bit in 0x6502 that says the drive has it. Velocity
#: mode is bit 1 and mode 2, homing is bit 5 and mode 6: the numbering is
#: off by one and not worth deriving in the head each time.
MODE_BITS = {1: 0, 2: 1, 3: 2, 4: 3, 6: 5, 7: 6, 8: 7, 9: 8, 10: 9}


def modes_in(supported: int) -> set[int]:
    """The modes a drive says it has, out of 0x6502."""
    return {mode for mode, bit in MODE_BITS.items() if supported & (1 << bit)}


#: Read every round. Deliberately short: an SDO round is a round trip per
#: object, and a screen that reads twenty things at 1 Hz is worse than one that
#: reads six at 5 Hz.
WATCHED = (STATUSWORD, MODE_DISPLAY, POSITION_ACTUAL, VELOCITY_ACTUAL, TORQUE_ACTUAL)


def watched(mode: int) -> tuple[Object, ...]:
    """What to read each round in this mode: velocity mode's own speed in place
    of 0x606C, so the speed shown is in the units its target is set in, and
    the mode's demand, if it has one."""
    objects = WATCHED
    if mode == 2:
        objects = tuple(
            VL_VELOCITY_ACTUAL if obj.where == VELOCITY_ACTUAL.where else obj for obj in WATCHED
        )
    demand = DEMAND_FOR.get(mode)
    return objects + ((demand,) if demand is not None else ()) + (DIGITAL_INPUTS,)


#: Written rather than watched: read once when a drive is chosen, and
#: again only when somebody asks.
LIMITS = (MAX_TORQUE, MAX_PROFILE_VELOCITY, MAX_MOTOR_SPEED)


def encode(obj: Object, value: int) -> bytes:
    """The bytes to write for a value, or a reason it will not fit.

    Refused rather than truncated. ``struct`` would happily wrap 70000 into a
    16-bit target and the drive would do exactly what the wrapped number says,
    which is the sort of mistake that moves machinery.
    """
    try:
        return struct.pack(obj.fmt, int(value))
    except struct.error as exc:
        raise ValueError(f"{value} will not fit in {obj.name}: {exc}") from exc


def decode(obj: Object, data: bytes) -> int:
    """The value in a reply, whatever length the drive chose to send.

    A drive answering a 16-bit object with two bytes and one answering with
    four are both being reasonable, and neither is worth an error.
    """
    size = struct.calcsize(obj.fmt)
    if isinstance(data, int):
        return data  # already decoded, by an EDS-aware read
    data = bytes(data)
    if len(data) < size:
        data = data.ljust(size, b"\x00")
    return int(struct.unpack(obj.fmt, data[:size])[0])


# --- the modes ----------------------------------------------------------------------------

#: What CiA 402 numbers mean. Negative numbers are the maker's own, and are
#: shown as themselves rather than guessed at.
MODES = {
    0: "No mode",
    1: "Profile position",
    2: "Velocity",
    3: "Profile velocity",
    4: "Profile torque",
    6: "Homing",
    7: "Interpolated position",
    8: "Cyclic sync position",
    9: "Cyclic sync velocity",
    10: "Cyclic sync torque",
}

#: Which target each mode actually uses. A mode not in here has no target that
#: can be set over SDO, which is a thing to say rather than a box to offer.
TARGET_FOR = {
    1: TARGET_POSITION,
    2: VL_TARGET_VELOCITY,
    3: TARGET_VELOCITY,
    4: TARGET_TORQUE,
    8: TARGET_POSITION,
    9: TARGET_VELOCITY,
    10: TARGET_TORQUE,
}

#: The modes whose target has to arrive every cycle, over a PDO, from something
#: keeping time. Writing one over SDO is not slow, it is meaningless -- the
#: drive expects the next one before the SDO would even have finished.
CYCLIC = (8, 9, 10)


def mode_name(mode: int) -> str:
    if mode in MODES:
        return MODES[mode]
    return f"Manufacturer specific ({mode})" if mode < 0 else f"Reserved ({mode})"


# --- the state machine ----------------------------------------------------------------------

#: Statusword mask, value and name, in the order they are tested. The masks
#: overlap and that is not a mistake: "Ready to switch on" and "Switched on"
#: differ in one bit, while "Fault" ignores bits the others test. This is the
#: table from the standard, and the reason the state cannot be a field on a
#: form.
STATES = (
    (0x4F, 0x00, "Not ready to switch on"),
    (0x4F, 0x40, "Switch on disabled"),
    (0x6F, 0x21, "Ready to switch on"),
    (0x6F, 0x23, "Switched on"),
    (0x6F, 0x27, "Operation enabled"),
    (0x6F, 0x07, "Quick stop active"),
    (0x4F, 0x0F, "Fault reaction active"),
    (0x4F, 0x08, "Fault"),
)

UNKNOWN = "Unknown"

#: Controlword commands. Bit 7 is the fault reset, and it acts on the *rising*
#: edge, which is why clearing one is two writes rather than one.
SHUTDOWN = 0x0006
SWITCH_ON = 0x0007
ENABLE_OPERATION = 0x000F
DISABLE_VOLTAGE = 0x0000
QUICK_STOP = 0x0002
FAULT_RESET = 0x0080

#: Bit 4 in profile position mode: the drive takes the target it has been given
#: on the rising edge of this and not before.
NEW_SETPOINT = 0x0010
#: Bits 5 and 6 in profile position mode, sent with the edge: take the new
#: target at once rather than after the move under way, and take it as a
#: distance from where the drive is rather than as a place.
CHANGE_IMMEDIATELY = 0x0020
RELATIVE = 0x0040
#: Bit 4 in homing mode: homing starts on its rising edge, and stops if it
#: is cleared before homing is done.
HOMING_START = 0x0010

#: Bits 4, 5 and 6 in velocity mode: enable the ramp, unlock it, and use the
#: reference. Each is a brake on the speed when clear -- the ramp output held
#: at zero, frozen, or fed zero -- so a drive enabled in this mode with them
#: clear sits still whatever target it is given. In the other modes these
#: bits mean other things (bit 4 is profile position's new set-point), which
#: is why they go out in velocity mode only.
VL_RAMP = 0x0070

#: Bit 8, and the one that matters most here. Halt is defined in every profiled
#: mode as "come to a standstill, the way 0x605D says to", which is what makes
#: it the one command that stops a drive without needing to know what mode it is
#: in or what its target means.
HALT = 0x0100

#: The modes whose target is a *rate*, where writing zero is a command to stop.
#:
#: A position target is not in here, and that is the whole point of the list.
#: Zero is a *place*: writing it to 0x607A does not stop a drive, it sends it to
#: position zero, which on a machine part way through a move is the opposite of
#: stopping and may be the longest move it has been asked for all day.
RATE_TARGETS = {
    2: VL_TARGET_VELOCITY,
    3: TARGET_VELOCITY,
    4: TARGET_TORQUE,
    9: TARGET_VELOCITY,
    10: TARGET_TORQUE,
}

#: Statusword bits worth showing beside the state, since none of them is part
#: of it. Bit 10 means different things in different modes, so it is named for
#: what the standard calls it rather than for what it implies.
#: Bit 5 is not here: it is the quick stop, and it reads the other way up --
#: set means *not* active -- which the state table already says plainly.
FLAGS = (
    (1 << 4, "Voltage enabled"),
    (1 << 7, "Warning"),
    (1 << 9, "Remote"),
    (1 << 10, "Target reached"),
    (1 << 11, "Internal limit active"),
)

#: The bits the profile hands to the maker, in both words.
#:
#: What they mean is not in CiA 402 and cannot be: one drive's bit 15 is a
#: brake release and another's is a spindle orientation request. So they are
#: shown and set by number and left unnamed, which is all that can be said
#: honestly without the manual in front of you.
#:
#: Statusword bit 8 is the maker's too, and bits 12 and 13 are not: those are
#: defined per mode by the standard, so naming them "manufacturer" would be
#: wrong in a way that matters when somebody is comparing this with a manual.
MANUFACTURER_STATUS_BITS = (8, 14, 15)
MANUFACTURER_CONTROL_BITS = (11, 12, 13, 14, 15)


#: Statusword bits 12 and 13, which each mode defines for itself.
MODE_FLAGS = {
    1: ((1 << 12, "Set-point acknowledged"), (1 << 13, "Following error")),
    3: ((1 << 12, "Speed zero"), (1 << 13, "Max slippage error")),
    6: ((1 << 12, "Homing attained"), (1 << 13, "Homing error")),
    8: ((1 << 12, "Following the command"), (1 << 13, "Following error")),
    9: ((1 << 12, "Following the command"),),
    10: ((1 << 12, "Following the command"),),
}

#: What homing is doing, from statusword bits 13, 12 and 10 (error,
#: attained, target reached), as CiA 402 tabulates them.
HOMING_STATUS = {
    (False, False, False): "Homing in progress",
    (False, False, True): "Homing interrupted, or not started",
    (False, True, False): "Homed, but not at the target yet",
    (False, True, True): "Homing done",
    (True, False, False): "Homing error, still moving",
    (True, False, True): "Homing error, stopped",
}


def homing_status(statusword: int) -> str:
    """What homing is doing, in words."""
    key = (bool(statusword & (1 << 13)), bool(statusword & (1 << 12)), bool(statusword & (1 << 10)))
    return HOMING_STATUS.get(key, "Homing: the statusword says something CiA 402 does not")


#: The drive's own inputs, bit by bit; bits 16 to 31 are the maker's.
DIGITAL_INPUTS = Object(0x60FD, 0, "Digital inputs", "<I")
INPUT_BITS = (
    (0, "Negative limit switch"),
    (1, "Positive limit switch"),
    (2, "Home switch"),
    (3, "Interlock"),
)


def inputs_of(value: int) -> list[str]:
    """Which inputs are active, the standard's by name and the maker's by number."""
    named = [name for bit, name in INPUT_BITS if value & (1 << bit)]
    maker = [bit for bit in range(16, 32) if value & (1 << bit)]
    if maker:
        named.append(f"maker input{'s' if len(maker) > 1 else ''} {', '.join(map(str, maker))}")
    return named


def bits_set(word: int, bits=MANUFACTURER_STATUS_BITS) -> list[int]:
    """Which of ``bits`` are set in ``word``, lowest first."""
    return [bit for bit in sorted(bits) if word & (1 << bit)]


def mask_of(bits) -> int:
    """The bits as one word, ready to be held in a controlword."""
    value = 0
    for bit in bits:
        value |= 1 << bit
    return value


def state_of(statusword: int) -> str:
    """Which of the eight states the drive is in, or that it is in none of them.

    ``Unknown`` rather than a guess. A statusword matching no row is a drive
    doing something this profile does not describe, and naming it anyway would
    put a confident word on screen with nothing behind it.
    """
    for mask, value, name in STATES:
        if statusword & mask == value:
            return name
    return UNKNOWN


def flags_of(statusword: int, mode: int = 0) -> list[str]:
    """The bits beside the state that are set, in the order the standard has
    them, and bits 12 and 13 as the mode the drive is in names them."""
    flags = FLAGS[:3] + MODE_FLAGS.get(mode, ()) + FLAGS[3:]
    flags = tuple(sorted(flags, key=lambda flag: flag[0]))
    return [name for bit, name in flags if statusword & bit]


def is_faulted(statusword: int) -> bool:
    return state_of(statusword) in ("Fault", "Fault reaction active")


def is_enabled(statusword: int) -> bool:
    return state_of(statusword) == "Operation enabled"


@dataclass(frozen=True)
class Step:
    """One controlword to write, and what to say while it is being written."""

    what: str
    controlword: int


#: States a drive leaves by itself, and which therefore cannot be commanded out
#: of. Writing a controlword at one of them is not refused by the drive; it is
#: ignored, which looks exactly like the tool having done nothing.
TRANSIENT = {
    "Not ready to switch on": (
        "The drive is still starting up. It leaves this state by itself, and a "
        "controlword written now is ignored rather than refused."
    ),
    "Fault reaction active": (
        "The drive is still reacting to a fault -- braking, most likely. It "
        "moves to Fault by itself, and can be reset from there."
    ),
    UNKNOWN: (
        "The statusword matches none of the states CiA 402 describes, so what "
        "the drive would make of a command cannot be worked out from here."
    ),
}


def steps_to_enable(statusword: int) -> list[Step]:
    """Everything that has to be written to get from here to Operation enabled.

    The whole reason this is a plugin rather than a pane full of fields: which
    writes are needed depends on the answer to the last one, and there is no
    arrangement of boxes on a form that expresses that.

    A drive in Quick stop active is taken out through Switch on disabled rather
    than straight back to Operation enabled. The direct route exists, but only
    for drives whose quick stop option code says so, and taking it on a drive
    whose code says otherwise leaves it sitting there while the tool claims to
    have enabled it. The long way round works everywhere.
    """
    state = state_of(statusword)
    if why := TRANSIENT.get(state):
        raise ValueError(f"{state}. {why}")
    if state == "Operation enabled":
        return []

    steps: list[Step] = []
    if state == "Fault":
        steps += clear_fault()
        state = "Switch on disabled"  # where a reset leaves it
    if state == "Quick stop active":
        steps.append(Step("Releasing the quick stop", DISABLE_VOLTAGE))
        state = "Switch on disabled"
    if state in ("Switch on disabled", "Ready to switch on", "Switched on"):
        if state == "Switch on disabled":
            steps.append(Step("Shutdown", SHUTDOWN))
            state = "Ready to switch on"
        if state == "Ready to switch on":
            steps.append(Step("Switching on", SWITCH_ON))
            state = "Switched on"
        steps.append(Step("Enabling operation", ENABLE_OPERATION))
    return steps


def clear_fault() -> list[Step]:
    """Reset a fault: two writes, because bit 7 acts on its rising edge.

    A single write of 0x0080 works on a drive whose controlword happened to
    have the bit clear, and does nothing at all on one where it did not. The
    zero first makes the edge rather than hoping for it.
    """
    return [
        Step("Clearing bit 7 to make an edge", DISABLE_VOLTAGE),
        Step("Resetting the fault", FAULT_RESET),
    ]


def steps_to_disable(statusword: int) -> list[Step]:
    """Back to Switch on disabled, from wherever it is now."""
    state = state_of(statusword)
    if why := TRANSIENT.get(state):
        raise ValueError(f"{state}. {why}")
    if state == "Switch on disabled":
        return []
    return [Step("Disabling the drive", DISABLE_VOLTAGE)]


def steps_to_quick_stop(statusword: int) -> list[Step]:
    """Stop it the way the drive was configured to stop.

    Only from Operation enabled: quick stop is a command about motion, and a
    drive that is not enabled is not moving under its own power. Sending it
    anyway would be a button that appears to work and does nothing.
    """
    state = state_of(statusword)
    if state != "Operation enabled":
        raise ValueError(
            f"Quick stop is a command to a drive that is running, and this is {state}."
        )
    return [Step("Quick stop", QUICK_STOP)]


def steps_to_apply_target(mode: int, statusword: int, options: int = 0) -> list[Step]:
    """Make a profile position drive act on the target it has been given.

    Profile position is the mode where writing the target is not enough: the
    drive takes it on the rising edge of bit 4 and ignores it until then. Every
    other mode acts on the target as it is written, so asking for this in one of
    those would be two writes that mean nothing.

    Only from Operation enabled, and that is not fussiness. The edge is made
    by writing the *enable* controlword with bit 4 added and then without it, so
    doing this to a drive that is merely switched on would enable it on the way
    past -- which is the one thing here that gets asked about first. A button
    that quietly does the thing another button asks permission for is a hole in
    the permission.
    """
    if mode != 1:
        raise ValueError(
            f"{mode_name(mode)} acts on its target as it is written. "
            "Only profile position waits to be told."
        )
    if not is_enabled(statusword):
        raise ValueError(
            f"The drive has to be enabled before it will take a target, and this is "
            f"{state_of(statusword)}. Applying one is the enable controlword with bit 4 "
            "added, so doing it from here would enable the drive without asking."
        )
    # The options go in all three: the drive reads them at the edge, and a
    # word without them either side would be a change of its own.
    options &= RELATIVE | CHANGE_IMMEDIATELY
    return [
        Step("Clearing bit 4 to make an edge", ENABLE_OPERATION | options),
        Step("Applying the target", ENABLE_OPERATION | NEW_SETPOINT | options),
        Step("Clearing bit 4 again", ENABLE_OPERATION | options),
    ]


def steps_to_home(mode: int, statusword: int) -> list[Step]:
    """Start homing: the rising edge of bit 4, left set.

    Not cleared again as Go clears it: in homing mode clearing bit 4 stops
    homing part way. The next controlword this pane writes -- a command, or
    the halt when it goes away -- clears it, which is a stop as well. Only
    from Operation enabled, for the reason Go is.
    """
    if mode != 6:
        raise ValueError(f"Homing starts in homing mode, and the drive is in {mode_name(mode)}.")
    if not is_enabled(statusword):
        raise ValueError(
            f"The drive has to be enabled before it will home, and this is "
            f"{state_of(statusword)}. Starting homing is the enable controlword with bit 4 "
            "added, so doing it from here would enable the drive without asking."
        )
    return [
        Step("Clearing bit 4 to make an edge", ENABLE_OPERATION),
        Step("Starting homing", ENABLE_OPERATION | HOMING_START),
    ]


def operation_bits(mode: int) -> int:
    """The controlword bits a mode needs held in every controlword to move.

    Velocity mode's ramp bits, and nothing for any other mode. Held in every
    word rather than added to one: the state commands below Operation enabled
    ignore bits 4 to 6, and a word written without them -- a halt, or a fault
    reset -- would otherwise put the ramp back to holding the drive still.
    """
    return VL_RAMP if mode == 2 else 0


def word_after_target(mode: int, statusword: int) -> int | None:
    """The controlword to write after a target, so the drive acts on it; None
    when there is none to write.

    The enable controlword again, with nothing else in it. Two things can be
    holding an enabled drive still whatever its target: halt (bit 8), which
    this pane itself leaves set whenever it is hidden or paused, and in
    velocity mode the ramp bits, clear if it was enabled before the mode was
    chosen. Either way 0x606B stays at zero. The drive is already enabled, so
    this enables nothing; a drive that is not is left alone, since enabling
    is asked about first. Profile position is not here: it waits for Go,
    which writes the enable controlword itself.
    """
    if mode == 1 or mode not in RATE_TARGETS or not is_enabled(statusword):
        return None
    return ENABLE_OPERATION | operation_bits(mode)


def can_set_target(mode: int) -> tuple[bool, str]:
    """Whether a target can usefully be written over SDO in this mode, and why not."""
    if mode in CYCLIC:
        return False, (
            f"{mode_name(mode)} expects a new target every cycle, over a PDO, from "
            "something keeping time. One written here would be stale before it "
            "arrived."
        )
    if mode not in TARGET_FOR:
        return False, f"{mode_name(mode)} has no target to set."
    return True, ""


def written_by_pane(mode: int) -> tuple[Object, ...]:
    """What this pane writes over SDO in a mode: the controlword, the mode, and
    the mode's target if it has one."""
    target = TARGET_FOR.get(mode)
    return (CONTROLWORD, MODE) + ((target,) if target is not None else ())


def rpdo_conflicts(received, mode: int) -> list[str]:
    """Each object this pane writes that process data also carries, a line each.

    An RPDO is the drive being written to by whoever sends it, once a cycle or
    on every change. While the node is Operational the next one overwrites a
    write from here -- the pane appears to do nothing, or its value flicks
    back. ``received`` is the device's ``receives()``: (index, sub, carried by).
    """
    wanted = {obj.where: obj for obj in written_by_pane(mode)}
    return [
        f"{carrier} carries {obj.name} (0x{obj.index:04X})"
        for index, sub, carrier in received
        if (obj := wanted.get((index, sub))) is not None
    ]


def stop_writes(mode: int, statusword: int, disable: bool = False) -> list[tuple[Object, int]]:
    """What to write to leave a drive at a standstill, as a screen goes away.

    A demand sent over SDO does not stop when the window showing it does. The
    drive holds the last controlword and the last target it was given, and goes
    on doing exactly what it was told by somebody who can no longer see it. So
    a pane that has commanded motion has to take it back on its way out.

    Halt rather than a zero target, because halt is the one command that means
    "stop" in every mode. A zero is then written to the target as well, but
    *only where the target is a rate*: a zero position is a place rather than a
    stop, and writing one would send the machine there.

    ``disable`` is offered and is not the default. Removing power is not
    obviously safer than commanding a standstill -- on a vertical axis it is the
    load that decides, and whether a brake catches it is a fact about the
    machine that pycangui has no way of knowing.
    """
    if not is_enabled(statusword):
        return []  # not driving anything, so there is nothing to take back
    writes = [(CONTROLWORD, ENABLE_OPERATION | HALT | operation_bits(mode))]
    # After the halt, so that switching it back on later does not start from
    # the demand it was left with.
    if (target := RATE_TARGETS.get(mode)) is not None:
        writes.append((target, 0))
    if disable:
        writes.append((CONTROLWORD, DISABLE_VOLTAGE))
    return writes


# --- what it is all written through -----------------------------------------------------------


class Drive:
    """Whatever can read and write this drive's objects.

    The same seam the firmware plugin uses, and for the same reason: the pane
    hands it a live node, a test hands it a dictionary, and everything above
    this line can be tested without a bus in the room.
    """

    def read(self, obj: Object) -> int:
        raise NotImplementedError

    def write(self, obj: Object, value: int) -> None:
        raise NotImplementedError
