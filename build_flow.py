#!/usr/bin/env python3
"""Builds the Node-RED tab "Bordnetz API" for the Bordnetz iPhone app as bordnetz-flow.json (import file).

The tab serves the app's API on Node-RED's own port (Venus OS: https://<gx>:1881), every endpoint behind a bearer
token: GET /api/camper/status (camper.status.v1), GET /api/camper/energy?days=<n>, GET /api/camper/track?since=<unix s>,
POST /api/camper/away {active}, POST /api/camper/shore/reset.

Configuration lives in the tab's environment variables (Node-RED editor: double-click the tab → Environment
variables), so the generated file fits every vehicle and nothing here has to be edited. See README.md, Configure:
    IOS_API_TOKEN          the app's bearer token (type cred: encrypted in Node-RED's credentials, never in a file)
    BATTERY_SERVICE        e.g. com.victronenergy.battery/512   (SOC, voltage, current, cells, BMS alarms)
    SOLAR_SERVICE          e.g. com.victronenergy.solarcharger/279
    CHARGER_SERVICE        e.g. com.victronenergy.charger/288   (AC charger)
    TEMP_OUTSIDE_SERVICE, TEMP_INSIDE_SERVICE, TEMP_FRIDGE_SERVICE   e.g. com.victronenergy.temperature/20
    DOOR_SERVICE           e.g. com.victronenergy.digitalinput/1 (input type "Door alarm")
    GPS_SERVICE            e.g. com.victronenergy.gps/1         (location, trips, away mode)
    SHORE_SHELLY_TOPIC     MQTT topic prefix of a Shelly (Gen2+) behind the shore inlet, reporting to the GX's broker
    FRIDGE_12V_URL         Switch.GetStatus URL of a Shelly switching the fridge's 12 V supply
    SOC_LOW, FRIDGE_MAX    alarm limits (%, °C), default 20 and 14
Empty means "not fitted": the input stays silent, the API sends null and the app leaves that row out.

Deploy from the Mac instead of importing (creates or replaces the tab, backs up all flows first): deploy.sh.

Alarms (codes in "alarms", the app translates them):
    soc_low      state of charge below SOC_LOW %, cleared from SOC_LOW + SOC_HYSTERESIS %
    fridge_warm  fridge above FRIDGE_MAX °C for FRIDGE_MINUTES
    shore_lost   Shelly went offline while the charger was charging (within CHARGING_WINDOW_MIN before);
                 shown for SHORE_LOST_MINUTES or until shore power is back
    fridge_off   12 V Shelly off and no shore power for FRIDGE_OFF_MINUTES (assumes the fridge's 230 V side hangs on
                 shore power, so it has power whenever either is there)
    away_moved   away mode on and the vehicle more than AWAY_RADIUS_M from where it was armed
    away_door    away mode on and the door opened; both stay until away mode is switched off
    bms_*        alarms of the battery's BMS (warning or alarm on the D-Bus)

Campsite energy: shore.sessionKWh counts the Shelly's energy since the app's reset (POST /api/camper/shore/reset),
for paying at a new pitch; it survives the Shelly's own counter starting again at 0. Kept in SHORE_SESSION_FILE.

Energy balance: every ENERGY_SECONDS the powers are added up per local day (solar, charger, alternator, DC
consumption, battery in/out, shore power at the inlet); kept ENERGY_DAYS in ENERGY_FILE, today and yesterday in the
status document, all days as GET /api/camper/energy?days=<n>.

GPS track: every TRACK_SECONDS a point while driving, otherwise when moved TRACK_MIN_MOVE_M or every
TRACK_PARKED_MINUTES; kept TRACK_DAYS in TRACK_FILE, served as GET /api/camper/track?since=<unix seconds>.
"""
import json
import pathlib

TAB = "bordnetz_api_tab"
TAB_LABEL = "Bordnetz API"
REPO = "github.com/silly82/bordnetz-nodered"
# MQTT broker of the GX itself (FlashMQ on localhost:1883), shipped with the flow for the shore Shelly.
BROKER = "bordnetz_broker"

# Tab environment: (name, type, default). Order = order in the editor.
SETTINGS = [
    ("IOS_API_TOKEN", "cred", ""),
    ("BATTERY_SERVICE", "str", ""),
    ("SOLAR_SERVICE", "str", ""),
    ("CHARGER_SERVICE", "str", ""),
    ("TEMP_OUTSIDE_SERVICE", "str", ""),
    ("TEMP_INSIDE_SERVICE", "str", ""),
    ("TEMP_FRIDGE_SERVICE", "str", ""),
    ("DOOR_SERVICE", "str", ""),
    ("GPS_SERVICE", "str", ""),
    ("SHORE_SHELLY_TOPIC", "str", ""),
    ("FRIDGE_12V_URL", "str", ""),
    ("SOC_LOW", "str", "20"),
    ("FRIDGE_MAX", "str", "14"),
]

FRIDGE_POLL_SECONDS = 30

ENERGY_SECONDS = 10
ENERGY_DAYS = 60
ENERGY_SAVE_MINUTES = 5

DRIVING_KMH = 5   # same as CamperKit's CamperStatus.Location.drivingKmh
TRACK_SECONDS = 10
TRACK_MIN_MOVE_M = 50
TRACK_PARKED_MINUTES = 15
TRACK_DAYS = 30
TRACK_SAVE_SECONDS = 60
# GPS drifts some ten metres while parked.
AWAY_RADIUS_M = 200

SOC_HYSTERESIS = 2
# LiFePO4 cells at rest are within a few mV; the app marks a larger spread between lowest and highest cell (V).
CELL_SPREAD_MAX = 0.1
# FRIDGE_MAX depends on where the sensor sits: in a fridge lid it reads 6-10 °C while the fridge works, up to 12.8 °C
# on warm afternoons, opening the lid adds short peaks; hence 14 °C for FRIDGE_MINUTES. The app gets both limits in
# "limits" of the status document.
FRIDGE_MINUTES = 30
# 12 V switched off on shore power is normal; off without shore power the fridge has no supply at all.
FRIDGE_OFF_MINUTES = 5
# The charger reports "off" before the Shelly counts as offline (up to 5 min watchdog): look back that far.
CHARGING_WINDOW_MIN = 10
SHORE_LOST_MINUTES = 60
# Victron charger states that mean "charging": bulk, absorption, float, storage, equalize, power supply.
CHARGING_STATES = [3, 4, 5, 6, 7, 11]
# Victron's TimeToGo cap (10 days), reported while charging or idle: means "no estimate".
TIME_TO_GO_MAX_S = 864000
# "dc.power" below this (W): more charge goes into the battery than solar and charger deliver, so the alternator
# charges through a DC-DC converter (usually not on the D-Bus). The margin covers measurement noise between devices.
ALTERNATOR_MIN_W = 20

ALARM_CONSTANTS = f"""// Limits from the tab's environment (SOC_LOW, FRIDGE_MAX), the rest fixed.
const setting = (name, fallback) => {{ const x = Number(env.get(name)); return env.get(name) !== '' && env.get(name) != null && isFinite(x) ? x : fallback; }};
const SOC_LOW = setting('SOC_LOW', 20), SOC_HYSTERESIS = {SOC_HYSTERESIS};
const FRIDGE_MAX = setting('FRIDGE_MAX', 14), FRIDGE_MS = {FRIDGE_MINUTES} * 60000, FRIDGE_OFF_MS = {FRIDGE_OFF_MINUTES} * 60000;
const CHARGING_WINDOW_MS = {CHARGING_WINDOW_MIN} * 60000, SHORE_LOST_MS = {SHORE_LOST_MINUTES} * 60000;
const CHARGING_STATES = {CHARGING_STATES};
const TIME_TO_GO_MAX_S = {TIME_TO_GO_MAX_S}, ALTERNATOR_MIN_W = {ALTERNATOR_MIN_W};
const CELL_SPREAD_MAX = {CELL_SPREAD_MAX};
const DRIVING_KMH = {DRIVING_KMH}, AWAY_RADIUS_M = {AWAY_RADIUS_M};
"""

# The Victron input nodes take their service from the tab environment: Node-RED replaces a property that is exactly
# "${NAME}" when the flow starts. An empty service subscribes to nothing.
BATTERY = "${BATTERY_SERVICE}"
SOLAR = "${SOLAR_SERVICE}"
CHARGER = "${CHARGER_SERVICE}"
OUTSIDE = "${TEMP_OUTSIDE_SERVICE}"
INSIDE = "${TEMP_INSIDE_SERVICE}"
FRIDGE = "${TEMP_FRIDGE_SERVICE}"
DOOR = "${DOOR_SERVICE}"
GPS = "${GPS_SERVICE}"

# BMS alarms on the D-Bus (0 ok, 1 warning, 2 alarm), reported as "bms_<snake_case>" in "alarms". LowSoc is left out:
# soc_low covers it with the app's own limit.
BMS_ALARMS = ["CellImbalance", "HighCellVoltage", "LowCellVoltage", "HighVoltage", "LowVoltage", "HighTemperature",
              "LowTemperature", "HighCurrent", "HighChargeCurrent", "HighDischargeCurrent", "InternalFailure",
              "StateOfHealth"]

# key -> (service, path). The node name is the key: Victron input nodes use their name as msg.topic.
INPUTS = [
    ("battery.soc", BATTERY, "/Soc"),
    ("battery.voltage", BATTERY, "/Dc/0/Voltage"),
    ("battery.current", BATTERY, "/Dc/0/Current"),
    ("battery.power", BATTERY, "/Dc/0/Power"),
    ("battery.timeToGo", BATTERY, "/TimeToGo"),
    ("solar.power", SOLAR, "/Yield/Power"),
    ("solar.current", SOLAR, "/Dc/0/Current"),
    ("solar.loadCurrent", SOLAR, "/Load/I"),
    ("solar.pvVoltage", SOLAR, "/Pv/V"),
    ("solar.state", SOLAR, "/State"),
    ("solar.yieldToday", SOLAR, "/History/Daily/0/Yield"),
    ("charger.state", CHARGER, "/State"),
    ("charger.current", CHARGER, "/Dc/0/Current"),
    ("temps.aussen", OUTSIDE, "/Temperature"),
    ("temps.kuehlschrank", FRIDGE, "/Temperature"),
    ("temps.innen", INSIDE, "/Temperature"),
    ("battery.cellMin", BATTERY, "/System/MinCellVoltage"),
    ("battery.cellMax", BATTERY, "/System/MaxCellVoltage"),
    ("door.state", DOOR, "/State"),
    ("gps.latitude", GPS, "/Position/Latitude"),
    ("gps.longitude", GPS, "/Position/Longitude"),
    ("gps.speed", GPS, "/Speed"),
    ("gps.fix", GPS, "/Fix"),
    ("humidity.aussen", OUTSIDE, "/Humidity"),
    ("humidity.kuehlschrank", FRIDGE, "/Humidity"),
    ("humidity.innen", INSIDE, "/Humidity"),
] + [(f"bms.{name}", BATTERY, f"/Alarms/{name}") for name in BMS_ALARMS]

COLLECT = ALARM_CONSTANTS + """// Keeps the latest value of every input under its key (the input node's name) in flow context,
// and the alarm state that needs history (flow context "alarmState").
const values = flow.get('values') || {};
values[msg.topic] = msg.payload;
flow.set('values', values);

const now = Date.now();
const alarm = flow.get('alarmState') || {};
const x = msg.payload;
if (msg.topic === 'battery.soc' && typeof x === 'number') {
    if (x < SOC_LOW) alarm.socLow = true;
    else if (x >= SOC_LOW + SOC_HYSTERESIS) alarm.socLow = false;
}
if (msg.topic === 'temps.kuehlschrank' && typeof x === 'number') {
    if (x > FRIDGE_MAX) alarm.fridgeWarmSince = alarm.fridgeWarmSince || now;
    else alarm.fridgeWarmSince = null;
}
if (msg.topic === 'charger.state' && CHARGING_STATES.includes(x)) alarm.chargingAt = now;
// Door from closed (7) to open (6) while away; the input repeats its value, so only the change counts.
if (msg.topic === 'door.state') {
    if (x === 6 && alarm.doorState === 7 && (flow.get('away') || {}).active) alarm.awayDoorAt = alarm.awayDoorAt || now;
    alarm.doorState = x;
}
flow.set('alarmState', alarm);
node.status({ text: Object.keys(values).length + ' values · ' + new Date().toLocaleTimeString() });
return null;"""

SHORE_SESSION = """const SHORE_SESSION_FILE = path.join(os.homedir(), '.node-red', 'bordnetz-shore-session.json');
function saveSession(session) {
    flow.set('shoreSession', session);
    fs.writeFileSync(SHORE_SESSION_FILE, JSON.stringify(session), { mode: 0o600 });
}
// kWh since the reset: what was counted before the Shelly's counter last started again, plus since then.
function sessionKWh(session) {
    if (!session || typeof session.lastKWh !== 'number' || typeof session.startKWh !== 'number') return session ? session.accumulatedKWh || 0 : null;
    return (session.accumulatedKWh || 0) + session.lastKWh - session.startKWh;
}
"""

LOAD_SHORE = SHORE_SESSION + """// On start: the campsite counter from its file.
try {
    flow.set('shoreSession', JSON.parse(fs.readFileSync(SHORE_SESSION_FILE, 'utf8')));
} catch (e) {}"""

SHORE = ALARM_CONSTANTS + SHORE_SESSION + """// Shore power from a Shelly (Gen2+, e.g. 1PM) behind the inlet, over MQTT: it is online only while shore power is
// there. Independent of the GX's grid meter, which disappears without shore power.
const STALE_MS = 300000;
const shore = flow.get('shore') || { connected: null, power: null, voltage: null, energyKWh: null };

function offline() {
    // Lost while the charger was charging: probably not unplugged on purpose (breaker, campsite fuse).
    if (shore.connected === true) {
        const alarm = flow.get('alarmState') || {};
        if (alarm.chargingAt && Date.now() - alarm.chargingAt < CHARGING_WINDOW_MS) {
            alarm.shoreLostAt = Date.now();
            flow.set('alarmState', alarm);
        }
    }
    shore.connected = false;
    shore.power = null;
    shore.voltage = null;
    flow.set('shore', shore);
    node.status({ fill: 'grey', shape: 'ring', text: 'no shore power' });
}

if (msg.topic.endsWith('/online')) {
    if (String(msg.payload).trim() !== 'true') {
        clearTimeout(context.get('watchdog'));
        offline();
    }
    return null;
}

const s = msg.payload;
if (!s || typeof s !== 'object' || typeof s.apower !== 'number') return null;
clearTimeout(context.get('watchdog'));
context.set('watchdog', setTimeout(offline, STALE_MS));

shore.connected = true;
const alarm = flow.get('alarmState') || {};
if (alarm.shoreLostAt) { alarm.shoreLostAt = null; flow.set('alarmState', alarm); }
shore.power = s.apower;
shore.voltage = typeof s.voltage === 'number' ? s.voltage : null;
if (s.aenergy && typeof s.aenergy.total === 'number') {
    shore.energyKWh = s.aenergy.total / 1000;
    // Campsite counter: starts at the first reading; a smaller total means the Shelly counts from 0 again.
    const session = flow.get('shoreSession') || { since: new Date().toISOString(), startKWh: null, accumulatedKWh: 0 };
    const isNew = !flow.get('shoreSession');
    if (typeof session.startKWh !== 'number') session.startKWh = shore.energyKWh;
    let restarted = false;
    if (typeof session.lastKWh === 'number' && shore.energyKWh < session.lastKWh - 0.001) {
        session.accumulatedKWh = (session.accumulatedKWh || 0) + session.lastKWh - session.startKWh;
        session.startKWh = 0;
        restarted = true;
    }
    session.lastKWh = shore.energyKWh;
    if (isNew || restarted || Date.now() - (context.get('sessionSavedAt') || 0) >= 300000) {
        saveSession(session);
        context.set('sessionSavedAt', Date.now());
    } else {
        flow.set('shoreSession', session);
    }
}
flow.set('shore', shore);
node.status({ fill: 'green', shape: 'dot', text: s.apower.toFixed(0) + ' W' });
return null;"""

FRIDGE_12V = ALARM_CONSTANTS + """// Answer of the fridge's 12 V Shelly (Switch.GetStatus): "output" is the relay. Unreachable: unknown.
// 12 V off and no shore power: the fridge has no supply (alarm fridge_off). Unknown on either side: no alarm.
const values = flow.get('values') || {};
const s = msg.payload;
const on = s && typeof s === 'object' && typeof s.output === 'boolean' ? s.output : null;
values['fridge.on12V'] = on;
flow.set('values', values);
const alarm = flow.get('alarmState') || {};
const shore = flow.get('shore') || {};
if (on === false && shore.connected === false) alarm.fridgeOffSince = alarm.fridgeOffSince || Date.now();
else alarm.fridgeOffSince = null;
flow.set('alarmState', alarm);
node.status(on === null ? { fill: 'grey', shape: 'ring', text: 'unreachable' }
                        : { fill: on ? 'green' : 'grey', shape: 'dot', text: on ? '12 V on' : '12 V off' });
return null;"""

SHELLY_SUBSCRIBE = """// SHORE_SHELLY_TOPIC (e.g. shelly1pm-08f9e0aabbcc): subscribes to its switch status and online flag.
const prefix = (env.get('SHORE_SHELLY_TOPIC') || '').trim().replace(/\\/+$/, '');
if (!prefix) {
    node.status({ fill: 'grey', shape: 'ring', text: 'no SHORE_SHELLY_TOPIC' });
    return null;
}
node.status({ fill: 'green', shape: 'dot', text: prefix });
return { action: 'subscribe', topic: [prefix + '/status/switch:0', prefix + '/online'] };"""

FRIDGE_URL = """// FRIDGE_12V_URL (e.g. http://192.168.1.50/rpc/Switch.GetStatus?id=0); empty: no fridge supply, no fridge_off.
const url = (env.get('FRIDGE_12V_URL') || '').trim();
if (!url) {
    node.status({ fill: 'grey', shape: 'ring', text: 'not set' });
    return null;
}
msg.url = url;
return msg;"""

# Bearer token = tab env IOS_API_TOKEN (type cred). Without a token set, the endpoints refuse everything.
AUTH = """const expected = env.get('IOS_API_TOKEN') || '';
const header = (msg.req.headers.authorization || '');
const given = header.startsWith('Bearer ') ? header.slice(7) : '';

function same(a, b) {   // constant time for equal lengths
    if (!a || a.length !== b.length) return false;
    let diff = 0;
    for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
    return diff === 0;
}

msg.headers = { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' };
if (!expected || !same(given, expected)) {
    msg.statusCode = 401;
    msg.headers['WWW-Authenticate'] = 'Bearer';
    msg.payload = { error: 'unauthorized' };
    node.status({ fill: 'red', shape: 'ring', text: '401 · ' + new Date().toLocaleTimeString() });
    return msg;
}
"""

ENERGY_FILE = """const ENERGY_FILE = path.join(os.homedir(), '.node-red', 'bordnetz-energy.json');
"""

# Local calendar day of the Cerbo ("YYYY-MM-DD"); the GX's time zone setting decides where a day starts.
DAY_KEY = """function dayKey(ms) {
    const t = new Date(ms), pad = x => String(x).padStart(2, '0');
    return t.getFullYear() + '-' + pad(t.getMonth() + 1) + '-' + pad(t.getDate());
}
// A day for the API: whole Wh, hours with two digits.
function energyDay(date, d) {
    if (!d) return null;
    const out = { date: date };
    for (const k of ['solarWh', 'chargerWh', 'alternatorWh', 'consumptionWh', 'batteryInWh', 'batteryOutWh', 'shoreWh']) {
        out[k] = Math.round(d[k] || 0);
    }
    out.hours = +(d.hours || 0).toFixed(2);
    out.socStart = typeof d.socStart === 'number' ? d.socStart : null;
    out.socEnd = typeof d.socEnd === 'number' ? d.socEnd : null;
    return out;
}
"""

LOAD_ENERGY = ENERGY_FILE + """// On start: the daily energy from the file into flow context.
try {
    flow.set('energy', JSON.parse(fs.readFileSync(ENERGY_FILE, 'utf8')));
} catch (e) {
    if (!flow.get('energy')) flow.set('energy', {});
}
node.status({ text: Object.keys(flow.get('energy')).length + ' days' });"""

ENERGY = ALARM_CONSTANTS + ENERGY_FILE + DAY_KEY + f"""const ENERGY_KEEP_DAYS = {ENERGY_DAYS}, ENERGY_SAVE_MS = {ENERGY_SAVE_MINUTES} * 60000;
""" + """// Every few seconds: adds the powers since the last tick to today's energy (Wh). Same currents as "dc" in the status
// document, all at the battery voltage: solar includes the MPPT's load output, DC consumption = solar + charger -
// battery; below -ALTERNATOR_MIN_W it counts as alternator. Gaps (restart, missing values) are not filled: "hours"
// says how much of the day was measured.
const v = flow.get('values') || {};
const now = Date.now();
const last = context.get('at');
context.set('at', now);
const n = k => typeof v[k] === 'number' && isFinite(v[k]) ? v[k] : null;
const volts = n('battery.voltage'), batI = n('battery.current'), solarI = n('solar.current');
if (!last || volts === null || batI === null || solarI === null) return null;
const hours = Math.min(now - last, 60000) / 3600000;

const solarW = Math.max(0, (solarI + (n('solar.loadCurrent') || 0)) * volts);
const chargerW = Math.max(0, (n('charger.current') || 0) * volts);
const batteryW = batI * volts;
const dcW = solarW + chargerW - batteryW;
const shore = flow.get('shore') || {};

const days = flow.get('energy') || {};
const key = dayKey(now);
const d = days[key] || (days[key] = { solarWh: 0, chargerWh: 0, alternatorWh: 0, consumptionWh: 0, batteryInWh: 0,
                                      batteryOutWh: 0, shoreWh: 0, hours: 0, socStart: n('battery.soc') });
d.solarWh += solarW * hours;
d.chargerWh += chargerW * hours;
if (dcW < -ALTERNATOR_MIN_W) d.alternatorWh += -dcW * hours;
else d.consumptionWh += Math.max(0, dcW) * hours;
if (batteryW > 0) d.batteryInWh += batteryW * hours;
else d.batteryOutWh += -batteryW * hours;
if (shore.connected === true && typeof shore.power === 'number') d.shoreWh += Math.max(0, shore.power) * hours;
d.hours += hours;
if (d.socStart === null) d.socStart = n('battery.soc');
d.socEnd = n('battery.soc');

for (const k of Object.keys(days).sort().slice(0, -ENERGY_KEEP_DAYS)) delete days[k];
flow.set('energy', days);
if (now - (context.get('savedAt') || 0) >= ENERGY_SAVE_MS) {
    fs.writeFileSync(ENERGY_FILE, JSON.stringify(days), { mode: 0o600 });
    context.set('savedAt', now);
}
node.status({ fill: 'green', shape: 'dot', text: 'today in ' + Math.round(d.solarWh + d.chargerWh + d.alternatorWh)
              + ' Wh, out ' + Math.round(d.consumptionWh) + ' Wh' });
return null;"""

ENERGY_GET = DAY_KEY + """// GET /api/camper/energy?days=<n> -> camper.energy.v1, the last n days (default 14) oldest first, today included.
""" + AUTH + """
const count = Math.min(Math.max(parseInt(msg.req.query.days, 10) || 14, 1), 366);
const days = flow.get('energy') || {};
const list = Object.keys(days).sort().slice(-count).map(k => energyDay(k, days[k]));
msg.statusCode = 200;
msg.payload = { schema: 'camper.energy.v1', days: list };
node.status({ fill: 'green', shape: 'dot', text: list.length + ' days · ' + new Date().toLocaleTimeString() });
return msg;"""

ACTIVE_ALARMS = """// Alarm codes active now (flow context "alarmState", kept up to date by "Collect values", "Shore power" and
// "Fridge 12 V").
function activeAlarms(now) {
    const alarm = flow.get('alarmState') || {};
    const alarms = [];
    if (alarm.socLow) alarms.push('soc_low');
    if (alarm.fridgeWarmSince && now - alarm.fridgeWarmSince >= FRIDGE_MS) alarms.push('fridge_warm');
    if (alarm.shoreLostAt && now - alarm.shoreLostAt < SHORE_LOST_MS) alarms.push('shore_lost');
    if (alarm.fridgeOffSince && now - alarm.fridgeOffSince >= FRIDGE_OFF_MS) alarms.push('fridge_off');
    if (alarm.awayMovedAt) alarms.push('away_moved');
    if (alarm.awayDoorAt) alarms.push('away_door');
    return alarms;
}
"""

STATUS = ALARM_CONSTANTS + ACTIVE_ALARMS + DAY_KEY + """// Campsite counter (same rule as in "Shore power (Shelly)").
function sessionKWh(session) {
    if (!session || typeof session.lastKWh !== 'number' || typeof session.startKWh !== 'number') return session ? session.accumulatedKWh || 0 : null;
    return (session.accumulatedKWh || 0) + session.lastKWh - session.startKWh;
}
""" + """// GET /api/camper/status -> camper.status.v1 (Bordnetz docs/PLAN.md, 3.1).
""" + AUTH + """

const v = flow.get('values') || {};
const shore = flow.get('shore') || {};
const num = (key, digits) => {
    const x = v[key];
    if (typeof x !== 'number' || !isFinite(x)) return null;
    return digits === undefined ? x : +x.toFixed(digits);
};
// Victron charge states (solarcharger and charger /State).
const STATES = { 0: 'off', 1: 'low power', 2: 'fault', 3: 'bulk', 4: 'absorption', 5: 'float', 6: 'storage',
                 7: 'equalize', 8: 'passthru', 9: 'inverting', 10: 'power assist', 11: 'power supply',
                 245: 'starting', 246: 'repeated absorption', 247: 'equalize', 248: 'battery safe',
                 252: 'external control' };
const state = key => (typeof v[key] === 'number') ? (STATES[v[key]] || ('state ' + v[key])) : null;
const ttgRaw = num('battery.timeToGo');   // seconds on D-Bus; TIME_TO_GO_MAX_S while charging or idle
const ttg = ttgRaw === null || ttgRaw >= TIME_TO_GO_MAX_S ? null : ttgRaw;

// DC consumption = what solar and charger deliver minus what goes into the battery, all as DC currents at the
// battery voltage (solar.power is on the PV side). The MPPT's battery current does not include its load output,
// whose loads count as consumption too. Negative: an unmeasured source charges, i.e. the alternator through the
// DC-DC converter. Charger and load output count as 0 while missing.
const batI = num('battery.current'), batV = num('battery.voltage'), solarI = num('solar.current');
const dcPower = batI === null || batV === null || solarI === null ? null
    : Math.round((solarI + (num('solar.loadCurrent') || 0) + (num('charger.current') || 0) - batI) * batV);

const alarms = activeAlarms(Date.now());
// BMS alarms (warning or alarm), e.g. "CellImbalance" -> "bms_cell_imbalance".
for (const key of Object.keys(v)) {
    if (key.startsWith('bms.') && typeof v[key] === 'number' && v[key] > 0) {
        alarms.push('bms_' + key.slice(4).replace(/([a-z])([A-Z])/g, '$1_$2').toLowerCase());
    }
}
// Digital input states: 6 open, 7 closed (Victron "Door alarm" type).
const door = v['door.state'] === 6 ? true : v['door.state'] === 7 ? false : null;
const speed = num('gps.speed');   // m/s
const hasFix = v['gps.fix'] === 1;
const away = flow.get('away') || {};
const orNull = x => x === undefined ? null : x;
// "com.victronenergy.battery/100" -> 100; null when the service is not set.
const instance = name => { const m = /\\/(\\d+)$/.exec(env.get(name) || ''); return m ? Number(m[1]) : null; };
const energy = flow.get('energy') || {};
const today = dayKey(Date.now()), yesterday = dayKey(Date.now() - 86400000);

msg.statusCode = 200;
msg.payload = {
    schema: 'camper.status.v1',
    ts: new Date().toISOString(),
    battery: {
        soc: num('battery.soc', 1),
        voltage: num('battery.voltage', 2),
        current: num('battery.current', 1),
        power: num('battery.power', 0),
        timeToGoMin: ttg === null ? null : Math.round(ttg / 60),
        cellMinV: num('battery.cellMin', 3),
        cellMaxV: num('battery.cellMax', 3)
    },
    solar: {
        power: num('solar.power', 0),
        pvVoltage: num('solar.pvVoltage', 1),
        state: state('solar.state'),
        yieldTodayKWh: num('solar.yieldToday', 2)
    },
    shore: {
        connected: typeof shore.connected === 'boolean' ? shore.connected : null,
        power: typeof shore.power === 'number' ? +shore.power.toFixed(1) : null,
        voltage: typeof shore.voltage === 'number' ? +shore.voltage.toFixed(1) : null,
        energyKWh: typeof shore.energyKWh === 'number' ? +shore.energyKWh.toFixed(2) : null,
        sessionKWh: sessionKWh(flow.get('shoreSession')) === null ? null : +sessionKWh(flow.get('shoreSession')).toFixed(2),
        sessionSince: (flow.get('shoreSession') || {}).since || null
    },
    charger: { state: state('charger.state'), current: num('charger.current', 1) },
    dc: { power: dcPower, alternator: dcPower === null ? null : dcPower < -ALTERNATOR_MIN_W },
    temps: {
        aussen: num('temps.aussen', 1),
        innen: num('temps.innen', 1),
        kuehlschrank: num('temps.kuehlschrank', 1)
    },
    humidity: {
        aussen: num('humidity.aussen', 0),
        innen: num('humidity.innen', 0),
        kuehlschrank: num('humidity.kuehlschrank', 0)
    },
    fridge: { on12V: typeof v['fridge.on12V'] === 'boolean' ? v['fridge.on12V'] : null },
    door: { open: door },
    location: hasFix ? {
        latitude: num('gps.latitude', 5),
        longitude: num('gps.longitude', 5),
        speedKmh: speed === null ? null : +(speed * 3.6).toFixed(1)
    } : null,
    away: away.active ? { active: true, since: orNull(away.since), latitude: orNull(away.latitude),
                          longitude: orNull(away.longitude), radius: orNull(away.radius) }
                      : { active: false, since: null, latitude: null, longitude: null, radius: null },
    energy: { today: energyDay(today, energy[today]), yesterday: energyDay(yesterday, energy[yesterday]) },
    alarms: alarms,
    // The app colours SOC, fridge and cell spread at the same limits as the alarms.
    limits: { socLow: SOC_LOW, fridgeMax: FRIDGE_MAX, cellSpreadMax: CELL_SPREAD_MAX },
    // Which devices are set up, as VRM instances: without the GX the app reads the same ones from VRM.
    devices: {
        shore: !!env.get('SHORE_SHELLY_TOPIC'),
        battery: instance('BATTERY_SERVICE'), solar: instance('SOLAR_SERVICE'), charger: instance('CHARGER_SERVICE'),
        tempOutside: instance('TEMP_OUTSIDE_SERVICE'), tempInside: instance('TEMP_INSIDE_SERVICE'),
        tempFridge: instance('TEMP_FRIDGE_SERVICE'), door: instance('DOOR_SERVICE')
    }
};
node.status({ fill: 'green', shape: 'dot', text: '200 · ' + new Date().toLocaleTimeString() });
return msg;"""


SHORE_RESET = SHORE_SESSION + """// POST /api/camper/shore/reset from the app: the campsite counter starts at 0 now.
""" + AUTH + """
const old = flow.get('shoreSession') || {};
const session = { since: new Date().toISOString(), startKWh: typeof old.lastKWh === 'number' ? old.lastKWh : null,
                  lastKWh: typeof old.lastKWh === 'number' ? old.lastKWh : undefined, accumulatedKWh: 0 };
saveSession(session);
node.status({ fill: 'green', shape: 'dot', text: 'reset · ' + new Date().toLocaleString() });
msg.statusCode = 200;
msg.payload = { sessionKWh: 0, sessionSince: session.since };
return msg;"""

GPS_FILES = """const TRACK_FILE = path.join(os.homedir(), '.node-red', 'bordnetz-track.json');
const AWAY_FILE = path.join(os.homedir(), '.node-red', 'bordnetz-away.json');
function saveJSON(file, value) { fs.writeFileSync(file, JSON.stringify(value), { mode: 0o600 }); }
function loadJSON(file, fallback) {
    try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch (e) { return fallback; }
}
// Great-circle distance in metres (haversine), points as {lat, lon}.
function distanceM(a, b) {
    const rad = Math.PI / 180, dLat = (b.lat - a.lat) * rad, dLon = (b.lon - a.lon) * rad;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
    return 2 * 6371000 * Math.asin(Math.min(1, Math.sqrt(h)));
}
"""

LOAD_GPS = GPS_FILES + """// On start: track and away mode from their files into flow context.
flow.set('track', loadJSON(TRACK_FILE, []));
flow.set('away', loadJSON(AWAY_FILE, { active: false }));
node.status({ text: flow.get('track').length + ' points' + (flow.get('away').active ? ' · away' : '') });"""

TRACK = ALARM_CONSTANTS + GPS_FILES + f"""const TRACK_MIN_MOVE_M = {TRACK_MIN_MOVE_M}, TRACK_PARKED_MS = {TRACK_PARKED_MINUTES} * 60000;
const TRACK_KEEP_MS = {TRACK_DAYS} * 86400000, TRACK_SAVE_MS = {TRACK_SAVE_SECONDS} * 1000;
""" + """// Every few seconds: records the GPS track ([unix seconds, latitude, longitude, km/h]) and checks away mode.
const v = flow.get('values') || {};
const now = Date.now();
const track = flow.get('track') || [];
const away = flow.get('away') || { active: false };
if (v['gps.fix'] !== 1 || typeof v['gps.latitude'] !== 'number' || typeof v['gps.longitude'] !== 'number') {
    node.status({ fill: 'grey', shape: 'ring', text: 'no fix · ' + track.length + ' points' });
    return null;
}
const here = { lat: v['gps.latitude'], lon: v['gps.longitude'] };
const kmh = typeof v['gps.speed'] === 'number' ? v['gps.speed'] * 3.6 : null;
const driving = kmh !== null && kmh >= DRIVING_KMH;

// While driving every tick, plus the point where the bus stopped; parked only when moved or as a heartbeat.
const last = track[track.length - 1];
const lastDriving = !!last && last[3] !== null && last[3] >= DRIVING_KMH;
if (!last || driving || lastDriving || distanceM(here, { lat: last[1], lon: last[2] }) >= TRACK_MIN_MOVE_M
    || now - last[0] * 1000 >= TRACK_PARKED_MS) {
    track.push([Math.round(now / 1000), +here.lat.toFixed(5), +here.lon.toFixed(5), kmh === null ? null : Math.round(kmh)]);
    while (track.length && track[0][0] * 1000 < now - TRACK_KEEP_MS) track.shift();
    flow.set('track', track);
    if (!driving || now - (context.get('savedAt') || 0) >= TRACK_SAVE_MS) {
        saveJSON(TRACK_FILE, track);
        context.set('savedAt', now);
    }
}

// Away mode armed without a fix: the first fix is the place to watch.
if (away.active && typeof away.latitude !== 'number') {
    away.latitude = +here.lat.toFixed(5);
    away.longitude = +here.lon.toFixed(5);
    flow.set('away', away);
    saveJSON(AWAY_FILE, away);
}
if (away.active && distanceM(here, { lat: away.latitude, lon: away.longitude }) > away.radius) {
    const alarm = flow.get('alarmState') || {};
    if (!alarm.awayMovedAt) { alarm.awayMovedAt = now; flow.set('alarmState', alarm); }
}
node.status({ fill: driving ? 'green' : 'blue', shape: 'dot',
              text: (driving ? Math.round(kmh) + ' km/h' : 'parked') + ' · ' + track.length + ' points' + (away.active ? ' · away' : '') });
return null;"""

TRACK_GET = """// GET /api/camper/track?since=<unix seconds> -> camper.track.v1, default the last 7 days.
""" + AUTH + """
const since = Number(msg.req.query.since) || Math.round(Date.now() / 1000) - 7 * 86400;
const points = (flow.get('track') || []).filter(p => p[0] >= since);
msg.statusCode = 200;
msg.payload = { schema: 'camper.track.v1', points: points };
node.status({ fill: 'green', shape: 'dot', text: points.length + ' points · ' + new Date().toLocaleTimeString() });
return msg;"""

AWAY = ALARM_CONSTANTS + GPS_FILES + """// POST /api/camper/away {active: true|false} from the app. On: watches the current place (or the next fix) and
// the door. Switching it (also on again) clears away_moved and away_door.
""" + AUTH + """
const b = msg.payload || {};
if (typeof b.active !== 'boolean') {
    msg.statusCode = 400;
    msg.payload = { error: 'expected {active: boolean}' };
    return msg;
}
const v = flow.get('values') || {};
const fix = v['gps.fix'] === 1 && typeof v['gps.latitude'] === 'number' && typeof v['gps.longitude'] === 'number';
const away = !b.active ? { active: false } : {
    active: true,
    since: new Date().toISOString(),
    latitude: fix ? +v['gps.latitude'].toFixed(5) : null,
    longitude: fix ? +v['gps.longitude'].toFixed(5) : null,
    radius: AWAY_RADIUS_M
};
flow.set('away', away);
saveJSON(AWAY_FILE, away);
const alarm = flow.get('alarmState') || {};
alarm.awayMovedAt = null;
alarm.awayDoorAt = null;
flow.set('alarmState', alarm);
node.status({ fill: away.active ? 'yellow' : 'grey', shape: 'dot', text: (away.active ? 'away' : 'off') + ' · ' + new Date().toLocaleTimeString() });
msg.statusCode = 200;
msg.payload = away;
return msg;"""


def build():
    nodes = []
    collect_id = "ios_collect"
    for i, (key, service, path) in enumerate(INPUTS):
        nodes.append({
            "id": f"ios_in_{i:02d}", "type": "victron-input-custom", "z": TAB, "name": key,
            "service": service, "path": path,
            "serviceObj": {"service": service, "name": service.strip("${}")},
            "pathObj": {"path": path, "name": path, "type": "number"},
            # No "roundValues": an empty string counts as 0 digits in the Victron nodes; rounding happens in the API.
            "onlyChanges": False,
            # The first 16 inputs keep their place; later ones go below the other nodes.
            "x": 170, "y": 60 + i * 40 if i < 16 else 1060 + (i - 16) * 40, "wires": [[collect_id]],
        })
    nodes.append({"id": collect_id, "type": "function", "z": TAB, "name": "Collect values", "func": COLLECT,
                  "outputs": 0, "timeout": 0, "noerr": 0, "initialize": "", "finalize": "", "libs": [],
                  "x": 460, "y": 320, "wires": []})

    # Shore Shelly: subscribed at start when SHORE_SHELLY_TOPIC is set (dynamic subscription, so an empty setting
    # subscribes to nothing). Auto-detect parses the status as JSON and "true"/"false" from <topic>/online.
    shore_id = "ios_shore"
    nodes += [
        {"id": "ios_shore_start", "type": "inject", "z": TAB, "name": "on start", "props": [], "repeat": "",
         "crontab": "", "once": True, "onceDelay": "2", "x": 150, "y": 660, "wires": [["ios_shore_subscribe"]]},
        {"id": "ios_shore_subscribe", "type": "function", "z": TAB, "name": "Subscribe Shelly", "func": SHELLY_SUBSCRIBE,
         "outputs": 1, "timeout": 0, "noerr": 0, "initialize": "", "finalize": "", "libs": [],
         "x": 320, "y": 660, "wires": [["ios_mq"]]},
        {"id": "ios_mq", "type": "mqtt in", "z": TAB, "name": "Shore Shelly", "topic": "", "qos": "1",
         "datatype": "auto-detect", "broker": BROKER, "nl": False, "rap": True, "rh": 0, "inputs": 1,
         "x": 330, "y": 700, "wires": [[shore_id]]},
        {"id": shore_id, "type": "function", "z": TAB, "name": "Shore power (Shelly)", "func": SHORE,
         "outputs": 0, "timeout": 0, "noerr": 0, "initialize": LOAD_SHORE, "finalize": "",
         "libs": [{"var": "fs", "module": "fs"}, {"var": "os", "module": "os"}, {"var": "path", "module": "path"}],
         "x": 540, "y": 700, "wires": []},
    ]

    nodes += [
        {"id": "ios_fridge_tick", "type": "inject", "z": TAB, "name": f"every {FRIDGE_POLL_SECONDS} s",
         "props": [], "repeat": str(FRIDGE_POLL_SECONDS), "crontab": "", "once": True, "onceDelay": "5",
         "x": 170, "y": 740, "wires": [["ios_fridge_url"]]},
        {"id": "ios_fridge_url", "type": "function", "z": TAB, "name": "FRIDGE_12V_URL", "func": FRIDGE_URL,
         "outputs": 1, "timeout": 0, "noerr": 0, "initialize": "", "finalize": "", "libs": [],
         "x": 340, "y": 740, "wires": [["ios_fridge_get"]]},
        {"id": "ios_fridge_get", "type": "http request", "z": TAB, "name": "Fridge 12 V Shelly", "method": "GET",
         "ret": "obj", "paytoqs": "ignore", "url": "", "tls": "", "persist": False, "proxy": "",
         "insecureHTTPParser": False, "authType": "", "senderr": False, "headers": [],
         "x": 530, "y": 740, "wires": [["ios_fridge"]]},
        {"id": "ios_fridge", "type": "function", "z": TAB, "name": "Fridge 12 V", "func": FRIDGE_12V,
         "outputs": 0, "timeout": 0, "noerr": 0, "initialize": "", "finalize": "", "libs": [],
         "x": 720, "y": 740, "wires": []},
    ]

    libs_fs = [{"var": "fs", "module": "fs"}, {"var": "os", "module": "os"}, {"var": "path", "module": "path"}]
    nodes += [
        {"id": "ios_shore_reset_in", "type": "http in", "z": TAB, "name": "POST /api/camper/shore/reset",
         "url": "/api/camper/shore/reset", "method": "post", "upload": False, "swaggerDoc": "",
         "x": 910, "y": 1060, "wires": [["ios_shore_reset"]]},
        {"id": "ios_shore_reset", "type": "function", "z": TAB, "name": "Reset campsite energy", "func": SHORE_RESET,
         "outputs": 1, "timeout": 0, "noerr": 0, "initialize": "", "finalize": "", "libs": libs_fs,
         "x": 1150, "y": 1060, "wires": [["ios_shore_reset_out"]]},
        {"id": "ios_shore_reset_out", "type": "http response", "z": TAB, "name": "", "statusCode": "", "headers": {},
         "x": 1390, "y": 1060, "wires": []},
    ]
    nodes += [
        {"id": "ios_energy_tick", "type": "inject", "z": TAB, "name": f"every {ENERGY_SECONDS} s",
         "props": [], "repeat": str(ENERGY_SECONDS), "crontab": "", "once": True, "onceDelay": "10",
         "x": 900, "y": 940, "wires": [["ios_energy"]]},
        {"id": "ios_energy", "type": "function", "z": TAB, "name": "Energy per day", "func": ENERGY,
         "outputs": 0, "timeout": 0, "noerr": 0, "initialize": LOAD_ENERGY, "finalize": "", "libs": libs_fs,
         "x": 1110, "y": 940, "wires": []},
        {"id": "ios_energy_in", "type": "http in", "z": TAB, "name": "GET /api/camper/energy",
         "url": "/api/camper/energy", "method": "get", "upload": False, "swaggerDoc": "",
         "x": 900, "y": 1000, "wires": [["ios_energy_get"]]},
        {"id": "ios_energy_get", "type": "function", "z": TAB, "name": "Token check + camper.energy.v1",
         "func": ENERGY_GET, "outputs": 1, "timeout": 0, "noerr": 0, "initialize": "", "finalize": "", "libs": [],
         "x": 1150, "y": 1000, "wires": [["ios_energy_out"]]},
        {"id": "ios_energy_out", "type": "http response", "z": TAB, "name": "", "statusCode": "", "headers": {},
         "x": 1390, "y": 1000, "wires": []},
    ]
    nodes += [
        {"id": "ios_track_tick", "type": "inject", "z": TAB, "name": f"every {TRACK_SECONDS} s",
         "props": [], "repeat": str(TRACK_SECONDS), "crontab": "", "once": True, "onceDelay": "15",
         "x": 900, "y": 740, "wires": [["ios_track"]]},
        {"id": "ios_track", "type": "function", "z": TAB, "name": "GPS track + away", "func": TRACK,
         "outputs": 0, "timeout": 0, "noerr": 0, "initialize": LOAD_GPS, "finalize": "", "libs": libs_fs,
         "x": 1110, "y": 740, "wires": []},
        {"id": "ios_track_in", "type": "http in", "z": TAB, "name": "GET /api/camper/track",
         "url": "/api/camper/track", "method": "get", "upload": False, "swaggerDoc": "",
         "x": 900, "y": 800, "wires": [["ios_track_get"]]},
        {"id": "ios_track_get", "type": "function", "z": TAB, "name": "Token check + camper.track.v1", "func": TRACK_GET,
         "outputs": 1, "timeout": 0, "noerr": 0, "initialize": "", "finalize": "", "libs": [],
         "x": 1150, "y": 800, "wires": [["ios_track_out"]]},
        {"id": "ios_track_out", "type": "http response", "z": TAB, "name": "", "statusCode": "", "headers": {},
         "x": 1390, "y": 800, "wires": []},
        {"id": "ios_away_in", "type": "http in", "z": TAB, "name": "POST /api/camper/away",
         "url": "/api/camper/away", "method": "post", "upload": False, "swaggerDoc": "",
         "x": 900, "y": 860, "wires": [["ios_away"]]},
        {"id": "ios_away", "type": "function", "z": TAB, "name": "Away mode", "func": AWAY,
         "outputs": 1, "timeout": 0, "noerr": 0, "initialize": "", "finalize": "", "libs": libs_fs,
         "x": 1110, "y": 860, "wires": [["ios_away_out"]]},
        {"id": "ios_away_out", "type": "http response", "z": TAB, "name": "", "statusCode": "", "headers": {},
         "x": 1390, "y": 860, "wires": []},
    ]
    nodes += [
        {"id": "ios_http_in", "type": "http in", "z": TAB, "name": "GET /api/camper/status",
         "url": "/api/camper/status", "method": "get", "upload": False, "swaggerDoc": "",
         "x": 180, "y": 780, "wires": [["ios_status"]]},
        {"id": "ios_status", "type": "function", "z": TAB, "name": "Token check + camper.status.v1",
         "func": STATUS, "outputs": 1, "timeout": 0, "noerr": 0, "initialize": "", "finalize": "", "libs": [],
         "x": 460, "y": 780, "wires": [["ios_http_out"]]},
        {"id": "ios_http_out", "type": "http response", "z": TAB, "name": "", "statusCode": "", "headers": {},
         "x": 710, "y": 780, "wires": []},
        {"id": "ios_comment", "type": "comment", "z": TAB, "name": f"Bordnetz API ({REPO})",
         "info": f"Generated by build_flow.py in {REPO}; change it there, not here.\n\n"
                 "Settings: tab environment variables (double-click the tab). "
                 "GET /api/camper/status, GET /api/camper/energy?days=<n>, POST /api/camper/shore/reset, "
                 "GET /api/camper/track?since=<unix s>, POST /api/camper/away {active}, "
                 "all with `Authorization: Bearer <IOS_API_TOKEN>`.",
         "x": 520, "y": 40, "wires": []},
    ]
    tab = {"id": TAB, "type": "tab", "label": TAB_LABEL, "disabled": False,
           "info": f"Bordnetz: the API for the Bordnetz iPhone app. Setup: {REPO}",
           "env": [{"name": name, "type": kind, "value": value} for name, kind, value in SETTINGS]}
    broker = {"id": BROKER, "type": "mqtt-broker", "name": "GX broker (Bordnetz)", "broker": "localhost", "port": "1883",
              "clientid": "", "autoConnect": True, "usetls": False, "protocolVersion": "4", "keepalive": "60",
              "cleansession": True, "autoUnsubscribe": True, "birthTopic": "", "closeTopic": "", "willTopic": "",
              "userProps": "", "sessionExpiry": ""}
    # Node-RED's import format: one flat list.
    return [tab] + nodes + [broker]


if __name__ == "__main__":
    out = pathlib.Path(__file__).with_name("bordnetz-flow.json")
    out.write_text(json.dumps(build(), indent=2, ensure_ascii=False) + "\n")
    print(out)
