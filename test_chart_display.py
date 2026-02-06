from main import SpeedChart
import time

c = SpeedChart(window_seconds=10)
print('initial samples:', len(c.samples))
# push several values quickly
for v in [10, 25, 40, 70, 90]:
    c.push_value(v)
    time.sleep(0.05)

# Force prune and rebuild
c.prune_old()
c._rebuild_points()
pts = c.data_1[0].points
print('points count:', len(pts))
for p in pts:
    try:
        print(f'x={p.x:.2f}, y={p.y}')
    except Exception:
        # some implementations may store attributes differently
        print(p)
