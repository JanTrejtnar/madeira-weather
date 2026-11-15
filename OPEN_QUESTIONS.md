# OPEN_QUESTIONS.md – otevřené pochybnosti o modelu a pipeline

Pracovní seznam věcí, které je vhodné zvážit nebo opravit. **Čte ho především Claude** při navazující práci:
neopravuje se tu nic „z hlavy", každá položka má důkazy, postup ověření a kritérium rozhodnutí.
Pravidla změn modelu jsou v `CLAUDE.md` (sekce 3) a mají přednost.

## Jak s dokumentem pracovat

1. Před úpravou skórování projdi položky `OPEN` a `WATCH`. Neřeš víc než jednu najednou.
2. Ověřovací data: `python3 scripts/backtest.py` (zlatá sada, `tests/fixtures/golden/`), hodnocení
   `ground_truth/ratings.json` (admin je zapisuje přes `madeira rate`), co model skutečně hlásil den předem
   `ground_truth/forecast_log.jsonl` (plní pipeline), historie a vstupy v `docs/data.json`.
3. Rozhodnutí: položka se **uzavře** (`FIXED` / `DROPPED`) jen s odkazem na commit, test nebo data. Do části
   „Log rozhodnutí" na konci připiš datum, výsledek a důvod, položku nemaž.
4. Stavy: `OPEN` (podezření, čeká na data), `WATCH` (sbírá se důkaz), `FIXED`, `DROPPED` (vyvráceno nebo
   nerelevantní), `PRODUCT` (rozhodnutí o produktu, ne o fyzice).
5. Změna skórování se přijme jen když (a) má fyzikální/logické odůvodnění, (b) backtest nezhorší žádný hodnocený
   den, (c) zlepšení potvrdí aspoň dva nezávislé dny. Oprava logické chyby (hladina čtená dvakrát apod.) je výjimka,
   ale musí mít test. Nikdy neladit podle jednoho dne.

## Stav dat (k 2026-11-15)

- Zlatá sada: 4 dny, 6 srovnatelných hodnocení. Model 3× přesně, 3× podhodnotil, 0× nadhodnotil.
- Hodnocení jsou **zkreslená výběrem** (admin jezdí tam, kde předpověď vypadá dobře). Falešná NO_GO se odhalí jen
  náhodou.
- Dosud nemáme žádné pozorování v pásmu RH800 60–75 % (viz Q1).
- Open-Meteo: 2026-11-15 vyčerpán denní limit (HTTP 429, „Daily API request limit exceeded"), viz Q12.

---

## Q1 · Strop přelévání u Arieira bez důkazu mraků (wet-column pravidlo) – `OPEN`

**Pozorování.** Arieiro 16. a 17. 11. 2026 mají 25 % NO_GO (`inside_cap`), `inversion_case: none`, žádnou horní
hranici mraků, ale alert `spillover`. Souhrn zároveň říká „moře mraků nepravděpodobné".

**Mechanismus** (`src/madeira/analyzers/sunrise.py`, blok „vlhký sloupec"): `RH(850) ≥ spill_hard_rh` (60) a
`RH(800) ≥ 60` ⇒ `spill_deck = z(aloft)`; pak `spillover_hard` ⇒ `spillover_cap` ⇒ pro `z(800) ≥ summit` platí
`inside_cap` 25. Pravidlo tedy z RH 60 % vyvodí „mrak sahá do 800 hPa" bez jakéhokoli důkazu mraku.

**Proč je to podezřelé.** RH 60–75 % není mrak. Mlhové prahy jinde: `aloft_rh_fog` 80 (70 při nárazech),
`summit_rh_fog` 90, `lc_rh_moist` 65, `spill_rh_moist` 55. Práh 60 je nekonzistentní a s nimi nesouvisí.

**Důkazy.** Ve 24 dnech z cache (říjen + listopad) zasáhlo pravidlo bez nalezené vrstvy **jen 17. 10.** (RH850 67,
RH800 61, nízká oblačnost 0 %, 25 % NO_GO); skutečnost Arieira ráno 17. 10. neznáme. Dny s oběma hladinami ≥ 60 %
byly jinak skutečné mlhy (RH800 73–100 %). Živé RH/z pro 16.–17. 11. se nepodařilo stáhnout (limit API).

**Jak ověřit.**
- `madeira rate arieiro 2026-11-16 GO|MAYBE|NO_GO` a totéž 17. 11. (admin / webkamera Arieiro). Pěkné ráno = pravidlo mine.
- Z `forecast_log.jsonl` vyhledat záznamy s `alerts: ["spillover"]` a porovnat se `ratings.json`.
- Stáhnout RH850/RH800/z a mraky pro 16.–17. 11. (archiv po obnovení limitu) a ověřit, co přesně pravidlo spustilo.

**Kandidáti oprav (nezvolit bez dat).** (a) vyžadovat spolu s RH ≥ 60 i nezávislý důkaz mraku (nízká/střední
oblačnost, RH ve výšce vrcholu ≥ mlhový práh); (b) sjednotit `spill_hard_rh` s ostatními mlhovými prahy; (c) cap jen
„spill" (39), ne `inside_cap` 25, když chybí důkaz mraku.

**Kritérium rozhodnutí.** Opravit, když aspoň dva hodnocené dny s `spillover` v pásmu RH800 60–75 % dopadly GO/MAYBE
a backtest nic nezhorší (mlhy 5.–11. 11. a 18. 10. musí zůstat NO_GO). Jinak `DROPPED` jako vyvrácené.

**Související:** souhrn a skóre si odporují („moře mraků nepravděpodobné" vs. strop přelévání) – viz Q13.

## Q2 · Bica da Cana 12. 11.: zbývající mlhová brána – `WATCH`

Po opravě wet-column (commit `0cb6b58`) vychází Bica 12. 11. **68 % MAYBE**, skutečnost GO. Zbývá `fog` 0,76
(RH ve výšce vrcholu 61 % vs. laděné `peak_rh_max` 70; nárazy 32 km/h nad laděným 30). Možná příčina: laděná
mlhová brána Bica je přísná při RH pod prahem, nebo nárazy 32 vs. 30 km/h. **Nezasahovat**, dokud nebude víc hodnocených
dnů Bica. Zlatá sada: `test_golden.py` povoluje chybu 1.

## Q3 · Dohlednost jako brána u pobřežních západů (Ponta do Pargo 12. 11.) – `WATCH`

Archiv dává Pargo 12. 11. 31 % (skutečnost GO, částečně štěstí: o pár set metrů dál pršelo). Dominuje brána
dohlednosti 0,59 (přízemní vrstva, ~9 km), obzor jen 0,94. Model bere dohlednost z přízemní vrstvy; při základně mraků
nad spotem to nemusí vypovídat o výhledu k obzoru. **Nápad:** nepenalizovat dohlednost, když je základna mraků dost nad
spotem (analogie Q6). Jeden den nestačí; potřeba dalších případů pobřežních západů.

## Q4 · Dvojí postih za nízké mraky na obzoru (komponenta + brána) – `DROPPED` (zatím)

Navrženo mírnit postih při 30–60 % pokrytí (rozbitá oblačnost = možná mezera). Backtest ukázal, že u Pargo 12. 11. je
obzor v archivu čistý (brána 0,94), takže by nic nezměnil. Nemáme žádný jiný případ. Navíc paprsek zapadajícího slunce
prochází desítky km nízké vrstvy; pokrytí podél paprsku může být vyšší než v bodě obzoru (55 km). **Znovu otevřít**,
až bude ≥ 3 hodnocených pobřežních západů s nízkými mraky 30–60 %.

## Q5 · Audit `aloft_hpa` / `rh_aloft` – `OPEN`

`aloft_hpa` znamená „suchá vrstva nad vrcholem", ale u Bica da Cana (1560 m ≈ 850 hPa) je to hladina vrcholu samotného.
Wet-column byl jeden výskyt (opraveno). Zkontrolovat další: `spillover_hard`, `surface_trust`, `wind_driven_fog`,
`fog_upper`, `fog_likelihood`, `rh_aloft` v `inversion.py`, laděné `dry_layer_hpa`. Výstup: seznam míst s popisem a
doporučení; opravy až po jednotlivých testech. Žádná změna chování bez zlaté sady.

## Q6 · Pobřežní „strop mraků" – práh 150 m je libovolný – `WATCH`

`LOCAL_BASE_CLEARANCE_M = 150` (`sunset.py`): základna mraků ≥ 150 m nad vyhlídkou ⇒ nízká oblačnost se nepočítá jako
mlha na vyhlídce. Fyzikálně rozumné, číselně zvolené bez dat; dopad ~2 body. Při přehodnocování zlaté sady zvážit vrácení
(`7bce708`), pokud se nepotvrdí.

## Q7 · Cristo Rei 6. 11.: krátké hezké okno – `DROPPED` (nerelevantní pro model)

Skutečnost MAYBE (~2 minuty hezky, pak mraky), model 28 % NO_GO. Model s hodinovým rozlišením a buňkou ~9 km
krátké okno znát nemůže. Neřešit; sledovat jen jako příklad nejistoty.

## Q8 · Co skóre znamená (produktová sémantika) – `PRODUCT`

Skóre míchá „vyplatí se jet" a „bude výjimečné". Admin: „i obyčejné východy/západy jsou super". Přitom `COLOR` dává při
0 % cirrů jen 0,40 a `canvas_clouds` 50 %, tj. jasná obloha bez cirrů drží skóre pod GO. Rozhodnout, co 0–100 znamená
a zda GO/MAYBE/NO_GO odpovídají „stojí to za cestu". **Před laděním křivek.**

## Q9 · Zkreslení hodnocení a chybějící štítky – `WATCH`

Hodnocení vznikají jen při výletech. Možnosti: pravidelné hodnocení dnů i bez výletu; denní hodnocení webkamer
(Fanal to už dělá, viz `scripts/fanal_labels.json`; subjektivní, kamery nepokrývají všechna místa). Cíl: ≥ 10–15
hodnocených dnů na typ spotu (`MIN_SAMPLES` = 10 v `evaluation.py`).

## Q10 · Nejistota skóre: jeden model – `OPEN` (vylepšení)

`score_range` je pevné ±5/10/18 podle dne předem. Více modelů (`models=` v Open-Meteo, dávkově: ECMWF, ICON, GFS) by dalo
poctivý rozptyl. Pozor na limity API (Q12).

## Q11 · Spoty bez modelu – `PRODUCT`

Admin hodnotí západy na Bica da Cana (17. 10., 11. 11.) a Ponta da Ladeira (9. 11.), které model neskóruje (uloženo jako
`bica_da_cana_sunset`, `ponta_da_ladeira_sunset` s `event`). 3 z 9 hodnocení. Přidat spoty jen pokud je admin skutečně
využívá.

## Q12 · Denní limit Open-Meteo (HTTP 429) – `OPEN` (provozní riziko)

2026-11-15 vrátilo API „Daily API request limit exceeded". Pipeline běží 8× denně a při každém běhu stahuje dávku
18 bodů × mnoho proměnných plus historii/backfill. Část vyčerpání mohly způsobit i lokální testy z vývojových relací.
Zkontrolovat: počet dotazů na běh (backfill ve `collect_spot_history`/`fetch_history`), využití `.cache`/TTL, zda
při 429 export nepadá (fetcher má retry a `RuntimeError`; `fetch_*_bundle` výpadek polykají). Pokud se opakuje,
snížit frekvenci cronu, nebo cachovat historii v repu.

## Q13 · Souhrn neodpovídá skóre při stropu – `OPEN` (malé)

`_summary_cs` při stropu přelévání doplňuje „Největší problém jsou mraky u hřebene", ale `inversion.description_cs`
současně píše „žádná výrazná vlhká vrstva – moře mraků nepravděpodobné". Texty se v jednom souhrnu odporují
(viz Q1). Opravit až společně s Q1 (stejná příčina).

---

## Log rozhodnutí

| Datum | Položka | Výsledek | Důvod / odkaz |
|---|---|---|---|
| 2026-11-13 | Bica wet-column (stejná hladina dvakrát) | `FIXED` | commit `0cb6b58`, test `test_bica_single_moist_level_at_summit_does_not_cap_clear_sunrise` |
| 2026-11-15 | Web „Načítám předpověď…" (chybějící čárka v `docs/index.html`) | `FIXED` | commit `9a763de`, test `tests/test_frontend.py` (`node --check` inline skriptů) |
| 2026-11-13 | Telegram bot, `data/madeira.db` jako zdroj pravdy | `FIXED` | skutečnost je v `ground_truth/ratings.json` (`madeira rate`), commit `b5152a2` |
