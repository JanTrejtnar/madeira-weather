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
