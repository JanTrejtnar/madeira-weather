# Fanal: hodinová validace modelu (20.–27. 10. 2026)

Vygenerováno `python scripts/validate_fanal.py --out reports/fanal-validace-hodinova-2026-10-27.md`.

**Realita:** 81 hodinových snímků kamery Rabaçal (08–18 h), ručně hodnocených 0 jasno / 1 cáry / 2 hustá mlha
(`scripts/fanal_labels.json`; hodnotil Claude ze snímků, je to subjektivní). **Model:** hodinové hodnoty Open-Meteo
přepočtené pro uplynulé dny, ne uložené předpovědi z minulosti.

## Co se změnilo v modelu

- Vlhkost se bere z hladin 900/850 hPa (interpolace na 1 150 m), ne z 2 m. RH ≥ 95 % → +50, ≥ 90 % → +30, 80–90 % lineárně.
- Do 10:00 místního času se vlhkost počítá jen s nízkou oblačností z modelu ≥ 50 %.
- MYSTIC jen při RH ≥ 80 % v 900 i 850 hPa (suchý 850 hPa < 60 % je zahrnut).
- Bezvětří (< 8 km/h) z 290°–020° dostane návětrný bonus při RH ≥ 95 % v 900 hPa.

## Jak číst výsledky

- Je to **in-sample**: pravidla vznikla z fyzikálního zadání, konstanty jsou zvolené předem a nebyly fitovány na těchto
  datech, ale vyhodnocují se na stejném týdnu, takže zlepšení je optimistické. Pro spolehlivé potvrzení je potřeba
  delší série (desítky dní). Štítky proto zůstávají v repozitáři.
- Citlivost: změny prahů (RH 93/97 %, ranní oblačnost 30/70 %, 900 hPa při bezvětří 90 %, brána 75 %) mění hodinovou shodu
  jen mezi 40 a 42 ze 81 a AUC mezi 0,79 a 0,82. Výsledek tedy nestojí na jediné hranici.
- Největší přínos má vlhkost ve výšce lesa. Brána 850/900 hPa sama přidává téměř nic: bez ní je falešných MYSTIC 8 místo 7.
- „Původní trefa 2 ze 7 dnů" byla měřena na skutečně vydaných večerních předpovědích proti hrubým štítkům po 2 h.
  Zde je srovnání na přepočtu a hodinových štítcích, proto vychází původní model na dny lépe (4/7). Spolehlivější je hodinové srovnání.
- Model stále není spolehlivý: 18 z 45 jasných hodin dostane PARTIAL a víc, 8 z 20 hodin husté mlhy MYSTIC nedostane.
  21. 10. drží MYSTIC celý den, ač mlha byla jen občas; 17. 10. ráno (původní kalibrační den) model nově dává CLEAR
  a k ověření už nejsou snímky.

## Souhrn

Hodnoceno 81 hodin ve 8 dnech (mlha 20, cáry 16, jasno 45)

| Metrika | Původní model | Nový model |
|---|---|---|
| Hodiny s přesně trefeným verdiktem | 32/81 | 42/81 |
| Hustá mlha: zachycena jako MYSTIC | 8/20 | 12/20 |
| Hustá mlha: aspoň PARTIAL | 17/20 | 19/20 |
| Jasno: falešný poplach (PARTIAL a víc) | 27/45 | 18/45 |
| Jasno: falešný MYSTIC | 12/45 | 7/45 |
| AUC skóre (mlha vs. ostatní; 0,5 = náhoda) | 0.66 | 0.82 |
| Dny (vrchol slotů 9/13/17 h vs. nejhorší stav dne; 7 kompletních dní) | 4/7 | 6/7 |

Dny: 10-20 Fog→Partial, 10-21 Fog→Fog, 10-22 Partial→Partial, 10-23 Fog→Fog, 10-24 Fog→Fog, 10-25 Clear→Clear, 10-26 Fog→Fog

| Datum | Čas | Realita | Předpověď (nový) | Předpověď (původní) | RH_2m | RH_900hPa | RH_850hPa | Cloud_Cover_Low | Vítr |
|---|---|---|---|---|---|---|---|---|---|
| 2026-10-20 | 08:00 | Clear | CLEAR 0 | CLEAR 30 | 79 % | 66 % | 71 % | 0 % | 5 km/h 73° |
| 2026-10-20 | 09:00 | Clear | CLEAR 0 | CLEAR 0 | 72 % | 62 % | 84 % | 29 % | 5 km/h 82° |
| 2026-10-20 | 10:00 | Clear | CLEAR 1 | CLEAR 0 | 75 % | 77 % | 94 % | 66 % | 6 km/h 57° |
| 2026-10-20 | 11:00 | Clear | CLEAR 30 | CLEAR 30 | 82 % | 91 % | 99 % | 81 % | 6 km/h 47° |
| 2026-10-20 | 12:00 | Partial | CLEAR 30 | CLEAR 30 | 80 % | 89 % | 98 % | 89 % | 7 km/h 39° |
| 2026-10-20 | 13:00 | Partial | CLEAR 20 | CLEAR 0 | 75 % | 84 % | 98 % | 94 % | 6 km/h 34° |
| 2026-10-20 | 14:00 | Partial | CLEAR 25 | CLEAR 30 | 78 % | 86 % | 99 % | 100 % | 6 km/h 36° |
| 2026-10-20 | 15:00 | Partial | CLEAR 30 | CLEAR 30 | 80 % | 88 % | 99 % | 100 % | 6 km/h 40° |
| 2026-10-20 | 16:00 | Partial | CLEAR 30 | CLEAR 30 | 83 % | 91 % | 100 % | 100 % | 5 km/h 37° |
| 2026-10-20 | 17:00 | Partial | PARTIAL 50 | PARTIAL 50 | 88 % | 96 % | 100 % | 100 % | 4 km/h 48° |
| 2026-10-20 | 18:00 | Fog | PARTIAL 50 | PARTIAL 51 | 93 % | 99 % | 100 % | 98 % | 6 km/h 43° |
| 2026-10-21 | 08:00 | Clear | MYSTIC 75 | MYSTIC 75 | 96 % | 96 % | 89 % | 55 % | 9 km/h 58° |
| 2026-10-21 | 09:00 | Clear | MYSTIC 75 | MYSTIC 75 | 96 % | 98 % | 95 % | 70 % | 10 km/h 53° |
| 2026-10-21 | 10:00 | Clear | MYSTIC 75 | MYSTIC 75 | 90 % | 97 % | 98 % | 75 % | 11 km/h 48° |
| 2026-10-21 | 11:00 | Partial | MYSTIC 75 | MYSTIC 75 | 86 % | 95 % | 97 % | 76 % | 10 km/h 46° |
| 2026-10-21 | 12:00 | Fog | PARTIAL 55 | PARTIAL 55 | 84 % | 95 % | 89 % | 76 % | 11 km/h 45° |
| 2026-10-21 | 13:00 | Partial | MYSTIC 75 | MYSTIC 75 | 87 % | 98 % | 100 % | 93 % | 10 km/h 45° |
| 2026-10-21 | 14:00 | Clear | MYSTIC 75 | MYSTIC 75 | 88 % | 98 % | 100 % | 99 % | 10 km/h 42° |
| 2026-10-21 | 15:00 | Partial | MYSTIC 75 | MYSTIC 75 | 88 % | 98 % | 100 % | 91 % | 9 km/h 36° |
| 2026-10-21 | 16:00 | Fog | MYSTIC 75 | MYSTIC 75 | 91 % | 99 % | 100 % | 95 % | 10 km/h 37° |
| 2026-10-21 | 17:00 | Clear | MYSTIC 75 | MYSTIC 75 | 91 % | 98 % | 100 % | 98 % | 11 km/h 41° |
| 2026-10-21 | 18:00 | Partial | MYSTIC 75 | MYSTIC 75 | 90 % | 97 % | 100 % | 94 % | 11 km/h 42° |
| 2026-10-22 | 08:00 | Clear | CLEAR 30 | PARTIAL 50 | 97 % | 95 % | 84 % | 58 % | 6 km/h 57° |
| 2026-10-22 | 09:00 | Clear | CLEAR 30 | PARTIAL 50 | 96 % | 96 % | 86 % | 58 % | 5 km/h 70° |
| 2026-10-22 | 10:00 | Clear | PARTIAL 50 | PARTIAL 50 | 92 % | 97 % | 91 % | 66 % | 6 km/h 47° |
| 2026-10-22 | 11:00 | Partial | PARTIAL 50 | PARTIAL 50 | 90 % | 99 % | 94 % | 74 % | 6 km/h 43° |
| 2026-10-22 | 12:00 | Clear | PARTIAL 50 | PARTIAL 50 | 86 % | 97 % | 87 % | 74 % | 7 km/h 41° |
| 2026-10-22 | 13:00 | Clear | CLEAR 18 | CLEAR 3 | 77 % | 86 % | 88 % | 79 % | 7 km/h 41° |
| 2026-10-22 | 14:00 | Clear | CLEAR 19 | CLEAR 0 | 77 % | 86 % | 92 % | 83 % | 5 km/h 42° |
| 2026-10-22 | 15:00 | Clear | CLEAR 22 | CLEAR 0 | 77 % | 87 % | 92 % | 89 % | 6 km/h 43° |
| 2026-10-22 | 16:00 | Clear | CLEAR 30 | CLEAR 30 | 81 % | 90 % | 95 % | 80 % | 7 km/h 41° |
| 2026-10-22 | 17:00 | Clear | PARTIAL 55 | PARTIAL 55 | 82 % | 92 % | 93 % | 85 % | 8 km/h 41° |
| 2026-10-22 | 18:00 | Clear | PARTIAL 55 | MYSTIC 75 | 85 % | 93 % | 99 % | 82 % | 10 km/h 44° |
| 2026-10-23 | 08:00 | Fog | MYSTIC 76 | MYSTIC 76 | 98 % | 99 % | 89 % | 67 % | 11 km/h 56° |
| 2026-10-23 | 09:00 | Fog | MYSTIC 76 | MYSTIC 76 | 97 % | 99 % | 100 % | 81 % | 11 km/h 54° |
| 2026-10-23 | 10:00 | Fog | MYSTIC 75 | MYSTIC 75 | 89 % | 96 % | 100 % | 80 % | 14 km/h 51° |
| 2026-10-23 | 11:00 | Fog | PARTIAL 52 | PARTIAL 55 | 79 % | 89 % | 81 % | 50 % | 14 km/h 50° |
| 2026-10-23 | 12:00 | Fog | PARTIAL 43 | CLEAR 0 | 74 % | 86 % | 94 % | 66 % | 14 km/h 49° |
| 2026-10-23 | 13:00 | Fog | PARTIAL 55 | CLEAR 0 | 77 % | 91 % | 99 % | 88 % | 14 km/h 46° |
| 2026-10-23 | 14:00 | Fog | PARTIAL 55 | PARTIAL 55 | 79 % | 94 % | 95 % | 84 % | 14 km/h 46° |
| 2026-10-23 | 15:00 | Partial | PARTIAL 55 | PARTIAL 55 | 78 % | 92 % | 91 % | 79 % | 14 km/h 46° |
| 2026-10-23 | 16:00 | Fog | MYSTIC 75 | PARTIAL 55 | 81 % | 95 % | 97 % | 85 % | 14 km/h 46° |
| 2026-10-23 | 17:00 | Clear | PARTIAL 55 | PARTIAL 55 | 82 % | 94 % | 85 % | 75 % | 14 km/h 48° |
| 2026-10-23 | 18:00 | Clear | PARTIAL 69 | MYSTIC 75 | 85 % | 96 % | 73 % | 69 % | 13 km/h 52° |
| 2026-10-24 | 08:00 | Clear | CLEAR 0 | MYSTIC 75 | 94 % | 91 % | 73 % | 41 % | 11 km/h 66° |
| 2026-10-24 | 09:00 | Clear | CLEAR 0 | MYSTIC 75 | 93 % | 91 % | 71 % | 44 % | 11 km/h 67° |
| 2026-10-24 | 10:00 | Clear | PARTIAL 55 | MYSTIC 75 | 85 % | 92 % | 95 % | 86 % | 14 km/h 58° |
| 2026-10-24 | 11:00 | Clear | PARTIAL 55 | PARTIAL 55 | 83 % | 94 % | 89 % | 74 % | 13 km/h 60° |
| 2026-10-24 | 12:00 | Clear | PARTIAL 55 | PARTIAL 55 | 81 % | 94 % | 87 % | 70 % | 13 km/h 59° |
| 2026-10-24 | 13:00 | Clear | PARTIAL 55 | PARTIAL 55 | 79 % | 92 % | 88 % | 88 % | 12 km/h 61° |
| 2026-10-24 | 14:00 | Fog | PARTIAL 55 | PARTIAL 55 | 79 % | 92 % | 91 % | 87 % | 12 km/h 56° |
| 2026-10-24 | 15:00 | Partial | MYSTIC 75 | PARTIAL 55 | 83 % | 96 % | 86 % | 83 % | 12 km/h 53° |
| 2026-10-24 | 16:00 | Fog | MYSTIC 75 | MYSTIC 75 | 86 % | 96 % | 90 % | 86 % | 14 km/h 53° |
| 2026-10-24 | 17:00 | Fog | MYSTIC 77 | MYSTIC 77 | 91 % | 99 % | 100 % | 78 % | 15 km/h 54° |
| 2026-10-24 | 18:00 | Fog | MYSTIC 76 | MYSTIC 76 | 92 % | 99 % | 92 % | 69 % | 16 km/h 53° |
| 2026-10-25 | 08:00 | Clear | CLEAR 0 | MYSTIC 75 | 85 % | 54 % | 3 % | 0 % | 10 km/h 70° |
| 2026-10-25 | 09:00 | Clear | CLEAR 0 | PARTIAL 55 | 82 % | 77 % | 8 % | 0 % | 12 km/h 57° |
| 2026-10-25 | 10:00 | Clear | CLEAR 36 | PARTIAL 55 | 79 % | 88 % | 7 % | 48 % | 12 km/h 57° |
| 2026-10-25 | 11:00 | Clear | CLEAR 38 | CLEAR 0 | 76 % | 88 % | 7 % | 63 % | 12 km/h 54° |
| 2026-10-25 | 12:00 | Clear | PARTIAL 50 | PARTIAL 55 | 80 % | 94 % | 2 % | 84 % | 10 km/h 63° |
| 2026-10-25 | 13:00 | Clear | CLEAR 28 | CLEAR 0 | 75 % | 87 % | 2 % | 83 % | 10 km/h 68° |
| 2026-10-25 | 14:00 | Clear | CLEAR 26 | CLEAR 0 | 75 % | 88 % | 4 % | 73 % | 10 km/h 65° |
| 2026-10-25 | 15:00 | Clear | CLEAR 1 | CLEAR 0 | 75 % | 88 % | 11 % | 53 % | 9 km/h 71° |
| 2026-10-25 | 16:00 | Clear | CLEAR 0 | CLEAR 0 | 76 % | 87 % | 12 % | 52 % | 8 km/h 75° |
| 2026-10-25 | 17:00 | Clear | CLEAR 0 | CLEAR 30 | 79 % | 88 % | 8 % | 57 % | 8 km/h 77° |
| 2026-10-25 | 18:00 | Clear | CLEAR 0 | CLEAR 30 | 83 % | 89 % | 5 % | 58 % | 5 km/h 90° |
| 2026-10-26 | 08:00 | Clear | CLEAR 0 | CLEAR 0 | 61 % | 40 % | 26 % | 0 % | 6 km/h 216° |
| 2026-10-26 | 09:00 | Clear | CLEAR 0 | CLEAR 30 | 80 % | 59 % | 24 % | 0 % | 3 km/h 225° |
| 2026-10-26 | 10:00 | Clear | CLEAR 12 | PARTIAL 50 | 85 % | 94 % | 31 % | 55 % | 4 km/h 241° |
| 2026-10-26 | 11:00 | Clear | CLEAR 4 | CLEAR 30 | 79 % | 88 % | 47 % | 64 % | 4 km/h 235° |
| 2026-10-26 | 12:00 | Fog | CLEAR 30 | CLEAR 30 | 82 % | 94 % | 75 % | 79 % | 3 km/h 270° |
| 2026-10-26 | 13:00 | Fog | MYSTIC 75 | PARTIAL 50 | 86 % | 97 % | 99 % | 87 % | 3 km/h 302° |
| 2026-10-26 | 14:00 | Clear | MYSTIC 100 | MYSTIC 75 | 90 % | 100 % | 100 % | 100 % | 3 km/h 293° |
| 2026-10-26 | 15:00 | Clear | MYSTIC 85 | PARTIAL 60 | 90 % | 99 % | 100 % | 90 % | 5 km/h 297° |
| 2026-10-26 | 16:00 | Fog | MYSTIC 97 | MYSTIC 72 | 94 % | 100 % | 100 % | 100 % | 6 km/h 302° |
| 2026-10-26 | 17:00 | Fog | MYSTIC 85 | PARTIAL 60 | 95 % | 100 % | 100 % | 100 % | 5 km/h 321° |
| 2026-10-26 | 18:00 | Fog | MYSTIC 76 | PARTIAL 51 | 98 % | 100 % | 100 % | 100 % | 6 km/h 350° |
| 2026-10-27 | 08:00 | Clear | CLEAR 27 | PARTIAL 51 | 99 % | 95 % | 55 % | 60 % | 6 km/h 62° |
| 2026-10-27 | 09:00 | Partial | CLEAR 25 | PARTIAL 50 | 95 % | 94 % | 55 % | 59 % | 5 km/h 56° |
| 2026-10-27 | 10:00 | Partial | CLEAR 30 | PARTIAL 50 | 88 % | 96 % | 59 % | 65 % | 6 km/h 32° |
| 2026-10-27 | 11:00 | Partial | CLEAR 30 | CLEAR 30 | 84 % | 93 % | 81 % | 74 % | 6 km/h 35° |
