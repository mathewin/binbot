"""
Estrategia QUAD3 - segue a tendencia definida pelo ultimo topo/fundo (swing).

Logica pura da estrategia (sem tela, sem clique - so decisao), no mesmo
padrao de strategy_quadrantes.py.

Timeframe: velas de 5 minutos.

QUADRANTE = bloco fixo de 3 velas de 5min, alinhado ao relogio
(ex: 12:00-12:04 = vela0, 12:05-12:09 = vela1 (vela do MEIO, onde entra a
operacao), 12:10-12:14 = vela2).

A operacao entra sempre na "cabeca" da vela do meio do quadrante (vela1),
ou seja, no instante em que ela abre - vela0 ja fechada, vela1 comecando.

Cada vela e um dict:
  {"color": "green" ou "red", "top": int, "bottom": int}
onde top/bottom sao coordenadas Y em pixel do CORPO da vela na tela
(Y menor = mais alto na tela = preco mais alto).

TENDENCIA (topo/fundo):
  - Um FUNDO e uma vela cujo "bottom" (minima do corpo, Y maior) e mais
    baixo que a vela anterior E a seguinte -> a partir dai, tendencia de
    ALTA (compra), pois o preco saiu do ponto mais baixo recente.
  - Um TOPO e uma vela cujo "top" (maxima do corpo, Y menor) e mais alto
    que a vela anterior E a seguinte -> a partir dai, tendencia de BAIXA
    (venda), pois o preco nao renovou a maxima e reverteu.
  - A tendencia so muda quando aparece um novo topo/fundo confirmado na
    direcao oposta (a "micro reversao"). Ate la, segue operando a favor
    do ultimo topo/fundo identificado - segue a MICRO tendencia, nao a
    macro.

IMPORTANTE: assim como no strategy_quadrantes, a analise usa apenas velas
JA FECHADAS - nunca a vela/quadrante que esta se formando agora.
"""

QUADRANT_SIZE = 3
TREND_LOOKBACK = 20  # quantas velas de 5min olhar pra achar o ultimo topo/fundo


def _is_topo(candles, i):
    return candles[i]["top"] < candles[i - 1]["top"] and candles[i]["top"] < candles[i + 1]["top"]


def _is_fundo(candles, i):
    return candles[i]["bottom"] > candles[i - 1]["bottom"] and candles[i]["bottom"] > candles[i + 1]["bottom"]


def find_last_swing(candles):
    """
    Varre as velas da mais recente para a mais antiga procurando o ultimo
    topo ou fundo confirmado (fractal de 3 velas: a vizinha anterior e a
    seguinte nao renovam o extremo da vela do meio).

    Retorna "topo", "fundo" ou None (se nao achar nenhum na janela).
    """
    for i in range(len(candles) - 2, 0, -1):
        if _is_topo(candles, i):
            return "topo"
        if _is_fundo(candles, i):
            return "fundo"
    return None


def decide(candles):
    """
    candles: TREND_LOOKBACK velas de 5min ja fechadas.

    Regra: entra a favor do ultimo topo/fundo identificado na janela.
      - ultimo swing = fundo -> tendencia de alta -> "buy"
      - ultimo swing = topo  -> tendencia de baixa -> "sell"
      - nenhum swing confirmado ainda na janela -> None (sem sinal)
    """
    if len(candles) != TREND_LOOKBACK:
        raise ValueError(f"Preciso exatamente {TREND_LOOKBACK} velas de 5min fechadas")

    swing = find_last_swing(candles)
    if swing == "fundo":
        return "buy"
    if swing == "topo":
        return "sell"
    return None


# --- Metadados usados pelo bot.py para funcionar com varias estrategias ---
WINDOW_SIZE = TREND_LOOKBACK  # quantas velas ja fechadas essa estrategia precisa
SKIP_FORMING = 1              # vela0 do quadrante atual ja fechada, vela1 (do meio) abrindo agora
IS_ENTRY_MINUTE = lambda minute: minute % 15 == 5  # abertura da vela do meio (minuto 5, 20, 35, 50...)
