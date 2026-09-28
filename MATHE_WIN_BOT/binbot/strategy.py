"""
Logica pura da estrategia (sem tela, sem clique - so decisao).
Isso permite testar a regra com dados fake antes de ligar no navegador de verdade.

Cada vela e um dict:
  {"color": "green" ou "red", "top": int, "bottom": int}
onde top/bottom sao coordenadas Y em pixel do CORPO da vela na tela
(Y menor = mais alto na tela = preco mais alto).

QUADRANTE = bloco fixo de 5 velas de 1 minuto, alinhado ao relogio
(ex: 12:00:00-12:04:59 e um quadrante, 12:05:00-12:09:59 e o proximo).

IMPORTANTE: no momento da decisao (2 segundos antes da vela3 de 1min abrir),
a analise usa os 2 ULTIMOS QUADRANTES JA FECHADOS POR COMPLETO (10 velas de
1 minuto), NUNCA o quadrante que esta se formando agora (o que contem a
vela3 que vai receber a operacao).

Regra:
  1. Calcula a cor "agregada" de cada um dos 2 quadrantes (abertura da 1a
     vela do quadrante vs fechamento da ultima vela do quadrante).
  2. So opera se os 2 quadrantes tiverem cores opostas (alternancia existe
     entre o quadrante anterior e o mais recente).
  3. Calcula a tendencia da janela toda de 10 velas: fecha da ultima vela
     vs abertura da primeira vela.
     - Fecha mais alto (Y menor) que abertura -> tendencia de ALTA
     - Fecha mais baixo (Y maior) que abertura -> tendencia de BAIXA
     - Diferenca pequena (dentro da tolerancia) -> LATERAL
  4. Se tendencia clara -> entra a favor da tendencia. Se lateral -> entra
     na direcao da alternancia (cor do quadrante mais recente).
"""

QUADRANT_SIZE = 5
LATERAL_TOLERANCE_PX = 8


def _open_y(candle):
    return candle["bottom"] if candle["color"] == "green" else candle["top"]


def _close_y(candle):
    return candle["top"] if candle["color"] == "green" else candle["bottom"]


def _quadrant_color(quadrant):
    open_y = _open_y(quadrant[0])
    close_y = _close_y(quadrant[-1])
    return "green" if close_y < open_y else "red"


def compute_trend(candles, tolerance_px=LATERAL_TOLERANCE_PX):
    open1 = _open_y(candles[0])
    close_last = _close_y(candles[-1])
    diff = open1 - close_last
    if abs(diff) <= tolerance_px:
        return "lateral"
    return "alta" if diff > 0 else "baixa"


def decide(candles, tolerance_px=LATERAL_TOLERANCE_PX):
    """
    candles: 10 velas de 1min ja fechadas (2 quadrantes completos).

    Prioridade:
      1. TENDENCIA manda sempre que estiver clara (alta/baixa) - entra a favor
         dela, independente de os 2 quadrantes terem alternado ou nao.
      2. So quando esta LATERAL (sem tendencia clara) e que a alternancia entre
         os 2 quadrantes decide: se alternou, entra na cor do quadrante mais
         recente. Se nao alternou e esta lateral, nao ha sinal -> nao opera.
    """
    if len(candles) != 2 * QUADRANT_SIZE:
        raise ValueError(f"Preciso exatamente {2 * QUADRANT_SIZE} velas de 1min (2 quadrantes completos)")

    quad_anterior = candles[:QUADRANT_SIZE]
    quad_recente = candles[QUADRANT_SIZE:]

    cor_anterior = _quadrant_color(quad_anterior)
    cor_recente = _quadrant_color(quad_recente)

    tendencia = compute_trend(candles, tolerance_px)
    if tendencia == "alta":
        return "buy"
    if tendencia == "baixa":
        return "sell"

    # Lateral: sem tendencia clara. Usa a alternancia entre os 2 quadrantes
    # como criterio de desempate. Se nao alternou tambem, nao ha sinal.
    if cor_anterior != cor_recente:
        return "buy" if cor_recente == "green" else "sell"
    return None
