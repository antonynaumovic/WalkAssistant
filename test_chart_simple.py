from main import SpeedChart
import time

c = SpeedChart(window_seconds=5)
print('initial samples:', len(c.samples))
# push values every second
for i in range(7):
    c.push_value(i)
    time.sleep(1)
    print(f'pushed {i}, samples len:', len(c.samples))

# after pushing 7 samples over 7s with window 5s, samples should be pruned to ~5-6
c.prune_old()
print('after prune samples:', len(c.samples))
# rebuild points and print count
c._rebuild_points()
print('points:', len(c.data_1[0].points))
print('last point x,y:', c.data_1[0].points[-1].x, c.data_1[0].points[-1].y)
