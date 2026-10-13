# Privacybeleid / Privacy policy — EMS Control

**App:** EMS Control (iOS)  
**EMS server:** self-hosted Energy Management System (open-source software in this repository)  
**Last updated:** 2026-10-14  
**Languages:** [Nederlands](#privacybeleid-nederlands) · [English](#privacy-policy-english)

This document describes how the **EMS Control** iOS app handles data, and how that app relates to your **own** EMS server. It is written for hosting as a public URL (for example GitHub Pages) and for App Store Connect.

---

## Privacybeleid (Nederlands)

### 1. Wie is verantwoordelijk?

- **EMS Control** is een client voor een **door jou gehoste** EMS-server op je thuisnetwerk (of via VPN). De app is geen cloud-dienst van de ontwikkelaar.
- **Geen gegevens naar de app-ontwikkelaar:** de iOS-app bevat **geen** analytics-, advertentie- of crash-reporting-SDK’s en stuurt **geen** gebruiks- of energiedata naar de makers van EMS Control. Apple’s privacy-manifest (`PrivacyInfo.xcprivacy`) vermeldt **geen** tracking (`NSPrivacyTracking` = false) en **geen** door de app verzamelde datatypen voor App Store-rapportage.
- **Jouw EMS-server** draait onder **jouw** beheer. Welke gegevens daar worden verwerkt en welke externe partijen je inschakelt, hangt af van **jouw** instellingen (zie §4).

**Contact voor privacyvragen over EMS Control:**

Voor privacyvragen over EMS Control kun je een issue openen op [GitHub Issues](https://github.com/jeroenniesen/EnergyManagementSystem/issues).

### 2. Welke gegevens verwerkt de iOS-app?

De app toont energie-informatie en stuurt alleen verzoeken naar het **serveradres dat jij invoert** (bijv. `http://192.168.x.x:8080` op LAN of hetzelfde adres via VPN). Er is **geen** vaste backend van de ontwikkelaar.

| Gegeven | Waar | Doel |
|--------|------|------|
| EMS-server-URL | iOS Keychain (`last-base-url`) | Laatste verbinding herstellen |
| API-token (sessie) | iOS Keychain | Authenticatie bij je EMS-server |
| Widget-token (alleen-lezen) | iOS Keychain + App Group `UserDefaults` | Home-schermwidget praat met dezelfde server |
| Laatste widget-weergave (accu-%, statuswoord, laadregel auto, tijdstempel) | App Group `UserDefaults` (`widget.last_render`) | Laatst bekende data tonen als een verversing mislukt |
| Demo-modus | Alleen lokaal gebundelde JSON-fixtures | App Store-review / uitproberen zonder server |
| Inhoud van schermen | Geheugen tijdens gebruik (behalve de widget-cache hierboven) | Dashboard, inzichten, chat-antwoorden van **jouw** server |

Bij inloggen met gebruikersnaam/wachtwoord stuurt de app die gegevens **alleen** naar jouw EMS-server om een bearer-token te verkrijgen; het wachtwoord wordt **niet** permanent op het toestel opgeslagen (alleen het token in de Keychain).

**Netwerk (app):** HTTPS/HTTP naar **jouw** EMS-base-URL: o.a. status, batterij, prijzen/plan, meldingen, instellingen (lezen/schrijven waar de server dat toestaat), inloggen (`POST /api/auth/login`), chat (`POST /api/chat`), widget-verversing (~elke 20 minuten). **Geen** WebSocket in de iOS-app; **geen** verbindingen naar derden buiten jouw server-URL.

**Lokaal netwerk:** iOS vraagt toestemming voor lokaal netwerk (`NSLocalNetworkUsageDescription`) omdat de app typisch een LAN-adres gebruikt.

**Demo-modus:** synthetische data uit de app-bundle; **geen** netwerk.

### 3. Tracking, analytics en crashrapportage

- **Geen tracking** volgens het privacy-manifest van de app.
- **Geen** ingebouwde crash- of analytics-diensten (geen Firebase, Sentry, enz.) in de Swift-package-afhankelijkheden.
- De ontwikkelaar ontvangt **geen** automatische telemetrie uit de app.

### 4. Jouw EMS-server en derden (belangrijk)

De app **weerspiegelt** data die **jouw server** verzamelt. Als je live apparaten en optionele functies inschakelt, kan de server (niet de iOS-app rechtstreeks) met **externe of LAN-diensten** praten. Dit is **jouw** keuze en configuratie.

| Dienst (voorbeelden in code) | Wanneer | Wat gaat er ongeveer mee |
|-----------------------------|---------|---------------------------|
| **HomeWizard** (LAN) | Live meters | Meterstanden vanaf IP’s op je netwerk |
| **Indevolt** (LAN) | Live batterij | Batterijstatus via lokale OpenData RPC |
| **Tibber** (`api.tibber.com`) | Live prijzen | GraphQL met **jouw** Tibber-token (abonnement/prijzen) |
| **Forecast.Solar** | Standaard zonforecast | Lat/lon, tilt, azimuth, kWp in de API-URL |
| **Solcast** (optioneel) | Zonforecast | API-sleutel + site-id |
| **Open-Meteo** | Bewolking (hulp voor forecast) | Breedtegraad/lengtegraad |
| **Electricity Maps** (optioneel) | CO₂-inzichten (rapportage) | API-sleutel + netzone-code (standaard `NL`) — geen coördinaten |
| **ntfy** (optioneel) | Pushmeldingen | Door jou gekozen server (bijv. ntfy.sh) + topic; titel/tekst van meldingen |
| **Externe LLM** (optioneel, standaard **uit**) | Uitleg / chat | Minimale, geredigeerde tekst (beslissingsreden + genoemde feiten of chat-context); **geen** tokens/locatie in die payload volgens serverlogica |
| **SmartThings** (optioneel, **cloud**) | Consumentenapparaten | Jouw Personal Access Token naar `api.smartthings.com`; apparaatlijst + apparaatstatus |
| **Home Assistant** (optioneel, LAN) | Consumentenapparaten | De HA-URL + token die jij invult; alleen-lezen entiteitsstatus |
| **OpenStreetMap-tiles** | Web-UI kaart (setup) | Kaarttiles bij locatiekeuze in de **browser-UI** van de server, niet in de iOS-app |

De iOS-app **configureert** deze serverkoppelingen niet; die staan in de EMS-web-UI op je server. Chat en “AI”-antwoorden in de app zijn **server-side**: als jij `external_llm` inschakelt, verwerkt **jouw gekozen** LLM-aanbieder (standaard endpoint in instellingen: MiniMax) de door de server opgestuurde, begrensde tekst.

**Opslag op de server:** historie en instellingen lokaal (SQLite onder `/data` of `ems/data/` in een standaardinstallatie). Geen verplichte cloud-back-up door EMS.

### 5. Beveiliging en jouw verantwoordelijkheid

- Gebruik een **sterk API-token**; zet **`web.require_auth`** aan als je via VPN of een breder netwerk verbindt (zie [remote access](./remote-access.md)).
- De EMS-poort **niet** publiek op internet zetten; ondersteund remote = VPN naar LAN.
- Widget-token staat in App Group `UserDefaults` (bewuste afweging voor LAN-only, read-only widget-token); bescherm je toestel en netwerk.

### 6. Bewaartermijn en verwijderen

- **App:** verwijder tokens/URL door de app te verwijderen of verbinding te verbreken; Keychain- en App Group-data worden dan door iOS beheerd.
- **Server:** bewaartermijnen volgen jouw SQLite/history-beleid en back-ups; jij beheert de server.

### 7. Rechten (AVG)

EMS Control verwerkt gegevens **niet** als verwerker voor de app-ontwikkelaar. Voor gegevens op **jouw** EMS-server ben **jij** (of jouw huishouden) verwerkingsverantwoordelijke. Voor vragen over App Store-privacy van de app: zie contact hierboven.

### 8. Wijzigingen

Dit document kan worden bijgewerkt in de repository; de datum bovenaan geldt als versie.

---

## Privacy policy (English)

### 1. Who is responsible?

- **EMS Control** is a client for a **self-hosted** EMS server on your home network (or over VPN). The app is not a developer-operated cloud service.
- **No data to the app developer:** the iOS app includes **no** analytics, advertising, or crash-reporting SDKs and does **not** send usage or energy data to the EMS Control authors. Apple privacy manifests declare **no** tracking (`NSPrivacyTracking` = false) and **no** data types collected for App Store disclosure.
- **Your EMS server** runs under **your** control. What is processed there and which third parties you enable depends on **your** settings (see §4).

**Contact for privacy questions about EMS Control:**

For privacy questions about EMS Control, open an issue on [GitHub Issues](https://github.com/jeroenniesen/EnergyManagementSystem/issues).

### 2. What data does the iOS app process?

The app displays energy information and only sends requests to the **server address you enter** (e.g. `http://192.168.x.x:8080` on LAN or the same over VPN). There is **no** fixed backend operated by the app developer.

| Data | Where | Purpose |
|------|-------|---------|
| EMS server URL | iOS Keychain (`last-base-url`) | Restore last connection |
| API token (session) | iOS Keychain | Authenticate to your EMS server |
| Widget token (read-only) | iOS Keychain + App Group `UserDefaults` | Home-screen widget uses the same server |
| Last widget render (battery %, verdict word, car line, timestamp) | App Group `UserDefaults` (`widget.last_render`) | Show last-known data when a refresh fails |
| Demo mode | Bundled JSON fixtures only | App Store review / try without a server |
| On-screen content | In-memory while in use (apart from the widget cache above) | Dashboard, insights, chat replies from **your** server |

When you sign in with username/password, the app sends those credentials **only** to your EMS server to obtain a bearer token; the password is **not** stored on the device (only the token in the Keychain).

**Network (app):** HTTPS/HTTP to **your** EMS base URL only — including status, battery, plan/prices, notifications, settings (read/write as permitted), login (`POST /api/auth/login`), chat (`POST /api/chat`), widget refresh (~every 20 minutes). **No** WebSocket in the iOS app; **no** connections to third parties except via your server URL.

**Local network:** iOS may prompt for local network access because the app typically uses a LAN address.

**Demo mode:** synthetic data from the app bundle; **no** network.

### 3. Tracking, analytics, and crash reporting

- **No tracking** per the app privacy manifests.
- **No** built-in crash or analytics services (no Firebase, Sentry, etc.) in the Swift package dependencies.
- The developer receives **no** automatic telemetry from the app.

### 4. Your EMS server and third parties (important)

The app **reflects** data your **server** collects. When you enable live devices and optional features, the server (not the iOS app directly) may talk to **external or LAN services**. That is **your** choice and configuration.

| Service (examples in code) | When | What is roughly shared |
|---------------------------|------|-------------------------|
| **HomeWizard** (LAN) | Live meters | Meter readings from IPs on your network |
| **Indevolt** (LAN) | Live battery | Battery status via local OpenData RPC |
| **Tibber** (`api.tibber.com`) | Live prices | GraphQL with **your** Tibber token |
| **Forecast.Solar** | Default solar forecast | Lat/lon, tilt, azimuth, kWp in the API URL |
| **Solcast** (optional) | Solar forecast | API key + site id |
| **Open-Meteo** | Cloud cover (forecast helper) | Latitude/longitude |
| **Electricity Maps** (optional) | CO₂ insights (reporting) | API key + grid zone code (default `NL`) — no coordinates |
| **ntfy** (optional) | Push notifications | Your chosen server (e.g. ntfy.sh) + topic; notification title/body |
| **External LLM** (optional, **off** by default) | Explanations / chat | Minimal redacted text (decision reason + cited facts, or chat context); server logic avoids sending tokens/location in that payload |
| **SmartThings** (optional, **cloud**) | Consumer devices | Your Personal Access Token to `api.smartthings.com`; device list + device status |
| **Home Assistant** (optional, LAN) | Consumer devices | The HA URL + token you enter; read-only entity state |
| **OpenStreetMap tiles** | Web UI map (setup) | Map tiles when picking location in the server **browser UI**, not in the iOS app |

The iOS app does **not** configure these server integrations; they are set in the EMS web UI on your server. Chat and “AI” answers in the app are **server-side**: if you enable `external_llm`, **your chosen** LLM provider (default settings point at MiniMax) processes the bounded text the server sends.

**Server storage:** history and settings are stored locally (SQLite under `/data` or `ems/data/` in a typical install). EMS does not require a vendor cloud backup.

### 5. Security and your responsibilities

- Use a **strong API token**; enable **`web.require_auth`** when connecting over VPN or a wider network (see [remote access](./remote-access.md)).
- Do **not** expose the EMS port to the public internet; supported remote access is VPN to the LAN.
- The widget token is stored in App Group `UserDefaults` (a deliberate LAN read-only widget trade-off); protect your device and network.

### 6. Retention and deletion

- **App:** remove tokens/URL by deleting the app or disconnecting; Keychain and App Group data are then managed by iOS.
- **Server:** retention follows your SQLite/history policy and backups; you operate the server.

### 7. Rights (GDPR)

EMS Control does **not** process personal data on behalf of the app developer as a processor. For data on **your** EMS server, **you** (or your household) are the controller. For App Store privacy questions about the app, use the contact above.

### 8. Changes

This document may be updated in the repository; the date at the top is the version marker.
