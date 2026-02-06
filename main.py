import asyncio
import logging
import time
import math
from typing import Optional
from collections import deque
import datetime
import flet as ft
import pystray
from PIL import Image
from flet import Padding
import flet_charts as fch
import numpy as np

tray_image = Image.open("assets/tray_img.png")
p: ft.Page

# Reference to the running Flet asyncio loop; set in main()
main_loop: Optional[asyncio.AbstractEventLoop] = None

wa_logger = logging.getLogger("Walk Assistant")
wa_logger.setLevel(logging.DEBUG)

logging.basicConfig(format="{asctime} - {levelname} - {message}", style="{", datefmt="%Y-%m-%d %H:%M", )


def button_clicked(e):
	"""
	Adds a new text element to the page
	indicating that the attached button was directly or indirectly (through the tray's menu items) pressed.
	"""
	#p.add(ft.Text("Button event handler was triggered!"))
	pass


max_chart_points = 30

# Chart window in seconds (display last N seconds)
CHART_WINDOW_SECONDS = 10


class SpeedChart(fch.LineChart):
	"""Simplified time-windowed line chart.

	- Stores timestamped samples (t, v) and renders the last `window_seconds` seconds
	  with x-axis mapped to 0..window_seconds.
	- `push_value` simply appends a new timestamped sample and triggers a UI update.
	- No internal animation tasks: keep control flow simple and deterministic.
	"""

	def __init__(self, window_seconds: int = CHART_WINDOW_SECONDS, max_samples: int = max_chart_points):
		super().__init__()
		self.window_seconds = window_seconds
		# pick a high-contrast line colour and visible stroke
		self.line_color = ft.Colors.GREEN
		# samples: deque of {'t': timestamp, 'v': float}
		self.samples: deque = deque(maxlen=max_samples)
		# seed with a single zero sample so chart has an initial point
		now = time.time()
		self.samples.append({'t': now, 'v': 0.0})

		self.data_1 = [fch.LineChartData(
			stroke_width=2,
			color=self.line_color,
			curved=True,
			below_line_gradient=ft.LinearGradient(colors=[ft.Colors.with_opacity(0.25, self.line_color), "transparent"], begin=ft.Alignment.TOP_CENTER, end=ft.Alignment.BOTTOM_CENTER),
			# initialise points evenly across the time window with zero values
			points=[fch.LineChartDataPoint((i / (max_samples - 1)) * self.window_seconds if max_samples > 1 else 0.0, 0.0) for i in range(max_samples)]
		)]

		# visual defaults
		self.interactive = False
		self.horizontal_grid_lines = fch.ChartGridLines(color=ft.Colors.with_opacity(0.2, ft.Colors.ON_SURFACE), width=1)
		self.left_axis = fch.ChartAxis(label_size=50, label_spacing=8000)
		#self.bottom_axis = fch.ChartAxis(label_size=40, label_spacing=CHART_WINDOW_SECONDS/2)

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
		self.offset=ft.Offset(-0.05, 0)
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
		while self.samples and self.samples[0]['t'] < cutoff:
			self.samples.popleft()

	def _rebuild_points(self) -> None:
		"""Rebuild LineChartDataPoint list for the current time window.

		X runs from 0 (the oldest visible) to window_seconds (now).
		"""
		now_ts = time.time()
		start_ts = now_ts - self.window_seconds
		visible = [s for s in list(self.samples) if s['t'] >= start_ts]
		points = []
		if len(visible) == 0:
			points = [fch.LineChartDataPoint(0.0, 0.0), fch.LineChartDataPoint(self.window_seconds, 0.0)]
		elif len(visible) == 1:
			# single sample: place it at the right edge
			s = visible[0]
			points = [fch.LineChartDataPoint(0.0, float(s['v'])), fch.LineChartDataPoint(self.window_seconds, float(s['v']))]
		else:
			# spread samples evenly across 0..window_seconds to ensure visibility
			n = len(visible)
			for i, s in enumerate(visible):
				x = (i / (n - 1)) * self.window_seconds
				points.append(fch.LineChartDataPoint(x, float(s['v'])))

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
				logging.exception("SpeedChart.update failed during UI update")
		except Exception:
			# keep UI robust in the face of chart exceptions
			logging.exception("Failed to update SpeedChart")

	def push_value(self, new_value: float, ts: Optional[float] = None) -> None:
		"""Append a timestamped sample and update the chart.

		This is intentionally simple and synchronous: higher-level code controls timing.
		"""
		if ts is None:
			ts = time.time()
		try:
			self.samples.append({'t': ts, 'v': float(new_value)})
			self.update_data()
		except Exception:
			logging.exception("Failed to push value to SpeedChart")


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


async def _exit_app(icon, query):
	"""
	Async helper scheduled on the Flet event loop to close the window safely.
	Cancels the background OSC server task if present and unregisters callbacks.
	"""
	global osc_task, osc_status_callback_fn, osc_message_callback_fn, osc_ip_callback_fn, chart_update_task
	button_clicked(None)
	if icon is not None:
		try:
			icon.stop()
		except Exception:
			logging.exception("Failed to stop tray icon from _exit_app")

	# Unregister callbacks if registered
	try:
		import osc_server
		if osc_status_callback_fn is not None:
			try:
				osc_server.unregister_status_callback(osc_status_callback_fn)
			except Exception:
				logging.exception("Failed to unregister status callback")
			osc_status_callback_fn = None
		if osc_message_callback_fn is not None:
			try:
				osc_server.unregister_message_callback(osc_message_callback_fn)
			except Exception:
				logging.exception("Failed to unregister message callback")
			osc_message_callback_fn = None
		if osc_ip_callback_fn is not None:
			try:
				osc_server.unregister_ip_callback(osc_ip_callback_fn)
			except Exception:
				logging.exception("Failed to unregister message callback")
			osc_ip_callback_fn = None
	except Exception:
		pass

	# Cancel OSC server task if running
	if osc_task is not None and not osc_task.done():
		try:
			osc_task.cancel()
			await osc_task
		except asyncio.CancelledError:
			logging.debug("OSC server task cancelled successfully")
		except Exception:
			logging.exception("Error while cancelling OSC server task")

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
		asyncio.create_task(p.window.close())
	except Exception:
		logging.exception("Failed to schedule window close from _exit_app")
	logging.debug("The App was closed/exited successfully!")


def exit_app(icon, query):
	"""
	Synchronous pystray callback that schedules the async window close on the main loop.
	If the main loop isn't available yet, perform a best-effort fallback (stop the icon only).
	"""
	p.window.visible = False
	p.window.skip_task_bar = True
	global main_loop
	if main_loop is not None:
		loop = main_loop
		try:
			asyncio.run_coroutine_threadsafe(_exit_app(icon, query), loop)
		except Exception:
			wa_logger.exception("Failed to schedule _exit_app on main loop")
	else:
		# Fallback: try to stop the icon only and avoid touching Flet objects from this thread.
		if icon is not None:
			try:
				icon.stop()
			except Exception:
				wa_logger.exception("Failed to stop tray icon in fallback exit_app")
		wa_logger.warning("main_loop not set; could not schedule window close")


async def _tray_clicked(icon, query):
	"""
	Async helper that performs UI actions to restore/maximise the window.
	"""
	button_clicked(None)
	p.window.skip_task_bar = False
	p.window.visible = True
	await p.window.to_front()
	p.update()
	wa_logger.debug("Tray icon clicked, bringing the App to the front.")


def tray_clicked(icon, query):
	"""
	Synchronous pystray callback that schedules UI updates on the main loop.
	"""
	global main_loop
	if main_loop is not None:
		loop = main_loop
		try:
			asyncio.run_coroutine_threadsafe(_tray_clicked(icon, query), loop)
		except Exception:
			wa_logger.exception("Failed to schedule _tray_clicked on main loop")
	else:
		# Avoid touching Flet objects from this thread.
		wa_logger.warning("main_loop not set; tray_clicked cannot modify UI safely")


tray_icon = pystray.Icon(name="Test", icon=tray_image, title="Flet in tray", menu=pystray.Menu(
	pystray.MenuItem("Open App", tray_clicked,  # alternative/broader callback: menu_item_clicked
	                 default=True  # set as default menu item
	                 ), pystray.MenuItem("Close App", exit_app  # alternative/broader callback: menu_item_clicked
	                                     )), visible=True, )


def setup_tray_icon(icon):
	"""
	An optional callback to execute in a separate thread once the loop has started.
	It is passed the icon as its sole argument.

	:type icon: pystray.Icon
	"""

	# set the visibility of the tray icon at program start to be True
	icon.visible = True



async def main(page: ft.Page):
	global p, main_loop, osc_task, osc_log_list, osc_status_control, osc_last_msg_control, osc_current_ip_control, \
		osc_status_callback_fn, osc_message_callback_fn, osc_ip_callback_fn, current_screen, \
		value_readout_text_control, osc_chart, chart_update_task, latest_smoothed
	p = page
	# capture the running asyncio loop so pystray callbacks (in another thread) can schedule work on it
	main_loop = asyncio.get_running_loop()

	main_screen = ft.ListView(expand=True, spacing=10)
	settings_screen = ft.ListView(expand=True, spacing=10)


	# start the OSC server in the background on the same loop (but don't auto-start)
	try:
		import osc_server
		# Don't auto-start here — provide explicit Start/Stop controls.
		osc_task = None
		logging.info("OSC server available to start from UI")
	except Exception:
		logging.exception("Failed to import osc_server module")

	# Set up OSC bind controls
	# Load stored preferences (if any)
	try:
		stored_addr = await ft.SharedPreferences().get('osc_bind_addr')
	except Exception:
		stored_addr = None
	try:
		stored_port = await ft.SharedPreferences().get('osc_bind_port')
	except Exception:
		stored_port = None
	try:
		stored_endpoint = await ft.SharedPreferences().get('osc_endpoint')
	except Exception:
		stored_endpoint = None
	try:
		stored_smoothing = await ft.SharedPreferences().get('osc_smoothing')
	except Exception:
		stored_smoothing = None
	try:
		stored_walk_threshold = await ft.SharedPreferences().get('osc_walk_threshold') if not None or "None" else 300
	except Exception:
		stored_walk_threshold = 300
	walk_threshold_value = str(stored_walk_threshold if stored_walk_threshold is not None else 300)

	try:
		stored_walk_key = await ft.SharedPreferences().get('osc_walk_key') if not None or "None" else "w"
	except Exception:
		stored_walk_key = "w"
	walk_key_value = str(stored_walk_key if stored_walk_key is not None else "w")

	try:
		stored_run_threshold = await ft.SharedPreferences().get('osc_run_threshold') if not None or "None" else 700
	except Exception:
		stored_run_threshold = 700
	run_threshold_value = str(stored_run_threshold if stored_run_threshold is not None else 700)

	try:
		stored_run_key = await ft.SharedPreferences().get('osc_run_key') if not None or "None" else "shift+w"
	except Exception:
		stored_run_key = "shift+w"
	run_key_value = str(stored_run_key if stored_run_key is not None else "shift+w")

	addr_value = stored_addr if stored_addr not in (None, "") else ""
	port_value = str(stored_port) if stored_port is not None else "9000"
	endpoint_value = str(stored_endpoint) if stored_endpoint is not None else "/accelerometer"
	smoothing_value = stored_smoothing if stored_smoothing is not None else 0.8

	bind_addr_field = ft.TextField(label="Bind address (leave empty for auto)", value=addr_value, width=300)
	bind_port_field = ft.TextField(label="Bind port", value=port_value, width=120)
	bind_endpoint_field = ft.TextField(label="Bind endpoint", value=endpoint_value, width=300)
	bind_smoothing_field = ft.TextField(label="Smoothing", value=str(smoothing_value), width=120)

	# Chart update interval is fixed (CHART_UPDATE_INTERVAL)

	# Auto-start preference
	try:
		stored_auto = await ft.SharedPreferences().get('osc_auto_start')
	except Exception:
		stored_auto = True
	auto_start_value = bool(stored_auto)

	def on_auto_toggle(e):
		# Persist preference and start/stop OSC accordingly
		val = bool(e.control.value)

		async def _save_and_apply():
			try:
				await ft.SharedPreferences().set('osc_auto_start', str(val))
			except Exception:
				logging.exception("Failed to persist auto-start preference")
			# start or stop based on a new value
			try:
				if val:
					await start_osc()
				else:
					await stop_osc()
			except Exception:
				logging.exception("Failed to apply auto-start preference change")

		# schedule task
		asyncio.create_task(_save_and_apply())

	async def save_bind_settings():
		addr = bind_addr_field.value.strip() if bind_addr_field.value is not None else ""
		port_str = bind_port_field.value.strip() if bind_port_field.value is not None else "9000"
		endpoint = bind_endpoint_field.value.strip() if bind_endpoint_field.value is not None else "/accelerometer"
		smoothing_str = bind_smoothing_field.value.strip() if bind_smoothing_field.value is not None else "0.8"
		try:
			port = int(port_str)
		except Exception:
			port = 9000
		try:
			await ft.SharedPreferences().set('osc_bind_addr', addr)
			await ft.SharedPreferences().set('osc_bind_port', port_str)
			await ft.SharedPreferences().set('osc_endpoint', endpoint)
			await ft.SharedPreferences().set('osc_smoothing', smoothing_str)
			wa_logger.info(
				f"Bind settings saved to SharedPreferences: addr={addr}, port={port}, endpoint={endpoint}, smoothing={smoothing_str}")
			if osc_current_ip_control.value != f"{addr}:{port_str}":
				osc_current_ip_control.italic = True
				osc_restart_icon_button.visible = True
				if not osc_current_ip_control.value.endswith("*"):
					osc_current_ip_control.value += "*"
					osc_current_ip_control.tooltip = "Bind address changed, restart the OSC server to apply"
			else:
				if osc_current_ip_control.tooltip is not None:
					osc_current_ip_control.tooltip = None
				osc_current_ip_control.italic = False
				osc_restart_icon_button.visible = False
			p.update()
		except Exception:
			logging.exception("Failed to save bind settings to SharedPreferences")
		# apply to osc_server if imported
		try:
			import osc_server
			osc_server.set_bind_address(addr if addr != "" else None, port, endpoint)
		except Exception:
			logging.exception("Failed to apply bind settings to osc_server")

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

	walk_threshold_field = ft.TextField(label="Walk Threshold", value=walk_threshold_value)
	walk_key_field = ft.TextField(label="Walk Key", value=walk_key_value)

	run_threshold_field = ft.TextField(label="Run Threshold", value=run_threshold_value)
	run_key_field = ft.TextField(label="Run Key", value=run_key_value)

	auto_start_checkbox = ft.Checkbox(label="Start OSC on launch", value=auto_start_value)
	auto_start_checkbox.on_change = on_auto_toggle
	save_bind_button = ft.Button("Save bind", on_click=on_save_bind)

	# Set up status, last-message controls, and log
	osc_current_ip_control = ft.Text(theme_style=ft.TextThemeStyle.LABEL_SMALL, selectable=True, color=ft.Colors.with_opacity(0.2, ft.Colors.WHITE))
	osc_status_control = ft.Text("OSC: stopped", color=ft.Colors.RED)
	osc_last_msg_control = ft.Text("", max_lines=3)
	osc_log_list = ft.ListView(expand=True, spacing=5, auto_scroll=True, controls=[], scroll=ft.ScrollMode.ALWAYS)
	osc_status_icon = ft.Icon(ft.Icons.CIRCLE, size=8, color=ft.Colors.WHITE)
	osc_restart_icon_button = ft.IconButton(icon=ft.Icons.RESTART_ALT, on_click=on_osc_restart, tooltip="Restart OSC server", icon_color=ft.Colors.with_opacity(0.2, ft.Colors.WHITE), visual_density=ft.VisualDensity.COMPACT, icon_size=16, padding=ft.Padding.all(0))
	osc_chart = SpeedChart()

	# Add bind controls, auto-start checkbox, status, and log to UI
	osc_toggle_button = ft.Button("Start OSC", on_click=on_osc_toggle)

	settings_screen.controls.extend([ft.Row(controls=[walk_threshold_field, walk_key_field]),
	                                 ft.Row(controls=[run_threshold_field, run_key_field]),
		ft.Row(
		controls=[bind_addr_field, bind_port_field, save_bind_button, ft.VerticalDivider(width=8),
				auto_start_checkbox]), ft.Row(controls=[bind_endpoint_field, bind_smoothing_field]),
		ft.Row(controls=[osc_toggle_button, ft.VerticalDivider(width=8), osc_status_control]), ft.ExpansionTile(controls=[ft.Card(content=ft.Container(content=osc_last_msg_control, padding=10, height=128))], title="Last OSC message:"),
		ft.ExpansionTile(title=ft.Text("Recent OSC messages:"), controls=[osc_log_list])])

	# Event-driven callbacks
	def status_cb(running: bool):
		try:
			if osc_status_control is not None:
				osc_status_control.value = "OSC: running" if running else "OSC: stopped"
				osc_status_control.color = ft.Colors.GREEN if running else ft.Colors.RED
				if osc_status_icon is not None:
					osc_status_icon.color = ft.Colors.GREEN if running else ft.Colors.RED
				# keep toggle button label in sync
				try:
					if osc_toggle_button is not None:
						osc_toggle_button.content = "Stop OSC Server" if running else "Start OSC Server"
				except Exception:
					pass
				p.update()
		except Exception:
			logging.exception("Error in status callback")

	def current_ip_cb(ip_str: str):
		try:
			if osc_current_ip_control and ip_str is not None:
				osc_current_ip_control.value = f"{ip_str}"
				p.update()
		except Exception:
			logging.exception("Error in current IP callback")

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
		try:
			if osc_last_msg_control is not None:
				osc_last_msg_control.value = f"{msg}"
			if value_readout_text_control is not None:
				try:
					# update the latest smoothed for the periodic chart updater
					global latest_smoothed, last_msg_time
					latest_smoothed = float(msg.get("smoothed", 0.0))
					# mark the last message time so decay doesn't start
					last_msg_time = time.time()
					# Do NOT push directly here; the periodic updater enforces the maximum update rate.
					# (Removed the immediate osc_chart.push_value for rate-limiting)
				except (ValueError, TypeError):
					value_readout_text_control.value = f"{msg.get("smoothed")}"
			p.update()
			# append to a log list
			try:
				if osc_log_list is not None:
					ts = datetime.datetime.fromtimestamp(msg.get('time', time.time())).strftime('%H:%M:%S')
					entry = f"{ts} {msg.get('endpoint')} x={msg.get('x'):.2f} y={msg.get('y'):.2f} z={msg.get('z'):.2f} mag={msg.get('magnitude'):.2f} sm={msg.get('smoothed'):.2f}"
					osc_log_list.controls.insert(0, ft.Text(entry))
					# keep the list bounded
					if len(osc_log_list.controls) > OSC_LOG_MAX:
						osc_log_list.controls = osc_log_list.controls[:OSC_LOG_MAX]
					p.update()
			except Exception:
				logging.exception("Failed to append OSC message to log list")
		except Exception:
			logging.exception("Error in message callback")

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
			if stored_addr not in (None, ""):
				osc_server.set_bind_address(stored_addr, int(port_value), endpoint_value)
		except Exception:
			pass
		try:
			osc_server.set_smoothing(float(smoothing_value))
		except Exception:
			logging.exception("Failed to set smoothing factor")
		# start automatically if preference set
		try:
			if auto_start_value:
				asyncio.create_task(start_osc())
		except Exception:
			logging.exception("Failed to auto-start OSC server")
		# Decay behaviour: start decaying toward 0 after no new messages for this many seconds
		DECAY_START = 0.8
		# Exponential decay rate (per second) applied to latest_smoothed once decay starts
		DECAY_RATE = 3
		# Apply decay every DECAY_TICK seconds for smooth interpolation
		DECAY_TICK = 0.025
		# start the periodic chart updater
		try:
			async def chart_updater():
				# Throttled chart updater: ensure we update the UI no more often than
				# once every CHART_UPDATE_INTERVAL seconds. Decay is applied on a separate
				# fixed tick (DECAY_TICK) so it interpolates smoothly regardless of push rate.
				global latest_smoothed, last_msg_time
				interval = CHART_UPDATE_INTERVAL
				last_push_time = 0.0
				last_tick = time.monotonic()
				last_decay_time = last_tick
				try:
						start_tick = time.monotonic()
						# compute elapsed time since the last iteration
						now_tick = start_tick
						dt = now_tick - last_tick if last_tick is not None else interval
						last_tick = now_tick
						# Apply decay on fixed ticks: if enough time has passed since last decay
						now_ts = time.time()
						if (now_ts - last_push_time) >= DECAY_START:
							# accumulate decay in DECAY_TICK steps
							decay_elapsed = now_tick - last_decay_time
							if decay_elapsed >= DECAY_TICK:
								# apply decay in fixed DECAY_TICK increments to simulate regular ticks
								num_steps = int(decay_elapsed // DECAY_TICK)
								for _ in range(num_steps):
									decay_factor = math.exp(-DECAY_RATE * DECAY_TICK)
									latest_smoothed = latest_smoothed * decay_factor
									last_decay_time += DECAY_TICK
								# clamp tiny values to zero to avoid noise
								if abs(latest_smoothed) < 0.1:
									latest_smoothed = 0.0
								# ensure last_decay_time doesn't drift beyond now_tick
								if last_decay_time > now_tick:
									last_decay_time = now_tick
						# snapshot value to operate on for this tick
						val = latest_smoothed
						# update readout immediately to reflect decay even when no push occurs
						try:
							if value_readout_text_control is not None:
								value_readout_text_control.value = f"{round(val)}"
								# update the page so the readout reflects the new value
								p.update()
						except Exception:
							logging.exception("Failed to update readout in chart_updater")
						# only push/update the chart if we've waited at least `interval` since the last push
						now = time.monotonic()
						if (now - last_push_time) >= interval:
							try:
								rounded = round(val)
								if osc_chart is not None:
									osc_chart.push_value(rounded)
								# apply the UI update once per push tick
								p.update()
							except Exception:
								logging.exception("Error in chart updater tick")
							last_push_time = time.monotonic()

						elapsed = time.monotonic() - start_tick
						sleep_for = max(0.0, interval - elapsed)
						await asyncio.sleep(sleep_for)
				except asyncio.CancelledError:
					return

			chart_update_task = asyncio.create_task(chart_updater())
		except Exception:
			logging.exception("Failed to start chart updater")
	except Exception:
		logging.exception("Failed to register event callbacks with osc_server")

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
	tray_icon.run_detached(setup=setup_tray_icon)

	async def store_value(key, value, alert):
		await ft.SharedPreferences().set(key, value)
		if alert:
			page.show_dialog(ft.SnackBar(f"{key} saved to SharedPreferences"))
		return True

	async def get_value(key):
		contents = await ft.SharedPreferences().get(key)
		page.add(ft.Text(f"SharedPreferences contents: {contents}"))
		return contents

	async def on_window_event(e):
		"""
		Synchronous handler for window events. Schedule any async window operations
		via asyncio.create_task so no coroutine is returned to the caller.
		"""
		match e.type:
			case ft.WindowEventType.CLOSE:
				# if the window is minimised, we make the icon visible and remove our app from the taskbar/dock.
				wa_logger.debug("Close button pressed.")
				tray_icon.visible = True
				p.window.skip_task_bar = True
				p.window.visible = False
			case ft.WindowEventType.RESTORE:
				# if the window is maximised/restored, we make the icon not visible and add our app back to the taskbar/dock.
				wa_logger.debug("Window was restored.")
				tray_icon.visible = False
				p.window.skip_task_bar = False

		p.update()


	def switch_page(container, new_page):
		global current_screen
		wa_logger.debug(f"Switching to page {new_page}")
		container.controls = screens[new_page]
		current_screen = new_page


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

	"""
	Theme Settings
	"""
	page.fonts = {"Geist": "fonts/Geist-VariableFont_wght.ttf"}
	page.theme = ft.Theme(color_scheme_seed=ft.Colors.BLUE, font_family="Geist")

	"""
	App Construction
	"""

	page.title = app_title
	page.window.on_event = on_window_event
	page.scroll = ft.ScrollMode.AUTO

	value_readout_text_control = ft.Text("0", weight=ft.FontWeight.W_600, size=80)

	main_screen.controls.extend([ft.Row([value_readout_text_control], alignment=ft.MainAxisAlignment.CENTER), osc_chart])

	screens = {"Main": main_screen, "Settings": settings_screen}

	current_screen = "Main"

	main_container = ft.Column(expand=True, scroll=ft.ScrollMode.ALWAYS)

	#page.add(ft.Text("Limit long text to 1 line with ellipsis", theme_style=ft.TextThemeStyle.HEADLINE_LARGE, ))
	settings_button = ft.IconButton(ft.Icons.SETTINGS, on_click=lambda e: switch_page(main_container, "Settings" if current_screen == "Main" else "Main"))
	top_appbar = ft.AppBar(title=ft.Text(page_header.upper(), theme_style=ft.TextThemeStyle.HEADLINE_SMALL, weight=ft.FontWeight.W_800, ), actions=[settings_button])


	bottom_appbar = ft.BottomAppBar(content=ft.Row(controls=[ft.Row(controls=[osc_status_icon, osc_current_ip_control, osc_restart_icon_button])], tight=True), height=32, padding=Padding.only(left=16), bgcolor=ft.Colors.with_opacity(0, ft.Colors.BLUE))
	page.bottom_appbar = bottom_appbar
	page.appbar = top_appbar
	switch_page(main_container, "Main")
	page.add(main_container)




if __name__ == "__main__":
	ft.run(main)
