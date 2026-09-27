"""Check that the interactive MuJoCo viewer opens and stays responsive."""
import glob
import json
import os
from pathlib import Path
import time

os.environ.setdefault('DISPLAY', ':0')
if 'XAUTHORITY' not in os.environ:
    matches = sorted(glob.glob('/run/user/1001/.mutter-Xwaylandauth.*'))
    if matches:
        os.environ['XAUTHORITY'] = matches[0]

import mujoco
import mujoco.viewer

model = mujoco.MjModel.from_xml_string(
    '''<mujoco><worldbody>
<light pos="0 0 3"/><geom type="plane" size="1 1 .1"/>
<body pos="0 0 .3"><freejoint/><geom type="sphere" size=".1" rgba=".8 .2 .1 1"/></body>
</worldbody></mujoco>'''
)
data = mujoco.MjData(model)
with mujoco.viewer.launch_passive(model, data) as viewer:
    for _ in range(100):
        assert viewer.is_running()
        mujoco.mj_step(model, data)
        viewer.sync()
        time.sleep(.05)
Path('/work/viewer-check.json').write_text(json.dumps({
    'opened': True,
    'sync_calls': 100,
    'closed': True
}) + '\n')
