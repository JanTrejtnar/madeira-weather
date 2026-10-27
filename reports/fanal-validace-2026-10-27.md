# Fanal: ověření predikce proti skutečnosti (20.–27. 10. 2026)

> **Poznámka:** hodnocení po 2 h v této zprávě minulo některou mlhu (21. a 26. 10. ve 12 h). Přesnější je hodinová validace v [fanal-validace-hodinova-2026-10-27.md](fanal-validace-hodinova-2026-10-27.md).


Zpracováno 27. 10. 2026. Nic v modelu ani v `docs/` jsem neměnil; jde o diagnostiku.

## 1. Metodika a její limity

| Co | Jak |
|---|---|
| **Skutečnost** | Snímky kamery Rabaçal (NetMadeira) v 09, 11, 13, 15 a 17 h, 7 dnů × 5 snímků = 35 hodin. Starší snímky server nearchivuje (12.–19. 10. vrací 404). |
| **Hodnocení snímků** | Ručně, na 3 stupně: 0 = jasno, 1 = cáry / mrak na hřebeni (≈ PARTIAL), 2 = hustá mlha v záběru (≈ MYSTIC). Hodnocení je subjektivní. |
| **Co jsme předpověděli** | Skutečné výstupy bota z git historie `docs/data.json` (50 běhů), ne přepočet. |
| **Modelové hodnoty v hodinách snímků** | Znovu stažené z Open-Meteo pro Fanal (32,81 N, −17,14 E, 1150 m), spočítané stávajícím `fanal.py`. |

Limity, které se nedají obejít:

- **7 dnů je málo.** Čísla níže jsou diagnostika, ne statistický důkaz.
- **Kamera není na Fanalu.** Rabaçal je ~8 km daleko ve stejné výšce. Mlha na Rabaçalu nemusí být mlha na Fanalu a naopak.
- **Mlha se mění rychleji než snímkování po 2 h.** 26. 10. byla v 13 h hustá mlha, v 15 h jasno, v 17 h zase mlha.
- Sekce „Historie" na webu je modelový přepočet, ne měření. Samotná nic neověřuje.

Přehled snímků: [20.–23. 10.](fanal-kamera-20-23-10.jpg), [24.–26. 10.](fanal-kamera-24-26-10.jpg).

## 2. Den po dni

Nejlepší pozorovaný stav dne vs. verdikt, který web ukazoval večer před tím (poslední běh bota před půlnocí) a během dne.

| Den | Pozorováno (09 / 11 / 13 / 15 / 17 h) | Pozorovaný vrchol | Večer předtím | Během dne | Shoda |
|---|---|---|---|---|---|
| 20. 10. | 0 / 0 / 1 / 1 / 1 | cáry | MYSTIC 76 | PARTIAL 50 | večer ✗ (nad), den ✓ |
| 21. 10. | 0 / 0 / 1 / 1 / 0 | cáry | MYSTIC 75 | MYSTIC 75 | ✗ nad |
| 22. 10. | 0 / 1 / 0 / 0 / 0 | cáry (jen 11 h) | MYSTIC 75 | PARTIAL 50 | večer ✗ (nad), den ✓ |
| 23. 10. | **2 / 2 / 2** / 1 / 0 | mlha ráno | MYSTIC 84 | PARTIAL 55 (běh po poledni) | večer ✓ |
| 24. 10. | 0 / 0 / 0 / 1 / **2** | mlha večer | MYSTIC 75 | MYSTIC 76 | ✓ |
| 25. 10. | 0 / 0 / 0 / 0 / 0 | jasno | PARTIAL 55 | CLEAR 30 | večer ✗ (nad), den ✓ |
| 26. 10. | 0 / 0 / **2** / 0 / **2** | mlha odpoledne | PARTIAL 50 | PARTIAL 50 | ✗ **pod** |
| 27. 10. (dnes) | 1 / 1 / – | cáry | PARTIAL 50 | PARTIAL 50 | ✓ (zatím) |

- **Předpověď z večera předtím trefila verdikt 2 ze 7 dnů** (23., 24.). Čtyřikrát přestřelila nahoru, jednou podstřelila.
- Zpřesňování přes den to zlepšuje (4 ze 6 vyhodnotitelných), ale to je „nowcast po tom, co už je skoro vidět". Pro plánování cesty je důležitá předpověď z večera předtím.
- Chyba má jasný směr: model **systematicky přestřeluje** a občas dá falešnou jistotu („MYSTIC 75 %"). Opačná chyba (26. 10.) je vzácnější, ale vznikla z konkrétního důvodu (viz 3.4).

## 3. Co je s modelem špatně

### 3.1 Skóre skoro nerozlišuje (nejdůležitější zjištění)

Po hodinách snímků (35 hodin; 6 hodin s hustou mlhou, 8 s cáry, 21 jasných):

| Verdikt modelu v dané hodině | počet hodin | skutečně mlha | cáry | jasno |
|---|---|---|---|---|
| MYSTIC (≥ 70) | 8 | 2 (25 %) | 2 | 4 |
| PARTIAL (40–69) | 14 | 3 (21 %) | 4 | 7 |
| CLEAR (< 40) | 13 | 1 (8 %) | 2 | 10 |

- MYSTIC je jen o málo „lepší" než PARTIAL (25 % vs. 21 % hodin s mlhou). Skóre „75 %" se v praxi naplnilo ve čtvrtině případů.
- Rozlišovací schopnost (AUC, 0,5 = náhoda, 1 = dokonalé): celkové **skóre 0,66**, přitom jediná surová veličina `rh_forest` (vlhkost ve výšce lesa z hladin 925/850 hPa) má 0,75, dohlednost 0,70, spread T−Td 0,68. Složené skóre tedy nepřidává oproti jednotlivým veličinám nic.
- Skóre navíc nabývá jen několika hodnot (0, 30, 50, 55, 75), je to schodiště ze dvou podmínek, ne spojitá pravděpodobnost. Zobrazení v procentech vyvolává přesnost, kterou nemá.

### 3.2 Ranní a večerní slot jsou téměř vždy „vlhké"

- Ve 09 h dává model skóre ≥ 40 v 5 ze 7 dnů, mlha byla jen 1× (23. 10.). Hodnoty vlhkosti kolem 93–97 % (spread < 1,5 °C) jsou v době rozbřesku skoro každé ráno, spustí +50 bodů „zdarma" a pak stačí pasát 8–30 km/h na MYSTIC.
- V 17 h je skóre ≥ 40 v 6 ze 7 dnů, mlha 2×.
- Naopak 13 h slot má průměrné skóre 26 a mlhu míjí (23. 10. skóre 0 při husté mlze).
- Důsledek: „Nejlepší okno" se skoro vždy vybere ráno nebo večer, i když pozorovaná mlha byla v různých slotech (23. 10. ráno až poledne, 24. 10. večer, 26. 10. odpoledne a večer).

### 3.3 Vlhkost u povrchu vs. ve výšce lesa

`dew_points()` bere spread a RH z **2 m** (`relative_humidity_2m`). Vlhkost ve výšce lesa (`rh_at_height`, 1150 m) se jen průměruje do informativního pole `humidity_pct`, do skóre nevstupuje. Přitom:

- 25. 10. byla vlhkost ve výšce lesa 50–55 % (suchá vrstva, kamera celý den jasno), povrchová RH ale 75–82 %, a model v 09 h dal PARTIAL 55.
- `rh_forest` ≥ 94 % platilo v 10 hodinách: 4× mlha, 3× cáry, 3× jasno. Lepší než povrchová RH, ale pořád ne rozhodující.

### 3.4 Brána větru 8–30 km/h (případ 26. 10.)

26. 10. byla v 13 h a 17 h hustá mlha. Model měl RH 86–98 %, spread 0,8–2,3 °C, dohlednost 1,8–13 km, vítr ze sektoru 297–321° při 3–5 km/h. Návětrný bonus +25 vyžaduje ≥ 8 km/h, takže skóre skončilo na 50 (PARTIAL) místo ≥ 70. Je to jediný případ, takže z něj nic neladím, ale ukazuje, že tvrdá hranice 8 km/h je křehká.

### 3.5 Dohlednost je nejsilnější signál v extrémech, ale skoro se nepoužije

Hodiny s modelovou dohledností < 7 km: 4, z toho 3× mlha (23. 10. 09 h, 24. 10. 17 h, 26. 10. 17 h) a 1× jasno (26. 10. 15 h, mezi dvěma mlhami). Křivka `visibility` ale dává při 6 km jen 1 bod z 25. Zároveň je dohlednost mezi běhy nestabilní (18. 10. dva běhy o 7 minut lišící 840 m vs. 14 880 m, skóre 68 vs. 43), proto ji `weights.yaml` záměrně neváží. To je rozumný kompromis, jen je potřeba vědět, že je to ztracená informace.

### 3.6 Mlha, kterou model nevidí vůbec

23. 10. ve 13 h byla na kameře hustá mlha, model měl RH 77 %, spread 4,0 °C, skóre 0. Je to orografická mlha na návětrném svahu, kterou grid globálního modelu (~9 km) nerozliší. Tohle se laděním vah nespraví a patří to do poctivého komunikování nejistoty.

### 3.7 Kalibrace stojí na dvou dnech

Komentáře v `weights.yaml` uvádějí, že `rh_extreme: 85` a práh nárazů 50 km/h byly kalibrovány na 17.–18. 10. (2 dny). Ve vzorku 20.–26. 10. se pravidlo „RH ≥ 85 % nebo spread ≤ 2 °C → +50" splnilo ve 14 hodinách: mlha 4×, cáry 4×, jasno 6×. To je typický příznak přeučení na malý vzorek, který CLAUDE.md zakazuje.

## 4. Co z toho plyne

**Nelaďte teď žádné konstanty.** 7 dnů ručně hodnocených snímků je málo na to, aby opravilo kalibraci, která vznikla z 2 dnů. Stejná chyba by se opakovala s jinými čísly.

Doporučené pořadí:

1. **Začít ukládat pravdu.** Kamera archivuje jen ~7 dnů. Bot by měl každý den uložit snímky v 9/11/13/15/17 h (nebo aspoň hodnocení) a vedle nich modelové hodnoty. Bez toho nejde nic spolehlivě ověřit. Nejlépe automatické hodnocení (kontrast/dohlednost snímku) nebo aspoň ruční štítek 0/1/2 v jednoduchém souboru.
2. **Za 4–8 týdnů** (≥ 30 dnů) na těch datech vyzkoušet hypotézy, které tahle analýza naznačuje:
   - vlhkost ve výšce lesa místo 2 m ve `dew_points`,
   - slabší „bonus zdarma" za rozbřesková a večerní nasycení,
   - měkčí hranice větru místo skoku na 8 km/h,
   - váha dohlednosti při < 7 km.
3. **Hned bez změny modelu**: přestat u skóre ukazovat přesná procenta („75 %") a zobrazovat jen verdikt, případně rozsah. Odpovídá to realitě (MYSTIC se naplnil ve čtvrtině hodin) a nic se tím neladí.
4. Přiznat u verdiktu limit: orografická mlha na svahu je pod rozlišením modelu.

## 5. Surová data

Hodinová tabulka (pozorování vs. modelové veličiny) a skripty jsou ve scratchpadu relace
(`feat.json`, `preds.json`, `obs.json`). Do repozitáře jsem je nedával.
