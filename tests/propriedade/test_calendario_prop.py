"""Propriedades do calendário de dias úteis, para qualquer data e prazo da cobertura."""

from __future__ import annotations

from datetime import date, timedelta

from hypothesis import given
from hypothesis import strategies as st

from app.dominio.calendario import DiasUteis
from app.dominio.erros import ForaDaCobertura
from app.infra.calendario import carregar_calendario

CALENDARIO = carregar_calendario()
datas = st.dates(min_value=CALENDARIO.cobertura_inicio, max_value=CALENDARIO.cobertura_fim)
prazos = st.integers(min_value=1, max_value=600)  # {dia_util}


@given(datas, prazos)
def test_somar_cai_em_dia_util_e_desfaz_com_dias_uteis_entre(d: date, n: int) -> None:
    try:
        resultado = CALENDARIO.somar(d, DiasUteis(n))
    except ForaDaCobertura:
        # Só falha quando de fato não cabem n dias úteis até o fim da cobertura.
        assert CALENDARIO.dias_uteis_entre(d, CALENDARIO.cobertura_fim) < n
        return
    assert resultado > d
    assert CALENDARIO.e_dia_util(resultado)
    assert CALENDARIO.dias_uteis_entre(d, resultado) == n
    # É o primeiro dia em que a contagem chega a n: o dia anterior ainda não chegava.
    assert CALENDARIO.dias_uteis_entre(d, resultado - timedelta(days=1)) == n - 1


@given(datas)
def test_dia_util_e_dia_de_semana_que_nao_e_feriado(d: date) -> None:
    assert CALENDARIO.e_dia_util(d) == (d.weekday() < 5 and d not in CALENDARIO.feriados)


@given(datas, datas, datas)
def test_dias_uteis_entre_soma_por_trechos(a: date, b: date, c: date) -> None:
    a, b, c = sorted((a, b, c))
    assert CALENDARIO.dias_uteis_entre(a, c) == (
        CALENDARIO.dias_uteis_entre(a, b) + CALENDARIO.dias_uteis_entre(b, c)
    )
