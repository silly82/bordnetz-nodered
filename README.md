# Bordnetz Node-RED flow

English first, [Deutsch below](#deutsch).

The server side of **Bordnetz**, the iPhone app that shows a camper's energy system (battery, solar, shore power, temperatures, alarms, trips) in the app, in widgets and in CarPlay. This flow runs in Node-RED on the camper's Victron GX device (Cerbo GX, Ekrano GX, …), reads the values from the GX's D-Bus and serves them to the app through a small HTTPS API. Nothing goes through a cloud.

Bordnetz is an independent project and not affiliated with or endorsed by Victron Energy.

## Requirements

- A Victron GX device with **Venus OS Large** and **Node-RED** switched on (Settings → Integrations, or Settings → Venus OS Large features on older versions). The Victron nodes come with it.
- A battery with a BMS or battery monitor on the GX. Everything else is optional: solar charger, AC charger, temperature sensors, a door contact on a digital input, GPS, Shellys for shore power and the fridge's 12 V supply.
- The iPhone in the vehicle's network, or connected to it, e.g. through Tailscale or another VPN.

## Install

1. Open the Node-RED editor: `https://<GX address>:1881`.
2. Menu → **Import** → select [`bordnetz-flow.json`](bordnetz-flow.json) (or paste its contents) → Import. A new tab **Bordnetz API** appears.
3. Double-click the tab → **Environment variables**: set the token and your devices (next section).
4. **Deploy**.

To update: import the new `bordnetz-flow.json` and choose **Replace** when Node-RED asks. The environment variables stay with the tab.

## Configure

All settings are the tab's environment variables. Empty means "not fitted": that input stays silent, the API sends `null` and the app leaves the row out.

| Variable | Example | What it is |
|---|---|---|
| `IOS_API_TOKEN` | output of `openssl rand -hex 32` | The app's access token (type *credential*: stored encrypted). Without it every request is refused. |
| `BATTERY_SERVICE` | `com.victronenergy.battery/512` | BMS or battery monitor: SOC, voltage, current, time to go, cell voltages, BMS alarms |
| `SOLAR_SERVICE` | `com.victronenergy.solarcharger/279` | MPPT solar charger |
| `CHARGER_SERVICE` | `com.victronenergy.charger/288` | AC charger (e.g. Blue Smart IP22) |
| `TEMP_OUTSIDE_SERVICE` | `com.victronenergy.temperature/20` | Temperature (and humidity) outside |
| `TEMP_INSIDE_SERVICE` | `com.victronenergy.temperature/22` | … inside |
| `TEMP_FRIDGE_SERVICE` | `com.victronenergy.temperature/21` | … in the fridge |
| `DOOR_SERVICE` | `com.victronenergy.digitalinput/1` | Door contact; set the input's type to *Door alarm* on the GX |
| `GPS_SERVICE` | `com.victronenergy.gps/1` | Location, trip log, away mode |
| `SHORE_SHELLY_TOPIC` | `shelly1pm-08f9e0aabbcc` | MQTT topic prefix of a Shelly (Gen2 or later) behind the shore power inlet |
| `FRIDGE_12V_URL` | `http://192.168.1.50/rpc/Switch.GetStatus?id=0` | A Shelly switching the fridge's 12 V supply |
| `SOC_LOW` | `20` | Alarm below this state of charge (%) |
| `FRIDGE_MAX` | `14` | Alarm when the fridge stays above this for 30 min (°C); depends on where the sensor sits |

Changes take effect on the next deploy.

### Find your devices

A service name is `com.victronenergy.<type>/<VRM instance>`; the types are `battery`, `solarcharger`, `charger`, `temperature`, `digitalinput` and `gps`. The instance is on the GX under Settings → Device list → the device → Device → **VRM instance**. In Node-RED, the device list of a Victron *Custom* input node shows the same names.

### Shore power (Shelly)

A Shelly 1PM (Gen2 or later) behind the shore power inlet tells whether shore power is there, how much is drawn and the energy used on the current campsite (reset in the app). It is online only while shore power is connected, and a loss while charging raises an alarm.

- On the GX, allow MQTT access (Settings → Integrations or Services, depending on the version).
- On the Shelly: Settings → MQTT → enable, server `<GX address>:1883`, "Generic status update over MQTT" on. The topic prefix shown there (e.g. `shelly1pm-08f9e0aabbcc`) goes into `SHORE_SHELLY_TOPIC`.

### Fridge 12 V (Shelly)

If a Shelly switches the fridge's 12 V supply, `FRIDGE_12V_URL` points to its `Switch.GetStatus` (polled every 30 s). The alarm `fridge_off` assumes the fridge's 230 V side runs on shore power: 12 V off and no shore power for 5 minutes means the fridge has no supply.

## Connect the app

In Bordnetz → Settings:

- **Address:** `https://<GX address>:1881/api/camper/status`
- **Token:** the value of `IOS_API_TOKEN`
- **Certificate (recommended):** the SHA-256 fingerprint of the GX's certificate, so the app trusts this one and no other:
  `openssl s_client -connect <GX address>:1881 </dev/null 2>/dev/null | openssl x509 -noout -fingerprint -sha256`

Or on a Mac, make a setup QR code that fills in all three and scan it with the Camera app:

```sh
NODE_RED_IP=<GX address> IOS_API_TOKEN=<token> swift setup-qr.swift
```

`--vrm` adds a Victron VRM access token (`VRM_TOKEN`) for the history. The code holds the token: do not share a picture of it.

## Alarms

Codes in the status document; the app shows them in words.

| Code | When |
|---|---|
| `soc_low` | state of charge below `SOC_LOW`, cleared 2 % above it |
| `fridge_warm` | fridge above `FRIDGE_MAX` for 30 minutes |
| `fridge_off` | fridge 12 V off and no shore power for 5 minutes |
| `shore_lost` | the shore Shelly went offline while the charger was charging (breaker, campsite fuse); shown for an hour or until shore power is back |
| `away_moved` | away mode on and the vehicle more than 200 m away from where it was switched on |
| `away_door` | away mode on and the door opened |
| `bms_*` | alarms of the BMS, e.g. `bms_cell_imbalance` |

## API

All endpoints need `Authorization: Bearer <IOS_API_TOKEN>`.

| Endpoint | Answer |
|---|---|
| `GET /api/camper/status` | `camper.status.v1`: all values, alarms, away mode, energy today and yesterday |
| `GET /api/camper/energy?days=<n>` | `camper.energy.v1`: energy per day (solar, charger, alternator, consumption, battery, shore), default 14 days |
| `GET /api/camper/track?since=<unix s>` | `camper.track.v1`: GPS points `[time, latitude, longitude, km/h]`, default 7 days |
| `POST /api/camper/away` `{"active": true}` | switches away mode |
| `POST /api/camper/shore/reset` | starts the campsite energy at 0 |

The status also names the devices set up (`devices`: VRM instances, and whether a shore Shelly is set up). While the GX is out of reach the app reads the same devices from Victron VRM, if a VRM token is set up; it learns them on the first connection to the GX. Shore power in VRM needs the Shelly as a grid meter on the GX as well; otherwise the app shows no shore power from VRM.

The flow keeps its data in `~/.node-red/` on the GX: `bordnetz-energy.json` (60 days), `bordnetz-track.json` (30 days), `bordnetz-away.json`, `bordnetz-shore-session.json`.

## Development

`bordnetz-flow.json` is generated: change [`build_flow.py`](build_flow.py), then `python3 build_flow.py` and `node alarm-test.mjs` (tests of the alarm, energy and track logic). `deploy.sh` replaces the tab on a GX from the command line and backs up all flows first; settings in `local.env` (see `local.env.example`).

## License

MIT, see [LICENSE](LICENSE).

---

<a id="deutsch"></a>

# Bordnetz-Flow für Node-RED

Die Serverseite von **Bordnetz**, der iPhone-App, die das Energiesystem eines Campers (Batterie, Solar, Landstrom, Temperaturen, Alarme, Fahrten) in der App, in Widgets und in CarPlay zeigt. Dieser Flow läuft in Node-RED auf dem Victron-GX-Gerät im Fahrzeug (Cerbo GX, Ekrano GX, …), liest die Werte vom D-Bus und stellt sie der App über eine kleine HTTPS-API bereit. Nichts geht über eine Cloud.

Bordnetz ist ein unabhängiges Projekt und steht in keiner Verbindung zu Victron Energy.

## Voraussetzungen

- Ein Victron-GX-Gerät mit **Venus OS Large** und eingeschaltetem **Node-RED** (Einstellungen → Integrationen, bei älteren Versionen Einstellungen → Venus OS Large features). Die Victron-Nodes sind dabei.
- Eine Batterie mit BMS oder Batteriemonitor am GX. Alles andere ist optional: Solarladeregler, Ladegerät, Temperatursensoren, ein Türkontakt an einem Digitaleingang, GPS, Shellys für Landstrom und die 12-V-Versorgung des Kühlschranks.
- Das iPhone im Netzwerk des Fahrzeugs oder damit verbunden, z. B. über Tailscale oder ein anderes VPN.

## Installieren

1. Den Node-RED-Editor öffnen: `https://<GX-Adresse>:1881`.
2. Menü → **Import** → [`bordnetz-flow.json`](bordnetz-flow.json) wählen (oder den Inhalt einfügen) → Import. Ein neuer Tab **Bordnetz API** erscheint.
3. Doppelklick auf den Tab → **Umgebungsvariablen**: Token und Geräte setzen (nächster Abschnitt).
4. **Deploy**.

Aktualisieren: die neue `bordnetz-flow.json` importieren und bei der Nachfrage **Ersetzen** wählen. Die Umgebungsvariablen bleiben beim Tab.

## Einstellen

Alle Einstellungen sind Umgebungsvariablen des Tabs. Leer heisst „nicht vorhanden“: Dieser Eingang bleibt still, die API sendet `null`, und die App lässt die Zeile weg.

| Variable | Beispiel | Bedeutung |
|---|---|---|
| `IOS_API_TOKEN` | Ausgabe von `openssl rand -hex 32` | Zugangstoken der App (Typ *Credential*: verschlüsselt gespeichert). Ohne Token wird jede Anfrage abgewiesen. |
| `BATTERY_SERVICE` | `com.victronenergy.battery/512` | BMS oder Batteriemonitor: SOC, Spannung, Strom, Restlaufzeit, Zellspannungen, BMS-Alarme |
| `SOLAR_SERVICE` | `com.victronenergy.solarcharger/279` | MPPT-Solarladeregler |
| `CHARGER_SERVICE` | `com.victronenergy.charger/288` | Landstrom-Ladegerät (z. B. Blue Smart IP22) |
| `TEMP_OUTSIDE_SERVICE` | `com.victronenergy.temperature/20` | Temperatur (und Luftfeuchtigkeit) aussen |
| `TEMP_INSIDE_SERVICE` | `com.victronenergy.temperature/22` | … innen |
| `TEMP_FRIDGE_SERVICE` | `com.victronenergy.temperature/21` | … im Kühlschrank |
| `DOOR_SERVICE` | `com.victronenergy.digitalinput/1` | Türkontakt; am GX den Typ des Eingangs auf *Türalarm* stellen |
| `GPS_SERVICE` | `com.victronenergy.gps/1` | Position, Fahrtenbuch, Abwesenheitsmodus |
| `SHORE_SHELLY_TOPIC` | `shelly1pm-08f9e0aabbcc` | MQTT-Topic-Präfix eines Shelly (Gen2 oder neuer) hinter der Landstrom-Einspeisung |
| `FRIDGE_12V_URL` | `http://192.168.1.50/rpc/Switch.GetStatus?id=0` | Ein Shelly, der die 12-V-Versorgung des Kühlschranks schaltet |
| `SOC_LOW` | `20` | Alarm unter diesem Ladezustand (%) |
| `FRIDGE_MAX` | `14` | Alarm, wenn der Kühlschrank 30 min darüber bleibt (°C); hängt davon ab, wo der Sensor sitzt |

Änderungen gelten ab dem nächsten Deploy.

### Geräte finden

Ein Service-Name lautet `com.victronenergy.<Typ>/<VRM-Instanz>`; die Typen sind `battery`, `solarcharger`, `charger`, `temperature`, `digitalinput` und `gps`. Die Instanz steht am GX unter Einstellungen → Geräteliste → das Gerät → Gerät → **VRM-Instanz**. In Node-RED zeigt die Geräteliste eines Victron-*Custom*-Eingangs dieselben Namen.

### Landstrom (Shelly)

Ein Shelly 1PM (Gen2 oder neuer) hinter der Landstrom-Einspeisung zeigt, ob Landstrom da ist, wie viel bezogen wird, und die Energie auf dem aktuellen Platz (Reset in der App). Er ist nur online, solange Landstrom angeschlossen ist, und ein Ausfall während des Ladens löst einen Alarm aus.

- Am GX den MQTT-Zugriff erlauben (Einstellungen → Integrationen oder Dienste, je nach Version).
- Am Shelly: Einstellungen → MQTT → einschalten, Server `<GX-Adresse>:1883`, „Generic status update over MQTT“ ein. Das dort angezeigte Topic-Präfix (z. B. `shelly1pm-08f9e0aabbcc`) kommt in `SHORE_SHELLY_TOPIC`.

### Kühlschrank 12 V (Shelly)

Schaltet ein Shelly die 12-V-Versorgung des Kühlschranks, zeigt `FRIDGE_12V_URL` auf sein `Switch.GetStatus` (alle 30 s abgefragt). Der Alarm `fridge_off` geht davon aus, dass die 230-V-Seite des Kühlschranks am Landstrom hängt: 12 V aus und 5 Minuten kein Landstrom heisst, der Kühlschrank hat keine Versorgung.

## App verbinden

In Bordnetz → Einstellungen:

- **Adresse:** `https://<GX-Adresse>:1881/api/camper/status`
- **Token:** der Wert von `IOS_API_TOKEN`
- **Zertifikat (empfohlen):** der SHA-256-Fingerabdruck des GX-Zertifikats, damit die App genau diesem vertraut und keinem anderen:
  `openssl s_client -connect <GX-Adresse>:1881 </dev/null 2>/dev/null | openssl x509 -noout -fingerprint -sha256`

Oder auf einem Mac einen Einrichtungscode erzeugen, der alle drei ausfüllt, und ihn mit der Kamera-App scannen:

```sh
NODE_RED_IP=<GX-Adresse> IOS_API_TOKEN=<Token> swift setup-qr.swift
```

`--vrm` fügt ein Victron-VRM-Zugangstoken (`VRM_TOKEN`) für den Verlauf hinzu. Der Code enthält das Token: kein Bild davon weitergeben.

## Alarme

Codes im Statusdokument; die App zeigt sie in Worten.

| Code | Wann |
|---|---|
| `soc_low` | Ladezustand unter `SOC_LOW`, erlischt 2 % darüber |
| `fridge_warm` | Kühlschrank 30 Minuten über `FRIDGE_MAX` |
| `fridge_off` | 12 V des Kühlschranks aus und 5 Minuten kein Landstrom |
| `shore_lost` | der Landstrom-Shelly ging offline, während das Ladegerät lud (Sicherung, Platzsicherung); eine Stunde sichtbar oder bis der Landstrom zurück ist |
| `away_moved` | Abwesenheitsmodus an und das Fahrzeug mehr als 200 m von dort, wo er eingeschaltet wurde |
| `away_door` | Abwesenheitsmodus an und die Tür geöffnet |
| `bms_*` | Alarme des BMS, z. B. `bms_cell_imbalance` |

## API

Alle Endpunkte brauchen `Authorization: Bearer <IOS_API_TOKEN>`.

| Endpunkt | Antwort |
|---|---|
| `GET /api/camper/status` | `camper.status.v1`: alle Werte, Alarme, Abwesenheitsmodus, Energie heute und gestern |
| `GET /api/camper/energy?days=<n>` | `camper.energy.v1`: Energie pro Tag (Solar, Ladegerät, Lichtmaschine, Verbrauch, Batterie, Landstrom), Standard 14 Tage |
| `GET /api/camper/track?since=<Unix-s>` | `camper.track.v1`: GPS-Punkte `[Zeit, Breite, Länge, km/h]`, Standard 7 Tage |
| `POST /api/camper/away` `{"active": true}` | schaltet den Abwesenheitsmodus |
| `POST /api/camper/shore/reset` | setzt die Energie des Platzes auf 0 |

Der Status nennt auch die eingerichteten Geräte (`devices`: VRM-Instanzen und ob ein Landstrom-Shelly eingerichtet ist). Ist das GX nicht erreichbar, liest die App dieselben Geräte aus Victron VRM, sofern ein VRM-Token eingerichtet ist; sie lernt sie bei der ersten Verbindung zum GX. Für Landstrom in VRM muss der Shelly am GX zusätzlich als Netzzähler laufen; sonst zeigt die App aus VRM keinen Landstrom.

Der Flow speichert seine Daten in `~/.node-red/` auf dem GX: `bordnetz-energy.json` (60 Tage), `bordnetz-track.json` (30 Tage), `bordnetz-away.json`, `bordnetz-shore-session.json`.

## Entwicklung

`bordnetz-flow.json` wird erzeugt: [`build_flow.py`](build_flow.py) ändern, dann `python3 build_flow.py` und `node alarm-test.mjs` (Tests der Alarm-, Energie- und Fahrtenlogik). `deploy.sh` ersetzt den Tab auf einem GX von der Kommandozeile aus und sichert vorher alle Flows; Einstellungen in `local.env` (siehe `local.env.example`).

## Lizenz

MIT, siehe [LICENSE](LICENSE).
