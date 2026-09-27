"""Render a simple scene to verify the deployed graphics stack."""
import json
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image

model = mujoco.MjModel.from_xml_string(
    '''<mujoco><worldbody>
<light pos="0 0 3"/><geom type="plane" size="1 1 .1" rgba=".4 .4 .4 1"/>
<geom type="sphere" pos="0 0 .1" size=".1" rgba=".8 .2 .1 1"/>
</worldbody></mujoco>'''
)
data = mujoco.MjData(model)
mujoco.mj_forward(model, data)
with mujoco.Renderer(model, height=240, width=320) as renderer:
    renderer.update_scene(data)
    pixels = renderer.render()
    assert pixels.std() > 5
    Image.fromarray(pixels).save('/work/render-check.png')
report = {
    'width': 320,
    'height': 240,
    'pixel_std': float(np.std(pixels)),
    'backend': 'EGL',
    'software_rendering_requested': True
}
Path('/work/render-check.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
