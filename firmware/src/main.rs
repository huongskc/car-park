use esp_idf_svc::eventloop::EspSystemEventLoop;
use esp_idf_svc::hal::delay::{Ets, FreeRtos};
use esp_idf_svc::hal::gpio::{Input, Output, PinDriver, Pull};
use esp_idf_svc::hal::i2c::I2cDriver;
use esp_idf_svc::hal::ledc::config::{Resolution, TimerConfig};
use esp_idf_svc::hal::ledc::{LedcDriver, LedcTimerDriver};
use esp_idf_svc::hal::peripherals::Peripherals;
use esp_idf_svc::hal::spi::{config, SpiDeviceDriver, SpiDriver};
use esp_idf_svc::hal::units::FromValueType;
use esp_idf_svc::mqtt::client::{
    EspMqttClient, EventPayload, LwtConfiguration, MqttClientConfiguration, MqttProtocolVersion,
    QoS,
};
use esp_idf_svc::nvs::{EspDefaultNvsPartition, EspNvs};
use esp_idf_svc::sys::EspError;
use esp_idf_svc::wifi::{BlockingWifi, ClientConfiguration, Configuration, EspWifi};
use lcd_lcm1602_i2c::sync_lcd::Lcd;
use mfrc522::comm::blocking::spi::SpiInterface;
use mfrc522::Mfrc522;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

const WIFI_SSID: &str = env!("WIFI_SSID");
const WIFI_PASS: &str = env!("WIFI_PASS");
const BROKER: &str = env!("BROKER");
const DEVICE_ID: &str = "ESP32_GATE_01";
const AUTH_TOKEN: &str = env!("AUTH_TOKEN");
const MQTT_PASSWORD: &str = env!("MQTT_PASSWORD");
const STATUS_TOPIC: &str = "parking/PTIT_ZONE_1/ESP32_GATE_01/status";
const TELEMETRY_TOPIC: &str = "parking/PTIT_ZONE_1/ESP32_GATE_01/telemetry";
const RFID_TOPIC: &str = "parking/PTIT_ZONE_1/ESP32_GATE_01/events/rfid";
const ACK_TOPIC: &str = "parking/PTIT_ZONE_1/ESP32_GATE_01/ack";
const COMMANDS_TOPIC: &str = "parking/PTIT_ZONE_1/ESP32_GATE_01/commands";
const CONFIG_TOPIC: &str = "parking/PTIT_ZONE_1/ESP32_GATE_01/config";
const FEEDBACK_TOPIC: &str = "parking/PTIT_ZONE_1/ESP32_GATE_01/feedback";

#[derive(Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct ParkConfig {
    version: u32,
    distance_threshold_cm: f32,
    servo_open_time_sec: u32,
    heartbeat_interval_sec: u32,
    buzzer_enabled: bool,
}

impl Default for ParkConfig {
    fn default() -> Self {
        Self {
            version: 1,
            distance_threshold_cm: 15.0,
            servo_open_time_sec: 10,
            heartbeat_interval_sec: 10,
            buzzer_enabled: true,
        }
    }
}

fn field_f32(v: &serde_json::Value, key: &str, default: f32) -> f32 {
    v.get(key)
        .and_then(|x| x.as_f64())
        .unwrap_or(default as f64) as f32
}
fn field_u32(v: &serde_json::Value, key: &str, default: u32) -> u32 {
    v.get(key)
        .and_then(|x| x.as_u64())
        .unwrap_or(default as u64) as u32
}
fn parse_config(text: &str) -> Result<ParkConfig, serde_json::Error> {
    let v: serde_json::Value = serde_json::from_str(text)?;
    Ok(ParkConfig {
        version: field_u32(&v, "version", 0),
        distance_threshold_cm: field_f32(&v, "distanceThresholdCm", 15.0),
        servo_open_time_sec: field_u32(&v, "servoOpenTimeSec", 10),
        heartbeat_interval_sec: field_u32(&v, "heartbeatIntervalSec", 10),
        buzzer_enabled: v
            .get("buzzerEnabled")
            .and_then(|x| x.as_bool())
            .unwrap_or(true),
    })
}

#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase")]
struct CommandMsg {
    command_id: String,
    #[serde(default)]
    gate: String,
    action: String,
    #[serde(default)]
    auto_close_ms: Option<u32>,
}

#[derive(Default)]
struct GateState {
    open: bool,
    held: bool,
    close_at: Option<i64>,
}

fn now_us() -> i64 {
    unsafe { esp_idf_svc::sys::esp_timer_get_time() }
}

fn measure_cm(trig: &mut PinDriver<'_, Output>, echo: &PinDriver<'_, Input>) -> Option<f32> {
    trig.set_low().ok()?;
    Ets::delay_us(2);
    trig.set_high().ok()?;
    Ets::delay_us(10);
    trig.set_low().ok()?;
    let deadline = now_us() + 30_000;
    while echo.is_low() {
        if now_us() > deadline {
            return None;
        }
    }
    let start = now_us();
    while echo.is_high() {
        if now_us() - start > 30_000 {
            return None;
        }
    }
    let cm = (now_us() - start) as f32 * 0.0343 / 2.0;
    if cm <= 2.0 {
        None
    } else {
        Some(cm)
    }
}

fn load_config(nvs: &EspNvs<esp_idf_svc::nvs::NvsDefault>) -> ParkConfig {
    let mut buf = [0u8; 256];
    match nvs.get_str("cfg", &mut buf) {
        Ok(Some(text)) => parse_config(text).unwrap_or_default(),
        _ => ParkConfig::default(),
    }
}

fn save_config(nvs: &EspNvs<esp_idf_svc::nvs::NvsDefault>, cfg: &ParkConfig) {
    if let Ok(text) = serde_json::to_string(cfg) {
        let _ = nvs.set_str("cfg", &text);
    }
}

fn paint<'a, 'd>(lcd: &mut Option<Lcd<'a, I2cDriver<'d>, Ets>>, line1: &str, line2: &str) {
    let Some(screen) = lcd else { return };
    let top = format!("{line1:<16}");
    let bot = format!("{line2:<16}");
    if screen.set_cursor(0, 0).is_ok() {
        let _ = screen.write_str(&top);
        let _ = screen.set_cursor(1, 0);
        let _ = screen.write_str(&bot);
    }
}

fn main() -> Result<(), EspError> {
    esp_idf_svc::sys::link_patches();
    esp_idf_svc::log::EspLogger::initialize_default();

    let p = Peripherals::take()?;
    let nvs_part = EspDefaultNvsPartition::take()?;
    let nvs = EspNvs::new(nvs_part.clone(), "parking", true)?;
    let mut cfg = load_config(&nvs);
    println!(
        "cau hinh version {} nguong {} giu {}s nhip {}s coi {}",
        cfg.version,
        cfg.distance_threshold_cm,
        cfg.servo_open_time_sec,
        cfg.heartbeat_interval_sec,
        cfg.buzzer_enabled
    );

    let mut trig = PinDriver::output(p.pins.gpio13)?;
    let echo = PinDriver::input(p.pins.gpio14, Pull::Floating)?;
    let mut buzzer = PinDriver::output(p.pins.gpio17)?;
    buzzer.set_low()?;

    let timer = LedcTimerDriver::new(
        p.ledc.timer0,
        &TimerConfig::new()
            .frequency(50.Hz())
            .resolution(Resolution::Bits14),
    )?;
    let mut gate_in = LedcDriver::new(p.ledc.channel0, &timer, p.pins.gpio25)?;
    let mut gate_out = LedcDriver::new(p.ledc.channel1, &timer, p.pins.gpio26)?;
    let duty_0 = gate_in.get_max_duty() / 20;
    let duty_up = duty_0 + duty_0;
    gate_in.set_duty(duty_0)?;
    gate_out.set_duty(duty_0)?;

    let i2c_cfg = esp_idf_svc::hal::i2c::config::Config::new().baudrate(100_u32.kHz().into());
    let mut i2c = I2cDriver::new(p.i2c0, p.pins.gpio21, p.pins.gpio22, &i2c_cfg)?;
    let mut lcd_delay = Ets;
    let mut lcd = match Lcd::new(&mut i2c, &mut lcd_delay)
        .with_address(0x27)
        .with_rows(2)
        .init()
    {
        Ok(screen) => {
            println!("lcd san sang");
            Some(screen)
        }
        Err(e) => {
            println!("lcd loi {e:?}");
            None
        }
    };

    let mut rst_in = PinDriver::output(p.pins.gpio16)?;
    let mut rst_out = PinDriver::output(p.pins.gpio32)?;
    rst_in.set_low()?;
    rst_out.set_low()?;
    FreeRtos::delay_ms(10);
    rst_in.set_high()?;
    rst_out.set_high()?;
    FreeRtos::delay_ms(10);

    let bus = SpiDriver::new(
        p.spi2,
        p.pins.gpio18,
        p.pins.gpio23,
        Some(p.pins.gpio19),
        &config::DriverConfig::new(),
    )?;
    let spi_cfg = config::Config::new().baudrate(1_u32.MHz().into());
    let dev_in = SpiDeviceDriver::new(&bus, Some(p.pins.gpio5), &spi_cfg)?;
    let dev_out = SpiDeviceDriver::new(&bus, Some(p.pins.gpio27), &spi_cfg)?;

    let mut rfid_in = match Mfrc522::new(SpiInterface::new(dev_in)).init() {
        Ok(mut reader) => {
            println!("rfid vao {:02X}", reader.version().unwrap_or(0));
            Some(reader)
        }
        Err(e) => {
            println!("rfid vao loi: {e:?}");
            None
        }
    };
    let mut rfid_out = match Mfrc522::new(SpiInterface::new(dev_out)).init() {
        Ok(mut reader) => {
            println!("rfid ra {:02X}", reader.version().unwrap_or(0));
            Some(reader)
        }
        Err(e) => {
            println!("rfid ra loi: {e:?}");
            None
        }
    };

    let sysloop = EspSystemEventLoop::take()?;
    let mut wifi = BlockingWifi::wrap(
        EspWifi::new(p.modem, sysloop.clone(), Some(nvs_part))?,
        sysloop,
    )?;
    wifi.set_configuration(&Configuration::Client(ClientConfiguration {
        ssid: WIFI_SSID.try_into().unwrap(),
        password: WIFI_PASS.try_into().unwrap(),
        ..Default::default()
    }))?;
    wifi.start()?;
    wifi.connect()?;
    wifi.wait_netif_up()?;
    let ip = wifi.wifi().sta_netif().get_ip_info()?.ip;
    println!("wifi ip {ip}");

    let online = format!(
        r#"{{"token":"{AUTH_TOKEN}","status":"online","fwVersion":"1.0.0","ipAddress":"{ip}"}}"#
    );
    let lwt = format!(r#"{{"status":"offline","token":"{AUTH_TOKEN}"}}"#);
    let mqtt_conf = MqttClientConfiguration {
        client_id: Some(DEVICE_ID),
        username: Some(DEVICE_ID),
        password: Some(MQTT_PASSWORD),
        keep_alive_interval: Some(Duration::from_secs(30)),
        protocol_version: Some(MqttProtocolVersion::V3_1_1),
        lwt: Some(LwtConfiguration {
            topic: STATUS_TOPIC,
            payload: lwt.as_bytes(),
            qos: QoS::AtLeastOnce,
            retain: false,
        }),
        ..Default::default()
    };

    let connected = Arc::new(AtomicBool::new(false));
    let flag = connected.clone();
    let inbox = Arc::new(Mutex::new(Vec::<(String, String)>::new()));
    let inbox_cb = inbox.clone();
    let mut client =
        EspMqttClient::new_cb(BROKER, &mqtt_conf, move |event| match event.payload() {
            EventPayload::Connected(_) => {
                println!("mqtt da noi");
                flag.store(true, Ordering::SeqCst);
            }
            EventPayload::Received { topic, data, .. } => {
                let topic = topic.unwrap_or("");
                let text = std::str::from_utf8(data).unwrap_or("");
                println!("nhan {topic} {text}");
                if topic.ends_with("/commands")
                    || topic.ends_with("/config")
                    || topic.ends_with("/feedback")
                {
                    inbox_cb
                        .lock()
                        .unwrap()
                        .push((topic.to_string(), text.to_string()));
                }
            }
            EventPayload::Disconnected => println!("mqtt ngat"),
            EventPayload::Error(e) => println!("mqtt loi {e:?}"),
            _ => {}
        })?;

    while !connected.load(Ordering::SeqCst) {
        FreeRtos::delay_ms(100);
    }
    client.subscribe(COMMANDS_TOPIC, QoS::AtLeastOnce)?;
    client.subscribe(CONFIG_TOPIC, QoS::AtLeastOnce)?;
    client.subscribe(FEEDBACK_TOPIC, QoS::AtLeastOnce)?;
    client.publish(STATUS_TOPIC, QoS::AtLeastOnce, false, online.as_bytes())?;

    let boot_id = format!("boot_{}", now_us());
    let mut seq = 0_u32;
    let mut last_status = now_us();
    let mut last_telem = 0_i64;
    let mut last_cm: Option<f32> = None;
    let mut last_uid = String::new();
    let mut last_uid_at = 0_i64;
    let mut gate_in_state = GateState::default();
    let mut gate_out_state = GateState::default();
    let mut buzzer_until: Option<i64> = None;

    let mut lcd_line1 = "READY".to_string();
    let mut lcd_line2 = "SAN SANG".to_string();
    let mut lcd_shown = String::new();
    let mut wait_until: Option<i64> = None;
    let mut seen_cmds: Vec<(String, String)> = Vec::new();

    loop {
        let now = now_us();
        if buzzer_until.is_some_and(|t| now >= t) {
            buzzer.set_low()?;
            buzzer_until = None;
        }
        if gate_in_state.close_at.is_some_and(|until| now >= until) {
            gate_in.set_duty(duty_0)?;
            gate_in_state = GateState::default();
            println!("dong GATE_IN");
        }
        if gate_out_state.close_at.is_some_and(|until| now >= until) {
            gate_out.set_duty(duty_0)?;
            gate_out_state = GateState::default();
            println!("dong GATE_OUT");
        }
        if !gate_in_state.open && !gate_out_state.open && wait_until.is_none() {
            lcd_line1 = "READY".to_string();
            lcd_line2 = "SAN SANG".to_string();
        }

        let interval = cfg.heartbeat_interval_sec.max(1) as i64 * 1_000_000;
        if now - last_status >= interval {
            client.publish(STATUS_TOPIC, QoS::AtLeastOnce, false, online.as_bytes())?;
            last_status = now;
            println!("gui status");
        }

        if let Some(cm) = measure_cm(&mut trig, &echo) {
            let changed = last_cm.map(|old| (cm - old).abs() >= 5.0).unwrap_or(true);
            if changed || now - last_telem >= 5_000_000 {
                seq += 1;
                let body = format!(
                    r#"{{"token":"{AUTH_TOKEN}","slotId":"SLOT_A1","distanceCm":{cm:.2},"bootId":"{boot_id}","seq":{seq}}}"#
                );
                client.publish(TELEMETRY_TOPIC, QoS::AtLeastOnce, false, body.as_bytes())?;
                last_cm = Some(cm);
                last_telem = now;
            }
        }

        for (reader, gate) in [(&mut rfid_in, "GATE_IN"), (&mut rfid_out, "GATE_OUT")] {
            let Some(reader) = reader.as_mut() else {
                continue;
            };
            if let Ok(atqa) = reader.reqa() {
                if let Ok(uid) = reader.select(&atqa) {
                    let text: String = uid.as_bytes().iter().map(|b| format!("{b:02X}")).collect();
                    if text != last_uid || now - last_uid_at > 1_500_000 {
                        let body = format!(
                            r#"{{"token":"{AUTH_TOKEN}","cardUid":"{text}","gate":"{gate}"}}"#
                        );
                        client.publish(RFID_TOPIC, QoS::AtLeastOnce, false, body.as_bytes())?;
                        last_uid = text;
                        last_uid_at = now;
                        lcd_line1 = "CHO XU LY".to_string();
                        lcd_line2 = if gate == "GATE_OUT" {
                            "CONG RA".to_string()
                        } else {
                            "CONG VAO".to_string()
                        };
                        wait_until = Some(now + 8_000_000);
                    }
                    let _ = reader.hlta();
                }
            }
        }

        let pending: Vec<(String, String)> = inbox.lock().unwrap().drain(..).collect();
        for (topic, raw) in pending {
            if topic.ends_with("/feedback") {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&raw) {
                    let message = v.get("message").and_then(|x| x.as_str()).unwrap_or("");
                    let chars: Vec<char> = message.chars().collect();
                    lcd_line1 = chars.iter().take(16).collect();
                    lcd_line2 = chars.iter().skip(16).take(16).collect();
                    if lcd_line2.is_empty() {
                        lcd_line2 = " ".to_string();
                    }
                    wait_until = Some(now_us() + 8_000_000);
                }
                continue;
            }
            if topic.ends_with("/config") {
                match parse_config(&raw) {
                    Ok(incoming) if incoming.version > cfg.version => {
                        cfg = incoming;
                        save_config(&nvs, &cfg);
                        println!("nhan config version {}", cfg.version);
                    }
                    Ok(incoming) => println!("bo qua config version {}", incoming.version),
                    Err(e) => println!("config loi {e}"),
                }
                continue;
            }

            let Ok(cmd) = serde_json::from_str::<CommandMsg>(&raw) else {
                continue;
            };
            let gate = if cmd.gate.is_empty() {
                "GATE_IN"
            } else {
                &cmd.gate
            };

            if let Some((_, body)) = seen_cmds.iter().find(|(id, _)| id == &cmd.command_id) {
                client.publish(ACK_TOPIC, QoS::AtLeastOnce, false, body.as_bytes())?;
                println!("bo qua lenh trung {}", cmd.command_id);
                continue;
            }
            let valid_gate = gate == "GATE_IN" || gate == "GATE_OUT";
            let state = if gate == "GATE_OUT" {
                &mut gate_out_state
            } else {
                &mut gate_in_state
            };
            let (status, reason) = match cmd.action.as_str() {
                _ if !valid_gate => ("FAILED", "Gate khong hop le"),
                "OPEN" => {
                    if gate == "GATE_OUT" {
                        gate_out.set_duty(duty_up)?;
                    } else {
                        gate_in.set_duty(duty_up)?;
                    }
                    if cfg.buzzer_enabled {
                        buzzer.set_high()?;
                        buzzer_until = Some(now_us() + 300_000);
                    }
                    state.open = true;
                    state.close_at = if state.held {
                        None
                    } else {
                        Some(now_us() + cmd.auto_close_ms.unwrap_or(30_000).max(1) as i64 * 1_000)
                    };
                    lcd_line1 = "DANG MO".to_string();
                    lcd_line2 = if gate == "GATE_OUT" {
                        "CONG RA".to_string()
                    } else {
                        "CONG VAO".to_string()
                    };
                    wait_until = None;
                    ("SUCCESS", "Barrier opened 90 deg successfully")
                }
                "CLOSE" => {
                    if gate == "GATE_OUT" {
                        gate_out.set_duty(duty_0)?;
                    } else {
                        gate_in.set_duty(duty_0)?;
                    }
                    *state = GateState::default();
                    ("SUCCESS", "Barrier closed")
                }
                "HOLD_OPEN" => {
                    if gate == "GATE_OUT" {
                        gate_out.set_duty(duty_up)?;
                    } else {
                        gate_in.set_duty(duty_up)?;
                    }
                    state.open = true;
                    state.held = true;
                    state.close_at = None;
                    ("SUCCESS", "Barrier held open")
                }
                "RESUME_AUTO" => {
                    state.held = false;
                    state.close_at = state.open.then(|| now_us() + 30_000_000);
                    ("SUCCESS", "Barrier auto close resumed")
                }
                "REBOOT" => {
                    let body = format!(
                        r#"{{"token":"{AUTH_TOKEN}","commandId":"{}","gate":"{gate}","status":"SUCCESS","reason":"Sap khoi dong lai"}}"#,
                        cmd.command_id
                    );
                    client.publish(ACK_TOPIC, QoS::AtLeastOnce, false, body.as_bytes())?;
                    FreeRtos::delay_ms(300);
                    esp_idf_svc::hal::reset::restart();
                }
                "UPDATE_CONFIG" => {
                    cfg = load_config(&nvs);
                    println!("doc lai config version {}", cfg.version);
                    ("SUCCESS", "Da doc lai cau hinh")
                }
                _ => ("FAILED", "Lenh khong ho tro"),
            };
            let body = format!(
                r#"{{"token":"{AUTH_TOKEN}","commandId":"{}","gate":"{gate}","status":"{status}","reason":"{reason}"}}"#,
                cmd.command_id
            );
            client.publish(ACK_TOPIC, QoS::AtLeastOnce, false, body.as_bytes())?;
            if seen_cmds.len() == 8 {
                seen_cmds.remove(0);
            }
            seen_cmds.push((cmd.command_id.clone(), body));
            println!("gui ack {} {status}", cmd.command_id);
        }

        if wait_until.is_some_and(|t| now >= t) && !gate_in_state.open && !gate_out_state.open {
            lcd_line1 = "READY".to_string();
            lcd_line2 = "SAN SANG".to_string();
            wait_until = None;
        }
        let key = format!("{lcd_line1}|{lcd_line2}");
        if key != lcd_shown {
            paint(&mut lcd, &lcd_line1, &lcd_line2);
            lcd_shown = key;
            println!("lcd {lcd_line1} / {lcd_line2}");
        }
        FreeRtos::delay_ms(50);
    }
}
