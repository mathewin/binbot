"""
CALIBRACAO - rode isso primeiro, uma unica vez (ou sempre que mudar o layout/zoom da tela).

Abra o navegador com o grafico visivel ANTES de rodar este script.
O script vai pedir para voce passar o mouse sobre pontos especificos e apertar ENTER
(nao clique - so posicione o mouse e volte pro teclado pra apertar Enter).

Ordem dos pontos pedidos:
  1. Canto SUPERIOR ESQUERDO da area do grafico (onde comecam as velas)
  2. Canto INFERIOR DIREITO da area do grafico
  3. Um ponto DENTRO do corpo de uma vela DE ALTA (qualquer que seja a cor usada)
  4. Um ponto DENTRO do corpo de uma vela DE BAIXA (qualquer que seja a cor usada)
  5. O botao de COMPRA
  6. O botao de VENDA

NAO pede mais pra contar quantas velas ficam visiveis - o bot agora encontra
as velas sozinho, escaneando a tela de verdade (veja candles.py).

Tambem NAO calibra mais a regiao do painel de Operacoes da corretora - o
resultado (ganhou/perdeu) agora e apurado comparando a cor da vela que
fechou com a direcao operada, usando a mesma calibracao do grafico (ver
bot.py: _read_candle_result).
"""
import json
import time
import pyautogui

CONFIG_PATH = "config.json"


def wait_for_position(label):
    print(f"\n>> Posicione o mouse sobre: {label}")
    print("   Quando estiver no lugar certo, aperte ENTER aqui no teclado.")
    input("   Pressione ENTER quando estiver pronto...")
    x, y = pyautogui.position()
    print(f"   Capturado: ({x}, {y})")
    return x, y


def main():
    print("=== CALIBRACAO DO BOT ===")
    time.sleep(1)

    chart_tl = wait_for_position("canto SUPERIOR ESQUERDO da area do grafico")
    chart_br = wait_for_position("canto INFERIOR DIREITO da area do grafico")

    up_pt = wait_for_position("DENTRO do corpo de uma vela DE ALTA (a cor que sobe no seu grafico, seja qual for)")
    up_rgb = pyautogui.pixel(*up_pt)

    down_pt = wait_for_position("DENTRO do corpo de uma vela DE BAIXA (a cor que desce no seu grafico, seja qual for)")
    down_rgb = pyautogui.pixel(*down_pt)

    dist = sum((a - b) ** 2 for a, b in zip(up_rgb[:3], down_rgb[:3])) ** 0.5
    if dist < 60:
        print(f"\nAVISO: as duas cores calibradas ficaram muito parecidas entre si (diferenca: {dist:.0f}).")
        print("Isso pode fazer o bot confundir vela de alta com vela de baixa.")
        print("Confira se clicou EXATAMENTE dentro do corpo de cada vela (nao no pavio nem na borda)")
        print("e recalibre se precisar - de preferencia com o grafico com zoom bom o suficiente.")

    buy_btn = wait_for_position("o botao de COMPRA")
    sell_btn = wait_for_position("o botao de VENDA")

    clock_offset = input("\nOffset de sincronizacao em segundos (deixe em branco se nao souber ainda): ")
    clock_offset = int(clock_offset.strip() or "0")

    config = {
        "chart_left": chart_tl[0],
        "chart_top": chart_tl[1],
        "chart_right": chart_br[0],
        "chart_bottom": chart_br[1],
        "up_rgb": list(up_rgb[:3]),
        "down_rgb": list(down_rgb[:3]),
        "color_tolerance": 35,
        "buy_button": list(buy_btn),
        "sell_button": list(sell_btn),
        "entry_delay_seconds": 0,
        "clock_offset_seconds": clock_offset,
    }

    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)

    print("\nCalibracao salva em config.json:")
    print(json.dumps(config, indent=2))
    print("\nRode agora: python debug_read.py   (para conferir se a deteccao esta certa)")


if __name__ == "__main__":
    main()
