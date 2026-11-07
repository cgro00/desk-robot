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

## Vad sidan visar

| Del | Innehåll |
| --- | --- |
| Rummet | En illustration av ett rum. Himlen i fönstret följer dygnet och vädret, lampan tänds när det är mörkt och luften blir disig när koldioxiden stiger. |
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
