# CLAUDE.md – závazná pravidla projektu Madeira GoldenHour

Platí pro všechny relace a úpravy. Architektura: Fetchers → Analyzers → Presenters (viz ARCHITECTURE.md).

## 1. Git & Quality Gate

- Před KAŽDÝM commitem nebo pushem MUSÍ proběhnout plná sada testů (`pytest`) a linter (`ruff check .`).
  Pokud cokoliv selže, commit je zakázán.
- Po změnách v modelech, konfiguraci nebo servisech vždy lokálně ověřit a zaktualizovat exporty
  (`madeira export-web` → `docs/data.json`, `madeira export-map` → `docs/map_data.json`).

## 2. Časové zóny a běhy (Atlantic/Madeira)

- Veškerá logika dat, exportu, zobrazení „dnes / zítra / včerejší ráno“ a historie MUSÍ striktně používat
  `ZoneInfo("Atlantic/Madeira")`.
- Nikdy nepoužívat naivní `datetime.now()` ani `datetime.utcnow()`.
- GitHub Actions cron běží v UTC, zatímco Madeira střídá WET (UTC+0) a WEST (UTC+1).

## 3. Meteorologická heuristika a modelování

- Horská fyzika (Pico do Arieiro, Bica da Cana, Ruivo) vyžaduje relativní odstup od mraků
  (`clearance_m = spot_altitude_m - cloud_top_m`).
- Přísný zákaz overfittingu: nikdy neohýbat globální váhy ani nevytvářet ad-hoc výjimky pro jediný den na
  úkor fyzikální konzistence ostatních dnů.
- Při posuzování inverzí vždy kontrolovat vlhkostní profil (800 hPa a hladina vrcholu) a nárazy větru.

## 4. Architektura API a frontendu

- Open-Meteo volat výhradně v dávkách (multi-location batch requests), nikdy neiterovat jednotlivá volání
  v cyklech.
- Frontend (`index.html`, `fanal.html`, `webcams.html`, `map.html`): zachovávat jednotný tmavý design,
  segmented navigaci a lokalizaci CZ/EN.
- Na mapě nikdy nepřekreslovat/nemazat Leaflet markery při pohybu slideru – vždy pouze aktualizovat
  existující DOM elementy.
