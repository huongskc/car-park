"""Interactive ESP32 MQTT simulator. Never use a production device identity."""
import json
import os
import sys
import threading
import time
from uuid import uuid4

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

HOST = os.getenv("SIM_MQTT_HOST", "127.0.0.1")
PORT = int(os.getenv("SIM_MQTT_PORT", "1883"))
SITE = os.getenv("SIM_SITE_ID", "PTIT_ZONE_1")
DEVICE = os.getenv("SIM_DEVICE_ID", "ESP32_GATE_01")
CARD = os.getenv("SIM_CARD_UID", "A1B2C3D4")
TOKEN = os.getenv("SIM_DEVICE_TOKEN", "")
PASSWORD = os.getenv("SIM_MQTT_PASSWORD", "")
if not TOKEN or not PASSWORD:
    raise SystemExit("Set SIM_DEVICE_TOKEN and SIM_MQTT_PASSWORD before running.")

topic = f"parking/{SITE}/{DEVICE}"
client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=f"demo_{uuid4().hex[:8]}")
client.username_pw_set(DEVICE, PASSWORD)
client.will_set(topic + "/status", json.dumps({"status": "offline", "token": TOKEN}), qos=1)
fail_next = False
stop = threading.Event()
held_gates = set()


def publish(path, data):
    client.publish(topic + path, json.dumps({"token": TOKEN, **data}), qos=1)


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code.value != 0:
        print("MQTT connection rejected:", reason_code)
        return
    client.subscribe(topic + "/commands", qos=1)
    client.subscribe(topic + "/feedback", qos=1)
    publish("/status", {"status": "online", "fwVersion": "demo-simulator"})
    print("Connected. Type: entry, occupied, exit, empty, fail, quit")


def on_message(client, userdata, message):
    global fail_next
    try:
        if message.topic.endswith("/feedback"):
            feedback = json.loads(message.payload)
            print("LCD", feedback.get("gate"), feedback.get("code"), feedback.get("message"))
            return
        command = json.loads(message.payload)
        failed = fail_next
        fail_next = False
        gate = command["gate"]
        action = command["action"]
        if not failed:
            if action == "HOLD_OPEN":
                held_gates.add(gate)
            elif action == "RESUME_AUTO":
                held_gates.discard(gate)
        publish("/ack", {"commandId": command["commandId"], "gate": command["gate"],
                         "status": "FAILED" if failed else "SUCCESS",
                         "reason": "DEMO forced failure" if failed else f"DEMO {action} accepted"})
        if not failed and action == "OPEN" and gate not in held_gates:
            interval = int(command.get("autoCloseMs", 30000)) / 1000
            timer = threading.Timer(interval, lambda: print("AUTO_CLOSED", gate, command["commandId"]))
            timer.daemon = True
            timer.start()
        print("ACK", command["commandId"], "FAILED" if failed else "SUCCESS")
    except (KeyError, ValueError) as exc:
        print("Invalid command:", exc)


def heartbeat():
    while not stop.wait(10):
        publish("/status", {"status": "online", "fwVersion": "demo-simulator"})


client.on_connect = on_connect
client.on_message = on_message
client.connect(HOST, PORT, keepalive=30)
client.loop_start()
threading.Thread(target=heartbeat, daemon=True).start()
try:
    while True:
        action = input("demo> ").strip().lower()
        if action == "entry":
            publish("/events/rfid", {"cardUid": CARD, "gate": "GATE_IN"})
        elif action == "exit":
            publish("/events/rfid", {"cardUid": CARD, "gate": "GATE_OUT"})
        elif action in ("occupied", "empty"):
            slot = input("slot ID> ").strip()
            publish("/telemetry", {"slotId": slot, "distanceCm": 8 if action == "occupied" else 50,
                                    "bootId": "demo", "seq": int(time.time())})
        elif action == "fail":
            fail_next = True
            print("The next command will receive FAILED ACK.")
        elif action == "quit":
            break
        else:
            print("Use entry, occupied, exit, empty, fail or quit.")
finally:
    stop.set()
    publish("/status", {"status": "offline"})
    client.loop_stop()
    client.disconnect()
