# Salad smoke test: EEVEE and Cycles on SaladCloud

Question this answers: can Blender 5.1.1 render in a Salad container with EEVEE (OpenGL via EGL, OpenGL via
Xvfb, or Vulkan) and with Cycles (OptiX/CUDA)? The result decides whether the Salad render pipeline can keep
your EEVEE scenes or must use Cycles / another provider.

Status 2026-11-24: **nothing built, pushed or deployed.** No Docker on this machine and no Salad key yet.

Checked without Docker or Salad:
- `smoke_blender.py` runs in the local Blender 5.1.1 (RTX 5080): EEVEE 1080p 48 samples renders in about 1.3-3.8 s
  (image looks right), Cycles picks OPTIX and renders in 3.4 s. Use these as the local baseline.
- The CUDA base image tag `nvidia/cuda:12.4.1-runtime-ubuntu22.04` exists on Docker Hub.
- The Blender 5.1.1 tarball URL in the Dockerfile returns 200 (396 MB).
- All apt packages in the Dockerfile exist in Ubuntu 22.04 (jammy / jammy-updates).
- Shell and Python syntax checks pass.

Not checked: the Docker build itself, `run_smoke.sh` in a container (including whether `glxinfo` ships in
`mesa-utils-bin`), and the Salad request body in `salad_smoke.py` (from the docs, not a live call).

## Files
- `Dockerfile`: CUDA 12.4 runtime base + Blender 5.1.1 (official tarball) + EGL/Vulkan/Xvfb tooling.
- `run_smoke.sh`: prints GPU/driver/EGL/Vulkan diagnostics, then four 1080p renders (48 samples):
  `eevee_opengl_egl`, `eevee_opengl_xvfb`, `eevee_vulkan`, `cycles_gpu`. Prints a JSON summary between
  `SMOKE_SUMMARY_BEGIN` / `SMOKE_SUMMARY_END`, then holds 15 min (HOLD_SECONDS) so logs can be read.
- `smoke_blender.py`: the in-Blender test (scene, engine setup, timing, PNG size, GPU renderer string).
- `salad_smoke.py`: create / start / status / logs / delete via the Salad API. `create` and `delete` are dry runs
  unless `--go` is passed.

## Steps (you do the account-bound ones)
1. Build and push a **public** image (no secrets inside). Needs Docker Desktop, or build in CI.
   ```powershell
   docker build -t <your-dockerhub-or-ghcr-user>/blender-salad-smoke:1 .
   docker push <your-dockerhub-or-ghcr-user>/blender-salad-smoke:1
   ```
2. Set credentials (API key from the Salad portal; never commit it):
   ```powershell
   $env:SALAD_API_KEY="..."; $env:SALAD_ORG="..."; $env:SALAD_PROJECT="..."
   python salad_smoke.py gpu-classes
   python salad_smoke.py create --image <user>/blender-salad-smoke:1 --gpu 4090      # dry run
   python salad_smoke.py create --image <user>/blender-salad-smoke:1 --gpu 4090 --go # creates, not started
   python salad_smoke.py start                                                       # billing starts per second
   ```
3. Read stdout in the Salad portal (container group > Logs). Look for the `SMOKE_SUMMARY` block.
4. **Stop billing:** `python salad_smoke.py delete --go` when done (the container also exits after the hold).

Expected cost: a few cents (Lowest/"batch" tier, RTX 4090 about $0.16/h, billed per second while running;
allocation and image download are free). A node may take a while to be allocated.

## Reading the result
- `eevee_opengl_egl` ok: EEVEE works headless. The pipeline can keep your scenes and EEVEE settings.
- Only `eevee_opengl_xvfb` ok: works, but may be slower; time it against your 2.1 s/frame RTX 5080 baseline.
- Only `eevee_vulkan` ok: use `--gpu-backend vulkan` in the worker.
- Only `cycles_gpu` ok: the node exposes compute but not graphics. Use Cycles (look changes, cost changes) or a
  provider with real Linux GPUs (RunPod, Vast).
- All fail: check `nvidia-smi` and the driver-libs sections; paste the log back and we adjust.
