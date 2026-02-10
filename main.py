import asyncio
import logging
import math
import os
import sys
import time
from collections import deque
from pathlib import Path
from typing import Optional

import flet as ft
import flet_charts as fch
import keyboard
from aenum import EnumType
from infi.systray import SysTrayIcon
from flet import Padding

from config import WalkAssistantConfig
from value_types import WalkAssistantValueTypes

import itertools

# /data/motion/accelerometer/x

p: ft.Page

# Reference to the running Flet asyncio loop; set in main()
main_loop: Optional[asyncio.AbstractEventLoop] = None

wa_logger = logging.getLogger("Walk Assistant")
# wa_logger.setLevel(logging.DEBUG)

logging.basicConfig(
    format="{asctime} - {levelname} - {module} - {message}",
    style="{",
    datefmt="%Y-%m-%d %H:%M:%S",
)

keybinds_enabled = False
keybinds_checkbox = None
run_is_down = False
walk_is_down = False
osc_is_running = None
input_smoothing_value = 0.8


def resource_path(relative_path):
    """Get the absolute path to resource, works for dev and for PyInstaller"""
    try:
        # PyInstaller creates a temp folder and stores the path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return str(os.path.join(base_path, relative_path))


def apply_saved_server_config(
    osc_server, addr_value, port_value, smoothing_value, debug_mode
):
    wa_logger.debug(
        f"Applying saved server config: addr={addr_value}, port={port_value}, smoothing={smoothing_value}, debug_mode={debug_mode}"
    )
    try:
        if addr_value not in (None, ""):
            osc_server.set_bind_address(addr_value, int(port_value))
    except Exception:
        pass
    try:
        osc_server.set_smoothing(float(smoothing_value))
    except Exception:
        wa_logger.exception("Failed to set smoothing factor")

    try:
        osc_server.set_bind_multiplier(float(config.config("multiplier", 1.0)))
    except Exception:
        wa_logger.exception("Failed to set bind multiplier")
    # start automatically if preference set

    osc_server.set_debug_mode(debug_mode)


def apply_endpoint_handlers_from_config(endpoint_groups: list[dict]) -> bool:
    """Tell osc_server to rebuild handlers for the configured endpoint groups."""
    if not endpoint_groups:
        wa_logger.warning("No endpoint_groups provided when building OSC handlers")
        return False
    try:
        import osc_server

        osc_server.create_handlers(endpoint_groups)
        wa_logger.debug("Registered %d endpoint_group handlers", len(endpoint_groups))
        return True
    except Exception:
        wa_logger.exception("Failed to apply endpoint handlers from config")
        return False


class WalkAssistantEndpoints:
    __endpoint_groups = []
    __endpoints = []
    __endpoints_logger = logging.getLogger("WA_Endpoints")
    __endpoints_container = ft.ReorderableListView(
        spacing=12,
        show_default_drag_handles=False,
        header=ft.Text(
            "Default group must be defined in config.yaml with alias 'Default'"
        ),
    )
    __endpoints_tile = ft.ExpansionTile(
        title="Endpoints",
        controls=[
            ft.Container(
                __endpoints_container,
            )
        ],
    )
    __endpoint_controls = []

    def __init__(self, wa_endpoint_groups: list[dict]):
        endpoint_groups = wa_endpoint_groups

        endpoints = list(
            itertools.chain(*[e.get("endpoints") for e in endpoint_groups])
        )
        self.__endpoints_logger.debug(f"Endpoints: {endpoints}")
        self.__endpoints = endpoints

        default_group = next((g for g in endpoint_groups if g.get("id") == 0), None)

        self.__endpoints_logger.debug(f"Default group: {default_group}")

        if default_group is None:
            self.__endpoints_logger.warning(
                "No default group found in config; using empty placeholder"
            )
            default_group = {
                "id": 0,
                "alias": "Default",
                "value_type": WalkAssistantValueTypes.VECTOR3.value,
                "endpoints": [],
            }

        self.__endpoints_container = ft.ReorderableListView(
            show_default_drag_handles=False,
            padding=4,
            expand=True,
            on_reorder=self.handle_reorder,
            header=self.endpoint_group_row(
                default_group["id"],
                default_group["alias"],
                default_group["value_type"],
                [default_group.get("endpoints")],
            ),
            footer=ft.Row(
                controls=[
                    ft.TextButton(
                        content="Add Group", on_click=lambda e: self.add_group(e)
                    ),
                    ft.TextButton(
                        content="Add Endpoint", on_click=lambda e: self.add(e)
                    ),
                    ft.TextButton(
                        content="Save", on_click=lambda e: self.save_endpoints(e)
                    ),
                ]
            ),
        )
        self.__endpoints_tile = ft.ExpansionTile(
            title="Endpoints",
            controls=[
                ft.Container(
                    self.__endpoints_container,
                    padding=ft.Padding.symmetric(vertical=8, horizontal=0),
                )
            ],
        )

        group_controls = []
        self.__endpoints = []
        self.__endpoint_groups = []
        for i, group in enumerate(endpoint_groups):
            self.__endpoints_logger.debug(f"Processing group {i}: {group}")
            group_control = self.endpoint_group_row(
                group["id"],
                group["alias"],
                group["value_type"],
                [group.get("endpoints")],
            )
            self.__endpoint_groups.append(group)
            if group["id"] == 0:
                group_control = self.__endpoints_container.header
            else:
                group_controls.append(group_control)
            for endpoint in group["endpoints"]:
                group_controls.append(
                    self.endpoint_row(
                        endpoint["id"],
                        endpoint["resource"],
                        endpoint["alias"],
                        endpoint["value_type"],
                        endpoint["bind"],
                    )
                )
                self.__endpoints.append(endpoint)

        self.__endpoint_controls.extend(group_controls)
        self.__endpoints_container.controls = self.__endpoint_controls

    @staticmethod
    def update_type_text(e: ft.Event[ft.Dropdown]):
        dropdown = e.control
        if dropdown.parent is None:
            return
        selected_value = dropdown.value
        type_text = next(
            (
                r.bind
                for r in WalkAssistantValueTypes.__members__.values()
                if r.value == selected_value
            ),
            "Error",
        )
        # The Text control is the third control in the Row (index 2)
        text_control = dropdown.parent.controls[2]
        text_control.value = type_text
        p.update()

    def endpoint_group_row(
        self,
        group_id: int = -1,
        alias: str = "",
        value_type: EnumType = WalkAssistantValueTypes.VECTOR3.string,
        endpoints=None,
    ):
        if endpoints is None:
            endpoints = []
        return ft.ListTile(
            key=f"g{group_id}",
            expand=True,
            content_padding=Padding.symmetric(vertical=6, horizontal=0),
            leading=ft.Row(
                controls=[
                    ft.TextField(
                        label=f"Group Alias",
                        value=alias,
                        hint_text="Group Alias",
                        expand=10,
                    ),
                    ft.Dropdown(
                        label=f"Type",
                        value=value_type,
                        options=[
                            ft.DropdownOption(key=t.value, text=t.string)
                            for t in WalkAssistantValueTypes.__members__.values()
                        ],
                        expand=10,
                        on_select=lambda e: self.update_type_text(e),
                    ),
                    ft.Text(
                        next(
                            (
                                r.bind
                                for r in WalkAssistantValueTypes.__members__.values()
                                if r.value == value_type
                            ),
                            "Error",
                        ),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.DELETE_SHARP,
                        on_click=lambda e: self.remove_group(e, group_id),
                        disabled=(group_id == 0),
                        expand=1,
                    ),
                ]
            ),
        )

    def get_lowest_group_id(self):
        if not self.__endpoint_groups:
            return -1
        for i, group in enumerate(reversed(self.__endpoint_controls)):
            if "g" in group.key:
                return int(group.key[1:])
        return -1

    def endpoint_row(
        self,
        row_id: int = -1,
        resource: str = "/accelerometer",
        alias: str = "",
        value_type: WalkAssistantValueTypes = WalkAssistantValueTypes.VECTOR3,
        binding: str = "",
    ):

        is_default = row_id == 0

        return ft.ListTile(
            expand=True,
            key=f"r{row_id}",
            content_padding=Padding.symmetric(vertical=6, horizontal=0),
            leading=ft.Row(
                controls=[
                    ft.ReorderableDragHandle(
                        content=ft.Icon(
                            ft.Icons.DRAG_HANDLE,
                            color=(
                                ft.Colors.with_opacity(0.1, ft.Colors.WHITE)
                                if is_default
                                else ft.Colors.with_opacity(0.6, ft.Colors.WHITE)
                            ),
                        ),
                        mouse_cursor=ft.MouseCursor.GRAB,
                        disabled=is_default,
                        expand=1,
                    ),
                    ft.TextField(
                        label=f"Alias",
                        value=alias,
                        hint_text="Alias",
                        disabled=is_default,
                        expand=6,
                    ),
                    ft.TextField(
                        label=f"Endpoint",
                        value=resource,
                        hint_text="Endpoint",
                        expand=7,
                    ),
                    ft.Dropdown(
                        label=f"Type",
                        value=value_type,
                        options=[
                            ft.DropdownOption(key=t.value, text=t.string)
                            for t in WalkAssistantValueTypes.__iter__()
                        ],
                        expand=5,
                    ),
                    ft.TextField(
                        label="Binding",
                        value=binding,
                        hint_text="XYZW",
                        expand=4,
                        input_filter=ft.InputFilter(regex_string=r"^[XYZWxyzw]{0,4}$"),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.DELETE_SHARP,
                        on_click=lambda e: self.remove(e, row_id),
                        disabled=is_default,
                        expand=1,
                    ),
                ]
            ),
        )

    def add_group(self, e: ft.Event):
        max_id = 0
        for i in self.__endpoint_groups:
            if i.get("id") > max_id:
                max_id = i.get("id")
        new_group_id = max_id + 1
        self.__endpoints_logger.debug(f"Adding new group with id: {new_group_id}")
        self.__endpoint_groups.append(
            {
                "id": new_group_id,
                "alias": f"{new_group_id}",
                "value_type": WalkAssistantValueTypes.VECTOR3.value,
                "endpoints": [],
            }
        )
        self.__endpoint_controls.append(
            self.endpoint_group_row(
                new_group_id,
                f"Group {new_group_id}",
                WalkAssistantValueTypes.VECTOR3.value,
                [],
            )
        )
        self.__endpoints_container.controls = self.__endpoint_controls
        p.update()

    def save_endpoints(self, e: ft.Event):
        out_groups = []
        controls = [self.__endpoints_container.header]
        controls.extend(self.__endpoint_controls)

        for group_control in controls:
            if group_control.key.startswith("g"):
                self.__endpoints_logger.debug(
                    f"Adding group with id: {group_control.key[1:]}"
                )
                out_groups.append(
                    {
                        "id": int(group_control.key[1:]),
                        "alias": group_control.leading.controls[0].value,
                        "value_type": group_control.leading.controls[1].value,
                        "endpoints": [],
                    }
                )
            elif group_control.key.startswith("r"):
                self.__endpoints_logger.debug(
                    f"Adding endpoint with id: {group_control.key[1:]} to group: {out_groups[-1].get("id")}"
                )
                out_groups[-1]["endpoints"].append(
                    {
                        "id": int(group_control.key[1:]),
                        "alias": group_control.leading.controls[1].value,
                        "resource": group_control.leading.controls[2].value,
                        "value_type": group_control.leading.controls[3].value,
                        "bind": group_control.leading.controls[4].value,
                    }
                )

        if len(out_groups) == 0:
            self.__endpoints_logger.warning("No groups found when saving endpoints")
            return False

        self.__endpoints_logger.debug(f"Saved groups: {out_groups}")
        try:
            saved = config.set_array("endpoint_groups", out_groups)
        except Exception:
            self.__endpoints_logger.exception("Failed to persist endpoint groups")
            return False
        apply_endpoint_handlers_from_config(out_groups)
        return saved

    def add(
        self,
        e: ft.Event,
        resource: str = "",
        alias: str = "",
        group: int = -1,
        value_type: WalkAssistantValueTypes = WalkAssistantValueTypes.VECTOR3.value,
    ):

        max_id = 0
        for i in self.__endpoints:
            if i.get("id") > max_id:
                max_id = i.get("id")
        new_id = max_id + 1
        new_alias = alias if alias != "" else f"Endpoint {new_id}"
        group = group if group != -1 else self.get_lowest_group_id()
        self.__endpoints_logger.debug(
            f"Adding new endpoint with resource: {resource}, alias: {alias}, group: {group}, value_type: {value_type}, id: {new_id}"
        )
        self.__endpoints.append(
            {
                "id": new_id,
                "resource": resource,
                "alias": new_alias,
                "group": group,
                "value_type": value_type,
            }
        )
        self.__endpoint_controls.append(
            self.endpoint_row(new_id, resource, new_alias, value_type)
        )
        self.__endpoints_container.controls = self.__endpoint_controls
        p.update()

    def remove(self, e: ft.Event, row_id: int):
        self.__endpoints_logger.debug(f"Removing row {row_id} from endpoints")
        row_index = self.__endpoint_controls.index(
            next((r for r in self.__endpoint_controls if r.key == f"r{row_id}"), None)
        )
        endpoint_index = self.__endpoints.index(
            next((g for g in self.__endpoints if g.get("id") == row_id), None)
        )
        if row_index is not None:
            del self.__endpoint_controls[row_index]
            self.__endpoints_container.controls = self.__endpoint_controls
            del self.__endpoints[endpoint_index]
            p.update()
        else:
            self.__endpoints_logger.error(f"Couldn't find row_id {row_id} for removal")

    def remove_group(self, e: ft.Event, group_id: int):
        self.__endpoints_logger.debug(f"Removing group {group_id} from endpoints")
        row_index = self.__endpoint_controls.index(
            next((r for r in self.__endpoint_controls if r.key == f"g{group_id}"), None)
        )
        group_index = self.__endpoint_groups.index(
            next((g for g in self.__endpoint_groups if g.get("id") == group_id), None)
        )
        if row_index is not None:
            del self.__endpoint_controls[row_index]
            self.__endpoints_container.controls = self.__endpoint_controls
            del self.__endpoint_groups[group_index]
            p.update()
        else:
            self.__endpoints_logger.error(f"Invalid group_id {group_id} for removal")

    def get_endpoints(self):
        self.__endpoints_logger.debug(f"Getting endpoints: {self.__endpoints}")
        return self.__endpoints

    @staticmethod
    def handle_reorder(e: ft.OnReorderEvent):
        e.control.controls.insert(e.new_index, e.control.controls.pop(e.old_index))

    def get_ui(self):
        return self.__endpoints_tile


max_chart_points = 50

# Chart window in seconds (display last N seconds)
CHART_WINDOW_SECONDS = 10

endpoint_ui_holder = None

storage_directory = ""
config: Optional[WalkAssistantConfig] = None
config_file = "config.yaml"


class SpeedChart(fch.LineChart):
    """Simplified time-windowed line chart.

    - Stores timestamped samples (t, v) and renders the last `window_seconds` seconds
      with x-axis mapped to 0..window_seconds.
    - `push_value` simply appends a new timestamped sample and triggers a UI update.
    - No internal animation tasks: keep control flow simple and deterministic.
    """

    def __init__(
        self,
        window_seconds: int = CHART_WINDOW_SECONDS,
        max_samples: int = max_chart_points,
    ):
        super().__init__()
        self.window_seconds = window_seconds
        # pick a high-contrast line colour and visible stroke
        self.line_color = ft.Colors.GREEN
        # samples: deque of {'t': timestamp, 'v': float}
        self.samples: deque = deque(maxlen=max_samples)
        # seed with a single zero sample so chart has an initial point
        now = time.time()
        self.samples.append({"t": now, "v": 0.0})
        self.animation = ft.Animation(60, ft.AnimationCurve.LINEAR_TO_EASE_OUT)
        self.data_1 = [
            fch.LineChartData(
                stroke_width=2,
                color=self.line_color,
                curved=True,
                below_line_gradient=ft.LinearGradient(
                    colors=[
                        ft.Colors.with_opacity(0.25, self.line_color),
                        "transparent",
                    ],
                    begin=ft.Alignment.TOP_CENTER,
                    end=ft.Alignment.BOTTOM_CENTER,
                ),
                # initialise points evenly across the time window with zero values
                points=[
                    fch.LineChartDataPoint(
                        (
                            (i / (max_samples - 1)) * self.window_seconds
                            if max_samples > 1
                            else 0.0
                        ),
                        0.0,
                    )
                    for i in range(max_samples)
                ],
            )
        ]

        # visual defaults
        self.interactive = False
        self.horizontal_grid_lines = fch.ChartGridLines(
            color=ft.Colors.with_opacity(0.2, ft.Colors.ON_SURFACE), width=1
        )
        self.left_axis = fch.ChartAxis(label_size=50, label_spacing=16000)
        # self.bottom_axis = fch.ChartAxis(label_size=40, label_spacing=CHART_WINDOW_SECONDS/2)

        # hint axis ranges to help the chart scale (might be ignored by implementation)
        try:
            self.min_y = 0
            self.min_x = 0
            self.max_x = int(self.window_seconds)
        except Exception:
            pass
        self.height = 128
        self.expand = True
        self.width = 400
        self.offset = ft.Offset(-0.05, 0)
        self.align = ft.Alignment.CENTER
        self.curved = True
        self.color = self.line_color
        self.data = self.data_1
        self.data_series = self.data_1

    def prune_old(self) -> None:
        """Remove samples older than window_seconds from the left side of the deque."""
        if not self.samples:
            return
        cutoff = time.time() - self.window_seconds
        # pop left while too old
        while self.samples and self.samples[0]["t"] < cutoff:
            self.samples.popleft()

    def _rebuild_points(self) -> None:
        """Rebuild LineChartDataPoint list for the current time window.

        X runs from 0 (the oldest visible) to window_seconds (now).
        """
        now_ts = time.time()
        start_ts = now_ts - self.window_seconds
        visible = [s for s in list(self.samples) if s["t"] >= start_ts]
        points = []
        if len(visible) == 0:
            points = [
                fch.LineChartDataPoint(0.0, 0.0),
                fch.LineChartDataPoint(self.window_seconds, 0.0),
            ]
        elif len(visible) == 1:
            # single sample: place it at the right edge
            s = visible[0]
            points = [
                fch.LineChartDataPoint(0.0, float(s["v"])),
                fch.LineChartDataPoint(self.window_seconds, float(s["v"])),
            ]
        else:
            # spread samples evenly across 0..window_seconds to ensure visibility
            n = len(visible)
            for i, s in enumerate(visible):
                x = (i / (n - 1)) * self.window_seconds
                points.append(fch.LineChartDataPoint(x, float(s["v"])))

        self.data_1[0].points = points
        self.data = self.data_1
        # keep data_series in sync for chart implementations that use it
        try:
            self.data_series = self.data_1
        except Exception:
            pass

    def update_data(self) -> None:
        """Trigger a rebuild and request a UI update."""
        try:
            self.prune_old()
            self._rebuild_points()
            # Only call self.update() if the control is attached to a page.
            try:
                _ = self.page
            except Exception:
                # control isn't attached to a page (e.g. in unit tests) — skip update
                return
            try:
                self.update()
            except Exception:
                wa_logger.exception("SpeedChart.update failed during UI update")
        except Exception:
            # keep UI robust in the face of chart exceptions
            wa_logger.exception("Failed to update SpeedChart")

    def push_value(self, new_value: float, ts: Optional[float] = None) -> None:
        """Append a timestamped sample and update the chart.

        This is intentionally simple and synchronous: higher-level code controls timing.
        """
        if ts is None:
            ts = time.time()
        try:
            self.samples.append({"t": ts, "v": float(new_value)})
            self.update_data()
        except Exception:
            wa_logger.exception("Failed to push value to SpeedChart")


# OSC server background task handle (set in main)
osc_task: Optional[asyncio.Task] = None
# Chart update task handle
chart_update_task: Optional[asyncio.Task] = None
# Fixed the chart update interval (seconds)
CHART_UPDATE_INTERVAL = 0.02

# latest smoothed value from incoming messages (sampled by updater)
latest_smoothed: float = 0.0
# the last time we received an OSC message (or updated latest_smoothed)
# initialise to now so decay doesn't start immediately on app launch
last_msg_time: float = time.time()

# UI control for OSC status
osc_status_control: Optional[ft.Text] = None
# UI control for the last message
osc_last_msg_control: Optional[ft.Text] = None
# UI control for the log area
osc_log_list: Optional[ft.ListView] = None
# UI control for the OSC ip
osc_current_ip_control: Optional[ft.Text] = None
# UI control for the value readout text
value_readout_text_control: Optional[ft.Text] = None
# UI control for the value readout chart
osc_chart: Optional[SpeedChart] = None

current_screen = "Main"

# maximum log entries
OSC_LOG_MAX = 200

# Registered callback references so _exit_app can unregister them
osc_status_callback_fn = None
osc_message_callback_fn = None
osc_ip_callback_fn = None


async def _exit_app(tray):
    """
    Async helper scheduled on the Flet event loop to close the window safely.
    Cancels the background OSC server task if present and unregisters callbacks.
    """
    global osc_task, osc_status_callback_fn, osc_message_callback_fn, osc_ip_callback_fn, chart_update_task
    if tray is not None:
        try:
            tray.shutdown()
        except Exception:
            wa_logger.exception("Failed to stop tray icon from _exit_app")

    # Unregister callbacks if registered
    try:
        import osc_server

        if osc_status_callback_fn is not None:
            try:
                osc_server.unregister_status_callback(osc_status_callback_fn)
            except Exception:
                wa_logger.exception("Failed to unregister status callback")
            osc_status_callback_fn = None
        if osc_message_callback_fn is not None:
            try:
                osc_server.unregister_message_callback(osc_message_callback_fn)
            except Exception:
                wa_logger.exception("Failed to unregister message callback")
            osc_message_callback_fn = None
        if osc_ip_callback_fn is not None:
            try:
                osc_server.unregister_ip_callback(osc_ip_callback_fn)
            except Exception:
                wa_logger.exception("Failed to unregister message callback")
            osc_ip_callback_fn = None
    except Exception:
        pass

    # Cancel OSC server task if running
    if osc_task is not None and not osc_task.done():
        try:
            osc_task.cancel()
            await osc_task
        except asyncio.CancelledError:
            wa_logger.debug("OSC server task cancelled successfully")
        except Exception:
            wa_logger.exception("Error while cancelling OSC server task")

    # Cancel chart updater if running
    if chart_update_task is not None and not chart_update_task.done():
        try:
            chart_update_task.cancel()
            await chart_update_task
        except asyncio.CancelledError:
            pass

    try:
        # Schedule the close on the running loop instead of awaiting directly. This avoids
        # 'coroutine was never awaited' warnings in edge cases where the coroutine might
        # not be driven to completion synchronously here.
        asyncio.create_task(p.window.destroy())
    except Exception:
        wa_logger.exception("Failed to schedule window close from _exit_app")
    wa_logger.debug("The App was closed/exited successfully!")


def exit_app(tray):
    """
    Synchronous infi stray callback that schedules the async window close on the main loop.
    If the main loop isn't available yet, perform a best-effort fallback (stop the icon only).
    """
    p.window.visible = False
    p.window.skip_task_bar = True
    global main_loop
    if main_loop is not None:
        loop = main_loop
        try:
            asyncio.run_coroutine_threadsafe(_exit_app(tray), loop)
        except Exception:
            wa_logger.exception("Failed to schedule _exit_app on main loop")
    else:
        # Fallback: try to stop the icon only and avoid touching Flet objects from this thread.
        if tray is not None:
            try:
                tray.shutdown()
            except Exception:
                wa_logger.exception("Failed to stop tray icon in fallback exit_app")
        wa_logger.warning("main_loop not set; could not schedule window close")


async def _tray_clicked():
    """
    Async helper that performs UI actions to restore/maximise the window.
    """
    p.window.skip_task_bar = False
    p.window.visible = True
    await p.window.to_front()
    p.update()
    wa_logger.debug("Tray icon clicked, bringing the App to the front.")


def tray_clicked(tray):
    """
    Synchronous pystray callback that schedules UI updates on the main loop.
    """
    global main_loop
    if main_loop is not None:
        loop = main_loop
        try:
            asyncio.run_coroutine_threadsafe(_tray_clicked(), loop)
        except Exception:
            wa_logger.exception("Failed to schedule _tray_clicked on main loop")
    else:
        # Avoid touching Flet objects from this thread.
        wa_logger.warning("main_loop not set; tray_clicked cannot modify UI safely")


menu_options = (("Open App", None, tray_clicked),)

tray_icon = SysTrayIcon(
    resource_path("favicon.ico"), "Walk Assistant", menu_options, on_quit=exit_app
)


async def main(page: ft.Page):
    global p, main_loop, osc_task, osc_log_list, osc_status_control, osc_last_msg_control, osc_current_ip_control, osc_status_callback_fn, osc_message_callback_fn, osc_ip_callback_fn, current_screen, value_readout_text_control, osc_chart, chart_update_task, latest_smoothed, storage_directory, config, keybinds_checkbox, walk_is_down, run_is_down, osc_is_running
    p = page
    # capture the running asyncio loop so pystray callbacks (in another thread) can schedule work on it
    main_loop = asyncio.get_running_loop()

    main_screen = ft.ListView(expand=True, spacing=10)

    settings_screen = ft.ListView(expand=True, spacing=10)
    settings_screen_container = ft.Container(
        content=settings_screen, padding=ft.Padding.symmetric(vertical=4)
    )

    def toggle_keybinds():
        global keybinds_enabled, keybinds_checkbox
        keybinds_enabled = not keybinds_enabled
        keybinds_checkbox.value = keybinds_enabled
        p.update()
        wa_logger.info(f"Keybinds {'enabled' if keybinds_enabled else 'disabled'}")

    async def init_config():
        global storage_directory, config_file, config, keybinds_enabled, input_smoothing_value
        storage_directory = (
            await ft.StoragePaths().get_application_documents_directory()
            + r"\walkassistant\\"
        )
        Path(storage_directory).mkdir(parents=True, exist_ok=True)
        config_file_path = storage_directory + config_file
        wa_logger.info(f"Using config file: {config_file_path}")
        config = WalkAssistantConfig(config_file_path)
        # Set the logging level from config
        keyboard.add_hotkey(
            config.config("toggle_keybinds_shortcut", "ctrl+shift+/"),
            toggle_keybinds,
            args=(),
        )
        input_smoothing_value = config.config("input_smoothing", 0.8)
        try:
            level_str = config.config("logging_level")
            level = getattr(logging, str(level_str).upper(), logging.INFO)
            # logging.getLogger().setLevel(level)
            wa_logger.setLevel(level)
            wa_logger.info(
                f"Logging level set to {logging.getLevelName(level)} from config"
            )
        except Exception as e:
            wa_logger.setLevel(logging.INFO)
            wa_logger.warning(f"Failed to set logging level from config: {e}")
        return config

    config = await init_config()

    apply_endpoint_handlers_from_config(config.config("endpoint_groups"))

    # start the OSC server in the background on the same loop (but don't auto-start)
    try:
        import osc_server

        # Don't auto-start here — provide explicit Start/Stop controls.
        osc_task = None
        wa_logger.info("OSC server available to start from UI")
    except Exception:
        wa_logger.exception("Failed to import osc_server module")

    walk_key_value = str(config.config("walk_key", "w"))
    run_key_value = str(config.config("run_key", "shift"))
    run_threshold_value = str(config.config("run_threshold", 500))
    walk_threshold_value = str(config.config("walk_threshold", 150))

    addr_value = str(config.config("bind_address", ""))
    port_value = str(config.config("bind_port", 9000))
    endpoint_value = str(
        config.config("endpoint_groups")[0]["endpoints"][0]["resource"]
    )
    smoothing_value = str(config.config("input_smoothing", 0.8))
    multiplier_value = str(config.config("multiplier", 1.0))
    debug_mode = bool(config.config("debug", False))

    bind_addr_field = ft.TextField(
        label="Bind address (leave empty for auto)", value=addr_value, width=300
    )
    bind_port_field = ft.TextField(
        label="Bind port",
        value=port_value,
        width=145,
        input_filter=ft.NumbersOnlyInputFilter(),
    )
    bind_smoothing_field = ft.TextField(
        label="Smoothing",
        value=str(smoothing_value),
        width=145,
        input_filter=ft.NumbersOnlyInputFilter(),
    )
    bind_multiplier_field = ft.TextField(
        label="Multiplier",
        value=str(multiplier_value),
        width=145,
        input_filter=ft.NumbersOnlyInputFilter(),
    )

    # Chart update interval is fixed (CHART_UPDATE_INTERVAL)

    auto_start_value = config.config("auto_start_osc")

    def on_auto_toggle(e):
        # Persist preference and start/stop OSC accordingly
        val = bool(e.control.value)
        try:
            config.set("auto_start_osc", val)
        except Exception:
            wa_logger.exception("Failed to save auto-start preference")

    async def save_bind_settings():
        addr = (
            bind_addr_field.value.strip() if bind_addr_field.value is not None else ""
        )
        port_str = (
            bind_port_field.value.strip()
            if bind_port_field.value is not None
            else "9000"
        )
        smoothing_str = (
            bind_smoothing_field.value.strip()
            if bind_smoothing_field.value is not None
            else "0.8"
        )
        walk_threshold_str = (
            walk_threshold_field.value.strip()
            if walk_threshold_field.value is not None
            else 150
        )
        run_threshold_str = (
            run_threshold_field.value.strip()
            if run_threshold_field.value is not None
            else 500
        )
        walk_key_str = (
            walk_key_field.value.strip() if walk_key_field.value is not None else "w"
        )
        run_key_str = (
            run_key_field.value.strip() if run_key_field.value is not None else "shift"
        )
        try:
            config.set("walk_key", walk_key_str)
        except TypeError:
            wa_logger.warning(
                f"Invalid walk key '{walk_key_str}', keeping previous value"
            )
        try:
            config.set("run_key", run_key_str)
        except TypeError:
            wa_logger.warning(
                f"Invalid run key '{run_key_str}', keeping previous value"
            )
        try:
            walk_threshold = float(walk_threshold_str)
            config.set("walk_threshold", walk_threshold)
        except TypeError:
            wa_logger.warning(
                f"Invalid walk threshold '{walk_threshold_str}', keeping previous value"
            )
        try:
            run_threshold = float(run_threshold_str)
            config.set("run_threshold", run_threshold)
        except TypeError:
            wa_logger.warning(
                f"Invalid run threshold '{run_threshold_str}', keeping previous value"
            )
        try:
            port = int(port_str)
        except TypeError:
            port = 9000
        try:
            smoothing = float(smoothing_str)
        except TypeError:
            smoothing = 0.8
        try:
            multiplier = (
                float(bind_multiplier_field.value.strip())
                if bind_multiplier_field.value is not None
                else 1.0
            )
        except TypeError:
            multiplier = 1.0
        try:
            config.set(
                ["bind_address", "bind_port"],
                [addr, port_str],
            )
            config.set("input_smoothing", smoothing)
            config.set("multiplier", multiplier)
            config.set("auto_start_osc", True)
            # await ft.SharedPreferences().set(
            #     "osc_endpoint", endpoint
            # )  # TODO: Replace with multiple endpoints
            wa_logger.info(f"Bind settings saved")
            import osc_server

            apply_saved_server_config(osc_server, addr, port, smoothing, multiplier)
            if osc_current_ip_control.value != f"{addr}:{port_str}":
                osc_current_ip_control.italic = True
                osc_restart_icon_button.visible = True
                if not osc_current_ip_control.value.endswith("*"):
                    osc_current_ip_control.value += "*"
                    osc_current_ip_control.tooltip = (
                        "Bind address changed, restart the OSC server to apply"
                    )
            else:
                if osc_current_ip_control.tooltip is not None:
                    osc_current_ip_control.tooltip = None
                osc_current_ip_control.italic = False
                osc_restart_icon_button.visible = False
            p.update()
        except Exception:
            wa_logger.exception("Failed to save bind settings")
        # apply to osc_server if imported
        try:
            import osc_server

            osc_server.set_bind_address(addr if addr != "" else None, port)
        except Exception:
            wa_logger.exception("Failed to apply bind settings to osc_server")

    def on_save_bind(e):
        asyncio.create_task(save_bind_settings())

    # Start/Stop button
    def on_osc_toggle(e):
        # schedule the coroutine on the main loop
        # capture into a local variable so static checkers know it's not None when .done() is called
        task = osc_task
        if task is not None and isinstance(task, asyncio.Task) and not task.done():
            asyncio.create_task(stop_osc())
        else:
            asyncio.create_task(start_osc())

    async def on_osc_restart(e):
        task = osc_task
        if task is not None and isinstance(task, asyncio.Task) and not task.done():
            await stop_osc()
            await start_osc()

    walk_threshold_field = ft.TextField(
        label="Walk Threshold", value=walk_threshold_value
    )
    walk_key_field = ft.TextField(label="Walk Key", value=walk_key_value)

    run_threshold_field = ft.TextField(label="Run Threshold", value=run_threshold_value)
    run_key_field = ft.TextField(label="Run Key", value=run_key_value)

    def on_keybinds_toggle(e):
        global keybinds_enabled
        keybinds_enabled = bool(e.control.value)

    keybinds_checkbox = ft.Checkbox(label="Enable keybinds", value=keybinds_enabled)
    keybinds_checkbox.on_change = on_keybinds_toggle

    auto_start_checkbox = ft.Checkbox(
        label="Start OSC on launch", value=auto_start_value
    )
    auto_start_checkbox.on_change = on_auto_toggle
    save_bind_button = ft.Button("Save Settings", on_click=on_save_bind)

    # Set up status, last-message controls, and log
    osc_current_ip_control = ft.Text(
        theme_style=ft.TextThemeStyle.LABEL_SMALL,
        selectable=True,
        color=ft.Colors.with_opacity(0.2, ft.Colors.WHITE),
    )
    osc_status_control = ft.Text("OSC: stopped", color=ft.Colors.RED)
    osc_last_msg_control = ft.Text("", max_lines=3, expand=True)
    osc_log_list = ft.ListView(
        expand=True,
        spacing=5,
        auto_scroll=True,
        controls=[],
        scroll=ft.ScrollMode.ALWAYS,
    )
    osc_status_icon = ft.Icon(ft.Icons.CIRCLE, size=8, color=ft.Colors.WHITE)
    osc_restart_icon_button = ft.IconButton(
        icon=ft.Icons.RESTART_ALT,
        on_click=on_osc_restart,
        tooltip="Restart OSC server",
        icon_color=ft.Colors.with_opacity(0.2, ft.Colors.WHITE),
        visual_density=ft.VisualDensity.COMPACT,
        icon_size=16,
        padding=ft.Padding.all(0),
    )
    osc_chart = SpeedChart()

    def set_debug(e: ft.Event[ft.Checkbox]):
        val = bool(e.control.value)
        try:
            config.set("debug", val)
        except Exception:
            wa_logger.exception("Failed to persist debug mode preference")
        try:
            import osc_server

            osc_server.set_debug_mode(val)
        except Exception:
            wa_logger.exception("Failed to set debug mode in osc_server")

    osc_debug_mode = ft.Checkbox(
        label="Debug mode", value=debug_mode, on_change=lambda e: set_debug(e)
    )
    # Add bind controls, auto-start checkbox, status, and log to UI
    osc_toggle_button = ft.Button("Start OSC", on_click=on_osc_toggle)

    # Event-driven callbacks
    def status_cb(running: bool):
        try:
            if osc_status_control is not None:
                osc_status_control.value = "OSC: running" if running else "OSC: stopped"
                osc_status_control.color = ft.Colors.GREEN if running else ft.Colors.RED
                if osc_status_icon is not None:
                    osc_status_icon.color = (
                        ft.Colors.GREEN if running else ft.Colors.RED
                    )
                # keep toggle button label in sync
                try:
                    if osc_toggle_button is not None:
                        osc_toggle_button.content = (
                            "Stop OSC Server" if running else "Start OSC Server"
                        )
                except Exception:
                    pass
                p.update()
        except Exception:
            wa_logger.exception("Error in status callback")

    def current_ip_cb(ip_str: str):
        try:
            if osc_current_ip_control and ip_str is not None:
                osc_current_ip_control.value = f"{ip_str}"
                p.update()
        except Exception:
            wa_logger.exception("Error in current IP callback")

    async def start_osc():
        """Start the OSC server in the background and update the UI."""
        global osc_task
        try:
            import osc_server

            if osc_task is None or osc_task.done():
                osc_task = asyncio.create_task(osc_server.start_async_osc_server())
                osc_toggle_button.text = "Stop OSC"
                osc_status_control.value = "OSC: starting"
                if osc_current_ip_control is not None:
                    osc_current_ip_control.tooltip = None
                    osc_current_ip_control.italic = False
                if osc_restart_icon_button is not None:
                    osc_restart_icon_button.visible = False
                p.update()
                wa_logger.info("OSC server started from UI")
        except Exception:
            wa_logger.exception("Failed to start OSC server from UI")

    def message_cb(msg):
        global osc_last_msg_control, value_readout_text_control, latest_smoothed, last_msg_time, osc_log_list
        try:
            # Only update the last message control if its tile is expanded
            if tile_e.expanded and osc_last_msg_control is not None:
                osc_last_msg_control.value = f"{msg}"
                p.update()
            if value_readout_text_control is not None:
                if msg.get("output") == "Default":
                    try:
                        x = float(msg.get("x"))
                        y = float(msg.get("y"))
                        z = float(msg.get("z"))

                        latest_smoothed = x**2 + y**2 + z**2
                        last_msg_time = time.time()
                    except (ValueError, TypeError):
                        value_readout_text_control.value = f"{msg.get('x')}"
        except Exception:
            wa_logger.exception("Error in message callback")

    # register callbacks
    try:
        import osc_server

        osc_status_callback_fn = status_cb
        osc_message_callback_fn = message_cb
        osc_current_ip_callback_fn = current_ip_cb
        osc_server.register_status_callback(osc_status_callback_fn)
        osc_server.register_message_callback(osc_message_callback_fn)
        osc_server.register_ip_callback(osc_current_ip_callback_fn)
        # also apply stored bind settings immediately
        try:
            if addr_value not in (None, ""):
                osc_server.set_bind_address(addr_value, int(port_value))
        except Exception:
            pass
        try:
            osc_server.set_smoothing(float(smoothing_value))
        except Exception:
            wa_logger.exception("Failed to set smoothing factor")

        try:
            osc_server.set_bind_multiplier(float(config.config("multiplier", 1.0)))
        except Exception:
            wa_logger.exception("Failed to set bind multiplier")
        # start automatically if preference set

        osc_server.set_debug_mode(debug_mode)
        print(wa_logger.level)
        osc_server.set_debug_level(wa_logger.level)

        try:
            if auto_start_value:
                asyncio.create_task(start_osc())
        except Exception:
            wa_logger.exception("Failed to auto-start OSC server")

        # Decay behaviour and chart updater configuration
        DECAY_START = 0.8
        DECAY_RATE = 3
        DECAY_TICK = 0.025

        # Track whether we have pressed the walk/run keys so we can release them later
        walk_is_down = False
        run_is_down = False
        # Toggle to enable/disable keybind automation from the UI (default True)

        # Attempt to import optional keyboard support; if unavailable, key actions are skipped
        import re

        try:
            import keyboard as _keyboard
        except Exception:
            _keyboard = None
            logging.warning(
                "`keyboard` module not available; keypress automation disabled"
            )

        # start the periodic chart updater
        try:

            async def chart_updater():
                global latest_smoothed, last_msg_time, keybinds_enabled, run_is_down, walk_is_down
                interval = CHART_UPDATE_INTERVAL
                last_push_time = 0.0
                last_tick = time.monotonic()
                last_decay_time = last_tick
                # track when value first went below the walk threshold while the walk key is down
                walk_below_since = None
                # track when value first went below the run threshold while the run key is down
                run_below_since = None
                try:
                    while True:
                        start_tick = time.monotonic()
                        now_tick = start_tick
                        dt = now_tick - last_tick
                        last_tick = now_tick
                        # Apply decay on fixed ticks once the decay window has passed
                        now_ts = time.time()
                        if (now_ts - last_msg_time) >= DECAY_START:
                            decay_elapsed = now_tick - last_decay_time
                            if decay_elapsed >= DECAY_TICK:
                                num_steps = int(decay_elapsed // DECAY_TICK)
                                for _ in range(num_steps):
                                    decay_factor = math.exp(-DECAY_RATE * DECAY_TICK)
                                    latest_smoothed *= decay_factor
                                    last_decay_time += DECAY_TICK
                                # clamp tiny values to zero
                                if abs(latest_smoothed) < 0.01:
                                    latest_smoothed = 0.0
                                if last_decay_time > now_tick:
                                    last_decay_time = now_tick
                        val = latest_smoothed
                        # update readout immediately so the UI shows the decayed value
                        if value_readout_text_control is not None:
                            try:
                                value_readout_text_control.value = f"{round(val)}"
                                p.update()
                            except Exception:
                                wa_logger.exception(
                                    "Failed to update readout in chart_updater"
                                )

                        # Keybind handling: press/release the walk key when the smoothed value crosses the threshold
                        try:
                            try:
                                thr_str = (
                                    walk_threshold_field.value
                                    if walk_threshold_field is not None
                                    else None
                                )
                                walk_thr = (
                                    float(thr_str)
                                    if thr_str is not None and thr_str != ""
                                    else 150.0
                                )
                            except Exception:
                                walk_thr = 15.0
                            key_str = (
                                walk_key_field.value
                                if walk_key_field is not None
                                else ""
                            )
                            key_str = (key_str or "").strip()
                            if _keyboard is not None and key_str != "":
                                keys = [k for k in re.split(r"[+\s-]+", key_str) if k]
                                if val >= walk_thr:
                                    # entered the walking region: reset the below-threshold timer
                                    walk_below_since = None
                                    if not walk_is_down and keybinds_enabled:
                                        try:
                                            for k in keys:
                                                _keyboard.press(k)
                                                wa_logger.debug(f"Pressing {k}")
                                            walk_is_down = True
                                            p.update()
                                        except Exception:
                                            wa_logger.exception(
                                                "Failed to press walk key(s)"
                                            )
                                else:
                                    # value is below walk threshold: only release after continuous 0.5s
                                    if walk_is_down:
                                        if walk_below_since is None:
                                            walk_below_since = time.monotonic()
                                        elif (
                                            time.monotonic() - walk_below_since
                                        ) >= input_smoothing_value:
                                            try:
                                                for k in reversed(keys):
                                                    wa_logger.debug(f"Releasing {k}")
                                                    _keyboard.release(k)
                                                walk_is_down = False
                                                p.update()
                                            except Exception:
                                                wa_logger.exception(
                                                    "Failed to release walk key(s)"
                                                )
                        except Exception:
                            wa_logger.exception("Error handling walk key press/release")

                        # Run key handling: press/release the run key based on smoothed value and threshold
                        try:
                            try:
                                thr_str = (
                                    run_threshold_field.value
                                    if run_threshold_field is not None
                                    else None
                                )
                                run_thr = (
                                    float(thr_str)
                                    if thr_str is not None and thr_str != ""
                                    else 400.0
                                )
                            except Exception:
                                run_thr = 400.0
                            key_str = (
                                run_key_field.value if run_key_field is not None else ""
                            )
                            key_str = (key_str or "").strip()
                            if _keyboard is not None and key_str != "":
                                if val >= run_thr:
                                    if not run_is_down and keybinds_enabled:
                                        try:
                                            for k in re.split(r"[\s+-]+", key_str):
                                                _keyboard.press(k)
                                                wa_logger.debug(f"Pressing {k}")
                                            run_is_down = True
                                        except Exception:
                                            wa_logger.exception(
                                                "Failed to press run key(s)"
                                            )
                                    # entered the run region: reset the below-threshold timer
                                    run_below_since = None
                                else:
                                    # value is below the run threshold: only release after continuous 0.5s
                                    if run_is_down:
                                        if run_below_since is None:
                                            run_below_since = time.monotonic()
                                        elif (
                                            time.monotonic() - run_below_since
                                        ) >= 0.5:
                                            try:
                                                for k in reversed(
                                                    re.split(r"[\s+-]+", key_str)
                                                ):
                                                    _keyboard.release(k)
                                                    wa_logger.debug(f"Releasing {k}")
                                                run_is_down = False
                                            except Exception:
                                                wa_logger.exception(
                                                    "Failed to release run key(s)"
                                                )
                            else:
                                if run_is_down:
                                    try:
                                        for k in reversed(
                                            re.split(r"[\s+-]+", key_str)
                                        ):
                                            _keyboard.release(k)
                                    except Exception:
                                        wa_logger.exception(
                                            "Failed to release run key(s) when disabling keybinds"
                                        )
                                    finally:
                                        run_is_down = False
                            osc_is_running.icon = (
                                ft.Icons.SELF_IMPROVEMENT
                                if not keybinds_enabled
                                else (
                                    ft.Icons.DIRECTIONS_RUN
                                    if run_is_down
                                    else (
                                        ft.Icons.DIRECTIONS_WALK
                                        if walk_is_down
                                        else ft.Icons.MAN
                                    )
                                )
                            )
                            osc_is_running.update()
                        except Exception:
                            wa_logger.exception("Error handling run key press/release")

                        # push chart update at the fixed interval
                        now = time.monotonic()
                        if (now - last_push_time) >= interval:
                            try:
                                rounded = round(val)
                                if (
                                    osc_chart is not None
                                    and current_screen == "Main"
                                    and getattr(osc_chart, "visible", True)
                                    and getattr(osc_chart, "page", None) is not None
                                ):
                                    osc_chart.push_value(rounded)
                                p.update()
                            except Exception:
                                wa_logger.exception("Error in chart updater tick")
                            last_push_time = time.monotonic()

                        # sleep to maintain the interval
                        elapsed = time.monotonic() - start_tick
                        sleep_for = max(0.0, interval - elapsed)
                        await asyncio.sleep(sleep_for)
                except asyncio.CancelledError:
                    # ensure any held key is released on cancellation
                    if _keyboard is not None and walk_is_down:
                        try:
                            key_str = (
                                walk_key_field.value
                                if walk_key_field is not None
                                else ""
                            )
                            keys = [
                                k
                                for k in re.split(r"[+\s-]+", (key_str or "").strip())
                                if k
                            ]
                            for k in reversed(keys):
                                try:
                                    _keyboard.release(k)
                                except Exception:
                                    wa_logger.exception(
                                        "Failed to release walk key(s) on cancellation"
                                    )
                        except Exception:
                            wa_logger.exception(
                                "Failed to release walk key(s) on cancellation"
                            )
                        finally:
                            walk_is_down = False
                    # also ensure run keys released
                    if _keyboard is not None and run_is_down:
                        try:
                            run_key_str = (
                                run_key_field.value if run_key_field is not None else ""
                            )
                            run_keys = [
                                k
                                for k in re.split(
                                    r"[\s+-]+", (run_key_str or "").strip()
                                )
                                if k
                            ]
                            for k in reversed(run_keys):
                                try:
                                    _keyboard.release(k)
                                except Exception:
                                    wa_logger.exception(
                                        "Failed to release run key(s) on cancellation"
                                    )
                        except Exception:
                            wa_logger.exception(
                                "Failed to release run key(s) on cancellation"
                            )
                        finally:
                            run_is_down = False
                    return

            chart_update_task = asyncio.create_task(chart_updater())
        except Exception:
            wa_logger.exception("Failed to start chart updater")
    except Exception:
        wa_logger.exception("Failed to register event callbacks with osc_server")

    async def stop_osc():
        """Stop the background OSC server and update the UI."""
        global osc_task, chart_update_task
        try:
            if osc_task is not None and not osc_task.done():
                osc_task.cancel()
                try:
                    await osc_task
                except asyncio.CancelledError:
                    pass
            osc_task = None
            osc_toggle_button.text = "Start OSC"
            osc_status_control.value = "OSC: stopped"
            osc_status_control.color = ft.Colors.RED
            p.update()
            wa_logger.info("OSC server stopped from UI")
        except Exception:
            wa_logger.exception("Failed to stop OSC server from UI")
        # Also, stop chart updater when stopping OSC
        if chart_update_task is not None and not chart_update_task.done():
            chart_update_task.cancel()
            try:
                await chart_update_task
            except asyncio.CancelledError:
                pass

    # Start the tray icon only after the UI and loop are ready
    tray_icon.start()

    async def on_window_event(e):
        """
        Synchronous handler for window events. Schedule any async window operations
        via asyncio.create_task so no coroutine is returned to the caller.
        """
        match e.type:
            case ft.WindowEventType.CLOSE:
                # if the window is minimised, we make the icon visible and remove our app from the taskbar/dock.
                wa_logger.debug("Close button pressed.")
                p.window.skip_task_bar = True
                p.window.visible = False
            case ft.WindowEventType.RESTORE:
                # if the window is maximised/restored, we make the icon not visible and add our app back to the taskbar/dock.
                wa_logger.debug("Window was restored.")
                p.window.skip_task_bar = False

        p.update()

    def switch_page(container, new_page):
        global current_screen
        wa_logger.debug(f"Switching to page {new_page}")
        container.controls = screens[new_page]
        current_screen = new_page

    async def get_persistent_value(key, param_type: type = None, default=None):
        has_key = await ft.SharedPreferences().contains_key(key)
        if not has_key:
            wa_logger.debug(f"SharedPreferences does not contain key {key}")
            return default
        if param_type is None:
            wa_logger.debug(
                f"Getting persistent value for {key} with no type conversion"
            )
            return await ft.SharedPreferences().get(key)
        else:
            try:
                wa_logger.debug(
                    f"Getting persistent value for {key} of type {param_type}"
                )
                return param_type(await ft.SharedPreferences().get(key))
            except ValueError:
                wa_logger.exception(f"Failed to parse value for key {key}")
                return default

    async def set_persistent_value(key, value):
        wa_logger.debug(f"Setting persistent value for {key} to {value}")
        wa_logger.debug(await ft.StoragePaths().get_application_documents_directory())
        success = await ft.SharedPreferences().set(key, value)
        wa_logger.debug(
            f"{"Successfully" if success else "Failed to"} set persistent value for key {key} to {value} with success {success}"
        )

        return success

    wa_logger.debug("Constructing Walk Assistant UI")
    wa_logger.debug(f"Got Groups: {config.config("endpoint_groups")}")
    wa_endpoints = WalkAssistantEndpoints(config.config("endpoint_groups"))
    endpoints_ui = wa_endpoints.get_ui()

    tile_e = ft.ExpansionTile(
        controls=[
            ft.Row(
                controls=[
                    ft.Card(
                        content=ft.Container(
                            content=osc_last_msg_control,
                            padding=10,
                            height=128,
                            expand=True,
                        ),
                        expand=True,
                    )
                ]
            )
        ],
        title="Last OSC message:",
    )

    settings_screen.controls.extend(
        [
            ft.Row(controls=[walk_threshold_field, walk_key_field]),
            ft.Row(controls=[run_threshold_field, run_key_field]),
            ft.Row(
                controls=[
                    bind_addr_field,
                    bind_port_field,
                ]
            ),
            ft.Row(
                controls=[
                    bind_smoothing_field,
                    bind_multiplier_field,
                    auto_start_checkbox,
                    osc_debug_mode,
                ]
            ),
            ft.Row(
                controls=[
                    save_bind_button,
                ]
            ),
            endpoints_ui,
            tile_e,
            ft.Row(
                controls=[
                    osc_toggle_button,
                    ft.VerticalDivider(width=8),
                    osc_status_control,
                ]
            ),
            # ft.ExpansionTile(
            #     title=ft.Text("Recent OSC messages:"), controls=[osc_log_list]
            # ),
        ]
    )

    osc_is_running = ft.Icon(
        ft.Icons.SELF_IMPROVEMENT,
        color=ft.Colors.with_opacity(0.2, ft.Colors.WHITE),
    )

    """
	Page Settings
	"""
    app_title = "Walk Assistant"
    page_header = "Walk Assistant"

    """
	Window Settings
	"""
    page.window.min_width = 600
    page.window.min_height = 400
    page.window.width = 800
    page.window.height = 500
    page.window.prevent_close = True
    page.window.icon = "favicon.ico"

    """
	Theme Settings
	"""
    font_path = resource_path("assets/fonts/Geist-VariableFont_wght.ttf")
    page.fonts = {"Geist": font_path}
    page.theme = ft.Theme(color_scheme_seed=ft.Colors.BLUE, font_family="Geist")

    """
	App Construction
	"""

    page.title = app_title
    page.window.on_event = on_window_event
    page.scroll = ft.ScrollMode.AUTO

    value_readout_text_control = ft.Text("0", weight=ft.FontWeight.W_600, size=80)

    main_screen.controls.extend(
        [
            ft.Row([value_readout_text_control], alignment=ft.MainAxisAlignment.CENTER),
            osc_chart,
        ]
    )

    screens = {"Main": main_screen, "Settings": settings_screen_container}

    current_screen = "Main"

    main_container = ft.Column(expand=True, scroll=ft.ScrollMode.ALWAYS)

    # page.add(ft.Text("Limit long text to 1 line with ellipsis", theme_style=ft.TextThemeStyle.HEADLINE_LARGE, ))
    settings_button = ft.IconButton(
        ft.Icons.SETTINGS,
        on_click=lambda e: switch_page(
            main_container, "Settings" if current_screen == "Main" else "Main"
        ),
    )
    top_appbar = ft.AppBar(
        title=ft.Text(
            page_header.upper(),
            theme_style=ft.TextThemeStyle.HEADLINE_SMALL,
            weight=ft.FontWeight.W_800,
        ),
        actions=[settings_button],
    )

    bottom_appbar = ft.BottomAppBar(
        content=ft.Row(
            controls=[
                ft.Row(
                    controls=[
                        osc_status_icon,
                        osc_current_ip_control,
                        osc_restart_icon_button,
                    ]
                ),
                keybinds_checkbox,
                ft.Container(expand=True),
                osc_is_running,
            ],
            tight=True,
        ),
        height=32,
        padding=Padding.only(left=16),
        bgcolor=ft.Colors.with_opacity(0, ft.Colors.BLUE),
    )
    page.bottom_appbar = bottom_appbar
    page.appbar = top_appbar
    switch_page(main_container, "Main")
    page.add(main_container)


if __name__ == "__main__":
    ft.run(main, assets_dir="assets")
