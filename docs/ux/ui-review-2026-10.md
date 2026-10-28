# UI/UX-review niet-dashboardschermen — oktober 2026

Issue: [jeroenniesen/EnergyManagementSystem#362](https://github.com/jeroenniesen/EnergyManagementSystem/issues/362).
Basiscommit: `0542df3305e6aa215757daac892ec3a49662f1ff` (`origin/main` op branch `docs/362-ui-ux-review`).

## 1. Korte samenvatting

De niet-dashboardschermen zijn bruikbaar en deels al op het niveau van de juli-audit (sidebar, zoeken, sticky save, switches, “More”-hulp). Wat een bewoner nog kan verrassen, zit in drie dingen: sturen van de batterij staat op drie plekken met drie opslaanpatronen, veiligheidsteksten mengen Nederlands en Engels met ruwe tokens (`restart_pending`, `dry_run`), en een leeg getalveld of een muiswiel kan een grenswaarde vastleggen zonder extra bevestiging.

De scope-tabel in #362 heeft **15 rijen** (14 schermroutes/onderdelen plus de rij Gedeelde tokens). Elke rij heeft hieronder een eigen sectie (§2.1–§2.15). Per scherm zijn de negen te beoordelen elementen beantwoord. Waar iets alleen in de browser te meten is (contrast, flits, echte 390 px-layout), staat dat erbij; het oordeel leunt dan op de CSS- en componentcode.

| Ernst | Aantal bevindingen |
|---|---|
| Hoog | 8 |
| Middel | 26 |
| Laag | 5 |
| Totaal | 39 |

## Methode

Code-review op de basiscommit. Gelezen: `CLAUDE.md`, SPEC §9.1 (web-UI, Engels in v1, WCAG 2.1 AA), `docs/2026-07-12-settings-ux-audit.md`, en de bestanden onder “Te lezen” in #362 (`ems/web/frontend/src/…` plus `e2e/a11y.spec.ts`, `e2e/settings.spec.ts`, `e2e/theme.spec.ts`). Zichtbare veldnamen van veiligheidsinstellingen zijn nagekeken in `ems/settings.py` (het schema dat `ems/web/frontend/src/Settings.tsx` rendert).

Issue-comments op #362: leeg. Gerelateerde issues gelezen via REST: #93, #96, #99, #100, #122, #123, #94, #291, #292, #293, #294, #295, #296, #331, #339. Open PR’s die dezelfde schermen raken: **#305** (`ems/web/frontend/src/Setup.tsx`, `ems/web/frontend/src/styles.css`, batterij-wizard voor #291) en **#360** (`ems/web/frontend/src/Settings.tsx`, `ems/web/frontend/src/labels.ts`: Nederlands label voor het dagplafond netinkoop, titel “NIET MERGEN”). Overige open PR’s (#218, #205, #110, #71, #69) wijzigen alleen backlog-documentatie.

**Geen live apparaten, geen live batterijsturing, geen arming, geen test-ontlading, en geen draaiende app.** Geen Vite, Docker, Playwright, screenshots of aanroep van `/api/system/restart`.

Startpunten uit #362, nagekeken op deze commit:

| Hint | Uitkomst |
|---|---|
| Taalmix in de navigatie en op Trading | Klopt. `ems/web/frontend/src/App.tsx` 862–921: Dashboard, Insights, Car, Chat, Trading, Manage naast Inrichten. Trading-kop “Test batterij” in `ems/web/frontend/src/Trading.tsx` 358, lichaam Engels. |
| Twee opslaanpatronen; instellingen op drie plekken | Klopt, en het zijn er drie patronen: sticky draft in Instellingen (`ems/web/frontend/src/Settings.tsx` 1471–1503), mix van direct opslaan en sticky bar op Auto (`ems/web/frontend/src/Car.tsx` 374–437 en 643–673), direct `POST /api/settings` op Trading (`ems/web/frontend/src/Trading.tsx` 98–116, 227–293). |
| `NumberInput` leeg → `min ?? 0` bij blur | Klopt. `ems/web/frontend/src/Settings.tsx` 283–284. |
| Tokens `restart_pending`, “Apply & restart”, “dry_run / mock” | Klopt. Zichtbare badge `restart_pending` op `ems/web/frontend/src/Settings.tsx` 325; save-tekst 1484; hint `control` 166–170. |
| ~75 hardgecodeerde hex-kleuren | Deels. In `ems/web/frontend/src/styles.css` staan **70** hex-literals (issue-hint ~75 telt ook `#`-refs in CSS-commentaren). **45** daarvan zitten in `:root` / `:root[data-theme="light"]` (regels 1–63). **25** staan daarbuiten (topbar, fallbacks, `#e05050`). |
| Breakpoints 480/520/560/640/700 | Klopt, plus **720** (`ems/web/frontend/src/styles.css` 2633). |
| Deep-link naar een Settings-sectie zit niet in de hash | Klopt. `ems/web/frontend/src/App.tsx` 453–456 en 544–555: `settingsSection` is React-state; de hash wordt `#manage` of `#manage/system` / `#manage/audit`. |
| Veiligheidsknoppen verspreid | Klopt: Trading (arm + test), Instellingen › Control & safety (`control.dry_run`, `control.operational`, zekering), Auto (batterijmodus tijdens laden). |

De juli-audit is op deze commit structureel verwerkt: sidebar met drie intent-groepen (`ems/web/frontend/src/Settings.tsx` 129–137), zoekveld (1288–1296), één sectie tegelijk, “More” op hulp (`FieldHelp` 214–230), bool → `role="switch"` (338–353), sticky save-bar (1471, CSS 2029–2058), mobiele drill-in ≤700 px (CSS 2050–2057). Die punten zijn hier geen open bevinding.

## 2. Per scherm

### 2.1 Globale header, topbar en hoofdnavigatie

Bestanden: `ems/web/frontend/src/App.tsx`, `ems/web/frontend/src/topbarChips.ts`, `ems/web/frontend/src/icons.tsx`, `ems/web/frontend/src/labels.ts`. Route: alle schermen.

| Element | Beoordeling |
|---|---|
| Navigatie & IA | Zeven knoppen met `aria-current="page"` (`ems/web/frontend/src/App.tsx` 862–921). Actieve staat is zichtbaar via `.nav-active`. De `<h1>` blijft “Smart Energy Manager” (827); `ems/web/frontend/index.html` titelregel 7 wijzigt niet per scherm. Browser-terug werkt voor de hash-views (`routeFromHash` 185–202, listener 495–498). |
| Visuele consistentie | De topbar forceert lichte tekst op een donkere scrim in beide thema’s (`ems/web/frontend/src/styles.css` 149–212, kleur `#f4f7ff` op regel 176). Dat is bewust voor de luchtfoto. Of het scrim-contrast op elk sky-moment voldoet, is runtime; de code legt de scrim vast als `rgba(6, 11, 30, 0.82→0)`. |
| Formulieren & input-controls | n.v.t. De balk heeft geen gegevensinvoer. |
| Teksten & microcopy | Navigatiewoorden zijn Engels, behalve Inrichten. De schrijfchip is Nederlands (`ems/web/frontend/src/labels.ts` `LIVE_WRITING` 56–64: “Live schrijven: JA/NEE”). |
| States | De onbereikbaarheidsbanner rendert alleen bij `view === "dashboard"` (`ems/web/frontend/src/App.tsx` 970). De dashboard-poll stopt buiten het dashboard (560). Op Insights, Auto, Trading en Beheer is die banner dus afwezig. |
| Responsiviteit/mobiel | `flex-wrap` op `.nav` (CSS 1407). ≤520 px neemt de titel de volle rij (215–217). Geen hamburgermenu. Touch-hoogte volgt uit `padding: 5px 12px` en `font-size: 0.82rem` (1408–1415): onder de 44 px-richtlijn. 390 px-wrapping is niet in de browser bekeken. |
| Toegankelijkheid | `<nav aria-label="Views">`, echte `<button>`s, `:focus-visible` (CSS 1420). `e2e/a11y.spec.ts` scant de nav mee op de pagina’s die de spec opent (dashboard, Insights, Manage, Car), niet op Trading, Chat, Inrichten, Audit of login. |
| Onboarding / eerste gebruik | n.v.t. voor de eerste-accountpoort. Inrichten staat wel altijd in de balk, ook na een groene checklist. |
| Veiligheidsgevoelige instellingen | De chip “Live schrijven” staat op elk scherm (`ems/web/frontend/src/App.tsx` 820–836, logica `ems/web/frontend/src/topbarChips.ts`). Dat is de juiste plek om te zien of er nú geschreven wordt. De chip stuurt niets. |

| ID | Probleem | Impact (voor de bewoner) | Voorstel | Ernst | Omvang | Bestandsverwijzing |
|---|---|---|---|---|---|---|
| NAV-01 | Zeven gelijkwaardige items, Engels naast “Inrichten”, vaste documenttitel. | Je ziet niet in één oogopslag welk scherm open is als de knoppen wrappen, en een schermlezer hoort steeds dezelfde paginatitel. | Groepeer dagelijks (Home, Inzichten, Auto) en instellen (Inrichten, Beheer). Zet `document.title` en een zichtbare paginakop per view. Woorden via #96. | Middel | M | `ems/web/frontend/src/App.tsx:827`, `ems/web/frontend/src/App.tsx:862-921`, `ems/web/frontend/index.html:7` |
| NAV-02 | Nav-knoppen, bel (32×32 px) en dagpijlen (30×30 px) zitten onder 44 px. | Op een telefoon mis je “Trading” of “Beheer”, of je tikt het buuritem. | Minimaal 44×44 px voor `.nav-btn`, `.notif-bell`, `.day-nav`, `.period-btn`, `.settings-nav-item`. | Middel | S | `ems/web/frontend/src/styles.css:222-229`, `ems/web/frontend/src/styles.css:556`, `ems/web/frontend/src/styles.css:1408-1415`, `ems/web/frontend/src/styles.css:1861-1865`, `ems/web/frontend/src/styles.css:2398-2400` |
| NAV-03 | “EMS is niet bereikbaar” staat alleen op het dashboard. | Op Beheer of Trading lijkt de app gezond terwijl de poll daar niet loopt. | Toon de bestaande `EMS_UNREACHABLE`-banner in de header op elke view. | Middel | S | `ems/web/frontend/src/App.tsx:560`, `ems/web/frontend/src/App.tsx:968-970`, `ems/web/frontend/src/labels.ts:18-25` |

### 2.2 Notificatie-bel en paneel

Bestand: `ems/web/frontend/src/Notifications.tsx`. Route: header, alle schermen.

| Element | Beoordeling |
|---|---|
| Navigatie & IA | De bel zit in de topbar, vóór de view-knoppen (`ems/web/frontend/src/App.tsx` 861). Het paneel is een overlay, geen route. Esc en klik-buiten sluiten (`ems/web/frontend/src/Notifications.tsx` 63–77). |
| Visuele consistentie | Zelfde lichte topbar-stijl als de nav (`ems/web/frontend/src/styles.css` 219–238). |
| Formulieren & input-controls | Eén actie: “Mark all read”, uit voor een reader (`canOperate`, regel 117). Geen invoervelden. |
| Teksten & microcopy | Engels: “Notifications”, “Mark all read”, “No notifications yet.”, “just now / 5m ago” (`relativeTime` 24–31). |
| States | Leeg: regel 129–130. Laden: geen skeleton; de lijst blijft leeg tot de eerste poll slaagt. Fout: `catch` houdt de laatste staat en toont geen fout (50–53). |
| Responsiviteit/mobiel | Bel is 32×32 px (zie NAV-02). Het paneel heeft geen eigen breakpoint in de gelezen CSS; of het binnen 390 px valt, is runtime. |
| Toegankelijkheid | `aria-expanded` en `aria-label` met ongelezen aantal (101–103). `role="dialog"` zonder `aria-modal`, zonder focus-verplaatsing en zonder focus-val (108–114). De ongelezen-stip is alleen visueel (106). |
| Onboarding / eerste gebruik | n.v.t. De bel verschijnt pas in de ingelogde schil. |
| Veiligheidsgevoelige instellingen | n.v.t. Markeren als gelezen schrijft niet naar de batterij. |

| ID | Probleem | Impact (voor de bewoner) | Voorstel | Ernst | Omvang | Bestandsverwijzing |
|---|---|---|---|---|---|---|
| NOT-01 | Dialoog zonder focusbeheer. Een mislukte poll is stil. | Toetsenbordgebruikers blijven op de bel terwijl het paneel open is. Een storing lijkt “geen meldingen”. | Bij openen focus in het paneel, Tab-val, `aria-modal="true"`. Bij fetch-fout een regel “Meldingen nu niet beschikbaar”. | Middel | S | `ems/web/frontend/src/Notifications.tsx:50-53`, `ems/web/frontend/src/Notifications.tsx:108-130` |
| NOT-02 | Tijd en knoppen zijn Engels (`5m ago`, “Mark all read”). | Een Nederlandstalige balk (“Live schrijven”) krijgt een Engelstalig paneel. | Zelfde woorden als #96; relatieve tijd in minuten/uur. | Laag | S | `ems/web/frontend/src/Notifications.tsx:24-31`, `ems/web/frontend/src/Notifications.tsx:125-130` |

### 2.3 Insights

Bestanden: `ems/web/frontend/src/Insights.tsx`, `ems/web/frontend/src/ScoreRing.tsx`, `ems/web/frontend/src/EnergyBehavior.tsx`, `ems/web/frontend/src/FinanceSection.tsx`, `ems/web/frontend/src/ConsumersCard.tsx`, `ems/web/frontend/src/HeatingAdvice.tsx`, `ems/web/frontend/src/WeekDigest.tsx`, `ems/web/frontend/src/WhatIf.tsx`, `ems/web/frontend/src/scoreCopy.ts`, `ems/web/frontend/src/savingsFraming.ts`. Route: `#insights`.

| Element | Beoordeling |
|---|---|
| Navigatie & IA | Paginakop “Insights” (`ems/web/frontend/src/Insights.tsx` 369). Periodeknoppen day/week/month/year (372–388) en ‹ › (391–414). Sectienav is in-page en schrijft bewust geen hash (commentaar 171–177, `aria-current` 243). Apparaten zitten bewust buiten die nav (356–358). |
| Visuele consistentie | Kaarten, ringen en tegels gebruiken tokens. Grafiekposities gebruiken `style={{}}` voor coördinaten (`ems/web/frontend/src/EnergyBehavior.tsx` 216–299, `ems/web/frontend/src/FinanceSection.tsx` 120, `ems/web/frontend/src/Insights.tsx` 132). Dat is layout, geen los kleurenpalet. |
| Formulieren & input-controls | Periode is een segmented control (`aria-pressed`). Wat-als: presets plus 7/14/30 dagen (`ems/web/frontend/src/WhatIf.tsx` 175–205), simulatie, geen “toepassen”. Weekdigest: uitklap en weekpijlen, geen schakelaar die instellingen schrijft. Verwarmingsadvies: “Mark as done” schrijft wél meteen (`ems/web/frontend/src/HeatingAdvice.tsx` 168–175, POST rond 245). |
| Teksten & microcopy | Scherm is Engels (“Your energy scores”, “What-if”, “Mark as done”). Euro-opmaak in de weekdigest kan Nederlands via `digestEurNl` (`ems/web/frontend/src/WeekDigest.tsx` importeert `ems/web/frontend/src/labels.ts`). |
| States | Rapport: “Loading…”, “Couldn't load this report.”, “No energy recorded…” (`ems/web/frontend/src/Insights.tsx` 416–421). Geen opnieuw-knop. Weekdigest geeft bij fout **én** tijdens laden `null` terug (`ems/web/frontend/src/WeekDigest.tsx` 75): het blok verdwijnt. |
| Responsiviteit/mobiel | `.insights-head` wrapt (CSS 2390–2393). Geldtegels worden een kolom ≤560 px (2729–2730). Dagpijlen 30 px (NAV-02). 390 px niet in de browser bekeken. |
| Toegankelijkheid | `aria-live="polite"` op het periodelabel (401). Ringen hebben een naam (`ems/web/frontend/src/ScoreRing.tsx`). `e2e/a11y.spec.ts` 60–64 wacht op `score-grid` en draait axe. Wat-als en weekdigest vallen daar alleen onder als ze op dat moment in de DOM staan; een fout-digest rendert niets. |
| Onboarding / eerste gebruik | n.v.t. als eerste poort. Het lege rapport (“No energy recorded”) zegt niet “ga naar Inrichten”. |
| Veiligheidsgevoelige instellingen | Wat-als posteert een simulatie en badge’t “nothing is changed” (`ems/web/frontend/src/WhatIf.tsx` 160). “Mark as done” wijzigt een adviesvlag, niet de batterijmodus. |

| ID | Probleem | Impact (voor de bewoner) | Voorstel | Ernst | Omvang | Bestandsverwijzing |
|---|---|---|---|---|---|---|
| INS-01 | Weekdigest rendert `null` bij fout en tijdens de eerste load. | Een mislukte weeksamenvatting lijkt alsof de functie er niet is. | Altijd de kop tonen; fout met “Opnieuw”, laden met één skeletonregel. | Middel | S | `ems/web/frontend/src/WeekDigest.tsx:73-75` |
| INS-02 | Periode, datum en wat-als-venster zitten alleen in `useState`. | Terug of een bladwijzer opent altijd “vandaag”, niet de maand die je bekeek. | Zet `period` en `date` in de hash (`#insights?period=week&date=`), zonder de app-router te breken (de sectienav mag in-page blijven). | Laag | M | `ems/web/frontend/src/Insights.tsx:287-288`, `ems/web/frontend/src/Insights.tsx:175-177` |
| INS-03 | “Mark as done” slaat direct op, in het Engels, los van de save-bar. | Een tik lijkt een notitie, maar schrijft een setting. | Zelfde draft- of undo-patroon als de save-bar, tekst “Gedaan” / “Ongedaan”. | Middel | S | `ems/web/frontend/src/HeatingAdvice.tsx:168-175`, `ems/web/frontend/src/HeatingAdvice.tsx:245` |
| INS-04 | Fout op het rapport heeft geen herstelactie. | Je moet de hele pagina verversen. | Knop “Opnieuw proberen” die hetzelfde effect als de periodewijziging opnieuw draait. | Laag | S | `ems/web/frontend/src/Insights.tsx:416` |

Dichtheid van dit scherm hoort bij **#93**. Grafiekprimitives bij **#100**. Geen aparte slice daarvoor.

### 2.4 Auto

Bestanden: `ems/web/frontend/src/Car.tsx`, `ems/web/frontend/src/CarCard.tsx`, `ems/web/frontend/src/ev.tsx`. Route: `#car`.

| Element | Beoordeling |
|---|---|
| Navigatie & IA | Geen eigen `<h2>`; de eerste kop is het label “Car” in de kaart (`ems/web/frontend/src/CarCard.tsx` 225–232). Dieper: laadadvies, batterijmodus, auto+schema, sessies. Link “Manage → Settings → Car” (`ems/web/frontend/src/Car.tsx` 568–571) opent sectie `ev` alleen in geheugen (`ems/web/frontend/src/App.tsx` 1121). |
| Visuele consistentie | Hergebruik van `.slider-row` voor SoC (`ems/web/frontend/src/CarCard.tsx` 181–197) en van de settings-save-bar (643–673). |
| Formulieren & input-controls | SoC is een slider 0–100 stap 5 met `<output>` — passend. Weekschema: `role="switch"` per dag, `type="time"` voor “Ready by”, `type="number"` voor minimum procent (`ems/web/frontend/src/ev.tsx` 93–137). Batterij-kWh is een kaal getalveld (606–618). Batterijmodus slaat **direct** op (`ems/web/frontend/src/Car.tsx` 374–437); schema en auto gebruiken de sticky bar. |
| Teksten & microcopy | Engels: “While the car charges”, “Hold the battery”, “Help with a fixed power”, “Cover the house automatically” (`ems/web/frontend/src/Car.tsx` 49–69). De hold-tekst zegt dat zon en net huis én auto dekken (61). |
| States | Sessies: “Reading charging history…”, lege tekst, lijst (`ems/web/frontend/src/Car.tsx` 94–99). Modus: “Saved” / fout (266–274), zonder `aria-live`. Uit-staat van de laadplanner heeft een lege kaart met een knop naar Instellingen (`ems/web/frontend/src/CarCard.tsx` 310–333). |
| Responsiviteit/mobiel | Schema-grid versmalt ≤480 px naar `40px 1fr 60px 84px` (`ems/web/frontend/src/styles.css` 1663–1691). Of “Monday” + tijdveld op 390 px knippen, is runtime; de code probeert horizontaal scrollen te vermijden (`min-width: 0`). |
| Toegankelijkheid | Radiogroep met pijltjes (`ems/web/frontend/src/Car.tsx` 160–167, 202–223). Schedule-inputs hebben `aria-label`. `e2e/a11y.spec.ts` 97–108 scant `car-view`. |
| Onboarding / eerste gebruik | Uit-staat legt uit dat de planner uit staat. De bewoner landt daarna in de Engelse Car-sectie van Instellingen, niet in een wizard. |
| Veiligheidsgevoelige instellingen | Moduskeuze `static_discharge` posteert meteen modus én wattage (`ems/web/frontend/src/Car.tsx` 418–428). Geen bevestiging. Boven huisverbruik verschijnt een waarschuwing nádat je het getal verlaat (253–257), niet vóór het opslaan. Default in code én schema: master aan, modus hold (`ems/web/frontend/src/Car.tsx` 42, `ems/settings.py` 339–340 en 347). |

| ID | Probleem | Impact (voor de bewoner) | Voorstel | Ernst | Omvang | Bestandsverwijzing |
|---|---|---|---|---|---|---|
| CAR-01 | “While the car charges” schrijft bij elke tik meteen naar `/api/settings`. Vaste-vermogensmodus stuurt ook het wattage mee. | Eén tik op “Help with a fixed power” laat de thuisbatterij tijdens het laden ontladen, zonder de save-bar of een gevolgzin vóóraf. | Zelfde sticky draft als het weekschema, plus één zin wat de batterij dan doet. Sessie-tekst op de kaart blijft #331. | Hoog | M | `ems/web/frontend/src/Car.tsx:49-52`, `ems/web/frontend/src/Car.tsx:374-428`, `ems/web/frontend/src/Car.tsx:177-193` |
| CAR-02 | Leegmaken van batterij-kWh zet meteen `0` in de draft (`Number("") === 0`). Leeg minimum-% in het schema wordt meteen 0. | Opslaan kan een accu van 0 kWh of een ochtenddoel van 0% vastleggen. De 422-fout komt in de balk, niet bij het veld. | Zelfde lege-veldregel als een stepper: ongeldig blokkeert Save en toont de fout onder het veld. | Middel | S | `ems/web/frontend/src/Car.tsx:614-617`, `ems/web/frontend/src/ev.tsx:119-122`, `ems/web/frontend/src/Car.tsx:506-508` |
| CAR-03 | Modus-copy is Engels en beschrijft het gedrag vóór een sessie, niet “EV laadt van het net” tijdens een sessie. | Je kunt denken dat de thuisbatterij de auto dekt. | Nederlandse radiokaarten. De sessieregel uit #331 hier aansluiten, geen tweede issue. | Middel | S | `ems/web/frontend/src/Car.tsx:49-69`, `ems/web/frontend/src/Car.tsx:253-257` |

**#331** dekt de eerlijke sessietekst. **#93** dekt de dichtheid van vier blokken op dit scherm. Deze review voegt daar het directe opslaan aan toe.

### 2.5 Chat

Bestand: `ems/web/frontend/src/ChatPanel.tsx`. Route: `#chat`.

| Element | Beoordeling |
|---|---|
| Navigatie & IA | Label “Ask the assistant”, geen `<h2>`. Verwijzing naar AI-instellingen is platte tekst, geen knop (`ems/web/frontend/src/ChatPanel.tsx` 107–109). |
| Visuele consistentie | Hergebruikt `override-head`, `badge`, `btn-primary`, FAQ-styling. |
| Formulieren & input-controls | Eén tekstveld met `aria-label="Your question"` en Send (159–175). Suggestiechips sturen meteen (122–130). Passend voor vrije tekst. |
| Teksten & microcopy | Engels, inclusief drie vaste suggesties (10–14). Taal van antwoorden komt van de explainer (`in ${language}`, 115). |
| States | `active === null` (nog aan het laden) toont al het invoerveld, want alleen `active === false` toont de uit-tekst (106–111). FAQ-fout wordt geslikt (40). Netwerkfout wordt een assistant-regel (60–64). Geen `aria-live` op nieuwe berichten. |
| Responsiviteit/mobiel | Berichten max. 88% (`ems/web/frontend/src/styles.css` 2201). Geen chat-specifiek 390 px-breakpoint. |
| Toegankelijkheid | Typ-indicator heeft `aria-label="Assistant is typing"` (145) maar geen live region. Niet opgenomen als eigen case in `e2e/a11y.spec.ts`. |
| Onboarding / eerste gebruik | Uit-staat legt uit dat FAQ werkt zonder AI en noemt het instellingenpad. |
| Veiligheidsgevoelige instellingen | n.v.t. Chat stuurt de batterij niet. Readers mogen niet verzenden (177–181). |

| ID | Probleem | Impact (voor de bewoner) | Voorstel | Ernst | Omvang | Bestandsverwijzing |
|---|---|---|---|---|---|---|
| CHT-01 | Het invoerveld verschijnt voordat bekend is of AI aan staat. Nieuwe antwoorden zijn niet live. | Je typt een vraag en krijgt daarna pas “AI off”, of een schermlezer hoort het antwoord niet. | Wacht op `/api/explainer` met “Laden…”. Zet `aria-live="polite"` op het log. Maak “AI explanations & chat” een knop naar sectie `ai`. | Middel | S | `ems/web/frontend/src/ChatPanel.tsx:17`, `ems/web/frontend/src/ChatPanel.tsx:106-115`, `ems/web/frontend/src/ChatPanel.tsx:136-147` |

### 2.6 Trading

Bestand: `ems/web/frontend/src/Trading.tsx`. Route: `#trading`.

| Element | Beoordeling |
|---|---|
| Navigatie & IA | Eigen `<h2>Trading</h2>` (168). Instellingen op deze pagina overlappen Control & safety en Planner (trading aan, drempel, cap, exportmodus, arm). |
| Visuele consistentie | Aparte `.trading-*` blokken onderaan `ems/web/frontend/src/styles.css` (3187–3199), geen kaarten met `--shadow` zoals de rest. |
| Formulieren & input-controls | “Enable trading” is een native checkbox, direct POST (227–237). Twee `type="number"` slaan op bij blur (250–274). Exportmodus is een `<select>`, direct POST. Geen eenheid in het veld. |
| Teksten & microcopy | Engels, met kop “Test batterij” (358) en toast “Test geweigerd” / “Test gestart” (131–135). Status noemt `EXPORT_FOR_PROFIT`, “dry-run / Watch only”, “Path T”, “Extra T−Z” (153–158, 191–203, 299–301). |
| States | Eerste load: “Laden…” (145–146). Fout: `role="alert"` met `String(e)` zoals `Error: HTTP 401` (175–177). Geen aparte lege staat. |
| Responsiviteit/mobiel | `.trading-metrics` is `repeat(3, 1fr)` (CSS 3192) zonder mediaquery. Of “Extra T−Z” op 390 px botst, is runtime. |
| Toegankelijkheid | Fout heeft `role="alert"`. Bevestigingspanelen (319–343, 380–403) zijn geen dialoog, verplaatsen de focus niet, en hebben geen `aria-live` op `testMsg`. Trading staat niet in `e2e/a11y.spec.ts`. |
| Onboarding / eerste gebruik | De intro noemt paden T en Z, niet “verkopen aan het net versus thuis houden” als enige kop. |
| Veiligheidsgevoelige instellingen | “Arm live export…” opent een tekstpaneel; “Yes, arm live export” zet `control.allow_export_discharge` (318–333). De knop blijft beschikbaar als Watch only aan staat; de tekst zegt dat floors blijven gelden (298–302), de knop wordt niet disabled. “Test batterij…” bevestigt een ontlading van ongeveer twee minuten op max vermogen (381–384) en is daarna `btn-primary`. Geen verplichte dry-run-stap in de UI. |

| ID | Probleem | Impact (voor de bewoner) | Voorstel | Ernst | Omvang | Bestandsverwijzing |
|---|---|---|---|---|---|---|
| TRD-01 | Live arming is één extra klik, ook als er nog niet naar een dry-run T−Z is gekeken, en ook terwijl schrijven geblokkeerd is. | Je bewapent export omdat de knop er is. De gevolgen (“forced DISCHARGE into the grid”) staan in het Engels onder de knop. | De drie stappen uit #339. Geen tweede issue. Tot die tijd: arm-knop disabled zolang `writes_allowed` false is, met de bestaande `block_reason` als zin. | Hoog | M | `ems/web/frontend/src/Trading.tsx:296-353` |
| TRD-02 | “Test batterij” start na één bevestiging een max-power discharge. Kop Nederlands, uitleg Engels, knop “Start test” is primair. | Het woord “test” klinkt ongevaarlijk. De zin “max-power discharge toward the reserve floor” staat pas in het bevestigingspaneel. | Destructieve knopstijl, Nederlandse gevolgzin, zelfde gate als arm (Watch only blokkeert al server-side; de knop mag dat vóór de klik tonen). Niet als kopie van #339: die issue dekt de arm-stappen, niet dit proefknop-patroon. | Hoog | S | `ems/web/frontend/src/Trading.tsx:357-414` |
| TRD-03 | Leeg drempel- of cap-veld wordt bij blur `0` en meteen opgeslagen (`Number("")` is eindig). | Je wist het veld om opnieuw te typen en legt “0 euro extra” of “onbeperkt exporteren” (cap 0 = uncapped, label regel 259) vast. | Draft + save-bar, of blur die leeg negeert en het vorige getal terugzet. | Hoog | S | `ems/web/frontend/src/Trading.tsx:239-275` |
| TRD-04 | Trading-instellingen zijn een derde formulier, met een native checkbox in plaats van de switch uit Instellingen. | “Enable trading” voelt anders dan “Watch only” twee schermen verderop. | Deze velden alleen op Trading, of alleen in Instellingen, met het switch-component uit `ems/web/frontend/src/Settings.tsx`. | Middel | M | `ems/web/frontend/src/Trading.tsx:225-293`, `ems/web/frontend/src/Settings.tsx:338-353` |

**#339** is de eigenaar van de arm-stappen. **#360** raakt dit scherm niet (dagplafond zit op Strategy).

### 2.7 Inrichten

Bestand: `ems/web/frontend/src/Setup.tsx`. Route: `#setup`.

| Element | Beoordeling |
|---|---|
| Navigatie & IA | Kop “Inrichten” (62). Elke regel is een knop naar een Settings-sectie (`onOpenSettings`, 86–92). De sectie zit niet in de URL (NAV/STG-patroon). |
| Visuele consistentie | Eigen checklist-CSS (1943–1966) op dezelfde panel-tokens. Statuskleur gebruikt `var(--danger, #b42318)` terwijl `--danger` niet in `:root` staat. |
| Formulieren & input-controls | n.v.t. op deze commit: read-only checklist, geen velden. |
| Teksten & microcopy | Nederlands in de vaste zinnen (63–66) en in de fout (53). Statussen komen van de API (`Werkt`, `Ontbreekt`, …). De knoppen openen Engelse Instellingen. |
| States | Fout met `role="alert"` (67–70), zonder opnieuw. Geen laadstaat: `data === null` toont alleen titel en lead (60–71). |
| Responsiviteit/mobiel | `flex-wrap` op de regel (CSS 1956). 390 px niet bekeken. |
| Toegankelijkheid | Knoppen zijn echte buttons. Geen koppeling in `e2e/a11y.spec.ts`. |
| Onboarding / eerste gebruik | Dit is de checklist uit #290, geen begeleide wizard. Na login opent hij niet vanzelf; hij is een nav-item. De lead zegt dat EMS in kijkmodus blijft (63–66). |
| Veiligheidsgevoelige instellingen | De checklist zelf schrijft niet. De knoppen zetten de bewoner in Control & safety, waar Watch only en “Let the system control” wél staan. |

| ID | Probleem | Impact (voor de bewoner) | Voorstel | Ernst | Omvang | Bestandsverwijzing |
|---|---|---|---|---|---|---|
| SETP-01 | Inrichten is een lijst met sprongen naar Instellingen, niet een stap die één ding vraagt en test. | De eerste keer beland je in “Battery (Indevolt)” of “Control & safety” zonder te zien wat de volgende veilige stap is. | De wizard-slices #291–#296. Open PR **#305** bouwt al de batterijstap op `ems/web/frontend/src/Setup.tsx`. Geen nieuw issue. | Hoog | L | `ems/web/frontend/src/Setup.tsx:37-93` |
| SETP-02 | Geen “Laden…” en geen opnieuw bij een fout. | Een trage of mislukte checklist lijkt een lege pagina met alleen de lead. | Laadregel en knop die het effect opnieuw doet. | Middel | S | `ems/web/frontend/src/Setup.tsx:39-71` |

### 2.8 Manage-shell

Bestand: `ems/web/frontend/src/Manage.tsx`. Routes: `#manage`, `#manage/system`, `#manage/audit`.

| Element | Beoordeling |
|---|---|
| Navigatie & IA | Subtabs Settings, System, Audit als `role="tablist"` met pijltjes en roving tabindex (`ems/web/frontend/src/Manage.tsx` 49–87). Hash rondtripped (`ems/web/frontend/src/App.tsx` 191–197, 547–551). Geen paginakop “Beheer”; de `<h2>` komt uit het kind. |
| Visuele consistentie | `.manage-tab` hergebruikt de nav-taal (`ems/web/frontend/src/styles.css` 1427–1444). |
| Formulieren & input-controls | n.v.t. De shell heeft alleen tabs. |
| Teksten & microcopy | Engels: Settings, System, Audit. `aria-label="Manage sections"`. |
| States | n.v.t. op de shell. Laden en fout zitten in de kinderen. |
| Responsiviteit/mobiel | `flex-wrap`, `max-width: 100%` (CSS 1429–1432). Tab-padding `6px 16px` (1437), onder 44 px. |
| Toegankelijkheid | Tabpatroon is in code compleet (`aria-selected`, `aria-controls`, `tabpanel`). System en Settings zitten in `e2e/a11y.spec.ts`; Audit niet. |
| Onboarding / eerste gebruik | n.v.t. |
| Veiligheidsgevoelige instellingen | n.v.t. op de shell. De tabs maken Control & safety, de test-vrije systeemtekst en het auditlog één klik diep. |

| ID | Probleem | Impact (voor de bewoner) | Voorstel | Ernst | Omvang | Bestandsverwijzing |
|---|---|---|---|---|---|---|
| MNG-01 | De subtabs heten Settings / System / Audit en zijn lager dan 44 px. | “Beheer” opent een tweede Engels menu. Op een telefoon is het raakvlak krap. | Labels via #96 (Instellingen, Systeem, Logboek). Zelfde 44 px-regel als NAV-02. | Middel | S | `ems/web/frontend/src/Manage.tsx:16-19`, `ems/web/frontend/src/styles.css:1434-1437` |

### 2.9 Instellingen

Bestanden: `ems/web/frontend/src/Settings.tsx`, `ems/web/frontend/src/settingsIcons.tsx`, `ems/web/frontend/src/AutomationBounds.tsx`, `ems/web/frontend/src/ExportModelConfirmBanner.tsx`. Route: `#manage`.

| Element | Beoordeling |
|---|---|
| Navigatie & IA | Sidebar: Your setup / How it runs / App (`ems/web/frontend/src/Settings.tsx` 131–137), zoeken, één sectie, mobiele drill-in. Sectie staat niet in de hash (diepe link alleen bij mount, 769–775). Terug in de browser wisselt van Beheer naar het vorige scherm, niet van sectie naar sectielijst; daar is “← All settings” (1350–1357) en die verschijnt alleen ≤700 px (CSS 2053–2056). |
| Visuele consistentie | Sluit aan op de juli-shell. Primaire/ghost-knoppen gedeeld. Save-bar is `position: fixed` (CSS 2029). |
| Formulieren & input-controls | Bool → switch. Enum → `<select>` met `humanize` of `SETTING_OPTION_LABEL` (361–372). Geheim → `password` (375–384). Getal → `type="number"`, eenheid tussen haakjes in het label (314), niet in het veld. Slider alleen als `field.slider` én min én max (252–270): in het schema o.a. leesinterval, SoC-leeftijd en `planner.solar_confidence` (`ems/settings.py` 385, 393, 432). Percentage reserve (`battery.min_reserve_soc`, min 5 max 50, eenheid `%`) is géén slider. Leeg getal bij blur wordt `field.min ?? 0` (283–284). Geen `onWheel`. |
| Teksten & microcopy | Sectietitels en hints Engels, inclusief “Battery (Indevolt)”, “dry_run / mock”, “Apply & restart” (139–170). Badge-tekst is het token `restart_pending` (325). Save-bevestiging herhaalt dat token (1484). Live-schrijven-paneel is Nederlands (`LIVE_WRITING_PANEL`, `ems/web/frontend/src/labels.ts` 77–83) op dezelfde pagina. |
| States | Laden: “Loading settings…” (1009). Fout: “Cannot load settings: ” plus de exception, geen opnieuw (1002–1007). Veldfouten onder het veld (397–399). Opgeslagen staat in de balk, zonder `aria-live` of `role="status"`. |
| Responsiviteit/mobiel | Drill-in ≤700 px is in CSS aanwezig. Sidebar-item `padding: 7px 9px` (1865). 390 px niet bekeken. |
| Toegankelijkheid | Labels `htmlFor`, zoeken `aria-label`, switches `role="switch"`. `e2e/a11y.spec.ts` 67–83 scant Settings en Access. Save-bar niet als live region. |
| Onboarding / eerste gebruik | Commissioning-velden (IP, token, locatie) staan nog tussen de dagelijkse secties. Weghalen is #296, na de wizard. |
| Veiligheidsgevoelige instellingen | `control.dry_run` label “Watch only (no battery writes)” en `control.operational` “Let the system control the battery” (`ems/settings.py` 277–291) renderen als gewone switches. Ze worden live ná Save én Apply & restart (hint 166–170). De restart-dialoog noemt het afbreken van een laadvenster (1212–1220). Er is geen aparte “weet je het zeker” op de switch zelf. Zekering is een enum met Nederlandse groottes in `SETTING_OPTION_LABEL` (`ems/web/frontend/src/Settings.tsx` 30–35) en een watt-veld bij custom. `AutomationBoundsCard` is read-only en wijst naar Trading voor “Sell to the grid” (`ems/web/frontend/src/AutomationBounds.tsx` 95–98). Exportmodel-banner slaat direct, in het Nederlands (`ems/web/frontend/src/ExportModelConfirmBanner.tsx` 27–48), buiten de save-bar. |

| ID | Probleem | Impact (voor de bewoner) | Voorstel | Ernst | Omvang | Bestandsverwijzing |
|---|---|---|---|---|---|---|
| STG-01 | Watch only en “Let the system control the battery” zijn dezelfde switch als een thema-toggle. De status daarna heet `restart_pending`. | Je zet sturen aan, denkt dat het klaar is, en leest een engineer-token. Of je zet Alleen kijken uit zonder de zin te lezen die zegt dat een herstart een laadvenster afbreekt. | Consequences vóór Save voor deze twee sleutels (de restart-tekst van 1214–1219 naar voren). Badge “Wacht op herstart” in plaats van `restart_pending`. | Hoog | M | `ems/web/frontend/src/Settings.tsx:166-170`, `ems/web/frontend/src/Settings.tsx:315-325`, `ems/web/frontend/src/Settings.tsx:1471-1485`, `ems/settings.py:277-291` |
| STG-02 | Een leeg getal wordt bij blur `min` of 0. Het muiswiel kan het getal wijzigen; er is geen `onWheel`. De eenheid staat in het label. | Scrollen over “Minimum reserve” of “Night reserve” en daarna ergens klikken zet een nieuwe grens in de draft. Leegmaken springt naar de ondergrens (reserve → 5). | Eenheid als suffix in het veld. Leeg = vorige waarde. `onWheel` + `blur()` op focus zodat scrollen de pagina schuift. Percentage-grenzen als stepper of slider. | Hoog | M | `ems/web/frontend/src/Settings.tsx:234-290`, `ems/web/frontend/src/Settings.tsx:312-314`, `ems/settings.py:185-191` |
| STG-03 | De open sectie zit niet in de hash. | Een bladwijzer of een gedeelde link opent Beheer op de eerste sectie, niet op Zekering of op de auto. Terug vanuit een tegel op het dashboard landt op `#manage` zonder sectie zodra je de nav gebruikt. | Hash `#manage/settings/<section>` die `routeFromHash` begrijpt. Mobiele “Alle instellingen” als history-stap. | Middel | M | `ems/web/frontend/src/App.tsx:453-456`, `ems/web/frontend/src/App.tsx:544-555`, `ems/web/frontend/src/Settings.tsx:769-775` |
| STG-04 | De save-bar kondigt “Saved — active now” niet aan een live region. | Wie niet naar de onderkant kijkt, hoort geen bevestiging. | `role="status"` `aria-live="polite"` op `.settings-savebar-msg`. Zelfde voor de Auto-balk. | Middel | S | `ems/web/frontend/src/Settings.tsx:1472-1486`, `ems/web/frontend/src/Car.tsx:645-660` |
| STG-05 | Sectietitels noemen HomeWizard, Indevolt en Tibber vast. | Een ander merk ziet andermans apparaat in de menustructuur. | Blijft #122. Geen nieuw issue. | Middel | L | `ems/web/frontend/src/Settings.tsx:139-144` |
| STG-06 | Zonder supervisor toont Apply changes het pad `scripts/restart.sh`. | Een bewoner krijgt een shell-pad als instructie. | “Herstart de app op de computer waar EMS draait.” Het scriptpad hoort in de hulp-disclosure. | Laag | S | `ems/web/frontend/src/Settings.tsx:1267-1276` |

Open PR **#360** voegt op Strategy een slider “Max. netinkoop per dag” toe (Nederlands label in `ems/web/frontend/src/labels.ts`, override in `ems/web/frontend/src/Settings.tsx`). Dat lost STG-01/STG-02 niet op; het is één extra Nederlandstalig veld op een Engelse pagina.

### 2.10 Home Assistant-koppelwizard

Bestanden: `ems/web/frontend/src/HaWizard.tsx`, `ems/web/frontend/src/haWizardHelpers.ts`. Route: `#manage`, sectie Appliances.

| Element | Beoordeling |
|---|---|
| Navigatie & IA | Stappen in de Appliances-sectie, met Back op de test-stap (`ems/web/frontend/src/HaWizard.tsx` 394–400). Geen eigen route. |
| Visuele consistentie | `.ha-wizard-*` op de panel-tokens. Foutkleur via ontbrekende `--danger` (CSS 1927). |
| Formulieren & input-controls | URL, token, checkboxes voor apparaten (426–429), handmatig entity-id `switch.washer` (441–452). Checkboxes zijn native, niet de switch van Instellingen. |
| Teksten & microcopy | Engels, inclusief een duidelijke http-waarschuwing (387–390). |
| States | `role="status"` en `role="alert"` (`ems/web/frontend/src/HaWizard.tsx` 207–215). Bezig-tekst “Testing…”. |
| Responsiviteit/mobiel | Geen eigen breakpoint gevonden voor de wizardlijst. 390 px niet bekeken. |
| Toegankelijkheid | Handmatig veld heeft een `<label for>`. Apparaat-checkbox zit in een `<label>` (424–436). Niet als aparte axe-case. |
| Onboarding / eerste gebruik | De wizard zit verstopt onder Appliances, niet in Inrichten. Ontdekking van Home Assistant in de setup-wizard is een latere slice van #94, niet deze checklist. |
| Veiligheidsgevoelige instellingen | Read-only richting apparaten (“EMS will ask Home Assistant for appliance entities (read-only)”, 392). URL en token hebben Apply & restart volgens de sectiehint (`ems/web/frontend/src/Settings.tsx` 162–164). |

| ID | Probleem | Impact (voor de bewoner) | Voorstel | Ernst | Omvang | Bestandsverwijzing |
|---|---|---|---|---|---|---|
| HAW-01 | Apparaten zijn native checkboxes; handmatig toevoegen vraagt een entity-id. | “switch.washer” is een Home Assistant-adres, geen apparaatnaam. | De lijst als de bestaande switch-rij met de naam groot en het id in de hulp. Entity-id achter “Geavanceerd”. | Middel | S | `ems/web/frontend/src/HaWizard.tsx:415-452` |

### 2.11 Access & security (gebruikers, uitnodigingen, API-tokens)

Bestanden: `ems/web/frontend/src/Admin.tsx`, `ems/web/frontend/src/AccountTokens.tsx`. Route: `#manage`, sectie Access, plus het Account-blok boven de sidebar.

| Element | Beoordeling |
|---|---|
| Navigatie & IA | Account (uitloggen + tokens) staat boven de panes voor elke ingelogde rol (`ems/web/frontend/src/Settings.tsx` 1139–1165). Gebruikerslijst alleen voor admin, in de sectie (`1380`). |
| Visuele consistentie | Zelfde `settings-access-bar` als Apply changes. |
| Formulieren & input-controls | Rol is een `<select>` dat bij `onChange` meteen PATCH’t (`ems/web/frontend/src/Admin.tsx` 203–207). Token-naam is tekst; tier is een select (184–195). Kopiëren via `useCopyToClipboard`. Tokenveld is `readOnly` tekst, eenmalig (210–226). |
| Teksten & microcopy | Engels: Users, Role, “Create”, “Copy it now — it's shown only once”, tiers “Read-only / Operate / Admin”. |
| States | Laden, `role="alert"` bij fout, `role="status"` bij een vers token of uitnodiging. 409 van de laatste admin komt inline (commentaar `ems/web/frontend/src/Admin.tsx` 9–12). |
| Responsiviteit/mobiel | Admin-rijen gebruiken de gedeelde lijst. 390 px niet bekeken. |
| Toegankelijkheid | Rol-select heeft een label (`ems/web/frontend/src/Admin.tsx` 200–202). Access-sectie zit in `e2e/a11y.spec.ts` 74–83. |
| Onboarding / eerste gebruik | Uitnodigen sluit aan op `AcceptInvite`. Het scherm legt niet uit dat “Operate” de batterij mag laten schakelen. |
| Veiligheidsgevoelige instellingen | Een Operate- of Admin-token is een sleutel naar sturing. Aanmaken is één knop na een naam (196–202), zonder gevolgzin. Rollen wijzigen zonder bevestiging; de server weigert de laatste admin. |

| ID | Probleem | Impact (voor de bewoner) | Voorstel | Ernst | Omvang | Bestandsverwijzing |
|---|---|---|---|---|---|---|
| ACC-01 | “Operate” / “Admin” bij een nieuw token heeft geen zin over batterijsturing. Aanmaken is direct. | Een token “voor het widget” met tier Operate kan schrijven, en die betekenis staat niet bij de knop. | Tier-labels in gewone taal (“Alleen kijken”, “Mag sturen”) en de eenmalige tokenzin ervoor, niet erna. | Middel | S | `ems/web/frontend/src/AccountTokens.tsx:52-63`, `ems/web/frontend/src/AccountTokens.tsx:184-226` |
| ACC-02 | Rol wijzigt bij het kiezen in de lijst, zonder bevestigen. | Een mis-tik op Admin geeft meteen die rol. De laatste-admin-weigering komt pas ná de fout. | Bevestigingsregel “Rol wijzigen naar beheerder?” voor admin, en voor het uitschakelen van een account. | Middel | S | `ems/web/frontend/src/Admin.tsx:203-207` |

### 2.12 Systeem

Bestanden: `ems/web/frontend/src/System.tsx`, `ems/web/frontend/src/DeviceHealth.tsx`, `ems/web/frontend/src/DecisionReasonDetails.tsx`. Route: `#manage/system`.

| Element | Beoordeling |
|---|---|
| Navigatie & IA | Actieregels springen naar een Settings-sectie via `onNavigate` (diepe link, zelfde hash-beperking). Geen eigen `<h2>` in de gelezen return; de blokken gebruiken `metric-label`. |
| Visuele consistentie | Checks gebruiken tokens, behalve `.dot-fail { background: #e05050 }` (`ems/web/frontend/src/styles.css` 2080) en `border: 1px solid var(--border)` op `.system-readiness` (2091–2093). `--border` is nergens gezet. |
| Formulieren & input-controls | n.v.t. Read-only plus deeplinks en exportlinks. |
| Teksten & microcopy | Herstelzinnen noemen `config.yaml`, `control.dry_run`, `operational`, SoC, MAPE, `dry_run/` in de control-loop (`ems/web/frontend/src/System.tsx` 311–314, 134–136, 674–682). `ems/web/frontend/src/DeviceHealth.tsx` mengt Nederlands (“zojuist”, “bijgewerkt om”, 32–36 en 78) met Engelse systeemkoppen. |
| States | “Checking the system…”, fout “Cannot load diagnostics: ” plus de exception, zonder opnieuw (`ems/web/frontend/src/System.tsx` 437–444). Incident- en accuracy-fouten verbergen het blok (best-effort). |
| Responsiviteit/mobiel | Check-grid `auto 1fr auto` (CSS 2067–2070). Lange control-loop-regel kan wrappen. 390 px niet bekeken. |
| Toegankelijkheid | `e2e/a11y.spec.ts` 85–95 scant System. Beslisreden is een `<dl>` met Engelse `dt`s (`ems/web/frontend/src/DecisionReasonDetails.tsx` 73–79: Summary, en gate-chips `validator`, `failsafe`, `dwell`). |
| Onboarding / eerste gebruik | n.v.t. |
| Veiligheidsgevoelige instellingen | Het scherm stuurt niet. De mode-herstelinstructie is wél het pad om Watch only en operational te begrijpen, en die zin is nu operator-Engels (`ems/web/frontend/src/System.tsx` 311–314). |

| ID | Probleem | Impact (voor de bewoner) | Voorstel | Ernst | Omvang | Bestandsverwijzing |
|---|---|---|---|---|---|---|
| SYS-01 | De herstelzin voor `mode` noemt Settings → operational, Watch only, `config.yaml`, `dev.mode` en `control.dry_run`. | Als sturen geblokkeerd is, is het hulpvenster het moeilijkste stuk tekst van de app. | Eén Nederlandse zin: wat er nú gebeurt, welke schakelaar, dat een herstart nodig is. `config.yaml` achter “Meer”. Vendor-zin “Indevolt IP” blijft #123. | Hoog | S | `ems/web/frontend/src/System.tsx:311-314` |
| SYS-02 | De control-loop toont `dry_run/0`, `fail_safe/0`, MAPE en “Clamped samples” als lopende tekst. | Het systeemblok leest als een log, niet als “alles in orde / dit heeft aandacht”. | De drie gezondheidsregels bovenaan houden; de tellers in het bestaande Advanced-patroon. | Middel | S | `ems/web/frontend/src/System.tsx:134-136`, `ems/web/frontend/src/System.tsx:656-682` |
| SYS-03 | Diagnosefout toont de ruwe exception en biedt geen opnieuw. | “Cannot load diagnostics: Error: HTTP 500” helpt niet. | Vaste zin + knop die `load()` opnieuw aanroept. | Middel | S | `ems/web/frontend/src/System.tsx:437-444` |

### 2.13 Audit-log

Bestand: `ems/web/frontend/src/AuditView.tsx`. Route: `#manage/audit`.

| Element | Beoordeling |
|---|---|
| Navigatie & IA | Filter en lijst onder het Manage-tabblad. Geen terug-naar-regel; de lijst is plat. |
| Visuele consistentie | Zelfde `override-head` en hint als andere beheerblokken. |
| Formulieren & input-controls | Eén `<select>` met `aria-label` (48–61). Passend voor een vaste lijst categorieën. |
| Teksten & microcopy | Engels: “Audit log”, “All changes”, “Battery decisions”, “Read-only.” Onbekende categorie valt terug op het ruwe token (78). |
| States | Laden: “Reading the change log…” (68). Leeg: vriendelijke zin (70–72). Fout vervangt het hele scherm inclusief het filter (43) en heeft geen `role="alert"` en geen opnieuw. |
| Responsiviteit/mobiel | Rijen zijn flex/list. 390 px niet bekeken. |
| Toegankelijkheid | Filter heeft een naam. Geen axe-case in `e2e/a11y.spec.ts`. |
| Onboarding / eerste gebruik | n.v.t. |
| Veiligheidsgevoelige instellingen | n.v.t. als bediening. Het log is wel de plek om te zien dát een arm of een moduswijziging is opgeslagen; `detail` wordt niet getoond (type regel 12, render 75–82 alleen `summary`). |

| ID | Probleem | Impact (voor de bewoner) | Voorstel | Ernst | Omvang | Bestandsverwijzing |
|---|---|---|---|---|---|---|
| AUD-01 | Een laadfout haalt het filter weg. De opgeslagen `detail` komt niet op het scherm. | Je kunt niet filteren na een hik, en een regel “config change” zegt niet welke sleutel (Watch only, arm, reserve). | Fout onder het filter met opnieuw. `<details>` met de sleutels uit `detail`, waarden gemaskeerd als het een geheim is. | Middel | S | `ems/web/frontend/src/AuditView.tsx:7-13`, `ems/web/frontend/src/AuditView.tsx:43-82` |

### 2.14 Eerste gebruik en auth

Bestanden: `ems/web/frontend/src/Onboarding.tsx`, `ems/web/frontend/src/Login.tsx`, `ems/web/frontend/src/AcceptInvite.tsx`, `ems/web/frontend/src/AuthLayout.tsx`. Routes: poort vóór de app, en `#/accept-invite`.

| Element | Beoordeling |
|---|---|
| Navigatie & IA | Drie schermen delen `AuthLayout` (één `<h1>`). Geen link naar Inrichten na login: `Login` roept `onDone` (28–29) en de app toont het dashboard. |
| Visuele consistentie | `.auth-screen` / `.auth-card` gebruiken `--bg`, `--panel`, `--radius` (`ems/web/frontend/src/styles.css` 3150–3185). Geen sky, bewust (`ems/web/frontend/src/AuthLayout.tsx` 8–14) zodat een 401 de nieuwe sessie niet wist. |
| Formulieren & input-controls | Tekst + wachtwoord, `autoComplete` gezet, labels gekoppeld. Wachtwoordhulp “At least 10 characters…” op onboarding en invite (`ems/web/frontend/src/Onboarding.tsx` 127, `ems/web/frontend/src/AcceptInvite.tsx` 85). Bestaande toegangstoken is `type="text"` (`ems/web/frontend/src/Onboarding.tsx` 133). |
| Teksten & microcopy | Engels. `AcceptInvite` zet `title="Join your home&apos;s EMS"` (60): in een JSX-attribuut blijft `&apos;` letterlijk staan. Login-fout “Invalid credentials” (25). |
| States | `role="alert"` op fouten. Onboarding-succes: “Admin account created”, knoppen “Continue to dashboard” en “Apply & restart” (76–96), `role="status"` op de herstarthint. |
| Responsiviteit/mobiel | Card `max-width: 420px`, `overflow-y: auto`, `100dvh` (CSS 3150–3171), bedoeld voor een open toetsenbord. 390 px niet bekeken. |
| Toegankelijkheid | Labels én `aria-label` op de inputs. Auth-schermen zitten niet in `e2e/a11y.spec.ts`. |
| Onboarding / eerste gebruik | Dit is alleen het eerste admin-account. De begeleide setup is #94. Na “Continue to dashboard” wijst niets naar Inrichten. |
| Veiligheidsgevoelige instellingen | “Apply & restart” op het succes-scherm posteert `/api/system/restart` (`ems/web/frontend/src/Onboarding.tsx` 52–56). De tekst noemt localhost en LAN (79–82), niet de batterij. De knop staat naast “Continue” als secundair. |

| ID | Probleem | Impact (voor de bewoner) | Voorstel | Ernst | Omvang | Bestandsverwijzing |
|---|---|---|---|---|---|---|
| AUTH-01 | Na het aanmaken van het account is de volgende stap “dashboard” of “Apply & restart”, niet Inrichten. | Je komt op een leeg dashboard zonder de checklist die uitlegt dat er nog niet gestuurd wordt. | Primaire knop “Ga naar Inrichten” zolang de checklist niet groen is. Hoort bij #295 (wizard opent na login). Geen nieuw issue. | Middel | S | `ems/web/frontend/src/Onboarding.tsx:76-96`, `ems/web/frontend/src/Login.tsx:28-29` |
| AUTH-02 | De uitnodigingstitel bevat de letterlijke tekst `&apos;`. | De kop leest “Join your home&apos;s EMS”. | Titel als JS-string `Join your home's EMS`, of Nederlandse kop “Account voor dit huis”. | Middel | S | `ems/web/frontend/src/AcceptInvite.tsx:60` |
| AUTH-03 | Het bestaande toegangstoken op het eerste scherm is een zichtbaar tekstveld. | Iemand die meekijkt leest het token mee. | `type="password"` met een toon-knop, zoals de geheime velden in Instellingen. | Laag | S | `ems/web/frontend/src/Onboarding.tsx:129-139` |

### 2.15 Gedeelde tokens, thema, iconen, labels

Bestanden: `ems/web/frontend/src/styles.css`, `ems/web/frontend/src/theme.ts`, `ems/web/frontend/src/icons.tsx`, `ems/web/frontend/src/labels.ts`, `ems/web/frontend/src/format.ts`.

| Element | Beoordeling |
|---|---|
| Navigatie & IA | n.v.t. Dit is geen scherm. |
| Visuele consistentie | Licht/donker via `html[data-theme]`. Eerste paint gebruikt `localStorage` vóór React (`ems/web/frontend/src/main.tsx` 7–8, `ems/web/frontend/src/theme.ts` 5–14, `ems/web/frontend/src/App.tsx` 473–503). Wijkt de serverwaarde af, dan volgt een tweede paint; een flits is dan mogelijk en hier niet geobserveerd. Reduced motion zet save-bar-, switch- en sky-animaties uit (`ems/web/frontend/src/styles.css` 105–107, 2045–2048). |
| Formulieren & input-controls | Het switch-uiterlijk (CSS 1996–2010) en de slider-rij zijn de gedeelde controles. Native checkbox blijft op Trading, HA en admin. |
| Teksten & microcopy | `ems/web/frontend/src/labels.ts` is deels Nederlands (schrijfchip, onbereikbaar, nachtreserve) en deels een humanize-laag voor Engelse tokens. Er is geen i18n-catalogus. SPEC §9.1 zegt nog “English-only in v1”; #362 en #96 vragen begrijpelijke taal voor de bewoner. De code zit tussen die twee in. |
| States | Skeletons bestaan (`.skel-line`, reduced motion op 487) en worden op deze schermen weinig gebruikt; de meeste laden als een tekstregel “Loading…”. |
| Responsiviteit/mobiel | Breakpoints in `ems/web/frontend/src/styles.css`: 480 (1112, 1264, 1688, 1709, 2538, 3010), 520 (215, 764, 1360), 560 (2267, 2495, 2729, 2753, 2943), 640 (414), 700 (2050), 720 (2633). Geen gedeelde `44px`-maat. |
| Toegankelijkheid | Focusring `outline: 2px solid var(--accent)` op de meeste knoppen. Contrast is in de licht-themacommentaren genoemd (regels 45–62) en niet in deze review nagerekend of gescreenshot. De axe-gate dekt niet elk niet-dashboardscherm. |
| Onboarding / eerste gebruik | n.v.t. |
| Veiligheidsgevoelige instellingen | n.v.t. als control. De tokens bepalen wel of een gevaarlijke actie eruitziet als een gewone primaire knop (`#07120e` op `--accent`, CSS 1732–1736): er is geen destructieve token. |

| ID | Probleem | Impact (voor de bewoner) | Voorstel | Ernst | Omvang | Bestandsverwijzing |
|---|---|---|---|---|---|---|
| TOK-01 | `--border`, `--danger` en `--warn-text` worden gebruikt en niet gezet in `:root`. | De rand van “What EMS may do”, van de systeemstatus en van verliesdagen in de weekdigest valt weg (ongeldige custom property). Fouttekst op Inrichten valt terug op hard `#b42318`, los van het thema. | Zet `--border: var(--line)`, `--danger: var(--error)`, `--warn-text: var(--amber-text)` in beide thema’s. Vervang `#e05050` op `.dot-fail`. | Middel | S | `ems/web/frontend/src/styles.css:1589`, `ems/web/frontend/src/styles.css:1616`, `ems/web/frontend/src/styles.css:1927-1928`, `ems/web/frontend/src/styles.css:1949`, `ems/web/frontend/src/styles.css:2080`, `ems/web/frontend/src/styles.css:2093`, `ems/web/frontend/src/styles.css:2487` |
| TOK-02 | 25 hex-literals ná het tokenblok, en zes breakpoint-breedtes. | Topbar, falen-stip en primaire knoptekst lopen uit de thema-schaal. Een telefoonfix op 560 px mist 520 px of 700 px. | Laat hex in `:root`. Eén `--bp-phone` (bijv. 700 px, waar de settings-drill-in al zit) en een `--hit-target: 44px`. | Middel | M | `ems/web/frontend/src/styles.css:176`, `ems/web/frontend/src/styles.css:2080`, `ems/web/frontend/src/styles.css:1732-1736`; mediaqueries op 215, 1112, 2050, 2633 |
| TOK-03 | Geen destructieve knopklasse. “Start test”, “Yes, arm live export” en “Apply & restart” gebruiken `btn-primary`. | Gevaar ziet er hetzelfde uit als “Save”. | Token `--danger-fill` en klasse `.btn-danger` voor arm, test en herstart-bevestigen. | Middel | S | `ems/web/frontend/src/styles.css:1732-1744`, `ems/web/frontend/src/Trading.tsx:326`, `ems/web/frontend/src/Trading.tsx:387`, `ems/web/frontend/src/Settings.tsx:1227` |

## 3. Schermoverstijgende patronen

### Navigatie

Hash-router voor zeven views en drie Manage-tabs (`ems/web/frontend/src/App.tsx` 181–202). Dat deel is bookmarkbaar en werkt met terug. De Settings-sectie, de Insights-periode en de mobiele drill-in doen dat niet. De `<h1>` en `document.title` blijven “Smart Energy Manager”. Zeven items wrappen; er is geen tweede niveau in de balk behalve de Manage-tabs.

### Opslaanpatroon

| Plek | Gedrag | Bestand |
|---|---|---|
| Instellingen (bijna alle velden) | Draft, sticky Save / Discard, daarna “active now” of herstart | `ems/web/frontend/src/Settings.tsx` 1471–1503 |
| Exportmodel-banner | Direct POST, buiten de balk | `ems/web/frontend/src/ExportModelConfirmBanner.tsx` 27–48 |
| Auto, batterij tijdens laden | Direct POST per tik | `ems/web/frontend/src/Car.tsx` 374–437 |
| Auto, schema en autokeuze | Eigen sticky balk | `ems/web/frontend/src/Car.tsx` 643–673 |
| Auto, SoC-anker | Direct “Set” | `ems/web/frontend/src/CarCard.tsx` 278–306 |
| Trading, vinkje, select, getallen | Direct POST | `ems/web/frontend/src/Trading.tsx` 98–116, 227–293 |
| Verwarmingsadvies | Direct POST | `ems/web/frontend/src/HeatingAdvice.tsx` 245 |
| Rollen en tokens | Direct PATCH/POST | `ems/web/frontend/src/Admin.tsx` 203–207, `ems/web/frontend/src/AccountTokens.tsx` 196–202 |

Eén patroon voor “dit verandert wat de batterij doet”: draft, gevolgzin, Save. Direct opslaan past bij “Mark all read” en bij een SoC die je bewust zet.

### Design tokens, hex, breakpoints

Palet in `:root` (regels 1–63) met aparte tekstkleuren voor licht (`--accent-text`, `--amber-text`, `--error`). Daarbuiten 25 hex-literals. Ontbrekende namen: `--border`, `--danger`, `--warn-text` (TOK-01). Breakpoints: 480, 520, 560, 640, 700, 720. Thema-flits bij gelijke localStorage en server is in code voorkomen; een afwijkende serverwaarde kan nog een tweede paint geven (`ems/web/frontend/src/App.tsx` 481–503). Niet runtime gecontroleerd.

### Taal

SPEC §9.1: Engels in v1, voorbereid op i18n. De code is al gemengd: navigatie grotendeels Engels, Inrichten en de schrijfchip Nederlands, Instellingen Engels inclusief `restart_pending`, Trading “Test batterij” plus Engels lichaam, Device Health deels Nederlands. Eigenaar: **#96**. Geen nieuw taal-issue.

### Input-control per datatype

| Datatype | Nu | Passender op dit scherm |
|---|---|---|
| Aan/uit | Switch in Instellingen en weekschema; native checkbox op Trading en in de HA-lijst | Overal de switch |
| Klein aantal keuzes | `<select>` (zekering, exportmodus, rol) | Segmented of radiokaarten als er ≤5 opties zijn; de zekering-enum is daar een kandidaat |
| Percentage met min/max | Slider alleen voor solar confidence; reserve is een number | Slider of stepper met `%` in het veld |
| Vermogen / kWh / euro | `type="number"`, eenheid in het label of nergens | Suffix in het veld, lege waarde behoudt de vorige |
| Tijd | `type="time"` in het weekschema | Houden |
| Vrije tekst / geheim | text / password | Houden; toegangstoken op onboarding ook password |
| Zoeken | `type="search"` | Houden |

### States

Laden is bijna overal een zin, niet een skeleton. Fouten zijn ongelijk: Trading en Inrichten gebruiken `role="alert"`; Systeem, Audit en Instellingen dumpen `String(error)`; Weekdigest en de notificatiepoll verbergen de fout. Offline op niet-dashboardschermen: zie NAV-03.

### Veiligheidsgevoelige bediening

| Bediening | Waar | Wat er nú live is | Bevestiging | Per ongeluk |
|---|---|---|---|---|
| Live schrijven-chip | Header | Ja, read-only | n.v.t. | n.v.t. |
| Watch only / Let the system control | Instellingen › Control | Pas na Save + Apply & restart; badge `restart_pending` | Restart-dialoog nádat het al opgeslagen is | Gewone switch; geen wiel (bool) |
| Minimum reserve, nachtreserve, zekering-watt | Instellingen › Battery / Control | Na Save (“active now” tenzij `applies=restart`) | Alleen de algemene balk | Wiel en leeg → min (STG-02) |
| Batterijmodus tijdens laden | Auto | Meteen na de tik | Geen | Eén tik (CAR-01) |
| Live export arm | Trading | Meteen na “Yes, arm…”; schrijven kan alsnog door floors geblokkeerd zijn | Eén paneel | Knop niet disabled bij Watch only (TRD-01) |
| Test-ontlading | Trading | Na “Start test”, als de server het toestaat | Eén paneel | Primaire knop, woord “test” (TRD-02) |
| Trading-drempel en cap | Trading | Bij blur | Geen | Leeg → 0 (TRD-03) |

## 4. Top 10

Volgorde: eerst wat de batterij kan wijzigen of wat een bewoner misleest op het moment van sturen.

| # | ID | Waarom deze volgorde |
|---|---|---|
| 1 | STG-01 | Watch only en “Let the system control” zijn de hoofdschakelaar. De feedback heet `restart_pending`. |
| 2 | CAR-01 | Batterijgedrag tijdens laden slaat op zonder draft of gevolgzin. |
| 3 | TRD-01 | Live export-arm zonder de dry-run-stap uit #339. |
| 4 | TRD-02 | Test-ontlading op max vermogen, lichte bevestiging, gemengde taal. |
| 5 | TRD-03 | Leeg getal op Trading wordt meteen 0 (drempel of “uncapped”). |
| 6 | STG-02 | Reserve en andere grenzen: leeg veld en muiswiel. |
| 7 | SYS-01 | Als sturen blokkeert, is de hersteltekst `config.yaml` / `dry_run`. |
| 8 | SETP-01 | Eerste gebruik wijst naar die Engelse schakelaars in plaats van een wizard. Dekking: #94 en #291–#296, PR #305. |
| 9 | STG-03 | Je kunt Zekering of Control niet bookmarken of terugvinden via de URL. |
| 10 | TOK-03 | Arm, test en herstart zien eruit als Save. |

STG-03 en TOK-03 zijn Middel en staan er omdat ze de hoge items versterken (terugvinden en herkennen van de gevaarlijke knop). De overige acht zijn Hoog.

## 5. Voorgestelde dunne vervolgslices

Geen nieuwe issues voor werk dat al een nummer heeft. Die staan onderaan.

| Slice | Wat | Scope | Ernst / omvang | Overlap |
|---|---|---|---|---|
| Bevestig sturen in Instellingen | Vóór Save een gevolgzin voor `control.dry_run` en `control.operational`. Badge “Wacht op herstart” in plaats van `restart_pending`. | `ems/web/frontend/src/Settings.tsx` badges, hint, save-bar | Hoog / M | Geen overlap met #339 (die is de Trading-arm). Geen overlap met #331. |
| Zelfde draft voor de automodus | De drie radiokaarten en het wattage gebruiken de Auto-save-bar, niet een POST per tik. | `ems/web/frontend/src/Car.tsx` `postCarMode` | Hoog / M | #331 blijft de sessietekst. Dit issue dekt het opslaan niet. |
| Getalvelden die niet verspringen | Leeg behoudt de vorige waarde; eenheid in het veld; wiel wijzigt het getal niet. Eerst reserve, nachtreserve, zekering-watt, Trading-drempel en cap. | `NumberInput` in `ems/web/frontend/src/Settings.tsx`, de twee inputs in `ems/web/frontend/src/Trading.tsx` | Hoog / M | Geen overlap. PR #360 voegt een slider toe en lost dit niet op. |
| Testknop als gevaarlijke actie | Nederlandse gevolgzin, `.btn-danger`, knop uit wanneer de server de test toch weigert, reden zichtbaar vóór de klik. | `ems/web/frontend/src/Trading.tsx` test-kaart, nieuwe knopklasse in `ems/web/frontend/src/styles.css` | Hoog / S | #339 dekt de arm-stappen, niet deze proefknop. Geen duplicaat. |
| Settings-sectie in de hash | `#manage/settings/<section>` rondtrippen, inclusief de diepe links vanuit Inrichten, Auto en Systeem. | `routeFromHash` / `navigate` in `ems/web/frontend/src/App.tsx` | Middel / M | Geen overlap. |
| Ontbrekende toestanden | Weekdigest-fout zichtbaar; Inrichten laad/opnieuw; onbereikbaarheidsbanner ook buiten het dashboard; Audit-fout laat het filter staan. | `ems/web/frontend/src/WeekDigest.tsx`, `ems/web/frontend/src/Setup.tsx`, `ems/web/frontend/src/App.tsx`, `ems/web/frontend/src/AuditView.tsx` | Middel / S | Geen overlap. |
| Tokens die de schermen al verwachten | `--border`, `--danger`, `--warn-text` definiëren; `.dot-fail` op het fouttoken; `.btn-danger`. | `ems/web/frontend/src/styles.css` `:root` en de regels uit TOK-01/TOK-03 | Middel / S | #93 gaat over dichtheid, niet over deze properties. |

Niet als nieuw issue:

| Onderwerp | Eigenaar |
|---|---|
| Nederlands voor veiligheidsteksten, nav en Trading | #96 |
| Wizard na de checklist (batterij, P1, tarief, reserve, locatie, afronden) | #94, #291, #292, #293, #294, #295, #296. Open PR #305 bouwt de batterijstap. |
| Trading-arm pas na dry-run T−Z | #339 |
| Eerlijke tekst tijdens een laadsessie | #331 |
| Dichtheid Insights / Beheer / Auto | #93 |
| Vendornamen uit de adapter | #122 |
| Verbergen wat de adapter niet kan, inclusief “Indevolt IP” | #123 |
| Gedeelde data-laag en chart-primitives | #99, #100 |
| Dagplafond netinkoop als Strategy-slider | Open PR #360 (issue #317), niet mergen volgens de PR-titel |
