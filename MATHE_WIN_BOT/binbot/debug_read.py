"""
DIAGNOSTICO - tira um print da area do grafico calibrada, DETECTA AUTOMATICAMENTE
todas as velas visiveis (sem precisar contar), e marca cada uma encontrada com
uma linha na posicao real do centro dela. Gera debug.png pra voce conferir.

As 2 velas mais a direita (amarelo) sao vela1/vela2 do quadrante ATUAL (ainda
se formando, fora da analise). As 10 anteriores a essas (rosa) sao os 2
quadrantes ja fechados, usados de verdade na decisao.

Uso:
  python debug_read.py
"""
import json
from PIL import ImageDraw

import candles

CONFIG_PATH = "config.json"


def main():
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)

    img = candles.grab_chart(cfg).convert("RGB")
    blobs = candles.detect_candle_blobs(cfg)

    print(f"Area do grafico: {img.width}x{img.height} pixels")
    print(f"Velas detectadas automaticamente: {len(blobs)}")
    print()

    if len(blobs) < 12:
        print("AVISO: menos de 12 velas detectadas. O bot precisa de pelo menos 12")
        print("(10 dos 2 quadrantes fechados + 2 do quadrante atual) para decidir.")
        print("Isso pode acontecer se: a area calibrada do grafico for pequena demais,")
        print("a cor calibrada (verde/vermelho) nao bate direito, ou o zoom do grafico")
        print("mostra poucas velas. Tente aumentar a area calibrada ou o zoom out do grafico.")

    draw = ImageDraw.Draw(img)
    for idx, b in enumerate(blobs):
        x = int(b["x_center"] - cfg["chart_left"])
        cor_linha = (255, 220, 0) if idx >= len(blobs) - 2 else (255, 0, 255)
        tag = "[quadrante atual]" if idx >= len(blobs) - 2 else "[analise]"
        print(f"Vela {idx} (x={x}): {b['color']} {tag}")
        draw.line([(x, 0), (x, img.height)], fill=cor_linha, width=2)

    img.save("debug.png")
    print("\nImagem salva em debug.png. Confira se cada linha cai EM CIMA de uma vela")
    print("diferente (uma linha por corpo de vela, nao mais de uma na mesma vela e")
    print("nenhuma vela sem linha).")


if __name__ == "__main__":
    main()
