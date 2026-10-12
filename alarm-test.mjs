// Simulates the alarm logic of the iOS API tab with a mocked Node-RED context. Run from the repo root:
//   python3 build_flow.py && node alarm-test.mjs
import { readFileSync } from 'fs';
const flowJson = JSON.parse(readFileSync('bordnetz-flow.json'));
const fn = name => flowJson.find(n => n.name === name).func;
const store = {}; let clock = 1_000_000;
Date.now = () => clock;
const flow = { get: k => store[k], set: (k, v) => { store[k] = v; } };
const ctx = {}; const context = { get: k => ctx[k], set: (k, v) => { ctx[k] = v; } };
const node = { status() {}, send() {} };
const envVars = {};
const env = { get: k => k in envVars ? envVars[k] : 'tok' };
// fs/os/path are mocked: files live in `files`.
const files = {};
const fs = { readFileSync: f => { if (!(f in files)) throw new Error('ENOENT'); return files[f]; },
             writeFileSync: (f, data) => { files[f] = data; } };
const os = { homedir: () => '/data/home/nodered' };
const path = { join: (...parts) => parts.join('/') };
const run = (name, msg) => new Function('msg', 'flow', 'node', 'context', 'env', 'fs', 'os', 'path', fn(name))(msg, flow, node, context, env, fs, os, path);
const collect = (topic, payload) => run('Collect values', { topic, payload });
const shelly = (topic, payload) => run('Shore power (Shelly)', { topic, payload });
const alarms = () => run('Token check + camper.status.v1', { req: { headers: { authorization: 'Bearer tok' } } }).payload.alarms;
const check = (label, want) => { const got = JSON.stringify(alarms()); console.log(got === JSON.stringify(want) ? 'ok  ' : 'FAIL', label, got); };
const min = 60000;

collect('battery.soc', 50); check('normal', []);
collect('battery.soc', 19); check('SOC 19', ['soc_low']);
collect('battery.soc', 21); check('SOC 21 (Hysterese)', ['soc_low']);
collect('battery.soc', 22); check('SOC 22', []);
collect('temps.kuehlschrank', 13); clock += 60 * min; check('Kühlschrank 13 °C seit 60 min (Deckel, warmer Tag)', []);
collect('temps.kuehlschrank', 15); clock += 20 * min; check('Kühlschrank 15 °C seit 20 min', []);
clock += 11 * min; check('… seit 31 min', ['fridge_warm']);
collect('temps.kuehlschrank', 7); check('Kühlschrank 7 °C', []);
shelly('x/status/switch:0', { apower: 30, voltage: 230 });
collect('charger.state', 6); clock += 2 * min; collect('charger.state', 0); clock += 3 * min;
shelly('x/online', 'false'); check('Landstrom weg, lud vor 3 min', ['shore_lost']);
clock += 61 * min; check('… nach 61 min', []);
shelly('x/status/switch:0', { apower: 30, voltage: 230 }); clock += 30 * min;
shelly('x/online', 'false'); check('Landstrom weg, lud seit 30+ min nicht', []);
shelly('x/status/switch:0', { apower: 30, voltage: 230 }); collect('charger.state', 4);
shelly('x/online', 'false'); check('Landstrom weg während Laden', ['shore_lost']);
shelly('x/status/switch:0', { apower: 30, voltage: 230 }); check('Landstrom zurück', []);

// DC consumption (field "dc"): solar + load output + charger - battery, at the battery voltage.
const dc = () => run('Token check + camper.status.v1', { req: { headers: { authorization: 'Bearer tok' } } }).payload.dc;
const checkDc = (label, want) => { const got = JSON.stringify(dc()); console.log(got === JSON.stringify(want) ? 'ok  ' : 'FAIL', label, got); };
checkDc('DC ohne Werte', { power: null, alternator: null });
collect('battery.voltage', 13.3); collect('battery.current', 4); collect('solar.current', 3.8);
collect('solar.loadCurrent', 2.2); collect('charger.current', 0);
checkDc('DC Verbrauch 2 A', { power: 27, alternator: false });
collect('battery.current', 15); collect('solar.loadCurrent', null);
checkDc('Lichtmaschine lädt', { power: -149, alternator: true });
collect('battery.current', 3.8);
checkDc('Rauschen um 0', { power: 0, alternator: false });

// Fields added 2026-10-11: BMS alarms, door, GPS, humidity, fridge 12 V.
const status = () => run('Token check + camper.status.v1', { req: { headers: { authorization: 'Bearer tok' } } }).payload;
const checkField = (label, got, want) => { got = JSON.stringify(got); want = JSON.stringify(want); console.log(got === want ? 'ok  ' : 'FAIL', label, got); };
collect('bms.CellImbalance', 1); collect('bms.LowTemperature', 0);
checkField('BMS-Alarm Zellen', status().alarms, ['bms_cell_imbalance']);
collect('bms.CellImbalance', 0); checkField('BMS ok', status().alarms, []);
checkField('Tür unbekannt', status().door, { open: null });
collect('door.state', 6); checkField('Tür offen', status().door, { open: true });
collect('door.state', 7); checkField('Tür zu', status().door, { open: false });
checkField('GPS ohne Fix', status().location, null);
collect('gps.fix', 1); collect('gps.latitude', 47.710880279); collect('gps.longitude', 7.546285152); collect('gps.speed', 25);
checkField('GPS', status().location, { latitude: 47.71088, longitude: 7.54629, speedKmh: 90 });
collect('humidity.innen', 69.04); checkField('Feuchte', status().humidity, { aussen: null, innen: 69, kuehlschrank: null });
collect('battery.cellMin', 3.4612); collect('battery.cellMax', 3.468);
checkField('Zellen', [status().battery.cellMinV, status().battery.cellMaxV], [3.461, 3.468]);
const fridge = payload => run('Fridge 12 V', { payload });
checkField('Kühlschrank 12 V unbekannt', status().fridge, { on12V: null });
fridge({ id: 0, output: true }); checkField('Kühlschrank 12 V an', status().fridge, { on12V: true });
fridge('Error: connect EHOSTUNREACH'); checkField('Shelly weg', status().fridge, { on12V: null });
fridge({ id: 0, output: false }); clock += 10 * min; fridge({ id: 0, output: false });
checkField('12 V aus mit Landstrom', status().alarms, []);
shelly('x/online', 'false'); fridge({ id: 0, output: false }); clock += 4 * min; fridge({ id: 0, output: false });
checkField('12 V aus ohne Landstrom, 4 min', status().alarms, []);
clock += 1 * min; fridge({ id: 0, output: false }); checkField('… 5 min', status().alarms, ['fridge_off']);
fridge('Error: connect EHOSTUNREACH'); checkField('Shelly weg: kein Alarm', status().alarms, []);
fridge({ id: 0, output: false }); clock += 5 * min; fridge({ id: 0, output: false });
shelly('x/status/switch:0', { apower: 30, voltage: 230 }); fridge({ id: 0, output: false });
checkField('Landstrom zurück', status().alarms, []);
fridge({ id: 0, output: true }); shelly('x/online', 'false'); fridge({ id: 0, output: true }); clock += 10 * min;
checkField('12 V an ohne Landstrom', status().alarms, []);
checkField('Grenzen', status().limits, { socLow: 20, fridgeMax: 14, cellSpreadMax: 0.1 });
Object.assign(envVars, { BATTERY_SERVICE: 'com.victronenergy.battery/100', TEMP_FRIDGE_SERVICE: 'com.victronenergy.temperature/21',
                        SOLAR_SERVICE: '', CHARGER_SERVICE: '', TEMP_OUTSIDE_SERVICE: '', TEMP_INSIDE_SERVICE: '', DOOR_SERVICE: '',
                        SHORE_SHELLY_TOPIC: '' });
checkField('Geräte', status().devices, { shore: false, battery: 100, solar: null, charger: null, tempOutside: null,
                                         tempInside: null, tempFridge: 21, door: null });
for (const k of Object.keys(envVars)) delete envVars[k];

// GPS track and away mode (2026-10-11).
const flowNode = id => flowJson.find(n => n.id === id);
const runLib = (code, msg) => new Function('msg', 'flow', 'node', 'context', 'env', 'fs', 'os', 'path', code)(msg, flow, node, context, env, fs, os, path);
const tick = () => runLib(flowNode('ios_track').func, {});
const setAway = active => runLib(flowNode('ios_away').func, { req: { headers: { authorization: 'Bearer tok' } }, payload: { active } });
const trackSince = since => runLib(flowNode('ios_track_get').func, { req: { headers: { authorization: 'Bearer tok' }, query: { since } } }).payload;
const sec = 1000;
runLib(flowNode('ios_track').initialize, {});
checkField('Spur leer nach Start', store.track, []);
collect('gps.speed', 0); collect('gps.latitude', 47.71088); collect('gps.longitude', 7.54629);
tick(); tick(); clock += 10 * sec; tick();
checkField('Parkiert: ein Punkt', store.track.length, 1);
const t0 = clock;
for (let i = 1; i <= 6; i++) { clock += 10 * sec; collect('gps.speed', 25); collect('gps.latitude', 47.71088 + i * 0.002); tick(); }
checkField('Fahrt: alle 10 s', store.track.length, 7);
clock += 10 * sec; collect('gps.speed', 0); tick(); clock += 10 * sec; tick();
checkField('Halt: ein Punkt, dann Ruhe', store.track.length, 8);
checkField('Punkt', store.track[7].slice(1), [47.72288, 7.54629, 0]);
checkField('Datei geschrieben', JSON.parse(files['/data/home/nodered/.node-red/bordnetz-track.json']).length, 8);
clock += 15 * min; tick(); checkField('Herzschlag nach 15 min', store.track.length, 9);
checkField('GET track since', trackSince(Math.round(t0 / 1000) + 25).points.length, 6);
checkField('GET track Schema', trackSince(undefined).schema, 'camper.track.v1');
checkField('Abwesend aus', status().away.active, false);
collect('door.state', 7);
setAway(true);
checkField('Abwesend an', [status().away.active, status().away.latitude, status().away.radius], [true, 47.72288, 200]);
collect('gps.latitude', 47.7238); tick(); checkField('100 m bewegt', status().alarms, []);
collect('door.state', 7); checkField('Tür bleibt zu', status().alarms, []);
collect('door.state', 6); checkField('Tür geht auf', status().alarms, ['away_door']);
collect('door.state', 7); checkField('Tür wieder zu: bleibt', status().alarms, ['away_door']);
collect('gps.latitude', 47.7260); tick(); checkField('300 m weg', status().alarms, ['away_moved', 'away_door']);
collect('gps.latitude', 47.72288); tick(); checkField('zurück: bleibt bis aus', status().alarms, ['away_moved', 'away_door']);
setAway(false); checkField('Abwesend aus: Alarme weg', [status().alarms, status().away.active], [[], false]);
collect('door.state', 6); checkField('Tür auf ohne Abwesend', status().alarms, []);
collect('gps.fix', 0); setAway(true); checkField('Abwesend ohne Fix', status().away.latitude, null);
collect('gps.fix', 1); tick(); checkField('erster Fix wird Mittelpunkt', status().away.latitude, 47.72288);
checkField('Abwesend in Datei', JSON.parse(files['/data/home/nodered/.node-red/bordnetz-away.json']).active, true);
setAway(false);
checkField('Abwesend 400', runLib(flowNode('ios_away').func, { req: { headers: { authorization: 'Bearer tok' } }, payload: {} }).statusCode, 400);
checkField('Spur 401', runLib(flowNode('ios_track_get').func, { req: { headers: {}, query: {} } }).statusCode, 401);

// Energy per day (2026-10-11): an hour at constant powers, 13 V.
const energyTick = () => runLib(flowNode('ios_energy').func, {});
const energyGet = days => runLib(flowNode('ios_energy_get').func, { req: { headers: { authorization: 'Bearer tok' }, query: { days } } }).payload;
runLib(flowNode('ios_energy').initialize, {});
clock = new Date(2026, 9, 11, 10, 0, 0).getTime();
for (const k of Object.keys(ctx)) delete ctx[k];
collect('battery.voltage', 13); collect('battery.soc', 60);
collect('solar.current', 10); collect('solar.loadCurrent', 0); collect('charger.current', 0); collect('battery.current', 8);
shelly('x/online', 'false');
energyTick();   // first tick only sets the start
for (let i = 0; i < 360; i++) { clock += 10 * sec; energyTick(); }
let today = status().energy.today;
checkField('1 h Solar 130 W, Verbrauch 26 W', [today.solarWh, today.consumptionWh, today.batteryInWh, today.alternatorWh, today.hours],
           [130, 26, 104, 0, 1]);
collect('solar.current', 0); collect('battery.current', 15);   // alternator 195 W into the battery
for (let i = 0; i < 360; i++) { clock += 10 * sec; energyTick(); }
today = status().energy.today;
checkField('1 h Lichtmaschine', [today.alternatorWh, today.consumptionWh, today.batteryInWh], [195, 26, 299]);
collect('battery.current', -2); collect('battery.soc', 58);
clock += 30 * min; energyTick();   // gap: at most 60 s count
today = status().energy.today;
checkField('Lücke zählt höchstens 60 s', [today.batteryOutWh, today.hours, today.socStart, today.socEnd], [0, 2.02, 60, 58]);
clock = new Date(2026, 9, 12, 0, 0, 5).getTime(); energyTick(); clock += 10 * sec; energyTick();
checkField('Tageswechsel', [status().energy.today.date, status().energy.yesterday.date, status().energy.today.batteryOutWh],
           ['2026-10-12', '2026-10-11', 1]);   // 26 W for 60 + 10 s
checkField('GET energy', energyGet('1').days.map(d => d.date), ['2026-10-12']);
checkField('GET energy Standard', [energyGet(undefined).schema, energyGet(undefined).days.length], ['camper.energy.v1', 2]);
checkField('Datei geschrieben', Object.keys(JSON.parse(files['/data/home/nodered/.node-red/bordnetz-energy.json'])), ['2026-10-11', '2026-10-12']);

// Campsite energy (2026-10-11): counts from the reset, survives the Shelly counting from 0 again.
const shoreOf = () => status().shore;
const resetShore = () => runLib(flowNode('ios_shore_reset').func, { req: { headers: { authorization: 'Bearer tok' } } });
delete store.shoreSession;
shelly('x/status/switch:0', { apower: 500, voltage: 230, aenergy: { total: 7080 } });
checkField('Platz: Start beim ersten Wert', shoreOf().sessionKWh, 0);
shelly('x/status/switch:0', { apower: 500, voltage: 230, aenergy: { total: 8580 } });
checkField('Platz: 1.5 kWh', [shoreOf().sessionKWh, shoreOf().energyKWh], [1.5, 8.58]);
shelly('x/online', 'false');
shelly('x/status/switch:0', { apower: 500, voltage: 230, aenergy: { total: 200 } });
checkField('Shelly zählt neu ab 0', shoreOf().sessionKWh, 1.7);
checkField('Reset Antwort', resetShore().payload.sessionKWh, 0);
checkField('nach Reset', shoreOf().sessionKWh, 0);
shelly('x/status/switch:0', { apower: 500, voltage: 230, aenergy: { total: 1200 } });
checkField('neuer Platz 1 kWh', shoreOf().sessionKWh, 1);
checkField('Datei', JSON.parse(files['/data/home/nodered/.node-red/bordnetz-shore-session.json']).startKWh, 0.2);
checkField('Reset 401', runLib(flowNode('ios_shore_reset').func, { req: { headers: {} } }).statusCode, 401);
runLib(flowNode('ios_shore').initialize, {}); checkField('nach Neustart geladen', typeof store.shoreSession.since, 'string');

for (const t of Object.values(ctx)) clearTimeout(t);
