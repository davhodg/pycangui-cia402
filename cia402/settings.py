# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The Settings tab: what is set once for a machine rather than while it runs.

Limits, the profile's ramps, homing, the position windows and polarity, and
what the drive does when it is stopped -- read when the tab is first shown
for a drive, and on *Read all*; written by *Write changed*, after a question.
A value typed here is tinted until it is written, and a read puts the tint
away, as on a custom pane. Scaling is shown, not written: it is what the
units and conversions on the pane come from.

Nothing here is polled. These values change when somebody changes them,
and reading twenty objects ten times a second to show that they have not
would be a bus full of nothing.
"""

from __future__ import annotations

import struct

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import drive as cia402
from .drive import Object

#: The tint pycangui's custom panes give a value typed and not yet written.
PENDING = "background-color: rgba(230, 150, 0, 70);"

SECTIONS = (
    (
        "Limits",
        (
            cia402.MAX_TORQUE,
            cia402.MAX_PROFILE_VELOCITY,
            cia402.MAX_MOTOR_SPEED,
            cia402.SOFTWARE_LIMIT_MIN,
            cia402.SOFTWARE_LIMIT_MAX,
        ),
    ),
    (
        "Profile",
        (
            cia402.PROFILE_VELOCITY,
            cia402.PROFILE_ACCELERATION,
            cia402.PROFILE_DECELERATION,
            cia402.QUICK_STOP_DECELERATION,
        ),
    ),
    (
        "Homing",
        (
            cia402.HOMING_METHOD,
            cia402.HOMING_SPEED_SWITCH,
            cia402.HOMING_SPEED_ZERO,
            cia402.HOMING_ACCELERATION,
            cia402.HOME_OFFSET,
        ),
    ),
    (
        "Position",
        (cia402.FOLLOWING_ERROR_WINDOW, cia402.POSITION_WINDOW, cia402.POLARITY),
    ),
    (
        "Stopping",
        (
            cia402.QUICK_STOP_OPTION,
            cia402.HALT_OPTION,
            cia402.SHUTDOWN_OPTION,
            cia402.DISABLE_OPTION,
            cia402.FAULT_REACTION_OPTION,
        ),
    ),
)

#: Every object the tab reads and writes.
OBJECTS = tuple(obj for _title, objects in SECTIONS for obj in objects)

READ_ALL_TIP = "Read every setting from the drive. Anything typed and not written is put back."
WRITE_TIP = (
    "Write the tinted values to the drive, and nothing else.\n"
    "Asks first: these change what the machine does."
)
WRITE_TITLE = "Change the drive's settings?"
WRITE_WARNING = (
    "These are set once for a machine: its limits, its ramps, how it\n"
    "homes and how it stops. Raising a limit or a speed, or changing how\n"
    "it stops, changes what the machine does whatever is asking.\n\n"
    "Write {count}?"
)
NOT_READ = "Not read from this drive yet."
STOPPING_NOTE = (
    "What the drive does on each command, from its option codes. Quick stop "
    "and halt here are what the buttons above, and closing this pane, do."
)


def value_range(obj: Object) -> tuple[int, int]:
    """What a spin box can hold for an object: its type's range, within a
    spin box's own 32-bit signed one."""
    bits = struct.calcsize(obj.fmt) * 8
    if obj.fmt[-1].islower():
        low, high = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    else:
        low, high = 0, (1 << bits) - 1
    return max(low, -(1 << 31)), min(high, (1 << 31) - 1)


class Field:
    """One setting on screen: its widget, the value last read, and whether
    what is shown has been changed since."""

    def __init__(self, obj: Object, widget: QWidget, on_change) -> None:
        self.obj = obj
        self.widget = widget
        self.known: int | None = None
        self._on_change = on_change

    def value(self) -> int:
        raise NotImplementedError

    def _show(self, value: int) -> None:
        raise NotImplementedError

    def take(self, value: int) -> None:
        """A value read from the drive, or just written to it: shown, and not
        a change any more."""
        self.known = int(value)
        self.widget.blockSignals(True)
        self._show(self.known)
        self.widget.blockSignals(False)
        self.refresh()

    def forget(self) -> None:
        """Another drive: nothing known of this one, and nothing changed."""
        self.known = None
        self.widget.blockSignals(True)
        self._show(0)
        self.widget.blockSignals(False)
        self.refresh()

    @property
    def changed(self) -> bool:
        return self.widget.isEnabled() and self.value() != (self.known or 0)

    def refresh(self) -> None:
        self.widget.setStyleSheet(PENDING if self.changed else "")
        self._on_change()


class NumberField(Field):
    def __init__(self, obj: Object, on_change) -> None:
        box = QSpinBox()
        box.setRange(*value_range(obj))
        box.setGroupSeparatorShown(True)
        super().__init__(obj, box, on_change)
        box.valueChanged.connect(lambda _v: self.refresh())

    def value(self) -> int:
        return self.widget.value()

    def _show(self, value: int) -> None:
        self.widget.setValue(value)


class ChoiceField(Field):
    """A code with a meaning: the standard's list, and whatever this drive
    answered with, if that is not on it."""

    def __init__(self, obj: Object, choices: dict[int, str], on_change) -> None:
        box = QComboBox()
        self.choices = choices
        super().__init__(obj, box, on_change)
        self._fill(None)
        box.currentIndexChanged.connect(lambda _i: self.refresh())

    def _fill(self, extra: int | None) -> None:
        box = self.widget
        box.clear()
        codes = sorted(set(self.choices) | ({extra} if extra is not None else set()))
        for code in codes:
            box.addItem(cia402.choice_name(self.choices, code), code)

    def value(self) -> int:
        data = self.widget.currentData()
        return 0 if data is None else int(data)

    def _show(self, value: int) -> None:
        if self.widget.findData(value) < 0:
            self._fill(value)
        self.widget.setCurrentIndex(max(self.widget.findData(value), 0))


class BitsField(Field):
    """One byte shown as the bits that mean something."""

    def __init__(self, obj: Object, bits, on_change) -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        self.ticks: dict[int, QCheckBox] = {}
        for bit, name in bits:
            tick = QCheckBox(name)
            layout.addWidget(tick)
            self.ticks[bit] = tick
        layout.addStretch()
        super().__init__(obj, row, on_change)
        self._other = 0  # bits not shown, kept as read
        for tick in self.ticks.values():
            tick.toggled.connect(lambda _on: self.refresh())

    def value(self) -> int:
        shown = sum(1 << bit for bit, tick in self.ticks.items() if tick.isChecked())
        return self._other | shown

    def _show(self, value: int) -> None:
        self._other = value & ~sum(1 << bit for bit in self.ticks)
        for bit, tick in self.ticks.items():
            tick.blockSignals(True)
            tick.setChecked(bool(value & (1 << bit)))
            tick.blockSignals(False)

    def refresh(self) -> None:
        style = f"QCheckBox {{ {PENDING} }}" if self.changed else ""
        self.widget.setStyleSheet(style)
        self._on_change()


def make_field(obj: Object, on_change) -> Field:
    if obj.where == cia402.HOMING_METHOD.where:
        return ChoiceField(obj, cia402.HOMING_METHODS, on_change)
    if obj.where in cia402.OPTIONS:
        return ChoiceField(obj, cia402.OPTIONS[obj.where], on_change)
    if obj.where == cia402.POLARITY.where:
        return BitsField(obj, cia402.POLARITY_BITS, on_change)
    return NumberField(obj, on_change)


class SettingsTab(QWidget):
    """The tab, over the pane it sits in: the pane knows the drive, whether
    it has an object, its units, and the worker its reads go through."""

    def __init__(self, view, about) -> None:
        super().__init__()
        self.view = view
        self.fields: dict[tuple[int, int], Field] = {}
        #: Each speed limit in the other's unit: see the pane's _show_speed_limits.
        self.in_other: dict[tuple[int, int], QLabel] = {}
        #: Whether this drive's settings have been read since it was chosen.
        self.read_once = False

        layout = QVBoxLayout(self)
        names: list[QLabel] = []
        grids: list[QGridLayout] = []
        for title, objects in SECTIONS:
            box = QGroupBox(title)
            grid = QGridLayout(box)
            for row, obj in enumerate(objects):
                field = make_field(obj, self._show_buttons)
                self.fields[obj.where] = field
                name = QLabel(f"{obj.name}:")
                grid.addWidget(name, row, 0)
                if isinstance(field, NumberField):
                    # As wide as its numbers, not as wide as its column.
                    grid.addWidget(field.widget, row, 1, Qt.AlignmentFlag.AlignLeft)
                else:  # a list's lines are long, and it has no unit
                    grid.addWidget(field.widget, row, 1, 1, 4)
                names.append(name)
                # The field's own tooltip is set by fit(): it also says when
                # the drive does not have the object.
                widgets = (name,)
                if obj.unit:
                    unit = QLabel(obj.unit)
                    grid.addWidget(unit, row, 2)
                    widgets += (unit,)
                    view._unit_labels.append((obj, unit, widgets))
                for widget in (*widgets, field.widget):
                    widget.setToolTip(about(obj))
                if obj in (cia402.MAX_PROFILE_VELOCITY, cia402.MAX_MOTOR_SPEED):
                    other = QLabel("")
                    other.setEnabled(False)
                    self.in_other[obj.where] = other
                    grid.addWidget(other, row, 3)
            grid.setColumnStretch(4, 1)
            if title == "Stopping":
                note = QLabel(STOPPING_NOTE)
                note.setWordWrap(True)
                note.setEnabled(False)
                grid.addWidget(note, len(objects), 0, 1, 5)
            layout.addWidget(box)
            grids.append(grid)
        # One width for the names in every section, so the values line up
        # down the tab rather than section by section.
        widest = max(name.sizeHint().width() for name in names)
        for grid in grids:
            grid.setColumnMinimumWidth(0, widest)
        # And one width for the number boxes, so their units line up too.
        boxes = [f.widget for f in self.fields.values() if isinstance(f, NumberField)]
        widest_box = max(box.sizeHint().width() for box in boxes)
        for box in boxes:
            box.setMinimumWidth(widest_box)

        scaling = QGroupBox("Scaling")
        scaling_inside = QVBoxLayout(scaling)
        self.scaling = QLabel("")
        self.scaling.setWordWrap(True)
        scaling_inside.addWidget(self.scaling)
        self.units_note = QLabel("")
        self.units_note.setWordWrap(True)
        self.units_note.setEnabled(False)
        scaling_inside.addWidget(self.units_note)
        layout.addWidget(scaling)

        self.read_btn = view.app.with_icon(QPushButton("Read all"), "read")
        self.read_btn.setToolTip(READ_ALL_TIP)
        self.read_btn.clicked.connect(lambda: self.read_all(quietly=False))
        self.write_btn = view.app.with_icon(QPushButton("Write changed"), "write")
        self.write_btn.setToolTip(WRITE_TIP)
        self.write_btn.clicked.connect(self.write_changed)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.read_btn)
        buttons.addWidget(self.write_btn)
        layout.addLayout(buttons)
        layout.addStretch()
        self._show_buttons()

    # --- what is on screen ---------------------------------------------------------------
    def changed(self) -> list[Field]:
        return [field for field in self.fields.values() if field.changed]

    def _show_buttons(self) -> None:
        if hasattr(self, "write_btn"):
            count = len(self.changed())
            self.write_btn.setText(f"Write changed ({count})" if count else "Write changed")
            self.write_btn.setEnabled(bool(count))
        if hasattr(self, "view") and hasattr(self.view, "_show_speed_limits"):
            self.view._show_speed_limits()

    def forget(self) -> None:
        """Another drive: what was read from the last one is not this one's."""
        self.read_once = False
        for field in self.fields.values():
            field.forget()

    def fit(self, has, missing_tip: str, about) -> None:
        """Offer what this drive has: an object its EDS does not list is greyed."""
        for field in self.fields.values():
            on = has(field.obj)
            field.widget.setEnabled(on)
            said = about(field.obj)
            field.widget.setToolTip(said if on else f"{said}\n{missing_tip}")
            field.refresh()

    def show_scaling(self, units: dict[str, str], si_velocity, scaling: dict) -> None:
        """What the units and the conversions on this pane come from."""
        lines = [
            f"Position in {units.get('position', cia402.POSITION_UNITS)}, "
            f"velocity in {units.get('velocity', cia402.VELOCITY_UNITS)} (0x60A8, 0x60A9)."
        ]
        increments = scaling.get(cia402.ENCODER_INCREMENTS.where)
        revolutions = scaling.get(cia402.MOTOR_REVOLUTIONS.where)
        if increments and revolutions:
            lines.append(f"Encoder: {increments:,} increments in {revolutions:,} rev (0x608F).")
        numerator = scaling.get(cia402.VELOCITY_FACTOR_NUMERATOR.where)
        divisor = scaling.get(cia402.VELOCITY_FACTOR_DIVISOR.where)
        if numerator and divisor:
            lines.append(f"Velocity factor: {numerator:,} / {divisor:,} (0x6096).")
        current = scaling.get(cia402.MOTOR_RATED_CURRENT.where)
        torque = scaling.get(cia402.MOTOR_RATED_TORQUE.where)
        if current:
            lines.append(f"Motor rated current: {current:,} mA (0x6075).")
        if torque:
            lines.append(f"Motor rated torque: {torque:,} mN m (0x6076).")
        self.scaling.setText("\n".join(lines))

    # --- reading and writing --------------------------------------------------------------
    def read_all(self, quietly: bool = True) -> None:
        """Every setting this drive has. ``quietly`` when the tab is opened
        rather than asked: a drive with no drive chosen is said only when asked."""
        view = self.view
        drive = view._quietly() if quietly else view._drive()
        if drive is None:
            return
        self.read_once = True
        wanted = [field for field in self.fields.values() if view._has(field.obj)]
        failed: list[str] = []
        left = [len(wanted)]

        def finished() -> None:
            left[0] -= 1
            if left[0] == 0 and failed:
                view.app.warn(f"Not read from the drive: {', '.join(failed)}.")

        for field in wanted:

            def done(value, error, field=field) -> None:
                if error:
                    failed.append(f"{field.obj.name} (0x{field.obj.index:04X})")
                else:
                    field.take(int(value))
                finished()

            view._background(lambda o=field.obj: drive.read(o), done)

    def write_changed(self) -> None:
        """Write what has been changed, and only that, after a question."""
        view = self.view
        fields = self.changed()
        drive = view._drive() if fields else None
        if drive is None:
            return
        key = view.node.currentData()
        count = f"{len(fields)} setting{'s' if len(fields) != 1 else ''}"
        warning = WRITE_WARNING.format(count=count)
        if not view.app.confirm.ask(view, f"cia402.settings.{key}", WRITE_TITLE, warning):
            return
        for field in fields:
            value = field.value()

            def done(_result, error, field=field, value=value) -> None:
                if error:
                    view.app.warn(f"{field.obj.name}: {error}")
                else:
                    view.app.log(f"{field.obj.name} <- {value:,}")
                    field.take(value)

            view._background(lambda o=field.obj, v=value: drive.write(o, v), done)
