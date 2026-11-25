"""Render a Blender frame range with Cycles on SaladCloud, using Cloudflare R2 for files and Kelpie as the job queue.

  python salad_render.py upload   --job NAME --blend scene.blend [--assets DIR]
  python salad_render.py deploy   --job NAME [--gpu 4090,5090,5080] [--replicas 4] [--priority batch] [--go]
  python salad_render.py start    --job NAME
  python salad_render.py submit   --job NAME --start 1 --end 3400 --step 2 [--chunk 20] [--samples 48] [--go]
  python salad_render.py status   --job NAME
  python salad_render.py download --job NAME --out DIR
  python salad_render.py stop|delete --job NAME [--go for delete]

Env (read from the Windows User environment, never written to disk by this script):
  SALAD_API_KEY, SALAD_ORG (default blender-render-lab), SALAD_PROJECT (default default)
  R2_ENDPOINT (https://<account>.r2.cloudflarestorage.com), R2_BUCKET, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY

Bucket layout:  <job>/scene/scene.blend (+ assets)   <job>/frames/c0001/<prefix>_####.png ...
`deploy`, `submit` and `delete` only PRINT what they would send unless you pass --go. `start` begins billing.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

SALAD = "https://api.salad.com/api/public"
KELPIE = "https://kelpie.saladexamples.com"
IMAGE = "ghcr.io/ralphbondage/blender-salad-render:1"
STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".state")


def env(name, default=None):
    v = os.environ.get(name, default)
    if not v:
        sys.exit(f"missing env var {name}")
    return v


def http(method, url, body=None, headers=None):
    req = urllib.request.Request(
        url, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={"accept": "application/json", "content-type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


def salad(method, path, body=None):
    return http(method, SALAD + path, body, {"Salad-Api-Key": env("SALAD_API_KEY")})


def kelpie(method, path, body=None):
    return http(method, KELPIE + path, body, {
        "Salad-Api-Key": env("SALAD_API_KEY"),
        "Salad-Organization": env("SALAD_ORG", "blender-render-lab"),
        "Salad-Project": env("SALAD_PROJECT", "default")})


def grp_base():
    return f"/organizations/{env('SALAD_ORG', 'blender-render-lab')}/projects/{env('SALAD_PROJECT', 'default')}/containers"


def group_name(job):
    return f"render-{job}".lower()[:63]


def state_path(job):
    return os.path.join(STATE_DIR, job + ".json")


def load_state(job):
    try:
        with open(state_path(job)) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_state(job, st):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(state_path(job), "w") as f:
        json.dump(st, f, indent=2)


def s3():
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        sys.exit("pip install boto3   (needed for upload/download/status)")
    return boto3.client(
        "s3", endpoint_url=env("R2_ENDPOINT"), region_name="auto",
        aws_access_key_id=env("R2_ACCESS_KEY_ID"), aws_secret_access_key=env("R2_SECRET_ACCESS_KEY"),
        config=Config(retries={"max_attempts": 5, "mode": "standard"}))


def list_keys(client, prefix):
    tok = None
    while True:
        kw = {"Bucket": env("R2_BUCKET"), "Prefix": prefix}
        if tok:
            kw["ContinuationToken"] = tok
        r = client.list_objects_v2(**kw)
        for o in r.get("Contents", []):
            yield o
        if not r.get("IsTruncated"):
            return
        tok = r["NextContinuationToken"]


def chunk_jobs(a, group_id):
    """One Kelpie job per chunk of `chunk` rendered frames. Frames are named by their real frame number."""
    frames = list(range(a.start, a.end + 1, a.step))
    bucket = env("R2_BUCKET")
    jobs = []
    for i in range(0, len(frames), a.chunk):
        part = frames[i:i + a.chunk]
        cdir = f"{a.job}/frames/c{i // a.chunk + 1:04d}/"
        up = {"bucket": bucket, "prefix": cdir, "local_path": "outputs/", "direction": "upload"}
        jobs.append({
            "container_group_id": group_id,
            "command": "blender",
            "arguments": ["-b", "--factory-startup", "scene/scene.blend", "--python", "render_cycles.py", "--",
                          "outputs", str(part[0]), str(part[-1]), str(a.step), str(a.width), str(a.height),
                          str(a.samples), a.camera, a.prefix],
            "environment": {"CYCLES_DEVICE_TYPE": "CUDA", "DENOISER": "OIDN", "OIDN_GPU": "1"},
            "max_failures": 3,
            "sync": {
                "before": [
                    {"bucket": bucket, "prefix": f"{a.job}/scene/", "local_path": "scene/", "direction": "download"},
                    # already-rendered frames of this chunk, so an interrupted chunk resumes instead of restarting
                    {"bucket": bucket, "prefix": cdir, "local_path": "outputs/", "direction": "download"}],
                "during": [up],
                "after": [up]},
        })
    return jobs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["upload", "deploy", "start", "submit", "status", "download", "stop", "delete"])
    ap.add_argument("--job", required=True, help="short name; becomes the R2 prefix and the Salad group name")
    ap.add_argument("--blend")
    ap.add_argument("--assets")
    ap.add_argument("--gpu", default="4090,5090,5080,4080,3090", help="comma list of GPU class name substrings")
    ap.add_argument("--replicas", type=int, default=4)
    ap.add_argument("--priority", default="batch", choices=["batch", "low", "medium", "high"])
    ap.add_argument("--memory-gb", type=int, default=16)
    ap.add_argument("--start", type=int)
    ap.add_argument("--end", type=int)
    ap.add_argument("--step", type=int, default=1)
    ap.add_argument("--chunk", type=int, default=20, help="rendered frames per job")
    ap.add_argument("--samples", type=int, default=48)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--camera", default="auto")
    ap.add_argument("--prefix", default="frame")
    ap.add_argument("--out")
    ap.add_argument("--go", action="store_true")
    a = ap.parse_args()
    st = load_state(a.job)

    if a.cmd == "upload":
        if not a.blend:
            sys.exit("--blend required")
        c = s3()
        files = [(a.blend, f"{a.job}/scene/scene.blend")]
        if a.assets:
            for root, _, names in os.walk(a.assets):
                for n in names:
                    p = os.path.join(root, n)
                    files.append((p, f"{a.job}/scene/" + os.path.relpath(p, a.assets).replace(os.sep, "/")))
        for src, key in files:
            mb = os.path.getsize(src) / 1e6
            print(f"uploading {src} ({mb:.0f} MB) -> {key}", flush=True)
            c.upload_file(src, env("R2_BUCKET"), key)
        print("done")

    elif a.cmd == "deploy":
        classes = salad("GET", f"/organizations/{env('SALAD_ORG', 'blender-render-lab')}/gpu-classes")[1]["items"]
        wanted = [w.strip().lower() for w in a.gpu.split(",") if w.strip()]
        ids = [g["id"] for g in classes if any(w in g["name"].lower() for w in wanted)]
        names = [g["name"] for g in classes if any(w in g["name"].lower() for w in wanted)]
        if not ids:
            sys.exit("no GPU class matched; check --gpu")
        body = {
            "name": group_name(a.job),
            "display_name": f"Cycles render {a.job}",
            "replicas": a.replicas,
            "restart_policy": "always",
            "autostart_policy": False,
            "container": {
                "image": IMAGE,
                "resources": {"cpu": 4, "memory": a.memory_gb * 1024, "gpu_classes": ids,
                              "storage_amount": 30 * 1024 ** 3},
                "priority": a.priority,
                "environment_variables": {
                    "AWS_ACCESS_KEY_ID": env("R2_ACCESS_KEY_ID"),
                    "AWS_SECRET_ACCESS_KEY": env("R2_SECRET_ACCESS_KEY"),
                    "AWS_ENDPOINT_URL": env("R2_ENDPOINT"),
                    "AWS_REGION": "auto"},
            },
        }
        shown = json.loads(json.dumps(body))
        shown["container"]["environment_variables"]["AWS_SECRET_ACCESS_KEY"] = "<hidden>"
        print("GPU classes:", names)
        print(json.dumps(shown, indent=2))
        if not a.go:
            print("\n(dry run: nothing sent; add --go to create the group, which does not start it)")
            return
        code, data = salad("POST", grp_base(), body)
        print(code, json.dumps(data, indent=2) if isinstance(data, dict) else data)
        if code in (200, 201) and isinstance(data, dict):
            st.update(group_id=data["id"], group_name=body["name"])
            save_state(a.job, st)

    elif a.cmd in ("start", "stop"):
        print(*salad("POST", f"{grp_base()}/{group_name(a.job)}/{a.cmd}"))

    elif a.cmd == "submit":
        if a.start is None or a.end is None:
            sys.exit("--start and --end required")
        gid = st.get("group_id")
        if not gid:
            code, data = salad("GET", f"{grp_base()}/{group_name(a.job)}")
            gid = data.get("id") if isinstance(data, dict) else None
        if not gid:
            sys.exit("no container group found for this job; run deploy --go first")
        jobs = chunk_jobs(a, gid)
        n_frames = len(range(a.start, a.end + 1, a.step))
        print(f"{n_frames} frames -> {len(jobs)} jobs of up to {a.chunk} frames; queue = group {gid}")
        print(json.dumps(jobs[0], indent=2))
        if not a.go:
            print("\n(dry run: nothing sent; add --go to queue the jobs)")
            return
        for i in range(0, len(jobs), 100):
            code, data = kelpie("POST", "/jobs/batch", jobs[i:i + 100])
            print("batch", i // 100 + 1, code, "" if code == 202 else data)

    elif a.cmd == "status":
        gid = st.get("group_id")
        counts = {}
        if gid:
            for s in ("pending", "running", "completed", "failed", "canceled"):
                n, page = 0, 1
                while True:
                    code, data = kelpie("GET", f"/jobs?status={s}&container_group_id={gid}&page_size=100&page={page}")
                    if code != 200:
                        print("kelpie", s, code, data)
                        break
                    got = len(data.get("jobs", []))
                    n += got
                    if got < 100:
                        break
                    page += 1
                counts[s] = n
            print("jobs:", counts)
        code, data = salad("GET", f"{grp_base()}/{group_name(a.job)}")
        if isinstance(data, dict):
            cs = data.get("current_state", {})
            print("group:", cs.get("status"), "instances:", json.dumps(cs.get("instance_status_counts")))
        total = sum(1 for o in list_keys(s3(), f"{a.job}/frames/") if o["Key"].lower().endswith(".png"))
        print("frames in R2:", total)

    elif a.cmd == "download":
        if not a.out:
            sys.exit("--out required")
        os.makedirs(a.out, exist_ok=True)
        c = s3()
        got = skipped = 0
        for o in list_keys(c, f"{a.job}/frames/"):
            if not o["Key"].lower().endswith(".png"):
                continue
            dst = os.path.join(a.out, os.path.basename(o["Key"]))
            if os.path.exists(dst) and os.path.getsize(dst) == o["Size"]:
                skipped += 1
                continue
            c.download_file(env("R2_BUCKET"), o["Key"], dst)
            got += 1
        print(f"downloaded {got}, already present {skipped} -> {a.out}")

    elif a.cmd == "delete":
        if not a.go:
            print(f"dry run: would DELETE group {group_name(a.job)}; add --go")
            return
        print(*salad("DELETE", f"{grp_base()}/{group_name(a.job)}"))


if __name__ == "__main__":
    main()
