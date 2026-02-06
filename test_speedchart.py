from main import SpeedChart

c = SpeedChart()
# push 120 increasing values
for i in range(120):
    # call push_value synchronously; in the absence of a running loop, it appends directly
    c.push_value(i)

print('num_values:', len(c.values))
print('first:', c.values[0])
print('last:', c.values[-1])
# assert bounded
assert len(c.values) <= 100
assert c.values[-1] == 119
print('test passed')
