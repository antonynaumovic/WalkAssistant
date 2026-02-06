import osc_server

msgs = []

def status_cb(r):
    print('status_cb ->', r)

def msg_cb(m):
    print('msg_cb ->', m)
    msgs.append(m)

# register callbacks
osc_server.register_status_callback(status_cb)
osc_server.register_message_callback(msg_cb)

# Simulate incoming OSC accel message
osc_server.acceleration_handler('/accelerometer', 1.0, 2.0, 3.0)

print('collected messages:', msgs)

# cleanup
osc_server.unregister_status_callback(status_cb)
osc_server.unregister_message_callback(msg_cb)
