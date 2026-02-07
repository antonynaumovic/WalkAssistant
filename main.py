import logging
import asyncio
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

import flet as ft

from charts import SpeedChart
from config import WalkAssistantConfig
import osc_server

wa_logger = logging.getLogger("Walk Assistant")
wa_logger.setLevel(logging.DEBUG)

logging.basicConfig(
    format="{asctime} - {levelname} - {message}",
    style="{",
    datefmt="%Y-%m-%d %H:%M",
)


async def init_config(storage_directory: str, config_file: str = "config.yaml"):
    storage_directory = (
        await ft.StoragePaths().get_application_documents_directory()
        + r"\walkassistant\\"
    )
    Path(storage_directory).mkdir(parents=True, exist_ok=True)
    config_file_path = storage_directory + config_file
    wa_logger.info(f"Using config file: {config_file_path}")
    config = WalkAssistantConfig(config_file_path)
    return config


@ft.observable
@dataclass
class WalkAssistantAppState:
    current_screen: str = "Main"
    server_running: bool = False
    current_ip: str = "Server not running"
    last_value: float = 0.0
    keybinds_enabled: bool = False
    page_container: Optional[ft.Control] = None
    config: Optional[WalkAssistantConfig] = None
    osc_server_task: Optional[asyncio.Task] = field(default=None, compare=False)

    def change_screen(self, new_screen: str):
        wa_logger.debug(f"Changing screen to {new_screen}")
        self.current_screen = new_screen


@ft.component
def WalkAssistantAppView():
    # use the observable WalkAssistantAppState as the component state
    # provide a concrete instance so static typing sees the correct type for `state`
    state, set_state = ft.use_state(WalkAssistantAppState())

    # Stable Refs across renders
    value_readout_container_ref = ft.use_ref()
    status_icon_container_ref = ft.use_ref()
    ip_control_container_ref = ft.use_ref()
    restart_button_ref = ft.use_ref()
    main_container_ref = ft.use_ref()
    osc_chart_ref = ft.use_ref()

    # create a Ref for the chart and readout to update them directly if needed,
    # or rely on state for the readout.
    # We use a Ref for the chart to ensure the callback always updates the correct instance.
    if not osc_chart_ref.current:
        osc_chart_ref.current = SpeedChart()
    osc_chart = osc_chart_ref.current
    value_readout_text_control = ft.Text(
        value=f"{state.last_value:.2f}",
        weight=ft.FontWeight.W_600,
        size=80,
    )
    value_readout_container = ft.Container(
        content=value_readout_text_control,
        ref=value_readout_container_ref,
    )

    # simple builders for the two screens; keep minimal placeholders here
    def build_main_screen():
        wa_logger.debug(f"Building Main screen, server_running={state.server_running}")
        return [
            ft.Row([value_readout_container], alignment=ft.MainAxisAlignment.CENTER),
            osc_chart,
        ]

    def build_settings_screen():
        return [
            ft.Text("Settings", weight=ft.FontWeight.W_700),
            ft.Text("Settings controls go here."),
        ]

    # DETERMINISTIC CONTROL GENERATION
    # map screen name to builder call so we always get fresh controls
    screens = {
        "Main": build_main_screen,
        "Settings": build_settings_screen,
    }

    wa_logger.debug(
        f"Rendering AppView: server_running={state.server_running}, current_ip={state.current_ip}"
    )

    # Main container that will hold the current screen's controls
    main_container = ft.Column(
        ref=main_container_ref, expand=True, scroll=ft.ScrollMode.AUTO, spacing=10
    )

    # determine the current screen from the state (cast for static typing)
    current_screen = state.current_screen
    # initialise the container with the current screen using the dataclass field
    if current_screen in screens:
        main_container.controls = screens[current_screen]()
    else:
        wa_logger.error(f"Screen {current_screen} not found")
        main_container.controls = [ft.Text(f"Error: Screen {current_screen} not found")]

    async def start_osc_server():
        wa_logger.info("Starting OSC server...")
        # Get settings from config
        addr = WalkAssistantConfig.config("bind_address")
        if not addr:
            addr = None  # let it auto-detect
        port = WalkAssistantConfig.config("bind_port")
        smoothing = WalkAssistantConfig.config("input_smoothing")

        # Start the server task
        task = asyncio.create_task(
            osc_server.start_async_osc_server(addr=addr, port=port, smoothing=smoothing)
        )
        set_state(lambda s: replace(s, osc_server_task=task))

    async def stop_osc_server():
        wa_logger.debug(
            f"stop_osc_server called, task exists: {state.osc_server_task is not None}"
        )
        if state.osc_server_task:
            wa_logger.info("Stopping OSC server...")
            state.osc_server_task.cancel()
            try:
                await state.osc_server_task
            except asyncio.CancelledError:
                pass
            except Exception:
                wa_logger.exception("Error while awaiting cancelled OSC server task")
            set_state(lambda s: replace(s, osc_server_task=None))
        elif osc_server.is_running():
            # If we don't have the task handle but the server is running,
            # we should still try to stop it if this is a genuine unmount.
            # However, during rapid unmount/remount, we might want to be careful.
            wa_logger.info(
                "Server is running but task handle is missing. Stopping via module..."
            )
            # We don't have a clean way to await it here without the task,
            # but start_async_osc_server handles its own cancellation if the task it runs in is cancelled.
            # If we just want to stop the server regardless:
            if osc_server.server_transport:
                osc_server.server_transport.close()

    async def on_osc_restart(e):
        if osc_server.is_running():
            wa_logger.info("Restarting OSC server...")
            await stop_osc_server()
            await start_osc_server()
        else:
            wa_logger.info("Starting OSC server...")
            await start_osc_server()

    async def on_osc_status_change(running: bool):
        # wa_logger.info(f"OSC server status changed: {running}")

        def update_status(s):
            new_state = replace(s, server_running=running)
            return new_state

        set_state(update_status)
        # Direct update as fallback
        if status_icon_container_ref.current:
            try:
                status_icon_container_ref.current.content = ft.Icon(
                    icon=ft.Icons.CIRCLE,
                    size=8,
                    color=ft.Colors.GREEN if running else ft.Colors.RED,
                )
                status_icon_container_ref.current.update()
            except RuntimeError as e:
                if "Frozen controls cannot be updated" in str(e):
                    pass
                else:
                    raise
            except Exception:
                pass

        if restart_button_ref.current:
            try:
                restart_button_ref.current.icon = (
                    ft.Icons.RESTART_ALT if running else ft.Icons.PLAY_ARROW
                )
                restart_button_ref.current.update()
            except RuntimeError as e:
                if "Frozen controls cannot be updated" in str(e):
                    pass
                else:
                    raise
            except Exception:
                pass

        if ip_control_container_ref.current:
            try:
                # We need to preserve the IP string if possible, but status change usually happens with IP change
                # For now, just ensuring the color updates if it's currently showing something
                current_ip = state.current_ip
                ip_control_container_ref.current.content = ft.Text(
                    value=current_ip,
                    theme_style=ft.TextThemeStyle.LABEL_SMALL,
                    selectable=True,
                    color=(
                        ft.Colors.GREEN
                        if running
                        else ft.Colors.with_opacity(0.2, ft.Colors.WHITE)
                    ),
                )
                ip_control_container_ref.current.update()
            except RuntimeError as e:
                if "Frozen controls cannot be updated" in str(e):
                    pass
                else:
                    raise
            except Exception:
                pass

    async def on_osc_ip_change(ip: str):
        # wa_logger.info(f"OSC server IP changed: {ip}")

        def update_ip(s):
            new_state = replace(s, current_ip=ip)
            return new_state

        set_state(update_ip)
        # Direct update as fallback
        if ip_control_container_ref.current:
            try:
                ip_control_container_ref.current.content = ft.Text(
                    value=ip,
                    theme_style=ft.TextThemeStyle.LABEL_SMALL,
                    selectable=True,
                    color=(
                        ft.Colors.GREEN
                        if state.server_running
                        else ft.Colors.with_opacity(0.2, ft.Colors.WHITE)
                    ),
                )
                ip_control_container_ref.current.update()
            except RuntimeError as e:
                if "Frozen controls cannot be updated" in str(e):
                    pass
                else:
                    raise
            except Exception:
                pass

    async def on_osc_message(msg):
        # msg is a dict with 'smoothed', 'x', 'y', 'z', 'magnitude', 'time', 'endpoint'
        val = msg.get("smoothed", 0.0)
        wa_logger.info(f"Received OSC message with value: {val}")
        # update chart
        try:
            if osc_chart_ref.current:
                osc_chart_ref.current.push_value(val)
        except Exception:
            # wa_logger.exception("Error pushing value to chart")
            pass
        # update state for readout (this will trigger a re-render of the component)
        # set_state(lambda s: replace(s, last_value=val))
        # Direct update for readout
        if value_readout_container_ref.current:
            new_val_str = f"{val:.2f}"
            # Instead of updating the .value of the text control, which might be frozen,
            # we replace the entire content of the container with a new Text control.
            # This is a more robust way to handle high-frequency updates in Flet
            # when dealing with potential frozen controls.
            current_text = value_readout_container_ref.current.content
            if (
                not isinstance(current_text, ft.Text)
                or current_text.value != new_val_str
            ):
                try:
                    value_readout_container_ref.current.content = ft.Text(
                        value=new_val_str,
                        weight=ft.FontWeight.W_600,
                        size=80,
                    )
                    value_readout_container_ref.current.update()
                except RuntimeError as e:
                    if "Frozen controls cannot be updated" in str(e):
                        # If the container itself is frozen, we might need a different approach
                        # for future updates, but for now we just skip this one.
                        pass
                    else:
                        raise
                except Exception:
                    pass

    def did_mount():
        wa_logger.info("Component MOUNTED")
        wa_logger.debug("Mounting OSC server callbacks...")
        osc_server.register_status_callback(on_osc_status_change)
        osc_server.register_ip_callback(on_osc_ip_change)
        osc_server.register_message_callback(on_osc_message)
        # Initial status
        is_running = osc_server.is_running()
        ip_str = osc_server.get_ip_string() or "Server not running"

        # Check if we need to update the state to match server status
        # Note: state.server_running is a bool, is_running is a bool
        if state.server_running != is_running or state.current_ip != ip_str:
            wa_logger.debug(
                f"Initial sync: server_running={is_running}, current_ip={ip_str}"
            )
            set_state(
                lambda s: replace(
                    s,
                    server_running=is_running,
                    current_ip=ip_str,
                )
            )

        if WalkAssistantConfig.config("auto_start_osc") and not is_running:
            wa_logger.info("Auto-starting OSC server from did_mount")
            asyncio.create_task(start_osc_server())

    def will_unmount():
        wa_logger.info("Component UNMOUNTING")
        wa_logger.debug("Unmounting OSC server callbacks...")
        osc_server.unregister_status_callback(on_osc_status_change)
        osc_server.unregister_ip_callback(on_osc_ip_change)
        osc_server.unregister_message_callback(on_osc_message)

    def effect():
        did_mount()
        return will_unmount

    ft.use_effect(effect, [])

    def switch_page(e=None):
        # toggle between Main and Settings
        cur_screen = state.current_screen
        new_page = "Settings" if cur_screen == "Main" else "Main"
        wa_logger.debug(f"Switching to page {new_page}")
        # update state and rely on Flet to re-render the component.
        set_state(lambda s: replace(s, current_screen=new_page))

    osc_status_icon = ft.Icon(
        icon=ft.Icons.CIRCLE,
        size=8,
        color=ft.Colors.GREEN if state.server_running else ft.Colors.RED,
    )
    osc_status_icon_container = ft.Container(
        content=osc_status_icon,
        ref=status_icon_container_ref,
    )
    wa_logger.debug(f"Created osc_status_icon with color: {osc_status_icon.color}")

    osc_current_ip_control = ft.Text(
        value=state.current_ip,
        theme_style=ft.TextThemeStyle.LABEL_SMALL,
        selectable=True,
        color=(
            ft.Colors.GREEN
            if state.server_running
            else ft.Colors.with_opacity(0.2, ft.Colors.WHITE)
        ),
    )
    osc_current_ip_container = ft.Container(
        content=osc_current_ip_control,
        ref=ip_control_container_ref,
    )

    keybinds_checkbox = ft.Checkbox(
        label="Enable Keybinds", value=state.keybinds_enabled
    )

    settings_button = ft.IconButton(
        ft.Icons.SETTINGS,
        on_click=switch_page,
    )
    osc_restart_icon_button = ft.IconButton(
        ref=restart_button_ref,
        icon=ft.Icons.RESTART_ALT if state.server_running else ft.Icons.PLAY_ARROW,
        on_click=on_osc_restart,
        tooltip="Restart OSC server",
        icon_color=ft.Colors.with_opacity(0.2, ft.Colors.WHITE),
        visual_density=ft.VisualDensity.COMPACT,
        icon_size=16,
        padding=ft.Padding.all(0),
    )

    top_appbar = ft.AppBar(
        title=ft.Text(
            "Walk Assistant".upper(),
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
                        osc_status_icon_container,
                        osc_current_ip_container,
                        osc_restart_icon_button,
                    ]
                ),
                keybinds_checkbox,
            ],
            tight=True,
        ),
        height=32,
        padding=ft.Padding.only(left=16),
        bgcolor=ft.Colors.with_opacity(0, ft.Colors.BLUE),
    )

    return ft.View(
        appbar=top_appbar, bottom_appbar=bottom_appbar, controls=[main_container]
    )


async def main(page: ft.Page):
    """
    Main function to initialise page settings, window properties, theme, and construct the
    application interface.

    This function is responsible for configuring the primary application window, setting
    its dimensions, enabling or disabling specific behaviours, and applying theme
    customisations. It also renders the application's main view into the provided page.

    :param page: The `ft.Page` object that represents the application window and user
                 interface settings.
    :type page: ft.Page
    """

    wa_logger.info("Starting Walk Assistant...")

    """
    Page Settings
    """
    app_title = "Walk Assistant"

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
    page.fonts = {"Geist": "fonts/Geist-VariableFont_wght.ttf"}
    page.theme = ft.Theme(color_scheme_seed=ft.Colors.BLUE, font_family="Geist")

    """
    App Construction
    """

    page.title = app_title
    page.scroll = ft.ScrollMode.AUTO

    # Initialise config before rendering views
    try:
        config = await init_config("")
    except Exception as e:
        wa_logger.error(f"Failed to initialize config: {e}")
        # Optionally, exit or continue with defaults if init_config failed

    page.render_views(WalkAssistantAppView)


if __name__ == "__main__":
    ft.run(main, assets_dir="assets")
