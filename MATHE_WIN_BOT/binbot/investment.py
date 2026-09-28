"""
Ajusta o campo de valor do investimento na tela da corretora, ANTES de
clicar compra/venda. Precisa ter sido calibrado antes (veja
calibrate_investment_field.py).

A Quotex nao aceita virgula/decimal nesse campo - por isso todo valor
calculado pelo gerenciamento (soros/gale) e SEMPRE arredondado pra CIMA
(math.ceil) e digitado como numero inteiro.
"""
import math
import time

import pyautogui


def set_investment_value(cfg, valor, dry_run=False):
    """
    valor: numero (pode ter decimais) calculado pelo gerenciamento.py.
    Arredonda pra cima e digita esse valor inteiro no campo calibrado.
    """
    valor_int = math.ceil(valor)

    if "investment_field" not in cfg:
        print("AVISO: campo de investimento nao calibrado ainda - rode "
              "'python calibrate_investment_field.py'. Mantendo o valor atual na tela.")
        return valor_int

    if dry_run:
        print(f"   (dry-run: ajustaria o investimento para {valor_int} R$, sem clicar de verdade)")
        return valor_int

    x, y = cfg["investment_field"]
    pyautogui.click(x, y)
    time.sleep(0.05)
    pyautogui.hotkey("ctrl", "a")   # seleciona o valor atual todo
    pyautogui.press("backspace")    # apaga
    pyautogui.typewrite(str(valor_int), interval=0.02)
    pyautogui.press("tab")          # confirma e tira o foco do campo

    return valor_int


if __name__ == "__main__":
    import json
    with open("config.json") as f:
        cfg = json.load(f)
    print("Teste (dry-run) - valores que seriam digitados:")
    for v in [10, 12.8, 4.992, 17.792]:
        resultado = set_investment_value(cfg, v, dry_run=True)
        print(f"   {v} -> {resultado}")
