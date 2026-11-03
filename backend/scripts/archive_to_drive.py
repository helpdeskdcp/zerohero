"""Archive big local research files to Google Drive (gdrive200:ZeroHero-archive/historical/), VERIFIED by md5, then (only with --delete-local) remove the local copy. Streams gzip -> rclone rcat: no extra local disk.
Never deletes anything that is not verified byte-for-byte on the remote (the md5 of the gzip stream we sent == the md5 Drive reports). python scripts/archive_to_drive.py FILE [FILE ...] [--delete-local]"""
import hashlib, os, subprocess, sys, json, time
REMOTE = os.environ.get("ARCHIVE_REMOTE", "gdrive200:ZeroHero-archive/historical")
files = [a for a in sys.argv[1:] if not a.startswith("--")]; delete = "--delete-local" in sys.argv; res = []
for f in files:
    f = os.path.abspath(f); name = f.split("/data/", 1)[-1].replace("/", "__") + ".gz"; dest = f"{REMOTE}/{name}"; size = os.path.getsize(f); t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] {f} ({size / 1e9:.2f} GB) -> {dest}", flush=True)
    gz = subprocess.Popen(["nice", "-n", "15", "gzip", "-3", "-c", f], stdout=subprocess.PIPE); rc = subprocess.Popen(["rclone", "rcat", dest, "--drive-chunk-size", "64M"], stdin=subprocess.PIPE)
    md5 = hashlib.md5(); sent = 0
    while True:
        b = gz.stdout.read(1 << 20)
        if not b: break
        md5.update(b); rc.stdin.write(b); sent += len(b)
    rc.stdin.close(); ok = rc.wait() == 0 and gz.wait() == 0
    rem = subprocess.run(["rclone", "md5sum", dest], capture_output=True, text=True).stdout.split()
    verified = ok and bool(rem) and rem[0].lower() == md5.hexdigest().lower()
    rsize = subprocess.run(["rclone", "size", "--json", dest], capture_output=True, text=True).stdout
    row = {"file": f, "bytes": size, "gz_bytes_sent": sent, "remote_md5": rem[0] if rem else None, "local_stream_md5": md5.hexdigest(), "verified": verified, "seconds": round(time.time() - t0)}
    print(json.dumps(row), flush=True); res.append(row)
    if verified and delete:
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(f + ext): os.remove(f + ext)
        print("  local copy removed", flush=True)
    elif not verified: print("  NOT VERIFIED: local file kept", flush=True)
print("DONE", json.dumps([(r["file"].split("/")[-1], r["verified"]) for r in res]))
