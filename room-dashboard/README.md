# Rumsdashboard

En egen liten dashboard för ditt rum: **temperatur, luftfuktighet och
koldioxid** från sensorerna i ditt smarta hem, **vädret ute** och
**klockan**, på en sida i webbläsaren. Den ger också råd, till exempel
"vädra nu" när luften är tung, eller att ett kort korsdrag räcker när det
är kallt ute.

Idén och färgerna kommer från
[PRMGR Observatory Starter](https://github.com/prmgr-org/prmgr-observatory-starter)
(MIT), men den här versionen är byggd för ett rum i stället för ett helt hus,
och koden är skriven från grunden.

Den behöver inte Rocky-roboten i resten av repot. Mappen fungerar helt på
egen hand.

```
 sensorer i rummet ──► Home Assistant / Homey ──┐
                                                ├──► server.py ──► webbläsaren
             Open-Meteo (vädret, gratis) ───────┘     (din dator)   http://127.0.0.1:8790
```

## Prova direkt (demodata)

Du behöver **Python 3.10 eller senare**. Inget annat behöver installeras.

```bash
cd room-dashboard
python3 server.py
```

Öppna **http://127.0.0.1:8790**. Utan `config.json` visar sidan
**påhittade demovärden**, och det står tydligt på sidan. Stoppa med Ctrl+C.

Du kan också dubbelklicka på `public/index.html` för att se designen utan
server. Då visas också bara demodata.

## Koppla in dina sensorer

1. Kopiera exempelfilen:

   ```bash
   cp config.example.json config.json
   ```

2. Skriv in din ort under `location`. Latitud och longitud hittar du genom
   att högerklicka på platsen i Google Maps. Det är bara de två talen som
   skickas till Open-Meteo för att hämta vädret.
3. Välj källa i `source.type`: `homeassistant`, `homey` eller `file`, och
   fyll i delen för den källan (se nedan). Lämna en sensor tom (`""`) om du
   inte har den. Kortet på sidan visar då "Ingen sensor kopplad".
4. Starta om `python3 server.py`.

`config.json` innehåller din token och är undantagen från git (`.gitignore`).
Lägg den aldrig i ett publikt repo.

### Home Assistant

- **url**: adressen du öppnar Home Assistant på, t.ex.
  `http://homeassistant.local:8123`.
- **token**: din profil i Home Assistant (klicka på ditt namn nere till
  vänster) → fliken *Säkerhet* → *Långlivade åtkomsttokens* → *Skapa token*.
- **entities**: entity-id för varje sensor, t.ex. `sensor.sovrum_temperature`.
  Du hittar dem under *Inställningar → Enheter och tjänster → Entiteter*.
  Temperatur i °F räknas om till °C automatiskt.

### Homey

Gäller **Homey Pro**, som har ett lokalt API. (Homey Bridge saknar det. Då
fungerar `file`-källan nedan, med ett HomeyScript som skriver filen.)

- **url**: Homeyns IP-adress i ditt nätverk, t.ex. `http://192.168.1.50`.
- **token**: en API-nyckel från Homeys webbapp (*Inställningar → API-nycklar*)
  med rätt att läsa enheter.
- **devices**: enhetens id för varje sensor. Id:t står i adressfältet när
  du öppnar enheten i webbappen (my.homey.app). Har en enhet både temperatur
  och fukt anger du samma id för båda; den frågas bara en gång.
  `capability` kan utelämnas: standard är `measure_temperature`,
  `measure_humidity` och `measure_co2`.

> Homey-kopplingen följer Homeys dokumenterade Web API men är inte provad mot
> en riktig Homey. Visar sidan ett fel för Homey, skriv vad som står så tar
> vi det därifrån.

### En JSON-fil (allt annat)

Har du något annat system kan det skriva en fil som dashboarden läser:

```json
{ "at": "2026-11-07T18:00:00+01:00", "values": { "temperature": 21.4, "humidity": 41, "co2": 760 } }
```

Ange sökvägen i `file.path`. Tiden måste ha tidszon. Är filen äldre än
`max_age_minutes` (15 som standard) visas värdena som saknade, så att ett
stoppat skript syns som ett glapp i stället för ett fruset värde.

## Livebild från webbkameran

Tryck på **Kamera** uppe i rutan *Just nu* för att byta illustrationen mot
en livebild, med temperatur och luftfuktighet ovanpå. Webbläsaren frågar en
gång om sidan får använda kameran. Har du flera kameror väljs en Logitech
C922 automatiskt om den finns, annars kan du välja i listan bredvid knappen.

- Bilden går direkt från kameran till webbläsarfönstret. Den spelas inte in,
  sparas inte och skickas inte till servern eller någon annan.
- När du byter flik eller minimerar fönstret släpps kameran, och lampan på
  den slocknar. Sidan minns om du senast valde *Kamera*.
- Det fungerar bara när sidan öppnas via `http://127.0.0.1:8790`, inte när
  `index.html` öppnas som fil.
- Vill du inte ha knappen alls: sätt `"camera": false` i `config.json`.

Visar den "Kameran används av ett annat program" är det oftast Zoom, Teams,
OBS eller Logitech-programmet som håller i den.

## Bygg en rumsgivare med SunFounder-satsen

SunFounders *Universal Maker Sensor Kit* har inget eget WiFi, så modulerna
kopplas till ett **ESP32-C3-kort** som skickar värdena trådlöst till Home
Assistant med **ESPHome**. Dashboarden läser dem sedan från Home Assistant.

```
moduler ──sladdar──► ESP32-C3 ──WiFi──► Home Assistant ──► dashboarden
```

### Vilka moduler passar ett rum

| Modul i satsen | Mäter | Värd att koppla? |
| --- | --- | --- |
| **BMP280** | temperatur (±1 °C) och lufttryck | Ja. Bästa temperaturen i satsen. |
| **DHT11** | luftfuktighet (±5 %) och temperatur (±2 °C) | Ja, för fukten. |
| **Fotoresistor** | hur ljust det är, relativt | Ja. Visar om lampan är tänd eller det blivit mörkt. Inte lux. |
| **PIR HC-SR501** | rörelse / närvaro | Ja. Bra för "någon är i rummet". |
| **OLED SSD1306** | (skärm) | Valfritt. Visar värdena direkt på givaren. |
| DS18B20 | temperatur | Behövs inte när du har BMP280. |
| MQ2 gas/rök | brandfarlig gas, rök | Nej. Mäter inte koldioxid, drar mycket ström och dess 5 V-signal kan skada C3:an. |
| HC-SR04, MPU6050 m.fl. | avstånd, rörelse i 3D | Inte för rumsklimat. |

SunFounder kallar BMP280-modulen "Temperature, Humidity & Pressure", men
BMP280-chippet mäter inte fukt. Därför tar vi fukten från DHT11. Står det
`Wrong chip ID` i ESPHome-loggen har du en BME280 i stället, som mäter fukt
också: byt då `bmp280_i2c` mot `bme280_i2c` i filen.

### 1. Koppla

Siffrorna är GPIO-nummer. På en *ESP32-C3 SuperMini* och Espressifs
*DevKitM-1* står de tryckta vid stiften. På en *Seeed XIAO ESP32C3* står
D-namnet, se kolumnen längst till höger. Satsen kan innehålla olika versioner
av modulerna, så läs texten på just dina.

| Modul | Modulens stift | ESP32-C3 | XIAO ESP32C3 |
| --- | --- | --- | --- |
| BMP280 | VCC / GND | 3V3 / GND | 3V3 / GND |
| | SDA | GPIO6 | D4 |
| | SCL | GPIO7 | D5 |
| DHT11 | `+` / `-` | 3V3 / GND | 3V3 / GND |
| | `S` / DATA | GPIO10 | D10 |
| Fotoresistor | VCC / GND | **3V3** / GND | 3V3 / GND |
| | AO | GPIO3 | D1 |
| PIR HC-SR501 | VCC / GND | **5V** / GND | 5V / GND |
| | OUT | GPIO4 | D2 |

- PIR-modulen behöver 5 V som ström, men signalen den skickar är 3,3 V och
  tål C3:an. Allt annat ska ha **3V3**, aldrig 5V.
- Använd inte GPIO2, 8 eller 9 (styr hur kortet startar), 18/19 (USB) eller
  20/21 (seriellporten) till moduler.
- ESP32-C3 blir varm. Låt temperaturgivarna sitta några centimeter från
  kortet på sladdar, annars visar de för varmt.
- Börja gärna med en modul i taget och kontrollera att den fungerar.

### 2. Installera ESPHome i Home Assistant

*Inställningar → Tillägg → Tilläggsbutik*, sök på **ESPHome Device
Builder**, installera, starta och slå på *Visa i sidofältet*. (Kör du Home
Assistant i Docker utan tillägg kan ESPHome köras som en egen container.)

### 3. Skapa enheten

I ESPHome: *+ New device*, döp den till `rumsgivare` och välj *ESP32-C3*.
Guiden skapar WiFi-inställningar och nycklar åt dig. Tryck sedan på *Edit*
och klistra in delarna `i2c:`, `sensor:` och `binary_sensor:` från
[`esphome/rumsgivare.yaml`](esphome/rumsgivare.yaml) längst ned. Ta bort
blocken för moduler du inte kopplat in. Hela filen finns där som referens,
kontrollerad med ESPHome 2026.10.

### 4. Första installationen över USB

Koppla C3:an till datorn med en USB-kabel som klarar data (inte bara
laddning). I ESPHome: *Install → Manual download → Factory format*. Öppna
[web.esphome.io](https://web.esphome.io) i Chrome eller Edge, tryck
*Connect*, välj kortet och installera filen. Senare ändringar skickas
trådlöst.

Syns inget kort i listan: håll ned knappen **BOOT** (eller **B**) medan du
sätter i USB-kabeln, och försök igen.

### 5. Lägg till i Home Assistant

Efter en minut dyker *Rumsgivare* upp under *Inställningar → Enheter och
tjänster* som upptäckt. Tryck *Konfigurera*. Då finns:

| Entitet | Används av dashboarden |
| --- | --- |
| `sensor.rumsgivare_temperatur` | ja (redan i `config.example.json`) |
| `sensor.rumsgivare_luftfuktighet` | ja (redan i `config.example.json`) |
| `sensor.rumsgivare_lufttryck` | inte än, men syns i Home Assistant |
| `sensor.rumsgivare_ljus` | inte än |
| `binary_sensor.rumsgivare_rorelse` | inte än |

**Kontrollera ljusgivaren:** håll handen över den. Procenten ska sjunka.
Stiger den i stället, ta bort raden `pct = 100.0f - pct;` i filen.

**Justera temperaturen** med `offset` i filen om den skiljer sig från en
termometer du litar på.

### Koldioxid

Ingen modul i satsen mäter koldioxid. För "dags att vädra"-rådet behövs en
riktig CO2-givare, t.ex. **Sensirion SCD40/SCD41**, som kan sitta på samma
I2C-sladdar som BMP280 (ESPHome har stöd för den). Fram till dess visar
CO2-kortet "Ingen sensor kopplad".

## Vad sidan visar

| Del | Innehåll |
| --- | --- |
| Rummet / Kamera | En illustration av ett rum, eller livebild från webbkameran. Himlen i fönstret följer dygnet och vädret, lampan tänds när det är mörkt och luften blir disig när koldioxiden stiger. |
| Råd | Den viktigaste saken just nu, och ett konkret tips. |
| Klimatkort | Temperatur, luftfuktighet och koldioxid med en färgmarkering (grön/gul/röd) och förändringen den senaste timmen. |
| Väder | Just nu, dagens min/max, sol upp/ned och de kommande 12 timmarna med regnrisk. |
| Över tid | Diagram för senaste 6 timmarna, dygnet eller veckan. Temperaturdiagrammet visar inne och ute tillsammans. |

Gränserna för grönt, gult och rött står överst i `public/app.js` (`LIMITS`)
om du vill ändra dem. Standard: 19,5–23,5 °C, 35–60 % fukt, under 1000 ppm
koldioxid.

Mätvärdena sparas varje minut i `data/history.json` (en vecka bakåt) så att
diagrammen finns kvar efter en omstart. Byter du källa sparas den gamla
historiken undan som en egen fil i stället för att blandas med den nya.

## Integritet

- Servern lyssnar bara på den här datorn (`127.0.0.1`). Andra enheter i
  nätverket når den inte.
- Sidan hämtar inget från internet. Vädret hämtas av servern.
- Kamerabilden lämnar aldrig webbläsarfönstret och sparas inte.
- Bara siffror och tider sparas, på din dator.

## Vanliga problem

- **"nekad (401)"**: fel eller utgången token.
- **"hittades inte (404)"**: fel entity-id eller enhets-id.
- **"når inte enheten"**: fel adress, eller datorn är inte på samma nätverk
  som Home Assistant/Homey.
- **Inget väder**: kontrollera `location.latitude` och `location.longitude`,
  och att datorn har internet.
- **Port upptagen**: `python3 server.py --port 8791`.

## Tester

```bash
python3 -m unittest discover tests
```

Testerna behöver varken nätverk eller sensorer.

## Idéer för senare

- Visa den på en surfplatta på väggen. Det kräver att servern får svara
  andra enheter i nätverket, vilket bör göras med lösenord.
- Fler mätvärden: ljud, ljus, elförbrukning från ett smart uttag.
- Kalender för dagen bredvid klockan.
