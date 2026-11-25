# Render a frame range with Cycles as one animation job (PNG sequence). Never saves the .blend.
# args after "--": OUTDIR START END STEP W H SAMPLES CAMERA PREFIX
#   CAMERA is an object name, or "auto" to honour the scene's camera-bound markers (camera cuts).
# Environment (all optional):
#   CYCLES_DEVICE_TYPE   CUDA (default, what Salad nodes support) | OPTIX | HIP | NONE (CPU)
#   DENOISER             OIDN (default) | OPTIX | NONE
#   OIDN_GPU             1 (default) = run OpenImageDenoise on the GPU; 0 = on the CPU
#   ADAPTIVE_THRESHOLD   e.g. 0.02 to turn adaptive sampling on (SAMPLES becomes the max); unset = fixed samples
#   HIDE_RENDER_OBJECTS  comma-separated object names hidden from render (default "Cone", as in the EEVEE script)
# Existing frames are skipped (use_overwrite off), so an interrupted chunk resumes where it stopped.
import os
import sys
import time

import bpy

a = sys.argv[sys.argv.index("--") + 1:]
outdir, s0, s1, step = a[0], int(a[1]), int(a[2]), int(a[3])
W, H, S, cam, prefix = int(a[4]), int(a[5]), int(a[6]), a[7], a[8]

sc = bpy.context.scene
r = sc.render
r.engine = "CYCLES"
r.resolution_x, r.resolution_y, r.resolution_percentage = W, H, 100
r.use_motion_blur = False                       # frames are interpolated later; blur would smear the flow
r.image_settings.media_type = "IMAGE"
r.image_settings.file_format = "PNG"
r.image_settings.color_mode = "RGB"
r.image_settings.compression = 30
r.use_file_extension = True
r.use_overwrite = False                         # resume: skip frames already on disk
r.use_placeholder = False                       # an interrupted frame must not leave an empty file that is then skipped

if cam != "auto":
    sc.camera = bpy.data.objects[cam]

for n in [x.strip() for x in os.environ.get("HIDE_RENDER_OBJECTS", "Cone").split(",") if x.strip()]:
    o = bpy.data.objects.get(n)
    if o:
        o.hide_render = True

# ---- Cycles device ----
dev_type = os.environ.get("CYCLES_DEVICE_TYPE", "CUDA").upper()
c = sc.cycles
c.samples = S
adaptive = os.environ.get("ADAPTIVE_THRESHOLD")
c.use_adaptive_sampling = adaptive is not None
if adaptive is not None:
    c.adaptive_threshold = float(adaptive)
    c.adaptive_min_samples = 16

prefs = bpy.context.preferences.addons["cycles"].preferences
if dev_type == "NONE":
    c.device = "CPU"
else:
    c.device = "GPU"
    prefs.compute_device_type = dev_type
    prefs.refresh_devices() if hasattr(prefs, "refresh_devices") else prefs.get_devices()
    used = []
    for d in prefs.devices:
        d.use = d.type == dev_type
        if d.use:
            used.append(d.name)
    print("CYCLES_DEVICES %s %s" % (dev_type, used))
    if not used:
        print("FATAL no %s device found; refusing to fall back to CPU" % dev_type)
        sys.stdout.flush()
        sys.exit(3)

# ---- denoising ----
den = os.environ.get("DENOISER", "OIDN").upper()
if den == "NONE":
    c.use_denoising = False
else:
    c.use_denoising = True
    c.denoiser = "OPTIX" if den == "OPTIX" else "OPENIMAGEDENOISE"
    if c.denoiser == "OPENIMAGEDENOISE":
        c.denoising_use_gpu = os.environ.get("OIDN_GPU", "1") == "1"

# ---- missing external files: say so loudly, the render will have pink or black stand-ins ----
missing = []
for img in bpy.data.images:
    if img.source == "FILE" and not img.packed_file and img.filepath:
        p = bpy.path.abspath(img.filepath)
        if not os.path.exists(p):
            missing.append(img.filepath)
if missing:
    print("MISSING_TEXTURES %d: %s" % (len(missing), "; ".join(missing[:10])))

os.makedirs(outdir, exist_ok=True)
sc.frame_start, sc.frame_end, sc.frame_step = s0, s1, step
r.filepath = os.path.join(outdir, prefix + "_####")

T = {}


def _pre(scene, *_):
    T["t0"] = time.time()


def _post(scene, *_):
    print("FRAME_T %d %.2f" % (scene.frame_current, time.time() - T["t0"]))
    sys.stdout.flush()


bpy.app.handlers.render_pre.append(_pre)
bpy.app.handlers.render_post.append(_post)
print("RENDER_PLAN frames %d..%d step %d, %dx%d, %d spp, %s/%s -> %s"
      % (s0, s1, step, W, H, S, dev_type, den, outdir))
sys.stdout.flush()
t_all = time.time()
bpy.ops.render.render(animation=True)
print("RENDER_DONE %.1f s" % (time.time() - t_all))
sys.stdout.flush()
