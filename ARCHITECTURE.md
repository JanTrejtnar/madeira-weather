# Madeira Microclimate & Sunrise Predictor — Technická specifikace

> Stav: **schváleno, implementace Phase 1 zahájena**
> Verze dokumentu: 0.2 · Datum: 2026-10-12

---

## 0. TL;DR

- Nástroj stahuje numerická předpovědní data z **Open-Meteo** pro 5 lokalit na Madeiře, počítá **explainable heuristické skóre (0–100 %)** pro východ slunce na Pico do Arieiro a **pravděpodobnost „moře mraků“ / teplotní inverze**, a porovnává mikroklimata napříč ostrovem.
- Stack pro MVP: **Python 3.12 + httpx + Pydantic + Typer (CLI)**; později FastAPI a Telegram bot (aiogram / python-telegram-bot).
- Architektura **Fetchers → Analyzers → Presenters** s čistými datovými modely (Pydantic) mezi vrstvami; analyzéry jsou čisté funkce bez I/O (snadné testování a kalibrace).
- Největší riziko: **hrubé rozlišení modelů (~2–11 km) vs. strmý terén Madeiry**. Proto je skóre heuristika, ne fyzikální pravda → nutná **validace proti webkamerám a vlastním pozorováním** (Phase 2) a **kalibrace vah**.

---

## 0.1 Schválená rozhodnutí (2026-10-12)

| # | Rozhodnutí |
|---|-----------|
| 1 | Stack: **Python** (na stroji je 3.11 → `requires-python >=3.11`; správa přes `venv` + `pip`, `uv` není nainstalováno) |
| 2 | **Moře mraků je neutrální.** Dobrý východ = s mraky pod vrcholem i bez nich. Rozhoduje, že na Arieiru není mlha/mrak/déšť, který brání výhledu. `S_cloudsea` se **neváží do skóre**, inverze je jen informativní (a vysvětluje „proč“). Váhu přebírá `S_sky` a `S_visibility`. |
| 3 | Bot poběží na **domácím stroji** (cron / Plánovač úloh Windows) |
| 4 | Lokality beze změny (5 + volitelný `east_horizon`) |
| 5 | Webkamery **nejsou esenciální**. Pokud je scraping náročný nebo nevhodný, Phase 2 se omezí jen na validaci ručními pozorováními |
| 6 | Kalibrace vah **na historických pozorováních**: porovnání předpovědi vs. realita za konkrétní dny (backtest přes Open-Meteo Historical Forecast API + ruční hodnocení) |
| 7 | Výstup česky |
| 8 | Horizont 7 dní stačí |

**Dopad na skóre (3.3):** nové výchozí váhy `S_sky 0.45`, `S_color 0.15`, `S_visibility 0.25`, `S_wind 0.15` (součet 1.0 po normalizaci vah; `S_cloudsea` = 0). Inverze se jen reportuje. Konfigurační přepínač `prefer_cloud_sea` zůstává (výchozí 0).

---

## 1. Business požadavky & Use-cases

### 1.1 Uživatel a kontext
Jeden primární uživatel (osobní nástroj), potenciálně sdílený s přáteli/cestovateli. Typický scénář: pobyt na Madeiře, rozhodování večer předem, zda vstávat na východ slunce (jízda Funchal → Arieiro ≈ 35–45 min, vstávání ~4:45–5:30 podle sezóny).

### 1.2 Use-cases

| ID | Otázka uživatele | Výstup | Priorita |
|----|------------------|--------|----------|
| UC1 | „Mám zítra vstávat ve 5:30 na Arieiro?“ | Verdikt GO / MAYBE / NO-GO + skóre 0–100 % + 3 hlavní důvody + doba východu slunce + doporučený čas odjezdu | **MUST** |
| UC2 | „Bude zítra na Arieiro moře mraků (inverze)?“ | Pravděpodobnost inverze 0–100 % + odhad výšky vrstvy | **MUST** |
| UC3 | „Kde na ostrově bude zítra odpoledne jasno?“ | Žebříček lokalit pro zadané časové okno (např. 13–18 h) s jasem/srážkami/větrem | **MUST** |
| UC4 | „Který ze 3–7 dnů je nejlepší na východ slunce?“ | Tabulka/graf skóre po dnech (7denní výhled, s klesající důvěrou) | SHOULD |
| UC5 | „Je teď aktuálně vidět? Jak to vypadá nahoře?“ | Aktuální snímek z webkamer + srovnání s předpovědí | SHOULD (Phase 2) |
| UC6 | „Co si vzít na sebe?“ | Teplota, pocitová teplota (vítr), srážky na Arieiro v čase východu | SHOULD |
| UC7 | Ranní digest v 20:00 (pro zítřek) a 04:30 (aktualizace) | Push zpráva v Telegramu | SHOULD (Phase 3) |
| UC8 | „Jak přesná byla včerejší předpověď?“ | Historie predikcí vs. pozorování, metriky přesnosti | COULD |

### 1.3 Nefunkční požadavky
- **Latence:** CLI odpověď < 5 s (cache TTL 30–60 min pro Open-Meteo).
- **Náklady:** 0 € (free tier Open-Meteo je pro nekomerční použití; Telegram zdarma; hosting na malém VPS / domácím stroji / GitHub Actions cron).
- **Vysvětlitelnost:** každé skóre musí být rozloženo na komponenty (žádná černá skříňka).
- **Odolnost:** výpadek jednoho zdroje (např. webkamera) nesmí shodit celý výstup — graceful degradation.
- **Jazyk UI:** čeština (výstup), kód a identifikátory anglicky.
- **Časové zóny:** Madeira = `Atlantic/Madeira` (UTC+0 v zimě, UTC+1 v létě, DST se mění). **Všechna interní data v UTC, prezentace v lokálním čase.**

### 1.4 Mimo rozsah (explicitně)
Komerční provoz, mobilní nativní aplikace, vlastní numerický model, předpověď pro jiné ostrovy, bezpečnostní varování pro turistiku (jen informativní poznámka o větru).

---

## 2. Datové zdroje

### 2.1 Open-Meteo Forecast API

**Endpoint:** `https://api.open-meteo.com/v1/forecast` (bez API klíče, nekomerční použití; limity free tieru je třeba ověřit v aktuální dokumentaci před nasazením — cca desítky tisíc volání/den).

**Lokality** (souřadnice jsou přibližné, k ověření/doladění při implementaci):

| Klíč | Lokalita | Lat | Lon | Elevace | Role v analýze |
|------|----------|-----|-----|---------|----------------|
| `funchal` | Funchal | 32.6669 | -16.9241 | ~25 m | Jižní pobřeží, referenční „spodní“ stanice, základna inverze |
| `arieiro` | Pico do Arieiro | 32.7358 | -16.9291 | **1818 m** | Cílová lokalita; vrchol nad inverzní vrstvou |
| `sao_vicente` | São Vicente | 32.7983 | -17.0381 | ~15 m | Severní pobřeží (návětrná strana, pasát) |
| `paul_da_serra` | Paul da Serra | 32.7500 | -17.1167 | ~1500 m | Západní náhorní plošina, srovnání s Arieirem |
| `ponta_do_sol` | Ponta do Sol | 32.6811 | -17.1022 | ~30 m | Jihozápad, „nejslunnější“ pobřeží |

**Doporučené doplňkové body (volitelné, Phase 1.5):**
- `east_horizon` — bod na moři/východním výběžku (např. okolí Machica / Ponta de São Lourenço, cca 32.72, -16.77) pro odhad oblačnosti **na východním obzoru**, kam se dívá pozorovatel při východu slunce. Oblačnost přímo nad Arieirem je pro východ slunce méně důležitá než ta na východě.

**Volání:** Open-Meteo podporuje více lokalit v jednom requestu (`latitude=a,b,c&longitude=x,y,z`) → 1 HTTP volání pro všech 5–6 bodů. Parametr `elevation=` přepíše výšku pro downscaling teploty (u Arieira explicitně 1818 m, protože model grid cell má nižší průměrnou výšku).

#### 2.1.1 Hodinové proměnné (`hourly=`)

| Proměnná | Proč |
|----------|------|
| `cloud_cover_low`, `cloud_cover_mid`, `cloud_cover_high`, `cloud_cover` | Jádro analýzy; low = pod ~2 km (na Madeiře zahrnuje i vrstvu moře mraků!) |
| `temperature_2m`, `apparent_temperature` | Komfort, rozdíl pobřeží vs. hora |
| `dew_point_2m`, `relative_humidity_2m` | Blízkost nasycení → mlha/oblačnost na vrcholu |
| `precipitation`, `precipitation_probability`, `rain`, `showers` | Hard gate na skóre |
| `wind_speed_10m`, `wind_direction_10m`, `wind_gusts_10m` | Komfort/bezpečnost na hřebeni, směr pasátu (SV) |
| `visibility` | Mlha/dohlednost |
| `pressure_msl` | Kontext synoptiky, tlaková tendence |
| `freezing_level_height` | Orientační zimní ověření (nízký význam na Madeiře) |
| `is_day`, `shortwave_radiation` (volitelně) | Odpolední „jasno“ (UC3) |

**Tlakové hladiny (klíčové pro inverzi)** — Open-Meteo je nabízí jako `temperature_<L>hPa`, `relative_humidity_<L>hPa`, `cloud_cover_<L>hPa`, `wind_speed_<L>hPa`, `geopotential_height_<L>hPa`:

| Hladina | Přibližná výška | Použití |
|---------|-----------------|---------|
| 1000 hPa | ~100 m | Přízemí / pobřeží |
| 925 hPa | ~750 m | Typický vrchol moře mraků (stratocumuly pasátové inverze) |
| 850 hPa | ~1500 m | Těsně pod Arieirem; inverzní vrstva |
| 700 hPa | ~3000 m | Suchý vzduch nad inverzí (subsidence) |

*Pozn.: reálná výška hladiny se mění; využít `geopotential_height_<L>hPa` pro přesné určení.*

#### 2.1.2 Denní proměnné (`daily=`)
`sunrise`, `sunset`, `daylight_duration`, `precipitation_sum`, `temperature_2m_max/min`, `wind_gusts_10m_max`.

#### 2.1.3 Další parametry
`timezone=UTC` (interně; převod až v prezentaci), `forecast_days=7`, `models=` — viz níže.

#### 2.1.4 Strategie modelů a nejistota
- Základ: `best_match` (Open-Meteo vybere model automaticky).
- Pro **důvěru** stahovat **2–3 modely explicitně** (např. `ecmwf_ifs025`, `icon_global`/`icon_eu`, `gfs_seamless`, případně `meteofrance_arpege_europe`) a měřit **rozptyl** (spread) klíčových veličin. Velký nesouhlas modelů → snížit `confidence` a rozšířit pásmo (např. „45–75 %“).
- Volitelně **Ensemble API** (`ensemble-api.open-meteo.com`) pro pravděpodobnost přímo z členů ensemblu (např. % členů s `cloud_cover_low < 30 %`). Zvážit až po MVP.
- **Upozornění k rozlišení:** ostrov je ~57 × 22 km; modely mají mřížku ~7–25 km. Pico do Arieiro, údolí a závětří jsou **sub-grid**. Proto: (a) používat tlakové hladiny a rozdíly mezi body, ne absolutní hodnoty; (b) kalibrovat proti pozorování; (c) komunikovat nejistotu.

#### 2.1.5 Cache & chyby
- File cache (SQLite / JSON) klíč = hash(parametry), TTL 30–60 min (modely se aktualizují 1×/h až 4×/den).
- Retry s exponenciálním backoffem (max 3×), timeout 10 s, respektovat HTTP 429.
- Uchovávat **raw JSON odpovědi** (pro replay/ladění a pozdější kalibraci).

### 2.2 Webkamery (NetMadeira a další)

**Cíl:** ground truth pro validaci (byl východ slunce opravdu vidět? je pod Arieirem moře mraků?) a aktuální obraz (UC5).

#### 2.2.1 Kandidátní zdroje
1. **NetMadeira** (netmadeira.com) — agregátor webkamer po celém ostrově (Pico do Arieiro, Funchal, Paul da Serra, severní pobřeží apod.). *Konkrétní URL kamer a způsob publikace snímků (statický JPG s timestampem / stream / iframe) je nutné ověřit v discovery spike před implementací.*
2. **Alternativy / záloha:** Windy Webcams API (vyžaduje API klíč, free tier), webkamery hotelů/obcí/Visit Madeira, Madeira Meteo/IPMA (oficiální stanice a radar — případně jako číselná validace místo obrazu).
3. **Satelitní snímky** (EUMETSAT / IPMA, viditelné pásmo) jako doplňkový zdroj pro „kde je oblačnost“ — Phase 3+.

#### 2.2.2 Právní a etická pravidla (závazná část designu)
- Před implementací **přečíst Terms of Use a `robots.txt`** a případně kontaktovat provozovatele. Pokud scraping zakazují, použít jen odkazy / ruční ověření / oficiální API (Windy).
- Nízká frekvence (max 1 snímek / kamera / 10–15 min, jen v relevantních oknech — okolo východu slunce a při dotazu), vlastní `User-Agent` s kontaktem, respektovat `Cache-Control`/`ETag`/`Last-Modified`.
- Nepřeposílat/neveřejnit cizí snímky (jen lokální analýza, ve výstupu odkaz na zdroj). Žádné obcházení ochran.

#### 2.2.3 Technická strategie
```
CameraSource (abstrakce)
 ├─ discover()      → seznam kamer + metadata (id, název, lat/lon, směr pohledu, URL)
 ├─ fetch_latest()  → (image_bytes, captured_at, etag)
 └─ health()        → stáří snímku, dostupnost
```
- **Preferovaný způsob získání:** přímý odkaz na obrázek (JPG) → `httpx` GET. HTML scraping (BeautifulSoup / selectolax) jen pro objevení URL; headless browser (Playwright) **až poslední možnost**.
- **Kontrola čerstvosti:** zahodit snímek starší než N minut / s nezměněným hashem (zamrzlá kamera) / tmavý (noc).
- **Konfigurace kamer v YAML** (`cameras.yaml`) – přidání kamery bez změny kódu.

#### 2.2.4 Analýza snímku (Phase 2)
Postupně od jednoduchého ke složitému:
1. **Heuristiky klasické CV (OpenCV/Pillow):** podíl „bílé/šedé“ (oblačnost) vs. modré oblohy v ROI nad horizontem, průměrný jas/kontrast, rozptyl (Laplacian variance → mlha = nízký kontrast), barevnost oblohy (oranžová/růžová při východu).
2. **ROI (region of interest) per kamera** v konfiguraci (která část obrazu je obloha, která údolí → detekce moře mraků = homogenní bílá plocha *pod* úrovní kamery).
3. **Volitelně Claude Vision / VLM** pro klasifikaci do štítků (`clear`, `partly_cloudy`, `fog`, `cloud_sea`, `sunrise_visible`) – pomalejší a placené, vhodné jako „labeler“ pro tvorbu validačního datasetu, ne pro každý běh.
4. Výstup: `CameraObservation` (viz modely) — porovnatelný s předpovědí.

---

## 3. Logika & heuristika hodnocení

### 3.0 Fyzikální kontext (proč to takhle počítat)
- Na Madeiře dominuje **pasát ze SV**. Vlhký vzduch narazí na sever ostrova, vznikají **stratocumuly pod pasátovou inverzí** (typicky ~800–1600 m). Hřeben (Arieiro 1818 m) často vyčnívá **nad** oblačnost → „moře mraků“ pod nohama, ideální východ slunce.
- **Pasátová/subsidenční inverze:** teplota roste s výškou nad vlhkou vrstvou; nad inverzí je vzduch **suchý a stabilní**.
- Špatný scénář: vlhká vrstva sahá **nad** vrchol (mlha/déšť na Arieiro, `cloud_cover_low` ~100 %, nízká dohlednost), nebo fronta/hluboká konvekce, nebo silný vítr a srážky.
- Dobrý scénář pro **barvy**: čistý východní obzor + trocha střední/vysoké oblačnosti nad hlavou (zachytí barvy), málo nízké oblačnosti *na úrovni vrcholu*.

### 3.1 Vstupní feature set (derivované veličiny)

Všechny hodnoty se vzorkují **v okně kolem východu slunce** (např. `sunrise − 60 min … sunrise + 30 min`), lineární interpolací mezi hodinovými body, a agregují se (průměr/maximum dle pravidla).

| Feature | Výpočet |
|---------|---------|
| `low_cloud_summit` | `cloud_cover_low` @ arieiro (agregace: max v okně) |
| `mid_cloud`, `high_cloud` | @ arieiro, průměr v okně |
| `east_cloud` | `cloud_cover` @ east_horizon (pokud k dispozici), jinak proxy z arieiro |
| `precip_mm`, `precip_prob` | součet / max v okně −1 h … +1 h |
| `gust_kmh`, `wind_kmh` | max / průměr v okně na arieiro |
| `rh_summit`, `dewpoint_depression` | `T − Td` @ arieiro; < 2 °C ≈ nasycení (mlha) |
| `visibility_km` | @ arieiro |
| `T_inv` (síla inverze) | `T_850 − T_925` @ funchal (nebo arieiro grid); > 0 = inverze |
| `dry_above` | `RH_700` / `RH_850` — nízké = suchý vzduch nad vrstvou |
| `cloud_deck_top_m` | nejvyšší hladina s `RH_L > 90 %` nebo `cloud_cover_L > 50 %` (z 925/850/700) |
| `summit_above_deck` | `1818 − cloud_deck_top_m` (kladné = vrchol nad mraky) |
| `lowland_cloud_delta` | `cloud_cover_low @ sao_vicente/funchal − cloud_cover_low @ arieiro` (moře mraků pod vrcholem) |
| `model_spread` | směrodatná odchylka klíčových veličin napříč modely → `confidence` |

### 3.2 Normalizační funkce
Pro každou komponentu **po částech lineární funkce** `f: hodnota → [0,1]` (jednoduché, auditovatelné, snadno ladit). Příklad:

```
clear_sky(low_cloud):        0–10 % → 1.0 ; 10–60 % → lineárně 1.0→0.2 ; 60–100 % → 0.2→0.0
precip_gate(mm, prob):       mm == 0 & prob < 20 % → 1.0 ; mm < 0.2 → 0.6 ; mm ≥ 0.5 → 0.0
wind_comfort(gust km/h):     ≤ 25 → 1.0 ; 25–50 → 1.0→0.5 ; 50–70 → 0.5→0.1 ; > 70 → 0.0
```

### 3.3 Skóre východu slunce na Arieiro (0–100 %)

**Princip:** vážený součet komponent × **gating** multiplikátory (tvrdé vylučující podmínky). Váhy jsou **konfigurovatelné** (`config/weights.yaml`) a budou kalibrovány v Phase 2.

```
raw = 100 × ( w_sky      · S_sky        # nízká oblačnost u vrcholu + východní obzor
            + w_color    · S_color      # střední/vysoká oblačnost pro barvy
            + w_cloudsea · S_cloudsea   # bonus za moře mraků pod vrcholem
            + w_vis      · S_visibility # dohlednost / vlhkost (anti-mlha)
            + w_wind     · S_wind )     # komfort

score = raw × G_precip × G_fog × G_gale          # gating ∈ [0,1]
```

**Výchozí váhy (návrh, součet = 1.0):**

| Komponenta | Váha | Definice |
|------------|------|----------|
| `S_sky` | **0.35** | `min(clear_sky(low_cloud_summit), clear_sky(east_cloud))` — východní obzor musí být volný |
| `S_color` | **0.15** | „Sweet spot“ střední+vysoké oblačnosti: nejlepší 20–60 % (barvy), 0 % = nudné, > 85 % = zataženo (zvon. funkce) |
| `S_cloudsea` | **0.20** | = `P_inversion` (viz 3.4), bonus za dramatický pohled |
| `S_visibility` | **0.15** | z `visibility_km` a `dewpoint_depression` |
| `S_wind` | **0.15** | z `wind_comfort(gust)` |

**Gating (multiplikátory):**
- `G_precip`: 1.0 / 0.6 / 0.0 podle srážek (viz výše).
- `G_fog`: pokud `dewpoint_depression < 1 °C` **a** `low_cloud_summit > 80 %` → 0.15 (jsme v mraku).
- `G_gale`: `gust > 80 km/h` → 0.3 (nebezpečné/nepříjemné).

**Důvěra (confidence)**: `confidence = 1 − clamp(model_spread_norm) × horizon_penalty(days_ahead)`; výstup jako pásmo `score ± margin` a štítek `LOW/MED/HIGH`.

**Verdikt:**

| Skóre | Verdikt | Text |
|-------|---------|------|
| ≥ 70 | **GO** | „Vyrazit.“ |
| 45–69 | **MAYBE** | „Hazard — záleží na ochotě riskovat; zkontroluj kamery ráno.“ |
| < 45 | **NO-GO** | „Spát.“ |

**Příklad (ilustrativní):** `low_cloud_summit=5 %`, `east_cloud=15 %`, `mid=35 %`, `high=40 %`, gust 22 km/h, `visibility=25 km`, `P_inversion=0.8`, srážky 0 → `S_sky≈0.97`, `S_color≈0.9`, `S_cloudsea=0.8`, `S_vis≈0.95`, `S_wind=1.0` → raw ≈ 100×(0.34+0.135+0.16+0.14+0.15)= **92** → GO.

### 3.4 Pravděpodobnost inverze / moře mraků `P_inversion` (0–1)

Kombinace čtyř nezávislých „důkazů“ (vážený průměr, ne součin, aby jedna chybějící veličina nezabila výsledek):

1. **Stabilita (E1):** `T_850 − T_925 > 0` (inverze), případně `T_700 − T_850` malý/kladný → `E1 = sigmoid((T_inv − 0)/σ)`.
2. **Vlhká vrstva dole, suchá nahoře (E2):** `RH_925 ≥ 85 %` a `RH_700 ≤ 50 %` (nebo `RH_850` výrazně nižší) → vysoké E2.
3. **Vrchol nad oblačností (E3):** `summit_above_deck > 100 m` → 1.0, lineárně klesá k 0 při `≤ −200 m`.
4. **Prostorový důkaz (E4):** `lowland_cloud_delta` — mnoho nízké oblačnosti na pobřeží (zejm. sever/São Vicente) a málo u Arieira → vysoké E4.
5. **Pasát (modifikátor):** směr větru 10–70° (SV) a rychlost 15–45 km/h → ×1.0; slabý/jiný směr → ×0.7 (moře mraků slabší nebo jiného typu).

```
P_inversion = clamp( (0.30·E1 + 0.25·E2 + 0.30·E3 + 0.15·E4) × trade_wind_modifier , 0, 1 )
```

**Výstupy navíc:** odhad `cloud_deck_top_m` (horní hranice moře mraků) a textová charakteristika („mraky ~900–1300 m, Arieiro nad nimi o ~500 m“).

*Pozn.: Sémantika „dobré inverze“ se liší podle uživatele (dramatické moře mraků × čisté bez mraků). Do konfigurace přidat preferenci `prefer_cloud_sea: true|false`, která mění znaménko váhy `S_cloudsea`.*

### 3.5 Porovnávací matice mikroklimat (UC3 a obecný přehled)

Pro dané časové okno (např. zítra 13–18 h) a každou lokalitu spočítat **agregované metriky** a **dílčí skóre „Jasno/Příjemnost“**:

| Metrika | funchal | arieiro | sao_vicente | paul_da_serra | ponta_do_sol |
|---------|:------:|:------:|:-----------:|:-------------:|:------------:|
| Ø celková oblačnost (low/mid/high) | | | | | |
| Max srážky (mm) / prob. | | | | | |
| Ø teplota / pocitová | | | | | |
| Max nárazy větru | | | | | |
| Slunečné hodiny (`shortwave_radiation` nebo 100−cloud) | | | | | |
| **Sky-Score (0–100)** | | | | | |
| Pořadí | | | | | |

**Sky-Score pro odpoledne:** `100 × (0.45·(1 − cloud_total) + 0.25·precip_gate + 0.15·wind_comfort + 0.15·temp_comfort)`.

**Mikroklimatický kontext (statické priory, konfigurovatelné):**

| Zóna | Typický vzor | Použití |
|------|--------------|---------|
| Jih (Funchal, Ponta do Sol) | Závětří, nejvíc slunce, odpolední oblačnost z orografie | Prior: +clear bias |
| Sever (São Vicente) | Návětří pasátu, častá oblačnost/srážky | Prior: −clear bias |
| Vrcholy (Arieiro) | Často nad moři mraků, ale i v mraku, vítr | Inverzní analýza |
| Plató (Paul da Serra) | Otevřená výšina, mlha, vítr, ráno chladno | Srovnání s Arieirem |

Matice se vypisuje jako tabulka (CLI) a slouží k doporučení „Nejlepší místo pro [okno]“ + vysvětlení rozdílů (např. „Sever zataženo, ale jih jasno — typický pasátový obraz“).

Dále **index kontrastu** `contrast = |cloud_north − cloud_south|` jako detekce typického ostrovního rozdělení.

### 3.6 Kalibrace 2026-10-12: brána mlhy a hladina 800 hPa

První kalibrace (8 dní, 5.–12. 10. 2026) ukázala, že přízemní vlhkost a dohlednost modelu u Arieira
pocházejí z vyhlazeného terénu (~1200 m), tedy často z vnitřku moře mraků, a sestřelovaly dny s inverzí.
Hladina 850 hPa (~1550 m) leží pod vrcholem a 700 hPa (~3150 m) je příliš vysoko (9. 10. mělo RH700 jen 21 %
a bylo to v mlze). Rozhodující je **800 hPa (~2050 m, těsně nad vrcholem 1818 m)**:

| Hodnocení | RH 800 hPa |
|-----------|-----------|
| inverze (6., 10., 11., 12. 10.) | 20, 42, 40, 15 % |
| mlha / nepovedlo se (5., 8., 9. 10.) | 78, 83, 90 % |

Pravidla (prahy v `config/weights.yaml`, sekce `sunrise.fog`):
- vlhkost ve výšce vrcholu se interpoluje mezi 850 a 800 hPa → hlavní signál mlhy;
- přízemní rosný bod a dohlednost se berou v úvahu jen úměrně `surface_trust`, který klesá k 0, když je v 800 hPa sucho;
- vlhko dole + sucho nahoře je pozitivní znak inverze, nikoli mlhy;
- nízká oblačnost u vrcholu i na východním obzoru blokuje pohled jen tehdy, když vrstva sahá k vrcholu;
- stabilita inverze = `T(800) − T(850)`.

Výsledek na 8 dnech: inverze 73–99 %, mlha 2–16 %, „částečně zataženo“ 41 %. **Pozor na přeučení:** prahy
vznikly z osmi dní, regresní test `tests/fixtures/calibration_2026-10.json` je hlídá a při přibývání
hodnocení je třeba je přehodnotit (`madeira recompute`).

---

## 4. Technický stack & architektura

### 4.1 Volba stacku

| Kritérium | Python | .NET (C#) |
|-----------|--------|-----------|
| Rychlost MVP | ⭐⭐⭐ | ⭐⭐ |
| Ekosystém (data, CV, Telegram) | ⭐⭐⭐ (pandas, OpenCV, aiogram) | ⭐⭐ |
| Typová bezpečnost | Pydantic + mypy (dobrá) | ⭐⭐⭐ |
| Nasazení (cron, VPS) | snadné | snadné |
| Kalibrace / notebooky | ⭐⭐⭐ (Jupyter) | ⭐ |

**Doporučení: Python 3.12.** Hlavní důvod: iterativní kalibrace heuristiky a analýza snímků (OpenCV/NumPy/Jupyter) jsou nejrychlejší v Pythonu. .NET zvolit, pokud je cílem integrace do existujícího .NET ekosystému — architektura níže je agnostická.

**Konkrétní knihovny:**

| Oblast | Volba |
|--------|-------|
| HTTP | `httpx` (async, retry přes `tenacity`) |
| Modely | `pydantic` v2 |
| CLI | `typer` + `rich` (tabulky, barvy) |
| Konfigurace | `pydantic-settings` + YAML (`weights.yaml`, `locations.yaml`, `cameras.yaml`) |
| Cache/Storage | SQLite (`sqlite3`/`SQLModel`) — predikce, pozorování, raw JSON |
| Čas | `zoneinfo` (`Atlantic/Madeira`) |
| Obraz (Phase 2) | `Pillow`, `opencv-python-headless`, `numpy` |
| API (Phase 3) | `FastAPI` + `uvicorn` |
| Bot (Phase 3) | `aiogram` 3 nebo `python-telegram-bot` |
| Scheduler | `cron` / `systemd timer` / GitHub Actions / APScheduler |
| Testy | `pytest`, `respx` (mock httpx), `hypothesis` (volitelně), `syrupy` (snapshot) |
| Kvalita | `ruff`, `mypy --strict` (analyzers), `pre-commit` |
| Správa balíčků | `uv` |

### 4.2 Vrstvená architektura

```
┌────────────┐   ┌─────────────────┐   ┌───────────────┐   ┌──────────────┐
│  Fetchers  │ → │   Normalizers   │ → │   Analyzers   │ → │  Presenters  │
│ (I/O)      │   │ (raw → DTO)     │   │ (pure funcs)  │   │ (CLI/Bot/API)│
└────────────┘   └─────────────────┘   └───────────────┘   └──────────────┘
      │                                       │
      ▼                                       ▼
  Cache / Raw store (SQLite)         History store (predikce, pozorování)
```

**Pravidla závislostí:**
- Analyzers **nikdy** nedělají I/O ani nezávisí na Fetchers/Presenters → deterministické, snadno testovatelné, kalibrovatelné nad historickými daty.
- Presenters závisí jen na výstupních DTO (`AnalysisResult`).
- Orchestrace v tenké vrstvě `services/` (use-case funkce: `plan_sunrise(date)`, `compare_microclimates(window)`).
- Zdroje za rozhraním (`typing.Protocol`) → snadná výměna (`WeatherProvider`, `CameraSource`).

### 4.3 Struktura repozitáře (cílová)

```
madeira-weather-forecast/
├─ ARCHITECTURE.md
├─ README.md
├─ pyproject.toml
├─ config/
│  ├─ locations.yaml        # lokality, elevace, role
│  ├─ weights.yaml          # váhy a prahy skóre
│  └─ cameras.yaml          # (Phase 2)
├─ src/madeira/
│  ├─ domain/               # Pydantic modely (DTOs), enumy
│  ├─ fetchers/
│  │  ├─ base.py            # Protocols: WeatherProvider, CameraSource
│  │  ├─ open_meteo.py
│  │  └─ webcams/           # (Phase 2) netmadeira.py, windy.py
│  ├─ normalizers/          # raw JSON → domain
│  ├─ analyzers/
│  │  ├─ features.py        # derivované veličiny
│  │  ├─ inversion.py       # P_inversion
│  │  ├─ sunrise.py         # skóre východu
│  │  ├─ microclimate.py    # matice, ranking
│  │  ├─ vision.py          # (Phase 2) analýza snímků
│  │  └─ scoring_utils.py   # piecewise-linear, sigmoid, clamp
│  ├─ presenters/
│  │  ├─ cli.py             # rich tabulky
│  │  ├─ text.py            # česká textová shrnutí
│  │  ├─ telegram.py        # (Phase 3)
│  │  └─ api.py             # (Phase 3) FastAPI
│  ├─ services/             # use-case orchestrace
│  ├─ storage/              # SQLite repo, cache
│  └─ cli.py                # Typer entrypoint
├─ tests/
│  ├─ fixtures/             # uložené Open-Meteo odpovědi (golden data)
│  ├─ unit/ · integration/
└─ notebooks/               # kalibrace, vizualizace
```

### 4.4 Datové modely (DTO — návrh, pseudo-Pydantic)

```python
class Location(BaseModel):
    key: str                      # "arieiro"
    name: str
    lat: float
    lon: float
    elevation_m: float
    role: Literal["coast_south","coast_north","summit","plateau","reference_east"]

class HourlyPoint(BaseModel):
    time_utc: datetime
    temperature_c: float
    apparent_temperature_c: float | None
    dew_point_c: float | None
    relative_humidity_pct: float | None
    cloud_cover_total_pct: float
    cloud_cover_low_pct: float
    cloud_cover_mid_pct: float
    cloud_cover_high_pct: float
    precipitation_mm: float
    precipitation_probability_pct: float | None
    wind_speed_kmh: float
    wind_gusts_kmh: float
    wind_direction_deg: float
    visibility_m: float | None
    pressure_msl_hpa: float | None
    levels: dict[int, PressureLevel] = {}   # 1000/925/850/700 hPa

class PressureLevel(BaseModel):
    temperature_c: float | None
    relative_humidity_pct: float | None
    cloud_cover_pct: float | None
    geopotential_height_m: float | None
    wind_speed_kmh: float | None

class DailyInfo(BaseModel):
    date: date
    sunrise_utc: datetime
    sunset_utc: datetime

class LocationForecast(BaseModel):
    location: Location
    model: str                          # "best_match" | "ecmwf_ifs025" | ...
    fetched_at_utc: datetime
    hourly: list[HourlyPoint]
    daily: list[DailyInfo]

class ForecastBundle(BaseModel):          # výstup Fetcheru
    forecasts: dict[str, dict[str, LocationForecast]]   # [location_key][model]

class ScoreComponent(BaseModel):
    name: str                          # "S_sky"
    value: float                       # 0..1
    weight: float
    contribution: float                # value*weight*100
    explanation: str                   # "Nízká oblačnost u vrcholu 5 % → výborné"

class SunriseAnalysis(BaseModel):
    target_date: date
    sunrise_local: datetime
    score_pct: float                   # 0..100
    score_range: tuple[float, float]   # pásmo nejistoty
    verdict: Literal["GO","MAYBE","NO_GO"]
    confidence: Literal["LOW","MEDIUM","HIGH"]
    inversion: InversionAnalysis
    components: list[ScoreComponent]
    gates: dict[str, float]            # {"precip":1.0,"fog":1.0,"gale":1.0}
    summary_cs: str                    # lidsky čitelné shrnutí
    warnings: list[str]

class InversionAnalysis(BaseModel):
    probability_pct: float
    cloud_deck_top_m: float | None
    summit_above_deck_m: float | None
    evidence: dict[str, float]         # E1..E4, trade_wind_modifier

class MicroclimateRow(BaseModel):
    location_key: str
    window_start_utc: datetime
    window_end_utc: datetime
    cloud_low/mid/high_avg_pct, precip_max_mm, temp_avg_c, gust_max_kmh: float
    sky_score_pct: float
    rank: int

class MicroclimateComparison(BaseModel):
    window: tuple[datetime, datetime]
    rows: list[MicroclimateRow]
    best_location: str
    narrative_cs: str

class CameraObservation(BaseModel):    # Phase 2
    camera_id: str
    captured_at_utc: datetime
    fresh: bool
    sky_clear_pct: float | None
    fog_likelihood: float | None
    cloud_sea_detected: bool | None
    label: str | None                  # clear / partly / fog / cloud_sea ...
    image_ref: str | None              # cesta k lokálnímu snímku

class ValidationRecord(BaseModel):     # Phase 2
    target_date: date
    predicted_score_pct: float
    predicted_inversion_pct: float
    observed_sunrise_quality: int | None    # 1–5, ruční zadání / z kamery
    observed_cloud_sea: bool | None
    source: Literal["camera","manual"]
```

### 4.5 Rozšiřitelnost a testovatelnost
- **Config-driven:** nové lokality/kamery/váhy bez změny kódu.
- **Golden-data testy:** uložené reálné odpovědi API (dobré i špatné dny) + očekávaný rozsah skóre; regresní ochrana při ladění vah.
- **Explainability by design:** každá komponenta nese `explanation` → lze přímo posílat do botu.
- **Dependency inversion:** `WeatherProvider` protokol umožní přidat IPMA, Meteoblue apod. a kombinovat.

---

## 5. Fázovaný implementační plán (Roadmap)

### Phase 0 — Discovery spike (0,5–1 den)
- Ověřit živé Open-Meteo odpovědi pro všech 5 bodů včetně tlakových hladin (dostupnost proměnných pro dané modely na Madeiře).
- Ověřit NetMadeira: URL kamer, formát snímků, ToS/`robots.txt`. **Výstup:** krátká poznámka `docs/discovery.md`, rozhodnutí o webcam strategii.
- Ručně projít 5–10 minulých dní (Open-Meteo Historical API / vlastní zkušenost) a zapsat „jaké to tehdy bylo“ → základ validačního setu.

### Phase 1 — MVP: CLI skript (2–4 dny) — ✅ hotovo 2026-10-12 (bez multi-model spreadu a SQLite historie, přesunuto do Phase 2)
**Cíl:** `madeira sunrise --date tomorrow` vypíše verdikt, skóre, rozpad komponent.
- Project skeleton (`uv`, `ruff`, `mypy`, `pytest`).
- `OpenMeteoFetcher` + normalizace do DTO + file/SQLite cache.
- `features.py`, `inversion.py`, `sunrise.py` (čisté funkce) + `weights.yaml`.
- `microclimate.py` + příkaz `madeira compare --from 13:00 --to 18:00`.
- `rich` výstup + česká textová shrnutí.
- Testy s fixtures; ≥ 80 % pokrytí analyzérů.
- **Definition of Done:** CLI běží offline z cache, dává reprodukovatelné skóre, vysvětluje komponenty, 7denní výhled.

### Phase 2 — Webkamery a validace (3–5 dní)
- `CameraSource` abstrakce + implementace (dle výsledku spike), `cameras.yaml` s ROI.
- `vision.py`: základní CV metriky (oblačnost, kontrast, mlha, moře mraků).
- Ukládání `CameraObservation` a **denní logování predikce vs. realita** (`ValidationRecord`), ruční zadání hodnocení (`madeira rate --date … --quality 4`).
- Notebook pro **kalibraci vah/prahů** (hledání vah maximalizujících shodu s pozorováním; jednoduchá logistická regrese / grid search).
- Příkaz `madeira live` (aktuální stav kamer vs. předpověď).
- **DoD:** report přesnosti (např. shoda GO/NO-GO na ≥ 15–20 dnech), úprava vah dle dat.

### Phase 3 — Automatizace & UI/Bot (3–5 dní) — ✅ bot + SQLite sběr hotov 2026-10-12 (FastAPI vynecháno; kalibrace vah zatím nedělá nic automaticky)
- FastAPI vrstva (`/sunrise?date=`, `/compare?window=`, `/health`), sdílí `services/`.
- **Telegram bot:** příkazy `/zitra`, `/tyden`, `/jasno 13-18`, `/kamera arieiro`; inline tlačítka.
- **Ranní/večerní digest:** večer 20:00 (plán na zítřek), ráno ~1 h před vstáváním (aktualizace + rozhodnutí GO/NO-GO s kamerou).
- Scheduler (systemd timer / cron / GitHub Actions), logování, jednoduchý monitoring (heartbeat).
- Nasazení (Docker, malý VPS nebo domácí stroj).
- **DoD:** push digest chodí spolehlivě 7 dní po sobě bez zásahu.

### Phase 4+ — Nice-to-have
Ensemble pravděpodobnosti, satelitní snímky, ML model trénovaný na vlastním logu, grafy (matplotlib/plotly), další vyhlídky (Ponta de São Lourenço, Pico Ruivo), web dashboard.

### Přehled rizik

| Riziko | Dopad | Mitigace |
|--------|-------|----------|
| Hrubé rozlišení modelů vs. terén | Nepřesná skóre | Tlakové hladiny + rozdíly mezi body, multi-model, kalibrace, komunikace nejistoty |
| Scraping NetMadeira nepovolen / změna struktury | Phase 2 blokována | Spike v Phase 0, abstrakce `CameraSource`, alternativa Windy API, ruční validace |
| Free tier limity Open-Meteo | Výpadky | Cache, jediný multi-location request, retry |
| Heuristické váhy jsou „od oka“ | Nízká užitečnost | Phase 2 kalibrace na reálných datech, config-driven váhy |
| DST/časové zóny | Špatný čas východu | UTC uvnitř, `zoneinfo`, testy kolem změny času |
| Mikroklima je nestacionární (sezóna) | Váhy platí jen pro část roku | Sezónní profily vah (léto/zima) |

---

## 6. Otevřené otázky k odsouhlasení

1. **Stack:** Python (doporučeno) vs. .NET?
2. **Preference moře mraků:** je „dobrý východ“ pro tebe = *s* mraky pod sebou (dramatické), nebo *bez* mraků? (ovlivní znaménko `S_cloudsea`.)
3. **Bot:** Telegram jako primární kanál potvrzen? Kde bude běžet (domácí stroj / VPS / cloud)?
4. **Rozsah lokalit:** ponechat přesně 5 + volitelný `east_horizon` bod?
5. **Webkamery:** pokud NetMadeira scraping nebude vhodný, souhlasíš s alternativou (Windy API / ruční ověření)?
6. **Validace:** jsi ochoten několik týdnů ručně zapisovat hodnocení skutečných východů (zlepší kalibraci)?
7. **Jazyk výstupu:** čeština (navrženo) pro CLI i bota?
8. **Časový horizont:** stačí 3–7 dní předpovědi?

---

*Další krok: po tvé zpětné vazbě a schválení upravím dokument a teprve poté zahájím Phase 0/1. Žádný kód zatím nevznikl.*
