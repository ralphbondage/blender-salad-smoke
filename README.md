# Blender on SaladCloud: smoke test results and the Cycles render pipeline

Two parts: the smoke test that answered "can EEVEE or Cycles render in a Salad container?", and `render/`, the
pipeline built on the answer. Images are public and contain no secrets or scene data.

## Smoke test result (2026-11-25)
Salad runs every container in WSL2 on volunteer Windows PCs. Measured on a real RTX 4090 node and a real AMD RX 7900 XTX node:

| Engine | NVIDIA node | AMD node |
|---|---|---|
| EEVEE OpenGL (EGL / Xvfb) | CPU only (Mesa llvmpipe), GPU idle, 13-40 s/frame | CPU only |
| EEVEE Vulkan | fails, falls back to CPU | falls back to CPU |
| EEVEE via Mesa d3d12 | not present | libraries load, device creation fails, Blender segfaults |
| Cycles CUDA | works at GPU speed | n/a (no CUDA) |
| Cycles OptiX | fails (init error 7804) | n/a |

So on Salad, EEVEE cannot use the GPU and Cycles can, with CUDA only (no OptiX/RT cores).

## Files
- `Dockerfile`, `Dockerfile.noble`, `run_smoke.sh`, `smoke_blender.py`, `salad_smoke.py`: the smoke test (kept for re-testing).
- `render/Dockerfile`: worker image (CUDA 12.4 runtime, Blender 5.1.1, Kelpie job runner).
- `render/render_cycles.py`: renders a frame range (PNG), skips existing frames, honours camera-cut markers.
- `render/prepare_scene.py`: lists external files a `.blend` needs (and can write a packed copy); never saves the source.
- `render/salad_render.py`: client: upload scene to R2, create the Salad group, queue chunk jobs in Kelpie, status, download frames.
- `.github/workflows/build.yml`: builds the images to `ghcr.io/ralphbondage/` on every push.

## Render pipeline
Scene and frames live in a Cloudflare R2 bucket (S3 API, no egress fees). Kelpie (Salad's job queue) hands each worker one chunk
of frames, downloads the scene first, uploads frames as they finish, and re-queues a chunk if its node disappears.

```powershell
# one-time: user env vars SALAD_API_KEY, R2_ENDPOINT, R2_BUCKET, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY; pip install boto3
python render/salad_render.py upload --job s7 --blend scene_s7_v11.blend
python render/salad_render.py deploy --job s7 --replicas 4 --go          # creates the group, not started
python render/salad_render.py start  --job s7                            # billing starts
python render/salad_render.py submit --job s7 --start 1 --end 1263 --step 2 --samples 48 --go
python render/salad_render.py status --job s7
python render/salad_render.py download --job s7 --out frames
python render/salad_render.py delete --job s7 --go                       # stop billing
```

Measured locally on an RTX 5080, real scene at 1080p, Cycles with CUDA only and OpenImageDenoise:
48 spp = 7.7-8.2 s/frame; 128 spp adaptive = 10-11 s/frame (EEVEE 64 spp is 4.4 s, Cycles with OptiX 5.6 s).
