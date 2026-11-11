"""Local HTTP controls for an MQTT parking-device simulator.

Publishes real MQTT events to the configured broker. Set SIM_DEVICE_TOKEN and
SIM_MQTT_PASSWORD in the environment before starting this script.
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion


SITE = os.getenv("SIM_SITE_ID", "PTIT_ZONE_1")
DEVICE = os.getenv("SIM_DEVICE_ID", "ESP32_GATE_01")
HOST = os.getenv("SIM_MQTT_HOST", "127.0.0.1")
PORT = int(os.getenv("SIM_MQTT_PORT", "1883"))
HTTP_PORT = int(os.getenv("SIM_HTTP_PORT", "8008"))
TOKEN = os.getenv("SIM_DEVICE_TOKEN", "")
PASSWORD = os.getenv("SIM_MQTT_PASSWORD", "")
if not TOKEN or not PASSWORD:
    raise SystemExit("Set SIM_DEVICE_TOKEN and SIM_MQTT_PASSWORD before running.")

TOPIC = f"parking/{SITE}/{DEVICE}"
BOOT_ID = "http_sim_" + uuid4().hex[:8]
client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id="http_sim_" + uuid4().hex[:8])
client.username_pw_set(DEVICE, PASSWORD)
client.will_set(TOPIC + "/status", json.dumps({"token": TOKEN, "status": "offline"}), qos=1)
lock = threading.Lock()
stop = threading.Event()
state = {
    "device_id": DEVICE,
    "is_online": False,
    "gate_in_open": False,
    "gate_out_open": False,
    "slot_status": {"SLOT_A1": "UNKNOWN"},
    "commands_received": 0,
    "acks_sent": 0,
    "last_feedback": None,
}
gates = {"GATE_IN": {"held": False, "close_at": None},
         "GATE_OUT": {"held": False, "close_at": None}}
ack_cache = {}
seq = 0


def publish(path, payload):
    info = client.publish(TOPIC + path, json.dumps({"token": TOKEN, **payload}), qos=1)
    if info.rc != mqtt.MQTT_ERR_SUCCESS:
        raise RuntimeError("MQTT publish failed")


def telemetry(slot_id, occupied):
    for index in range(2):
        telemetry_sample(slot_id, occupied)
        if index == 0:
            time.sleep(0.1)
    with lock:
        state["slot_status"][slot_id] = "OCCUPIED" if occupied else "EMPTY"


def telemetry_sample(slot_id, occupied):
    global seq
    distance = 8.5 if occupied else 50.0
    with lock:
        seq += 1
        number = seq
    publish("/telemetry", {"slotId": slot_id, "distanceCm": distance,
                           "bootId": BOOT_ID, "seq": number})


def on_connect(_client, _userdata, _flags, reason, _properties):
    online = reason.value == 0
    with lock:
        state["is_online"] = online
    if not online:
        print("MQTT rejected:", reason)
        return
    client.subscribe(TOPIC + "/commands", qos=1)
    client.subscribe(TOPIC + "/feedback", qos=1)
    publish("/status", {"status": "online", "fwVersion": "demo-http-simulator"})
    telemetry("SLOT_A1", False)
    print("MQTT connected; simulator ready on http://127.0.0.1:" + str(HTTP_PORT))


def on_disconnect(_client, _userdata, _flags, _reason, _properties):
    with lock:
        state["is_online"] = False


def on_message(_client, _userdata, message):
    try:
        payload = json.loads(message.payload)
        if message.topic.endswith("/feedback"):
            code = payload.get("code")
            msg = payload.get("message")
            gate = payload.get("gate")
            print("LCD", gate, code, msg, flush=True)
            with lock:
                state["last_feedback"] = {
                    "code": code,
                    "message": msg,
                    "gate": gate,
                    "time": time.time(),
                }
            return
        command_id = payload["commandId"]
        gate = payload["gate"]
        action = payload["action"]
        with lock:
            state["commands_received"] += 1
            cached = ack_cache.get(command_id)
            if cached is not None:
                ack = cached
            else:
                if gate not in gates or action not in {"OPEN", "CLOSE", "HOLD_OPEN", "RESUME_AUTO"}:
                    status, reason = "FAILED", "Unsupported gate or action"
                else:
                    status, reason = "SUCCESS", "DEMO command accepted"
                    key = "gate_in_open" if gate == "GATE_IN" else "gate_out_open"
                    target = gates[gate]
                    if action == "OPEN":
                        state[key] = True
                        target["close_at"] = None if target["held"] else time.monotonic() + max(1, int(payload.get("autoCloseMs", 30000))) / 1000
                    elif action == "CLOSE":
                        state[key] = False
                        target.update(held=False, close_at=None)
                    elif action == "HOLD_OPEN":
                        state[key] = True
                        target.update(held=True, close_at=None)
                    else:
                        target["held"] = False
                        target["close_at"] = time.monotonic() + 30 if state[key] else None
                ack = {"commandId": command_id, "gate": gate, "status": status, "reason": reason}
                if len(ack_cache) >= 16:
                    ack_cache.pop(next(iter(ack_cache)))
                ack_cache[command_id] = ack
        publish("/ack", ack)
        with lock:
            state["acks_sent"] += 1
        print("ACK", command_id, ack["status"])
    except (KeyError, ValueError, TypeError, RuntimeError) as exc:
        print("Invalid MQTT message:", exc)


def heartbeat():
    last_status = time.monotonic()
    last_telemetry = time.monotonic()
    while not stop.wait(0.2):
        now = time.monotonic()
        with lock:
            for gate, target in gates.items():
                if target["close_at"] is not None and now >= target["close_at"]:
                    state["gate_in_open" if gate == "GATE_IN" else "gate_out_open"] = False
                    target["close_at"] = None
            online = state["is_online"]
            occupied = state["slot_status"]["SLOT_A1"] == "OCCUPIED"
        if online and now - last_status >= 10:
            publish("/status", {"status": "online", "fwVersion": "demo-http-simulator"})
            last_status = now
        if online and now - last_telemetry >= 5:
            telemetry_sample("SLOT_A1", occupied)
            last_telemetry = now


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, payload=None):
        data = b"" if status == 204 else json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.respond(204)

    def do_GET(self):
        if self.path != "/status":
            return self.respond(404, {"message": "Unknown path"})
        with lock:
            self.respond(200, {**state, "slot_status": dict(state["slot_status"])})

    def do_POST(self):
        if self.path not in {"/rfid/entry", "/rfid/exit", "/slot/park", "/slot/leave"}:
            return self.respond(404, {"message": "Unknown path"})
        with lock:
            if not state["is_online"]:
                return self.respond(503, {"message": "MQTT chưa kết nối"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > 1024:
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(size))
            if self.path.startswith("/rfid/"):
                card = body.get("cardUid")
                if not isinstance(card, str) or not card.strip():
                    raise ValueError("cardUid is required")
                gate = "GATE_IN" if self.path.endswith("entry") else "GATE_OUT"
                publish("/events/rfid", {"cardUid": card.strip(), "gate": gate})
                message = f"Đã quẹt thẻ {card} tại {gate}"
            else:
                slot = body.get("slotId")
                if slot != "SLOT_A1":
                    raise ValueError("Simulator này chỉ mô phỏng SLOT_A1")
                occupied = self.path.endswith("park")
                telemetry(slot, occupied)
                message = "Xe đã vào ô A1" if occupied else "Ô A1 đã trống"
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self.respond(400, {"message": str(exc)})
        except RuntimeError as exc:
            return self.respond(503, {"message": str(exc)})
        self.respond(200, {"message": message})


client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message


def main():
    client.connect(HOST, PORT, keepalive=30)
    client.loop_start()
    threading.Thread(target=heartbeat, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        stop.set()
        with lock:
            online = state["is_online"]
        if online:
            publish("/status", {"status": "offline"})
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
