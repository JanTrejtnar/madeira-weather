# Madeira Microclimate & Sunrise Predictor

Návrh a rozhodnutí: [ARCHITECTURE.md](ARCHITECTURE.md). Stav: **Phase 1 (CLI MVP)**.

## Instalace (Windows)

    python -m venv .venv
    .venv\Scripts\pip install -e ".[dev]"

## Použití

    .venv\Scripts\madeira sunrise --date tomorrow      # skóre východu slunce na Arieiru
    .venv\Scripts\madeira week                         # 7denní výhled
    .venv\Scripts\madeira compare --from 13:00 --to 18:00   # srovnání lokalit

Váhy a prahy: `config/weights.yaml`, lokality: `config/locations.yaml`.
Odpovědi Open-Meteo se cachují 45 min v `.cache/`.

## Testy

    .venv\Scripts\python -m pytest

## Telegram bot + sběr zpětné vazby

1. V Telegramu vytvoř bota přes @BotFather, token ulož do `.env` (je v `.gitignore`):

       TELEGRAM_BOT_TOKEN=123456:ABC...
       MADEIRA_ALLOWED_CHATS=111222333     # tvoje chat ID (zjistíš např. přes @userinfobot); bez něj bot odpoví komukoli

2. Spusť: `.venv\Scripts\madeira bot` a v chatu pošli `/start`.

Příkazy: `/sunrise [today|tomorrow|YYYY-MM-DD]`, `/week`, `/compare [den] [od] [do]`, `/rate [den]`, `/start`, `/stop`.
Časy v `config/weights.yaml` (sekce `bot`, čas Madeiry): digest 20:00, dotaz „jak dopadl východ?“ 10:00.
Odpovědi na tlačítka se zapisují do `data/madeira.db` (tabulky `predictions`, `outcomes`, `subscribers`, pohled `calibration`).

Bot musí běžet nepřetržitě. Na Windows: Plánovač úloh → nová úloha „Při přihlášení“ → program
`C:\Users\honza\dev-projects\madeira-weather-forecast\.venv\Scripts\madeira.exe`, argument `bot`,
začít v `C:\Users\honza\dev-projects\madeira-weather-forecast`.

Kontrola dat: `madeira history` (predikce vs. realita), `madeira export-calibration` (JSON), `madeira rate 2026-10-13 cloud_sea` (ruční zápis).

## Veřejný web (GitHub Pages)

Statická stránka `docs/index.html` čte `docs/data.json` (Tailwind přes CDN, žádný build).

1. Aktualizace dat (zítřek + 3denní výhled):

       .venv\Scripts\madeira export-web            # zapíše docs/data.json
       git add docs/data.json && git commit -m "Update forecast" && git push

   Volby: `--days N` (1–7), `--history N` (počet hodnocených dní historie z `data/madeira.db`), `--out cesta.json`.
   Web má přepínač CZ/EN (volba se pamatuje) a rozklikávací historii predikcí vs. skutečnosti.

2. Zapnutí hostingu: na GitHubu **Settings → Pages → Build and deployment → Source: Deploy from a branch**,
   branch `main`, složka `/docs` → **Save**. Web poběží na `https://jantrejtnar.github.io/madeira-weather/`.

3. Lokální náhled: `python -m http.server -d docs 8000` a otevři <http://localhost:8000> (`fetch` nefunguje z `file://`).

`data.json` se mění jen při spuštění exportu; stránka zobrazuje čas poslední aktualizace a varuje, když jsou data starší než den.

## Automatická aktualizace (GitHub Actions)

Workflow `.github/workflows/update-forecast.yml` spouští `madeira export-web` každý den v **19:30 UTC**
(na Madeiře 19:30 v zimě, 20:30 v létě) a také ručně: **Actions → Update forecast → Run workflow**.
Pokud se `docs/data.json` změnil, workflow ho commitne a pushne zpět do `main`.

Nutné nastavení: **Settings → Actions → General → Workflow permissions → Read and write permissions** → Save.
Bez toho push z workflow selže (chyba 403).

Poznámky:
- GitHub spouští cron s odchylkou i desítek minut a po 60 dnech bez aktivity v repozitáři plánované běhy vypíná (stačí je znovu zapnout v záložce Actions).
- V Actions není lokální databáze `data/madeira.db`, proto se historie predikcí přebírá z předchozího `data.json`. Aktualizuješ ji lokálním `madeira export-web` + commit + push.
