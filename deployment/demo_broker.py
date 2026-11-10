"""Manage credentials for the disposable Mosquitto demo broker only."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
from urllib.request import Request, urlopen

from paho.mqtt.client import Client
from paho.mqtt.enums import CallbackAPIVersion

ROOT = Path(__file__).resolve().parent
PASSWD = ROOT / "mosquitto_passwd.demo"
CONF = ROOT / "mosquitto.demo.conf"
COMPOSE = ROOT / "compose.demo.yml"
IMAGE = "eclipse-mosquitto:2.0.22"
CONTAINER = "smart-parking-demo-broker"


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True)


def backup():
    if PASSWD.exists():
        target = PASSWD.with_name(PASSWD.name + "." + time.strftime("%Y%m%d%H%M%S") + ".bak")
        shutil.copy2(PASSWD, target)
        return target


def check_denied(device, password):
    import threading
    event = threading.Event()
    result = []
    client = Client(CallbackAPIVersion.VERSION2, client_id="revoke_check_" + secrets.token_hex(4))
    client.username_pw_set(device, password)
    client.on_connect = lambda _c, _u, _f, reason, _p: (result.append(reason.value), event.set())
    client.connect("127.0.0.1", 1883, 5)
    client.loop_start()
    try:
        if not event.wait(8) or result[0] == 0:
            raise RuntimeError("Old credential was not rejected by broker")
    finally:
        client.loop_stop()
        client.disconnect()


def setup(device, reset=False):
    backend = os.environ["DEMO_BACKEND_MQTT_PASSWORD"]
    password = os.environ["SIM_MQTT_PASSWORD"]
    if PASSWD.exists() and not reset:
        raise RuntimeError("Demo password file exists; use setup --reset after review")
    backup()
    run("docker", "run", "--rm", "-v", f"{ROOT}:/work", IMAGE,
        "mosquitto_passwd", "-b", "-c", "/work/mosquitto_passwd.demo", "backend_admin", backend)
    run("docker", "run", "--rm", "-v", f"{ROOT}:/work", IMAGE,
        "mosquitto_passwd", "-b", "/work/mosquitto_passwd.demo", device, password)
    CONF.write_text("listener 1883 0.0.0.0\nallow_anonymous false\n"
                    "password_file /mosquitto/config/passwd\n"
                    "acl_file /mosquitto/config/acl.conf\n"
                    "persistence false\nlog_dest stdout\n", encoding="utf-8")
    run("docker", "compose", "-f", str(COMPOSE), "up", "-d")
    return True


def revoke(device):
    token = os.environ["DEMO_API_TOKEN"]
    password = os.environ["SIM_MQTT_PASSWORD"]
    url = os.environ.get("DEMO_API_BASE", "http://127.0.0.1:8000/api/v1")
    request = Request(url + "/devices/" + device, headers={"Authorization": "Bearer " + token})
    with urlopen(request, timeout=8) as response:
        state = json.load(response)["status"]
    if state not in ("SUSPENDED", "DECOMMISSIONED"):
        raise RuntimeError("Suspend or decommission device through API first")
    if not PASSWD.exists():
        raise RuntimeError("Demo password file missing")
    saved = backup()
    try:
        run("docker", "exec", CONTAINER, "mosquitto_passwd", "-D",
            "/mosquitto/config/passwd", device)
        run("docker", "restart", CONTAINER)
        time.sleep(2)
        check_denied(device, password)
    except Exception:
        raise RuntimeError(f"Revocation verification failed; backup retained at {saved}")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("setup", "revoke"))
    parser.add_argument("device", nargs="?", default=os.environ.get("SIM_DEVICE_ID", "ESP32_GATE_01"))
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    if args.action == "setup":
        setup(args.device, reset=args.reset)
    else:
        revoke(args.device)
