#!/usr/bin/env bash
# Salad smoke test entrypoint. Runs a matrix of Blender render attempts, prints a summary
# between SMOKE_SUMMARY_BEGIN / SMOKE_SUMMARY_END, optionally PUTs it to $RESULT_URL, then
# sleeps $HOLD_SECONDS (default 600) so the logs can be read in the Salad portal before exit.
# Nothing here is destructive; it only writes under /tmp.
set -u
HOLD_SECONDS="${HOLD_SECONDS:-600}"
RESULTS=/tmp/smoke_results.jsonl
: > "$RESULTS"

section() { printf '\n===== %s =====\n' "$1"; }

section "environment"
date -u
uname -a
echo "NVIDIA_DRIVER_CAPABILITIES=${NVIDIA_DRIVER_CAPABILITIES:-}"
nproc; free -m | head -2; df -h /tmp | tail -1

section "nvidia-smi"
nvidia-smi 2>&1 | head -20 || echo "nvidia-smi missing"

section "driver libs visible"
ldconfig -p | grep -Ei 'libEGL_nvidia|libGLX_nvidia|libnvidia-glcore|libnvidia-vulkan|libnvoptix|libcuda\.so|libnvidia-rtcore' || echo "no NVIDIA libs listed by ldconfig"
ls -la /dev/dxg /dev/nvidia* /usr/lib/wsl/lib 2>&1 | head -30

section "eglinfo (surfaceless/device platform)"
timeout 60 eglinfo -B 2>&1 | head -40 || echo "eglinfo failed"

section "vulkaninfo"
timeout 60 vulkaninfo --summary 2>&1 | head -40 || echo "vulkaninfo failed"

section "WSL/D3D12 library probe (needed for Mesa d3d12 = GPU OpenGL over /dev/dxg)"
echo "--- find libd3d12 / libdxcore / libnvdxgdmal anywhere:"
find / -xdev \( -name 'libd3d12*' -o -name 'libdxcore*' -o -name 'libnvdxgdmal*' -o -name 'libnvidia-*' \) 2>/dev/null | head -40
echo "--- /usr/lib/wsl, /lib/wsl, /mnt/wsl:"
ls -la /usr/lib/wsl /usr/lib/wsl/lib /lib/wsl /mnt/wsl 2>&1 | head -20
echo "--- NVIDIA/WSL-looking mounts:"
grep -iE 'wsl|nvidia|dxg|lib' /proc/mounts | head -20
echo "--- Mesa dri drivers present:"
ls /usr/lib/x86_64-linux-gnu/dri 2>&1 | head -30
echo "--- EGL surfaceless with Mesa d3d12 forced:"
EGL_PLATFORM=surfaceless MESA_LOADER_DRIVER_OVERRIDE=d3d12 GALLIUM_DRIVER=d3d12 MESA_D3D12_DEFAULT_ADAPTER_NAME=NVIDIA LIBGL_DEBUG=verbose timeout 60 eglinfo -B 2>&1 | head -30
echo "--- GLX (xvfb) with Mesa d3d12 forced:"
MESA_LOADER_DRIVER_OVERRIDE=d3d12 GALLIUM_DRIVER=d3d12 MESA_D3D12_DEFAULT_ADAPTER_NAME=NVIDIA LIBGL_DEBUG=verbose timeout 60 xvfb-run -a -s "-screen 0 1280x720x24 +extension GLX" glxinfo -B 2>&1 | grep -iE 'renderer|d3d12|error|fail|libd3d|dxcore|MESA' | head -15

run_case() {  # name, command...
  local name="$1"; shift
  section "case: $name"
  local log="/tmp/case_${name}.log"
  # Sample GPU utilization every 0.5 s during the run, and count child CPU seconds
  # (fields 16+17 of /proc/$$/stat, in 1/100 s). GPU util ~0% with high CPU seconds
  # during an EEVEE render means it ran on the CPU (software GL), not the GPU.
  local smp="/tmp/gpu_${name}.csv"
  nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits -lms 500 >"$smp" 2>&1 &
  local smp_pid=$!
  local c0 t0
  c0=$(awk '{print $16+$17}' /proc/$$/stat); t0=$(date +%s.%N)
  timeout 600 "$@" >"$log" 2>&1
  local rc=$?
  local c1 t1 cpu_s wall_s
  c1=$(awk '{print $16+$17}' /proc/$$/stat); t1=$(date +%s.%N)
  kill "$smp_pid" 2>/dev/null; wait "$smp_pid" 2>/dev/null
  cpu_s=$(awk -v a="$c0" -v b="$c1" 'BEGIN{printf "%.1f",(b-a)/100}')
  wall_s=$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.1f",b-a}')
  local gmax gmem
  gmax=$(awk -F', *' 'NR>0 && $1+0>m {m=$1+0} END{print m+0}' "$smp")
  gmem=$(awk -F', *' '$2+0>m {m=$2+0} END{print m+0}' "$smp")
  tail -n 25 "$log"
  echo "usage: wall=${wall_s}s cpu=${cpu_s}s gpu_util_max=${gmax}% gpu_mem_max=${gmem}MiB"
  local line
  line=$(grep -h '^SMOKE_JSON=' "$log" | tail -1 | sed 's/^SMOKE_JSON=//')
  [ -z "$line" ] && line="{\"ok\":false,\"error\":\"no SMOKE_JSON (exit $rc, likely crash)\"}"
  echo "{\"case\":\"$name\",\"exit\":$rc,\"wall_s\":$wall_s,\"cpu_s\":$cpu_s,\"gpu_util_max_pct\":$gmax,\"gpu_mem_max_mib\":$gmem,\"result\":$line}" >> "$RESULTS"
}

section "which GL renderer would Blender get? (llvmpipe = CPU software, NOT a real GPU)"
EGL_R=$(timeout 60 eglinfo -B 2>&1 | grep -iE 'renderer' | head -3 | tr '\n' ';')
GLX_R=$(timeout 60 xvfb-run -a -s "-screen 0 1280x720x24 +extension GLX" glxinfo -B 2>&1 | grep -iE 'renderer' | head -2 | tr '\n' ';')
echo "EGL: $EGL_R"; echo "GLX(xvfb): $GLX_R"
echo "{\"case\":\"gl_renderer_probe\",\"egl\":\"${EGL_R//\"/}\",\"glx_xvfb\":\"${GLX_R//\"/}\",\"software_only\":$(echo "$EGL_R $GLX_R" | grep -qi llvmpipe && echo true || echo false)}" >> "$RESULTS"

B="blender -b --factory-startup --python /smoke/smoke_blender.py"
run_case eevee_opengl_egl     $B -- eevee /tmp/eevee_gl.png
run_case eevee_opengl_xvfb    xvfb-run -a -s "-screen 0 1280x720x24 +extension GLX" $B -- eevee /tmp/eevee_xvfb.png
run_case eevee_vulkan         blender -b --factory-startup --gpu-backend vulkan --python /smoke/smoke_blender.py -- eevee /tmp/eevee_vk.png
run_case eevee_d3d12_forced   env EGL_PLATFORM=surfaceless MESA_LOADER_DRIVER_OVERRIDE=d3d12 GALLIUM_DRIVER=d3d12 MESA_D3D12_DEFAULT_ADAPTER_NAME=NVIDIA $B -- eevee /tmp/eevee_d3d12.png
run_case cycles_gpu           $B -- cycles /tmp/cycles.png

section "SUMMARY"
echo SMOKE_SUMMARY_BEGIN
cat "$RESULTS"
echo SMOKE_SUMMARY_END

if [ -n "${RESULT_URL:-}" ]; then
  section "uploading summary to RESULT_URL"
  curl -fsS -X PUT --data-binary @"$RESULTS" "$RESULT_URL" && echo "uploaded" || echo "upload failed"
fi

echo "Holding ${HOLD_SECONDS}s so logs can be read; then exiting."
sleep "$HOLD_SECONDS"
exit 0
