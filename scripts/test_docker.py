"""Isolated local container build, CRUD and recreation/persistence smoke test."""

import json
from pathlib import Path
import subprocess
import tempfile
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def docker(*args):
    return subprocess.check_output(["docker", *args], cwd=ROOT, text=True).strip()


def request(base, path, method="GET", data=None):
    body = None if data is None else json.dumps(data).encode()
    req = Request(base + path, method=method, data=body,
                  headers={"Content-Type": "application/json"} if body else {})
    with urlopen(req, timeout=12) as response:
        return json.load(response)


def start(name, image, volume):
    docker("run", "-d", "--name", name, "--read-only", "--cap-drop=ALL",
           "--security-opt=no-new-privileges", "--tmpfs", "/tmp:size=16m",
           "-p", "127.0.0.1::8080", "-v", f"{volume}:/data", image)
    port = json.loads(docker("inspect", name))[0]["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostPort"]
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            request(base, "/api/health")
            return base
        except (URLError, OSError):
            time.sleep(0.5)
    raise RuntimeError(f"Container did not become ready: {docker('logs', name)}")


def main():
    name = "foodstash-test-" + uuid.uuid4().hex[:12]
    image = name + ":local"
    volume = name + "-data"
    try:
        docker("build", "-t", image, ".")
        docker("volume", "create", volume)
        base = start(name, image, volume)
        item = {"name": "Manual test beans", "barcode": "", "quantity": 2,
                "expires_on": "2028-03-01", "location": "Test shelf", "notes": "Persistence test"}
        first = request(base, "/api/items", "POST", item)["item"]
        second = request(base, "/api/items", "POST", {**item, "expires_on": "2028-04-01"})["item"]
        assert first["id"] != second["id"], "Batches must stay separate"
        request(base, f"/api/items/{first['id']}", "PATCH", {**item, "name": "Edited beans"})
        request(base, f"/api/items/{first['id']}/consume", "POST", {})
        before = request(base, "/api/items")["items"]
        assert before[0]["quantity"] == 1
        docker("rm", "-f", name)
        base = start(name, image, volume)
        assert before == request(base, "/api/items")["items"], "Inventory must survive recreation"
        with tempfile.TemporaryDirectory(prefix=name) as temp:
            backup = Path(temp) / "backup.db"
            docker("exec", name, "python", "-c",
                   "import sqlite3; s=sqlite3.connect('/data/foodstash.db'); "
                   "t=sqlite3.connect('/data/backup.db'); s.backup(t); "
                   "t.execute('PRAGMA journal_mode=DELETE'); t.close(); s.close()")
            docker("cp", f"{name}:/data/backup.db", str(backup))
            docker("rm", "-f", name)
            docker("run", "--rm", "--cap-drop=ALL", "--entrypoint", "python",
                   "-v", f"{volume}:/data", "-v", f"{backup}:/backup.db:ro", image, "-c",
                   "import sqlite3; from pathlib import Path; "
                   "s=sqlite3.connect('file:/backup.db?mode=ro',uri=True); "
                   "assert s.execute('PRAGMA integrity_check').fetchone()==('ok',); "
                   "[p.unlink(missing_ok=True) for p in "
                   "(Path('/data/foodstash.db'),Path('/data/foodstash.db-wal'),Path('/data/foodstash.db-shm'))]; "
                   "t=sqlite3.connect('/data/foodstash.db'); s.backup(t); t.close(); s.close()")
            base = start(name, image, volume)
            assert before == request(base, "/api/items")["items"], "Backup restore must preserve inventory"
        request(base, f"/api/items/{second['id']}", "DELETE", {})
        assert len(request(base, "/api/items")["items"]) == 1
        try:
            request(base, "/api/products/invalid")
            raise AssertionError("Invalid barcode must be rejected")
        except HTTPError as exc:
            assert exc.code == 400
        assert request(base, "/api/health")["version"] == (ROOT / "VERSION").read_text().strip()
        print("PASS: non-root read-only container startup, manual CRUD, separate batches, consume, persistence across recreation, backup/restore, invalid barcode and version.")
    finally:
        # These unique names belong only to this invocation.
        for args in [("rm", "-f", name), ("volume", "rm", volume), ("image", "rm", image)]:
            subprocess.run(["docker", *args], cwd=ROOT, check=False, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
