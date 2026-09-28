"""
balance_reader.py
==================
Script SEPARADO do bot principal. Unica funcao: tirar print de uma area
fixa da tela (onde a corretora mostra o saldo), ler o numero com OCR,
e manter esse valor sempre atualizado em um arquivo JSON
("balance_status.json"). O bot principal so precisa LER esse arquivo
para saber o saldo atual em tempo real -- este script nao mexe no
config.json do bot, entao nao ha risco de apagar suas calibracoes.

--------------------------------------------------------------------
DEPENDENCIAS (rode isso UMA VEZ no terminal / prompt de comando):

    pip install mss easyocr pillow

Nao precisa instalar mais nada alem disso -- nao tem programa externo,
nao precisa configurar caminho nenhum. Na primeira execucao o easyocr
baixa sozinho (da internet) os arquivos do modelo de leitura, o que
pode demorar um pouco so na primeira vez.

--------------------------------------------------------------------
COMO USAR:

1. Rode o script: python balance_reader.py
2. Na primeira vez, vai abrir uma tela para voce arrastar um
   retangulo em cima do numero do saldo na corretora (deixe a
   corretora aberta e visivel antes de rodar).
3. Depois disso ele fica rodando sozinho, atualizando o saldo a cada
   2 segundos (pode mudar o INTERVALO_SEGUNDOS abaixo) no arquivo
   balance_status.json, na mesma pasta do script.
4. Para o bot principal usar o saldo, e so ele ler esse arquivo
   JSON. Formato salvo:
       {"balance": 1234.56, "updated_at": "2026-08-05T12:34:56", "ok": true}

Para recalibrar a area (se a corretora mudar de lugar na tela),
apague o arquivo region_config.json e rode o script de novo.
"""

import json
import os
import re
import time
from datetime import datetime

import mss
import numpy as np
import easyocr
from PIL import Image

# ---------------------------------------------------------------
# CONFIGURACOES - ajuste aqui
# ---------------------------------------------------------------

INTERVALO_SEGUNDOS = 2  # de quanto em quanto tempo le o saldo de novo

PASTA_SCRIPT = os.path.dirname(os.path.abspath(__file__))
ARQUIVO_REGIAO = os.path.join(PASTA_SCRIPT, "region_config.json")
ARQUIVO_SAIDA = os.path.join(PASTA_SCRIPT, "balance_status.json")


# ---------------------------------------------------------------
# Passo 1: selecionar a area do saldo na tela (uma vez, com o mouse)
# ---------------------------------------------------------------

def selecionar_area():
    """Abre uma janela transparente cobrindo a tela toda para o
    usuario arrastar um retangulo sobre o numero do saldo."""
    import tkinter as tk

    coords = {}

    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-alpha", 0.3)
    root.attributes("-topmost", True)
    root.configure(bg="black")
    root.title("Arraste um retangulo sobre o SALDO e solte")

    canvas = tk.Canvas(root, cursor="cross", bg="grey")
    canvas.pack(fill=tk.BOTH, expand=True)

    rect = {"start_x": 0, "start_y": 0, "id": None}

    def on_press(event):
        rect["start_x"] = event.x
        rect["start_y"] = event.y
        rect["id"] = canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="red", width=3
        )

    def on_drag(event):
        canvas.coords(rect["id"], rect["start_x"], rect["start_y"], event.x, event.y)

    def on_release(event):
        x1, y1 = rect["start_x"], rect["start_y"]
        x2, y2 = event.x, event.y
        coords["left"] = int(min(x1, x2))
        coords["top"] = int(min(y1, y2))
        coords["width"] = int(abs(x2 - x1))
        coords["height"] = int(abs(y2 - y1))
        root.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)

    label = tk.Label(
        root,
        text="Arraste um retangulo em cima do numero do SALDO e solte o mouse",
        fg="white",
        bg="black",
        font=("Arial", 16),
    )
    label.place(relx=0.5, rely=0.05, anchor="n")

    root.mainloop()
    return coords


def carregar_regiao():
    if os.path.exists(ARQUIVO_REGIAO):
        with open(ARQUIVO_REGIAO, "r", encoding="utf-8") as f:
            return json.load(f)

    print("Nenhuma area configurada ainda. Vamos selecionar agora.")
    print("Deixe a corretora aberta e visivel na tela ANTES de continuar.")
    input("Pressione ENTER quando estiver pronto...")

    regiao = selecionar_area()
    if not regiao.get("width") or not regiao.get("height"):
        raise RuntimeError("Nenhuma area valida foi selecionada. Rode o script de novo.")

    with open(ARQUIVO_REGIAO, "w", encoding="utf-8") as f:
        json.dump(regiao, f, indent=2)

    print(f"Area salva em {ARQUIVO_REGIAO}. Se a corretora mudar de lugar,")
    print("apague esse arquivo para escolher a area de novo.")
    return regiao


# ---------------------------------------------------------------
# Passo 2: tirar print da area e ler o numero com OCR
# ---------------------------------------------------------------

def extrair_numero(texto):
    texto_limpo = texto.replace(" ", "")
    match = re.search(r"[\d]+[.,]?[\d]*", texto_limpo)
    if not match:
        return None

    numero_str = match.group(0).replace(",", ".")
    partes = numero_str.split(".")
    if len(partes) > 2:
        numero_str = "".join(partes[:-1]) + "." + partes[-1]

    try:
        return float(numero_str)
    except ValueError:
        return None


def ler_saldo(sct, regiao, reader):
    """Tira print da area configurada e tenta extrair um numero dela."""
    shot = sct.grab(regiao)
    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    img = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)

    resultados = reader.readtext(np.array(img), detail=0, allowlist="0123456789.,")
    texto_junto = " ".join(resultados)
    return extrair_numero(texto_junto)


def salvar_status(saldo, ok):
    dados = {
        "balance": saldo,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "ok": ok,
    }
    tmp = ARQUIVO_SAIDA + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f)
    os.replace(tmp, ARQUIVO_SAIDA)


# ---------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------

def main():
    regiao = carregar_regiao()
    ultimo_saldo_valido = None

    print("Carregando o leitor de OCR (so demora na primeira vez)...")
    reader = easyocr.Reader(["en"], gpu=False)

    print("Lendo saldo continuamente. Pressione CTRL+C para parar.")
    with mss.mss() as sct:
        while True:
            try:
                saldo = ler_saldo(sct, regiao, reader)
                if saldo is not None:
                    ultimo_saldo_valido = saldo
                    salvar_status(saldo, ok=True)
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Saldo: {saldo}")
                else:
                    salvar_status(ultimo_saldo_valido, ok=False)
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Falha na leitura, mantendo ultimo valor")
            except Exception as e:
                print(f"Erro: {e}")

            time.sleep(INTERVALO_SEGUNDOS)


if __name__ == "__main__":
    main()
