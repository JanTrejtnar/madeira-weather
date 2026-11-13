"""Zlatá sada: ručně hodnocené dny se počítají z uložených archivních dat Open-Meteo (bez sítě).

Test hlídá, že změna modelu nezhorší žádný hodnocený den. Dny, které model už teď míjí, mají povolenou
dosavadní chybu (`MAX_ERROR`); zlepšení je v pořádku, zhoršení ne. Až se model zlepší, chybu tady sniž."""

from datetime import date

import pytest

from madeira.services import backtest

# (den, spot) -> největší povolená absolutní řadová chyba (NO_GO < MAYBE < GO)
MAX_ERROR = {
    (date(2026, 11, 5), "pico_ruivo_sunset"): 0,
    (date(2026, 11, 6), "cristo_rei"): 1,  # hezké ~2 minuty, model NO_GO 28 %
    (date(2026, 11, 8), "ponta_do_pargo"): 0,
    (date(2026, 11, 12), "arieiro"): 0,
    (date(2026, 11, 12), "bica_da_cana"): 1,  # mlhová brána 0,76; strop přelévání odstraněn opravou wet-column
    (date(2026, 11, 12), "ponta_do_pargo"): 2,  # brána dohlednosti 0,59; skutečně pěkný, částečně štěstí
}


@pytest.fixture(scope="module")
def results():
    res, _ = backtest.run()
    return {(r.day, r.spot): r for r in res}


@pytest.mark.parametrize("key", sorted(MAX_ERROR))
def test_golden_day_does_not_get_worse(results, key):
    assert key in results, f"chybí fixture nebo štítek pro {key}"
    assert abs(results[key].error) <= MAX_ERROR[key], (results[key], MAX_ERROR[key])


def test_golden_set_has_no_unlisted_day(results):
    assert set(results) <= set(MAX_ERROR), "nový hodnocený den: přidej ho do MAX_ERROR"
