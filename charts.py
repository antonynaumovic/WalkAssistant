import flet as ft
import flet_charts as fch
from collections import deque
import time
import logging
from typing import Optional


class SpeedChart(fch.LineChart):
    """Simplified time-windowed line chart.

    - Stores timestamped samples (t, v) and renders the last `window_seconds` seconds
      with x-axis mapped to 0..window_seconds.
    - `push_value` simply appends a new timestamped sample and triggers a UI update.
    - No internal animation tasks: keep control flow simple and deterministic.
    """

    logger = logging.getLogger("SpeedChart")

    CHART_WINDOW_SECONDS = 10
    max_chart_points = 30

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

        self.data = [
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
        self.left_axis = fch.ChartAxis(label_size=50, label_spacing=8000)
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
        # self.data is already set above
        self.data_series = self.data

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

        # Create a new LineChartData object with updated points instead of modifying in-place
        new_data = fch.LineChartData(
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
            points=points,
        )

        self.data = [new_data]
        # keep data_series in sync for chart implementations that use it
        try:
            self.data_series = self.data
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
                self.logger.exception("SpeedChart.update failed during UI update")
        except Exception:
            # keep UI robust in the face of chart exceptions
            self.logger.exception("Failed to update SpeedChart")

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
            self.logger.exception("Failed to push value to SpeedChart")
