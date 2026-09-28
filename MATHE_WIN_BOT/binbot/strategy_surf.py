"""
ESTRATEGIA "SURFAR A TENDENCIA" - analisa vela por vela, sem olhar tendencia
de varias velas nem quadrantes. Regra simples:

  A cada minuto, olha a cor da ULTIMA VELA JA FECHADA (a que acabou de fechar).
  Entra na PROXIMA vela (a que esta prestes a abrir) na MESMA COR.

  Isso "surfa" a sequencia: enquanto a cor continuar repetindo, ele continua
  entrando na mesma direcao. Quando a cor muda (a aposta anterior errou), a
  proxima decisao automaticamente já segue a nova cor - nao precisa de logica
  especial de reversao, porque a regra sempre olha so a vela mais recente.

  Roda em TODO MINUTO (nao so nos minutos 3 e 8) - toda vela vira uma entrada.
"""

WINDOW_SIZE = 1          # so precisa da ultima vela ja fechada
SKIP_FORMING = 0         # nenhuma vela "em formacao" para pular - a proxima vela ainda nem existe
IS_ENTRY_MINUTE = lambda minute: True  # opera em qualquer minuto


def decide(candles):
    """
    candles: lista com exatamente 1 vela ja fechada: {"color": "green"/"red", ...}.
    Retorna 'buy' se a ultima vela foi verde (aposta que a proxima tambem sera),
    'sell' se foi vermelha. Nunca retorna None - sempre surfa a tendencia atual.
    """
    if len(candles) != WINDOW_SIZE:
        raise ValueError(f"Preciso exatamente {WINDOW_SIZE} vela(s) ja fechada(s)")

    ultima = candles[-1]
    return "buy" if ultima["color"] == "green" else "sell"
