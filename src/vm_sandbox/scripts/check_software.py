"""Verify a deployed Python environment with a free-fall simulation."""
import json
import platform
from pathlib import Path
import mujoco
import numpy as np
import scipy
import trimesh
import manifold3d

model = mujoco.MjModel.from_xml_string('''<mujoco><option timestep="0.001"/>
<worldbody><geom type="plane" size="1 1 .1"/><body pos="0 0 1">
<freejoint/><geom type="sphere" size=".05" mass="1"/></body></worldbody></mujoco>''')
data = mujoco.MjData(model)
for _ in range(100):
    mujoco.mj_step(model, data)
assert .94 < data.qpos[2] < .96, data.qpos[2]
assert platform.python_version() == '3.11.16'
assert mujoco.__version__ == '3.3.7'
report = {'python': platform.python_version(), 'mujoco': mujoco.__version__,
          'numpy': np.__version__, 'scipy': scipy.__version__, 'trimesh': trimesh.__version__,
          'free_fall_z': float(data.qpos[2]), 'simulation_time': data.time}
Path('/work/software-check.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps(report))
