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

## Skutečné výsledky (ground truth) a backtest

Projekt je webový, žádný Telegram bot. Jak východ nebo západ skutečně dopadl, zapisuje **admin** ručně
do jediného souboru v repu, `ground_truth/ratings.json`:

    .venv\Scripts\madeira rate ponta_do_pargo 2026-11-12 GO --note "Velmi pěkný západ, o kus dál pršelo"
    git add ground_truth tests/fixtures/golden && git commit -m "Hodnocení 12. 11." && git push

- Verdikt je `GO` / `MAYBE` / `NO_GO` (stálo to za cestu?); spoty, které model nezná, vyžadují `--event sunrise|sunset`.
- `rate` k modelovaným spotům stáhne i archivní data dne do zlaté sady `tests/fixtures/golden/`.
- Export webu (`madeira export-web`, i v GitHub Actions) hodnocení z tohoto souboru přidá do historie
  („Skutečnost“ + poznámka); dny bez hodnocení ukazují „Nevíme, jak to dopadlo“.
- `python3 scripts/backtest.py` porovná model s hodnocením (viz `CLAUDE.md`, sekce 3). `madeira history` a
  `madeira evaluate` ukážou predikci vs. realitu; `data/madeira.db` je jen přestavitelná cache predikcí.

## Veřejný web (GitHub Pages)

Statická stránka `docs/index.html` čte `docs/data.json` (Tailwind přes CDN, žádný build).

1. Aktualizace dat (zítřek + 3denní výhled):

       .venv\Scripts\madeira export-web            # zapíše docs/data.json
       .venvScriptsmadeira export-map            # zapíše docs/map_data.json (mapa, 48 h po hodinách)
       git add docs/data.json && git commit -m "Update forecast" && git push

   Volby: `--days N` (1–7), `--history N` (počet dní historie z cache predikcí), `--out cesta.json`.
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
- V Actions není lokální cache `data/madeira.db`, proto se historie predikcí přebírá z předchozího `data.json`; hodnocení se čtou přímo z `ground_truth/ratings.json` (v repu).
