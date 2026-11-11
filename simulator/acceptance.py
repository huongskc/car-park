"""Disposable end-to-end MQTT acceptance: broker, backend, ALPR mock and frontend."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
import pymysql
from pymysql.constants import CLIENT
from websockets.sync.client import connect as ws_connect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
from app.config import settings
DB_NAME = "smart_parking_demo"
DEVICE = "ESP32_GATE_01"
SITE = "PTIT_ZONE_1"
TOKEN = "token_esp32_secret_ptit_2026"  # Only in disposable seed DB.
BASE = "http://127.0.0.1:8000/api/v1"
AI = "http://127.0.0.1:8001"
procs = []
broker_started = False


def request(url, method="GET", body=None, token=None, key=None):
    headers = {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    if key:
        headers["X-API-Key"] = key
    req = Request(url, method=method, headers=headers,
                  data=json.dumps(body).encode() if body is not None else None)
    with urlopen(req, timeout=8) as response:
        data = response.read()
        return json.loads(data) if response.headers.get("content-type", "").startswith("application/json") else data


def wait_for(check, label, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            value = check()
            if value:
                return value
        except (HTTPError, OSError, AssertionError):
            pass
        time.sleep(0.15)
    raise AssertionError(f"Timed out waiting for {label}")


def db_connect(database=None):
    return pymysql.connect(host=settings.DB_HOST,
                           port=settings.DB_PORT,
                           user=settings.DB_USER,
                           password=settings.DB_PASSWORD,
                           database=database, charset="utf8mb4",
                           client_flag=CLIENT.MULTI_STATEMENTS, autocommit=True)


def query(sql, args=()):
    with db_connect(DB_NAME) as connection, connection.cursor() as cursor:
        cursor.execute(sql, args)
        return cursor.fetchall()


def reset_disposable_db():
    if DB_NAME != "smart_parking_demo":
        raise RuntimeError("Database guard rejected the target")
    with db_connect() as connection, connection.cursor() as cursor:
        for name in ("schema.sql", "seed.sql"):
            sql = (ROOT / "database" / name).read_text(encoding="utf-8")
            cursor.execute(sql.replace("`smart_parking_db`", f"`{DB_NAME}`"))
            while cursor.nextset():
                pass


def start(command, env, log_name):
    log = open(ROOT / ".local-backups" / log_name, "w", encoding="utf-8")
    proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log,
                            stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    procs.append((proc, log))


def browser_check(token, session_code, slot, lane):
    chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    if not chrome.is_file():
        raise RuntimeError("Chrome headless is required for dashboard acceptance on this host")
    with tempfile.TemporaryDirectory(prefix="smart_parking_browser_") as profile:
        browser = subprocess.Popen([str(chrome), "--headless=new", "--disable-gpu",
                                    "--window-size=1366,768", "--force-device-scale-factor=1",
                                    "--no-first-run", "--remote-debugging-port=19223",
                                    "--remote-allow-origins=*", f"--user-data-dir={profile}",
                                    "http://127.0.0.1:5500/#/gate"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            def page_socket():
                pages = request("http://127.0.0.1:19223/json")
                return next((p["webSocketDebuggerUrl"] for p in pages if p.get("type") == "page"), None)
            socket = wait_for(page_socket, "Chrome DevTools")
            with ws_connect(socket, open_timeout=5) as ws:
                call_id = 0

                def evaluate(expression):
                    nonlocal call_id
                    call_id += 1
                    ws.send(json.dumps({"id": call_id, "method": "Runtime.evaluate",
                                        "params": {"expression": expression, "awaitPromise": True,
                                                   "returnByValue": True}}))
                    while True:
                        response = json.loads(ws.recv(timeout=8))
                        if response.get("id") == call_id:
                            if "exceptionDetails" in response.get("result", {}):
                                raise AssertionError(response["result"]["exceptionDetails"])
                            return response.get("result", {}).get("result", {}).get("value")

                wait_for(lambda: evaluate("document.readyState === 'complete'"), "dashboard page")
                login = {"access_token": token, "role": "OPERATOR", "username": "operator",
                         "full_name": "Nhân viên Trực bãi", "user_id": 2}
                evaluate("localStorage.setItem('sess', " + json.dumps(json.dumps(login)) + "); location.hash='#/gate'; location.reload()")
                try:
                    wait_for(lambda: evaluate("document.querySelector('#live')?.innerText.includes('" + session_code + "')"),
                             "real dashboard session")
                except AssertionError:
                    print("Dashboard DOM:", ascii(evaluate("document.body.innerText.slice(0, 800)")))
                    raise
                text = evaluate("document.querySelector('#live')?.innerText")
                assert slot in text
                station = "#gate-station-" + lane
                camera = "#gate-camera-" + lane
                wait_for(lambda: evaluate("document.querySelector('" + station + " .station-photo')?.src.startsWith('blob:')"),
                         "protected image beside action")
                assert evaluate("document.querySelectorAll('" + station + " .station-photo').length") == 1
                assert evaluate("document.querySelector('" + camera + " [data-role=session]')?.innerText.includes(" + json.dumps(session_code) + ")")
                assert evaluate("document.querySelector('" + station + " .station-form button')?.getBoundingClientRect().bottom <= 768")
                call_id += 1
                ws.send(json.dumps({"id": call_id, "method": "Emulation.setDeviceMetricsOverride",
                                    "params": {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True}}))
                while json.loads(ws.recv(timeout=8)).get("id") != call_id:
                    pass
                assert evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Narrow dashboard overflows horizontally"
                assert evaluate("getComputedStyle(document.querySelector('.gate-stations')).gridTemplateColumns.split(' ').length === 1")
        finally:
            browser.terminate()
            try:
                browser.wait(timeout=5)
            except subprocess.TimeoutExpired:
                browser.kill()


class Sim:
    def __init__(self, password):
        self.connected = threading.Event()
        self.commands = []
        self.fail_next = False
        self.seq = 0
        self.feedback = []
        self.client = mqtt.Client(CallbackAPIVersion.VERSION2,
                                  client_id="acceptance_" + secrets.token_hex(4))
        self.client.username_pw_set(DEVICE, password)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.connect("127.0.0.1", 1884, 30)
        self.client.loop_start()
        assert self.connected.wait(5), "Device simulator could not connect"
        self.publish("status", {"status": "online", "fwVersion": "demo-simulator"})

    def on_connect(self, client, _userdata, _flags, reason, _properties):
        if reason.value == 0:
            client.subscribe(f"parking/{SITE}/{DEVICE}/commands", qos=1)
            client.subscribe(f"parking/{SITE}/{DEVICE}/feedback", qos=1)
            self.connected.set()

    def on_message(self, _client, _userdata, message):
        if message.topic.endswith("/feedback"):
            self.feedback.append(json.loads(message.payload))
            return
        command = json.loads(message.payload)
        self.commands.append(command)
        failed = self.fail_next
        self.fail_next = False
        self.publish("ack", {"commandId": command["commandId"], "gate": command["gate"],
                             "status": "FAILED" if failed else "SUCCESS",
                             "reason": "DEMO forced failure" if failed else "DEMO barrier opened"})

    def publish(self, path, payload):
        info = self.client.publish(f"parking/{SITE}/{DEVICE}/{path}",
                                   json.dumps({"token": TOKEN, **payload}), qos=1)
        assert info.rc == 0

    def rfid(self, card, gate):
        self.publish("events/rfid", {"cardUid": card, "gate": gate})

    def telemetry(self, slot, occupied):
        for _ in range(2):
            self.seq += 1
            self.publish("telemetry", {"slotId": slot, "distanceCm": 8 if occupied else 50,
                                       "bootId": "demo", "seq": self.seq})

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()


def main():
    global broker_started
    password = secrets.token_urlsafe(20)
    backend_password = secrets.token_urlsafe(20)
    key = secrets.token_urlsafe(20)
    env = os.environ.copy()
    env.update(DB_NAME=DB_NAME, DB_HOST=settings.DB_HOST,
               DB_PORT=str(settings.DB_PORT), DB_USER=settings.DB_USER,
               DB_PASSWORD=settings.DB_PASSWORD,
               MQTT_BROKER_PORT="1884",
               MQTT_PASSWORD=backend_password, DEMO_BACKEND_MQTT_PASSWORD=backend_password,
               SIM_MQTT_PASSWORD=password, ALPR_API_KEY=key, INTERNAL_ALPR_API_KEY=key,
               DEBUG="false", ENABLE_MOCK_ALPR="true", ENTRY_GRACE_PERIOD_SEC="1")
    reset_disposable_db()
    from deployment import demo_broker
    os.environ.update(DEMO_BACKEND_MQTT_PASSWORD=backend_password, SIM_MQTT_PASSWORD=password)
    demo_broker.setup(DEVICE, reset=True)
    broker_started = True
    (ROOT / ".local-backups").mkdir(exist_ok=True)
    start([str(ROOT / "ai-service" / ".venv" / "Scripts" / "python.exe"),
           "-m", "uvicorn", "simulator.mock_alpr:app", "--port", "8001"], env, "demo-alpr.log")
    start([str(ROOT / "backend" / ".venv" / "Scripts" / "python.exe"),
           "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--port", "8000"],
          env, "demo-backend.log")
    start([sys.executable, "-m", "http.server", "5500", "--directory", "frontend"],
          env, "demo-frontend.log")
    wait_for(lambda: request(AI + "/health"), "ALPR")
    wait_for(lambda: request("http://127.0.0.1:8000/health")["mqtt_connected"], "backend MQTT")
    assert "Quản lý Bãi" in request("http://127.0.0.1:5500").decode("utf-8")
    operator = request(BASE + "/auth/login", "POST", {"username": "operator", "password": "op123"})["access_token"]
    admin = request(BASE + "/auth/login", "POST", {"username": "admin", "password": "admin123"})["access_token"]
    viewer = request(BASE + "/auth/login", "POST", {"username": "viewer", "password": "view123"})["access_token"]
    sim = Sim(password)
    try:
        wait_for(lambda: request(BASE + f"/devices/{DEVICE}", token=admin)["is_online"], "device heartbeat")
        sim.telemetry("SLOT_A1", False)
        wait_for(lambda: query("SELECT status FROM slots WHERE id='SLOT_A1'")[0][0] == "EMPTY",
                 "initial empty sensor confirmation")
        sim.rfid("BAD_DEMO_CARD", "GATE_IN")
        wait_for(lambda: any(f.get("code") == "INVALID_CARD" for f in sim.feedback),
                 "LCD invalid-card feedback")
        for action in ("HOLD_OPEN", "RESUME_AUTO"):
            command = request(BASE + f"/devices/{DEVICE}/commands", "POST",
                              {"gate_id": "GATE_IN", "action": action,
                               "reason": "Kiểm tra hợp đồng demo"}, operator)
            wait_for(lambda: query("SELECT status FROM commands WHERE id=%s",
                                   (command["id"],))[0][0] == "ACKED",
                     action + " ACK")
            assert any(c["commandId"] == command["id"] and c["action"] == action
                       for c in sim.commands)
        print("PASS LCD feedback + HOLD_OPEN/RESUME_AUTO ACK")
        sim.rfid("A1B2C3D4", "GATE_IN")
        session = wait_for(lambda: next((s for s in request(BASE + "/sessions", token=operator)
                                         if s["card_uid"] == "A1B2C3D4"), None), "visitor entry")
        sid, slot = session["id"], session["slot_id"]
        wait_for(lambda: request(BASE + f"/sessions/{sid}", token=operator)["entry_ocr_plate"] == "30A12345", "entry OCR")
        browser_check(operator, session["session_code"], slot, "IN")
        request(BASE + f"/sessions/{sid}/entry-plate", "PATCH",
                {"plate": "30A12345", "note": "Đã đối chiếu ảnh"}, operator)
        wait_for(lambda: request(BASE + f"/sessions/{sid}", token=operator)["status"] == "ENTRY_ACKED_WAITING_PASS", "entry ACK")
        assert request(BASE + f"/sessions/{sid}", token=operator)["entry_time"] is None
        request(BASE + f"/sessions/{sid}/confirm-entry", "POST", {"confirmed_passage": True}, operator)
        sim.telemetry(slot, True)
        wait_for(lambda: request(BASE + "/slots", token=operator)[0]["status"] == "OCCUPIED", "occupied slot")
        sim.rfid("A1B2C3D4", "GATE_OUT")
        wait_for(lambda: request(BASE + f"/sessions/{sid}", token=operator)["status"] == "PENDING_REVIEW", "exit OCR review")
        browser_check(operator, session["session_code"], slot, "OUT")
        print("PASS real dashboard: both RFID lanes auto-bind, show one protected image and visible actions")
        request(BASE + f"/sessions/{sid}/resolve-plate", "PATCH",
                {"approved": True, "actual_exit_plate": "30A12345", "note": "Đã đối chiếu ảnh"}, operator)
        wait_for(lambda: request(BASE + f"/sessions/{sid}", token=operator)["status"] == "EXIT_READY", "exit plate")
        request(BASE + f"/sessions/{sid}/checkout", "POST", {"payment_method": "CASH"}, operator)
        wait_for(lambda: request(BASE + f"/sessions/{sid}", token=operator)["status"] == "EXIT_ACKED_WAITING_PASS", "exit ACK")
        assert request(BASE + f"/sessions/{sid}", token=operator)["exit_time"] is None
        request(BASE + f"/sessions/{sid}/confirm-exit", "POST", {"confirmed_passage": True}, operator)
        assert query("SELECT status FROM slots WHERE id=%s", (slot,))[0][0] == "OCCUPIED"
        assert query("SELECT COUNT(*) FROM payments WHERE session_id=%s", (sid,))[0][0] == 1
        sim.telemetry(slot, False)
        wait_for(lambda: query("SELECT status FROM slots WHERE id=%s", (slot,))[0][0] == "EMPTY", "slot release")
        print("PASS visitor: RFID -> slot -> ACK -> occupied -> RFID -> cash -> ACK -> empty")

        request(AI + "/scenario", "POST", {"entry_plate": "30A99988", "exit_plate": "29B00001"}, key=key)
        sim.rfid("E5F6G7H8", "GATE_IN")
        monthly = wait_for(lambda: next((s for s in request(BASE + "/sessions", token=operator)
                                         if s["card_uid"] == "E5F6G7H8"), None), "monthly entry")
        mid = monthly["id"]
        wait_for(lambda: request(BASE + f"/sessions/{mid}", token=operator)["entry_ocr_plate"] == "30A99988", "monthly OCR")
        request(BASE + f"/sessions/{mid}/entry-plate", "PATCH",
                {"plate": "30A99988", "note": "Đã đối chiếu ảnh"}, operator)
        wait_for(lambda: request(BASE + f"/sessions/{mid}", token=operator)["status"] == "ENTRY_ACKED_WAITING_PASS", "monthly ACK")
        request(BASE + f"/sessions/{mid}/confirm-entry", "POST", {"confirmed_passage": True}, operator)
        sim.telemetry(slot, True)
        sim.rfid("E5F6G7H8", "GATE_OUT")
        wait_for(lambda: request(BASE + f"/sessions/{mid}", token=operator)["status"] == "PENDING_REVIEW", "plate mismatch")
        try:
            request(BASE + f"/sessions/{mid}/resolve-plate", "PATCH",
                    {"approved": True, "note": "Checked image"}, operator)
            raise AssertionError("Missing confirmed exit plate was accepted")
        except HTTPError as error:
            assert error.code == 400
        request(BASE + f"/sessions/{mid}/resolve-plate", "PATCH",
                {"approved": True, "note": "Checked image", "actual_exit_plate": "30A99988"}, operator)
        wait_for(lambda: request(BASE + f"/sessions/{mid}", token=operator)["status"] == "EXIT_ACKED_WAITING_PASS", "monthly free exit ACK")
        request(BASE + f"/sessions/{mid}/confirm-exit", "POST", {"confirmed_passage": True}, operator)
        sim.telemetry(slot, False)
        wait_for(lambda: query("SELECT status FROM slots WHERE id=%s", (slot,))[0][0] == "EMPTY", "monthly release")
        print("PASS monthly + mismatch review + free automatic exit")

        request(AI + "/scenario", "POST", {"entry_plate": "30A12345", "exit_plate": "30A12345"}, key=key)
        sim.rfid("A1B2C3D4", "GATE_IN")
        retry_session = wait_for(lambda: next((s for s in request(BASE + "/sessions", token=operator)
                                               if s["card_uid"] == "A1B2C3D4" and s["id"] != sid), None),
                                 "second visitor entry")
        rid = retry_session["id"]
        wait_for(lambda: request(BASE + f"/sessions/{rid}", token=operator)["entry_ocr_plate"] == "30A12345", "retry entry OCR")
        sim.fail_next = True
        request(BASE + f"/sessions/{rid}/entry-plate", "PATCH",
                {"plate": "30A12345", "note": "Đã đối chiếu ảnh"}, operator)
        wait_for(lambda: query("SELECT COUNT(*) FROM commands WHERE session_id=%s AND status='FAILED'", (rid,))[0][0],
                 "failed entry ACK")
        sim.telemetry(slot, False)
        time.sleep(0.3)
        assert query("SELECT COUNT(*) FROM alerts WHERE session_id=%s", (rid,))[0][0] == 0
        request(BASE + f"/sessions/{rid}/retry-gate", "POST", token=operator)
        wait_for(lambda: request(BASE + f"/sessions/{rid}", token=operator)["status"] == "ENTRY_ACKED_WAITING_PASS", "entry retry ACK")
        request(BASE + f"/sessions/{rid}/confirm-entry", "POST", {"confirmed_passage": True}, operator)
        # MySQL DATETIME stores whole seconds; leave room for that rounding.
        time.sleep(2.2)
        sim.telemetry(slot, False)
        wait_for(lambda: query("SELECT COUNT(*) FROM alerts WHERE session_id=%s", (rid,))[0][0] > 0,
                 "late empty-slot alert")
        sim.telemetry(slot, True)
        wait_for(lambda: query("SELECT status FROM slots WHERE id=%s", (slot,))[0][0] == "OCCUPIED", "occupied after retry")
        sim.rfid("A1B2C3D4", "GATE_OUT")
        wait_for(lambda: request(BASE + f"/sessions/{rid}", token=operator)["status"] == "PENDING_REVIEW", "second exit OCR review")
        request(BASE + f"/sessions/{rid}/resolve-plate", "PATCH",
                {"approved": True, "actual_exit_plate": "30A12345", "note": "Đã đối chiếu ảnh"}, operator)
        wait_for(lambda: request(BASE + f"/sessions/{rid}", token=operator)["status"] == "EXIT_READY", "second exit")
        sim.fail_next = True
        request(BASE + f"/sessions/{rid}/checkout", "POST", {"payment_method": "CASH"}, operator)
        wait_for(lambda: query("SELECT COUNT(*) FROM commands WHERE session_id=%s AND gate_id='GATE_OUT' AND status='FAILED'", (rid,))[0][0],
                 "failed exit ACK")
        assert request(BASE + f"/sessions/{rid}", token=operator)["status"] == "PAID_WAITING_EXIT"
        request(BASE + f"/sessions/{rid}/retry-gate", "POST", token=operator)
        wait_for(lambda: request(BASE + f"/sessions/{rid}", token=operator)["status"] == "EXIT_ACKED_WAITING_PASS", "exit retry ACK")
        request(BASE + f"/sessions/{rid}/cancel-exit", "POST",
                {"reason": "Xe quay đầu", "confirmed_not_exited": True}, operator)
        assert request(BASE + f"/sessions/{rid}", token=operator)["status"] == "ACTIVE"
        request(BASE + f"/sessions/{rid}/prepare-exit", "POST",
                {"exit_plate": "30A12345"}, operator)
        wait_for(lambda: request(BASE + f"/sessions/{rid}", token=operator)["status"] == "EXIT_ACKED_WAITING_PASS",
                 "reused payment exit ACK")
        assert query("SELECT COUNT(*) FROM payments WHERE session_id=%s", (rid,))[0][0] == 1
        request(BASE + f"/sessions/{rid}/confirm-exit", "POST", {"confirmed_passage": True}, operator)
        assert query("SELECT COUNT(*) FROM payments WHERE session_id=%s", (rid,))[0][0] == 1
        assert query("SELECT status FROM slots WHERE id=%s", (slot,))[0][0] == "OCCUPIED"
        request(BASE + f"/slots/{slot}/status", "PATCH",
                {"status": "EMPTY", "reason": "Inspected empty after demo"}, operator)
        assert query("SELECT status FROM slots WHERE id=%s", (slot,))[0][0] == "EMPTY"
        assert query("SELECT COUNT(*) FROM audit_logs WHERE resource_type='slots' AND resource_id=%s AND action='UPDATE_SLOT_STATUS'", (slot,))[0][0] >= 1
        print("PASS failed ACK/retry both gates + alert timing + one payment + manual slot inspection")

        request(AI + "/scenario", "POST",
                {"entry_plate": "30A12345", "exit_plate": "30A12345",
                 "entry_needs_confirmation": True}, key=key)
        sim.rfid("A1B2C3D4", "GATE_IN")
        uncertain = wait_for(lambda: next((s for s in request(BASE + "/sessions", token=operator)
                                            if s["card_uid"] == "A1B2C3D4" and s["id"] not in (sid, rid)), None),
                             "uncertain OCR entry")
        uid = uncertain["id"]
        wait_for(lambda: request(BASE + f"/sessions/{uid}", token=operator)["entry_ocr_plate"] == "30A12345",
                 "raw uncertain OCR")
        assert request(BASE + f"/sessions/{uid}", token=operator)["status"] == "ENTRY_PENDING"
        assert query("SELECT COUNT(*) FROM commands WHERE session_id=%s", (uid,))[0][0] == 0
        wait_for(lambda: any(f.get("code") == "PLATE_REVIEW" for f in sim.feedback),
                 "LCD plate-review feedback")
        request(BASE + f"/sessions/{uid}/entry-plate", "PATCH",
                {"plate": "30A12345", "note": "Đối chiếu ảnh DEMO"}, operator)
        wait_for(lambda: request(BASE + f"/sessions/{uid}", token=operator)["status"] == "ENTRY_ACKED_WAITING_PASS",
                 "uncertain entry ACK")
        request(BASE + f"/sessions/{uid}/cancel-entry", "POST",
                {"reason": "Xe không qua cổng", "confirmed_departure": False}, operator)
        assert request(BASE + f"/sessions/{uid}", token=operator)["status"] == "CANCELLED"
        print("PASS uncertain OCR waits for operator + cancel after entry ACK")

        profile = request(BASE + "/auth/me", "PATCH", {"full_name": "Demo Operator"}, operator)
        assert profile["full_name"] == "Demo Operator"
        try:
            request(BASE + "/auth/me", "PATCH", {"email": "admin@ptit.edu.vn"}, operator)
            raise AssertionError("Duplicate email was accepted")
        except HTTPError as error:
            assert error.code == 409
        print("PASS own profile update and duplicate email rejection")

        try:
            request(BASE + f"/slots/{slot}/status", "PATCH", {"status": "MAINTENANCE", "reason": "Inspection"}, viewer)
            raise AssertionError("Viewer changed slot")
        except HTTPError as error:
            assert error.code == 403
        assert query("SELECT COUNT(*) FROM commands WHERE status='ACKED'")[0][0] >= 4
        request(BASE + f"/devices/{DEVICE}/status", "PATCH",
                {"status": "SUSPENDED", "reason": "Demo revocation"}, admin)
        os.environ["DEMO_API_TOKEN"] = admin
        demo_broker.revoke(DEVICE)
        print("PASS RBAC + broker credential revocation")
    finally:
        sim.close()


if __name__ == "__main__":
    try:
        main()
    finally:
        for proc, log in reversed(procs):
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            log.close()
        if broker_started:
            subprocess.run(["docker", "compose", "-f", str(ROOT / "deployment" / "compose.demo.yml"),
                            "down"], check=False)
