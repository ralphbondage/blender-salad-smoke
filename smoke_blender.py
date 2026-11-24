"""Blender-side smoke test. Run as:
  blender -b --factory-startup --python smoke_blender.py -- MODE OUT.png
MODE is "eevee" or "cycles". Prints one line: SMOKE_JSON={...}. Never saves a .blend.
"""
import json
import sys
import time

import bpy

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
mode = argv[0] if argv else "eevee"
out = argv[1] if len(argv) > 1 else "/tmp/smoke.png"

res = {"mode": mode, "blender": bpy.app.version_string}


def finish(ok, **kw):
    res.update(kw)
    res["ok"] = ok
    print("SMOKE_JSON=" + json.dumps(res), flush=True)
    sys.exit(0 if ok else 1)


try:
    import gpu
    res["gpu_backend"] = gpu.platform.backend_type_get()
    res["gpu_renderer"] = gpu.platform.renderer_get()
    res["gpu_vendor"] = gpu.platform.vendor_get()
    res["gpu_version"] = gpu.platform.version_get()
except Exception as e:  # noqa: BLE001 - diagnostics only
    # Expected in background mode (verified locally); the GPU renderer is read from eglinfo/glxinfo
    # in run_smoke.sh instead.
    res["gpu_query_note"] = "unavailable in background mode"

# Minimal but representative scene: glossy floor + sphere + area light + sun, so EEVEE
# actually exercises shadows, reflections and a volume-free lighting path.
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
bpy.ops.mesh.primitive_plane_add(size=10)
floor = bpy.context.object
bpy.ops.mesh.primitive_uv_sphere_add(location=(0, 0, 1))
bpy.ops.object.shade_smooth()
mat = bpy.data.materials.new("m")
mat.use_nodes = True
bsdf = mat.node_tree.nodes["Principled BSDF"]
bsdf.inputs["Base Color"].default_value = (0.8, 0.2, 0.1, 1)
bsdf.inputs["Roughness"].default_value = 0.2
bpy.context.object.data.materials.append(mat)
bpy.ops.object.light_add(type="AREA", location=(3, -3, 5))
bpy.context.object.data.energy = 800
bpy.ops.object.camera_add(location=(6, -6, 4))
cam = bpy.context.object
from mathutils import Vector  # noqa: E402

cam.rotation_euler = (Vector((0, 0, 1)) - cam.location).to_track_quat("-Z", "Y").to_euler()
scene.camera = cam

scene.render.resolution_x = 1920
scene.render.resolution_y = 1080
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.filepath = out

if mode == "eevee":
    items = [i.identifier for i in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
    engine = "BLENDER_EEVEE" if "BLENDER_EEVEE" in items else "BLENDER_EEVEE_NEXT"
    scene.render.engine = engine
    scene.eevee.taa_render_samples = 48
    res["engine"] = engine
elif mode == "cycles":
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 48
    scene.cycles.device = "GPU"
    prefs = bpy.context.preferences.addons["cycles"].preferences
    chosen = None
    for dev_type in ("OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = dev_type
            prefs.refresh_devices() if hasattr(prefs, "refresh_devices") else prefs.get_devices()
            devs = [d for d in prefs.devices if d.type == dev_type]
        except Exception as e:  # noqa: BLE001
            res["cycles_" + dev_type + "_error"] = repr(e)
            continue
        if devs:
            for d in prefs.devices:
                d.use = d.type == dev_type
            chosen = dev_type
            res["cycles_devices"] = [d.name for d in devs]
            break
    res["engine"] = "CYCLES"
    res["cycles_device_type"] = chosen
    if chosen is None:
        finish(False, error="no CUDA/OPTIX device visible to Cycles")
else:
    finish(False, error="unknown mode " + mode)

t0 = time.time()
try:
    bpy.ops.render.render(write_still=True)
except Exception as e:  # noqa: BLE001
    finish(False, error="render raised " + repr(e), seconds=round(time.time() - t0, 2))
sec = round(time.time() - t0, 2)

import os  # noqa: E402

size = os.path.getsize(out) if os.path.exists(out) else 0
finish(size > 10000, seconds=sec, png=out, png_bytes=size)
