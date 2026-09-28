"""
OVERLAY - janela pequena, sempre por cima de tudo (inclusive do navegador).

Versao com interface redesenhada: toda a UI vive num unico tk.Canvas (fundo
animado com linhas diagonais em movimento + botoes/pills desenhados na mao,
com cantos arredondados e hover). Isso permite ter um fundo "vivo" por tras
de tudo, coisa que widgets tk normais (Frame/Label/Button) nao deixam fazer
por serem opacos e nao empilharem com transparencia.

A LOGICA e a API sao exatamente as mesmas de antes - so a pele mudou:
  - run_overlay(state, cfg=None) continua sendo o ponto de entrada chamado
    pelo bot.py.
  - Titulo "MATHEWIN BOT" e relogio em tempo real.
  - Selecao de estrategia (pills: Quadrantes / Surf / Quad3) - so pode
    trocar enquanto o bot estiver PARADO.
  - Botao INICIAR/PARAR - liga e desliga o robo sem precisar fechar o CMD.
  - Botao CALIBRAR - abre calibrate.py numa janela separada.
  - Botao SAIR (antigo "DESLIGAR TUDO") - mata o processo inteiro.
  - Ponto de status pulsando, barra com a vela atual do quadrante (so
    aparece em estrategias com QUADRANT_SIZE + CANDLE_SECONDS definidos -
    hoje nenhuma estrategia define CANDLE_SECONDS, entao essa barra fica
    invisivel; isso ja era assim na versao anterior, nao mudei a logica),
    e anel de contagem regressiva ate a proxima entrada.
  - Ultima direcao operada e placar (ganhas/perdidas, taxa de acerto e
    sequencia atual).

JANELA REDIMENSIONAVEL (NOVO)
------------------------------
Antes a janela era travada num tamanho fixo de proposito: o Canvas so
desenhava os itens uma unica vez, com coordenadas absolutas. Se a janela
fosse redimensionada, o Windows mudava o tamanho visivel da janela mas os
itens do Canvas continuavam exatamente nas mesmas coordenadas -- entao a
"caixa" clicavel de uma pill (ex: "SURF") ficava para tras enquanto o
retangulo desenhado na tela se esticava/movia, e o clique parava de bater
no lugar certo (ou so funcionava num tamanho especifico).

A solucao agora nao e travar o tamanho, e sim REDESENHAR TUDO do zero toda
vez que a janela muda de tamanho (redimensionar na mao OU clicar no botao
de maximizar/restaurar da barra de titulo -- ambos disparam o mesmo evento
<Configure> do Tkinter). Cada elemento e calculado a partir de coordenadas
"de referencia" (o design original, 460x610) passadas por um fator de
escala (LY["scale"]) + um deslocamento de centralizacao (LY["ox"]/["oy"]),
e SEMPRE redesenhado nessas coordenadas finais reais -- entao a area
clicavel de cada pill/botao SEMPRE bate exatamente com o que aparece na
tela, em QUALQUER tamanho de janela. O redesenho e "debounced" (so roda um
pouquinho depois que voce solta o mouse de arrastar a borda) pra nao ficar
recriando tudo a cada pixel enquanto arrasta.

Usa tkinter (ja vem junto com o Python no Windows). Roda na THREAD PRINCIPAL
(obrigatorio pro tkinter); o robo de verdade (bot.py) roda numa thread
separada em segundo plano e so le/escreve no SharedState.
"""
import ctypes
import importlib
import json
import os
import subprocess
import sys
import tkinter as tk
from datetime import datetime, timedelta

import mss
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageTk

import gerenciamento

CONFIG_PATH = "config.json"

# ---------------------------------------------------------------- paleta ----
# Paleta puxada do personagem "Mathewin": verde vivo em destaque, com rosa
# (cabelo), azul (camisa) e laranja (mascara) espalhados como acentos.
BG          = "#050b06"   # fundo geral, quase preto com um fundo de verde
GRID_LINE   = "#0e1f10"   # linhas do fundo animado (verde bem discreto)
PANEL_EDGE  = "#17291d"   # bordas finas de paineis/pills
PANEL_BG    = "#060f08"   # fundo dos "cards" internos
FG          = "#f1f7ee"
MUTED       = "#647063"
ACCENT      = "#3ee61a"   # verde do personagem - cor principal do bot
ACCENT_DIM  = "#0e3308"
DANGER      = "#ff4757"   # mantido: vermelho = venda, nao mexe no significado
DANGER_DIM  = "#2c0f14"
AMBER       = "#f2a318"   # laranja da mascara do Mathewin
AMBER_DIM   = "#332108"
PILL_BG     = "#0c1710"
PINK        = "#ff7fc0"   # rosa do cabelo - acento decorativo
BLUE        = "#5b8fd6"   # azul da camisa - acento decorativo
PURPLE      = "#a855f7"   # roxo vivo do Mathewin - usado nas linhas do fundo animado
GREEN_WIN   = "#2ee66b"   # verde de ganho (placar)
RED_LOSS    = "#ff4757"   # vermelho de perda (placar) - mesmo tom do DANGER
GREEN_WIN_BG = "#0d2a16"  # fundo do card de ganhos
RED_LOSS_BG  = "#301017"  # fundo do card de perdas

# cores das linhas do fundo animado, mais vivas/saturadas, alternando
# verde <-> roxo (paleta do Mathewin) em vez do verde quase invisivel de antes
BG_LINE_COLORS = ["#1fae4c", "#8b3fd6"]

FONT_TITLE  = ("Chakra Petch", 13, "bold")
FONT_SUB    = ("JetBrains Mono", 8)
FONT_CLOCK  = ("JetBrains Mono", 22, "bold")
FONT_PILL   = ("JetBrains Mono", 9, "bold")
FONT_BTN    = ("JetBrains Mono", 11, "bold")
FONT_BTN_SM = ("JetBrains Mono", 9, "bold")
FONT_LABEL  = ("Inter", 9)
FONT_MONO_SM = ("JetBrains Mono", 8)

# Tamanho de REFERENCIA do design (o "100%"). Todas as coordenadas do layout
# abaixo sao pensadas neste tamanho e depois escaladas para o tamanho real
# da janela em tempo de execucao - ver build_ui() dentro de run_overlay().
WIDTH, HEIGHT = 460, 610

# tamanho minimo que a janela pode ser encolhida (pra nao virar uma sopa de
# texto ilegivel) - ainda mantem as proporcoes do design original
MIN_WIDTH, MIN_HEIGHT = 340, 451

# fator de escala maximo permitido (evita janelas absurdamente grandes com
# fontes gigantes/blur pesado se alguem maximizar num monitor enorme)
MAX_SCALE = 2.3
MIN_SCALE = 0.7

# maior tempo de espera (em segundos) que o anel de contagem consegue
# representar visualmente antes de considerar "cheio" - so estetico,
# nao afeta a logica real de entrada do bot.py.
RING_MAX_SECONDS = 60


def _quadrant_info(strategy_name):
    """
    Le QUADRANT_SIZE e CANDLE_SECONDS do modulo da estrategia selecionada.
    Retorna (quadrant_size, candle_seconds) ou None se a estrategia nao
    tiver conceito de quadrante (ex: surf, que opera todo minuto), ou se o
    modulo nao definir CANDLE_SECONDS (nenhuma estrategia atual define).
    """
    try:
        mod = importlib.import_module(f"strategy_{strategy_name}")
    except Exception:
        return None
    quadrant_size = getattr(mod, "QUADRANT_SIZE", None)
    candle_seconds = getattr(mod, "CANDLE_SECONDS", None)
    if not quadrant_size or not candle_seconds:
        return None
    return quadrant_size, candle_seconds


def _vela_atual(quadrant_size, candle_seconds, clock_offset_seconds=0):
    """Calcula, pelo relogio (com o mesmo ajuste de calibracao do bot.py), em
    qual vela do quadrante estamos agora (1..quadrant_size)."""
    now = datetime.now() + timedelta(seconds=clock_offset_seconds)
    segundos_do_dia = now.hour * 3600 + now.minute * 60 + now.second
    bloco_segundos = quadrant_size * candle_seconds
    return (segundos_do_dia % bloco_segundos) // candle_seconds + 1


def _rounded_rect(canvas, x0, y0, x1, y1, r=12, **kwargs):
    """Retangulo com cantos arredondados via poligono suavizado (smooth)."""
    r = max(0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    points = [
        x0 + r, y0,
        x1 - r, y0,
        x1, y0,
        x1, y0 + r,
        x1, y1 - r,
        x1, y1,
        x1 - r, y1,
        x0 + r, y1,
        x0, y1,
        x0, y1 - r,
        x0, y0 + r,
        x0, y0,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _make_vertical_gradient(width, height, top_hex, bottom_hex):
    """Imagem RGB com degrade vertical suave entre duas cores (igual o
    linear-gradient(180deg, ...) do site) - Tkinter Canvas nao tem gradiente
    nativo, entao pre-renderiza com PIL/numpy e usa como imagem de fundo."""
    width, height = max(1, int(width)), max(1, int(height))
    top = np.array(_hex_to_rgb(top_hex), dtype=np.float32)
    bottom = np.array(_hex_to_rgb(bottom_hex), dtype=np.float32)
    t = np.linspace(0, 1, height, dtype=np.float32).reshape(height, 1, 1)
    row = top.reshape(1, 1, 3) + (bottom - top).reshape(1, 1, 3) * t
    arr = np.repeat(row, width, axis=1).astype(np.uint8)
    return Image.fromarray(arr, mode="RGB")


def _make_glow(diameter, color_hex, alpha=140, blur=None):
    """Imagem RGBA com um brilho suave (circulo desfocado) - equivalente ao
    box-shadow/blur do CSS (--accent-glow etc), que o Canvas nao tem nativo."""
    diameter = max(2, int(diameter))
    blur = blur if blur is not None else diameter // 4
    blur = max(1, blur)
    pad = blur * 2
    size = diameter + pad * 2
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    r, g, b = _hex_to_rgb(color_hex)
    draw.ellipse([pad, pad, pad + diameter, pad + diameter], fill=(r, g, b, alpha))
    return img.filter(ImageFilter.GaussianBlur(blur))


def run_overlay(state, cfg=None):
    # DPI-AWARENESS (Windows): sem isso, em telas com escala diferente de
    # 100% (125%, 150% etc - muito comum hoje em dia), o Windows "escala"
    # a janela visualmente por fora do Tkinter, e a posicao onde o clique e
    # visto na tela para de bater com a posicao onde ele e realmente
    # registrado - fazendo clique em botao/pill parecer que "nao funciona"
    # (o clique acerta um ponto invisivel deslocado do que aparece na tela).
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # fallback p/ Windows mais antigo
        except Exception:
            pass

    # Da um nome unico pra janela do CONSOLE (CMD) que esta rodando este
    # programa, pra dar pra mata-la certeiramente pelo nome depois (no
    # botao SAIR), nao importa se foi aberta via .bat, atalho ou digitando
    # "python bot.py" direto.
    try:
        ctypes.windll.kernel32.SetConsoleTitleW("MATHEWIN BOT - console")
    except Exception:
        pass

    root = tk.Tk()
    root.title("MATHEWIN BOT")
    root.attributes("-topmost", True)  # sempre por cima
    root.attributes("-alpha", 0.97)    # levemente transparente
    root.configure(bg=BG)

    # Janela AGORA E REDIMENSIONAVEL - o usuario pode arrastar a borda ou
    # usar o botao de maximizar/restaurar da barra de titulo. O tamanho
    # inicial (o "que ja abre") continua sendo o design de referencia
    # 460x610, mas a partir dai pode crescer/encolher livremente entre
    # MIN_WIDTH/MIN_HEIGHT e o tamanho da tela.
    root.resizable(True, True)
    root.minsize(MIN_WIDTH, MIN_HEIGHT)

    screen_w = root.winfo_screenwidth()
    # posicionado no canto superior ESQUERDO (nao mais direito) - o painel
    # cresceu de tamanho e no canto direito ele estava cobrindo os botoes
    # de compra/venda da corretora, fazendo o clique automatico do bot cair
    # em cima do proprio painel em vez de acertar o botao de verdade.
    root.geometry(f"{WIDTH}x{HEIGHT}+20+20")

    # cfg pode chegar None (maquina nova, sem calibracao ainda). Guardo num
    # dict mutavel pra poder atualizar de dentro do refresh() assim que o
    # config.json for criado (ex: pelo botao CALIBRAR), sem reiniciar nada.
    cfg_state = {"cfg": cfg}

    def _tenta_carregar_cfg():
        if cfg_state["cfg"] is not None:
            return
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH) as f:
                    cfg_state["cfg"] = json.load(f)
            except Exception:
                pass  # arquivo ainda sendo escrito ou invalido - tenta de novo no proximo refresh

    canvas = tk.Canvas(root, bg=BG, highlightthickness=0)
    canvas.pack(fill="both", expand=True)

    # avatar do personagem Mathewin - carregado uma vez com PIL (pra poder
    # redimensionar em qualquer proporcao depois, ao contrario do
    # tk.PhotoImage.subsample que so aceita fatores inteiros)
    try:
        avatar_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mathewin.png")
        _avatar_src = Image.open(avatar_path).convert("RGBA")
    except Exception:
        _avatar_src = None

    # ============================================================ LAYOUT ===
    # LY guarda o estado de escala/centralizacao ATUAL. X()/Y()/S()/F() usam
    # SEMPRE os valores mais recentes de LY - por isso funcoes que redesenham
    # a cada "tick" do refresh() (fundo animado, barra da vela, aneis) nao
    # precisam saber que a janela mudou de tamanho: elas so chamam X()/Y()
    # de novo e ja saem na posicao/escala certa.
    LY = {"scale": 1.0, "ox": 0.0, "oy": 0.0, "w": WIDTH, "h": HEIGHT}

    def X(bx):
        return LY["ox"] + bx * LY["scale"]

    def Y(by):
        return LY["oy"] + by * LY["scale"]

    def S(v):
        return v * LY["scale"]

    def SW(v):
        """Espessura de traco/borda - sempre pelo menos 1px."""
        return max(1, round(v * LY["scale"]))

    def F(base_font):
        family = base_font[0]
        size = max(6, round(base_font[1] * LY["scale"]))
        rest = base_font[2:]
        return (family, size) + tuple(rest)

    # ui guarda as referencias (ids de item do canvas / widgets) da
    # construcao MAIS RECENTE - e recriado do zero a cada build_ui().
    # Os "estados" de verdade (qual estrategia esta selecionada, se esta
    # pausado etc.) NAO ficam aqui - ficam nas variaveis abaixo, que
    # sobrevivem a qualquer redesenho.
    ui = {}
    strategy_var = {"value": state.get_strategy()}
    surf_exp_var = {"value": state.get_surf_expiracao()}  # 1 a 15 (minutos) - so vale p/ "surf"
    pulse = {"grow": True, "r": 3.0}
    manual_status_reset = {"job": None}
    vela_visible = {"on": False}
    anim = {"offset": 0.0}

    # ---------------------------------------------------- acoes/handlers ---
    # Definidos ANTES do build_ui() e ligados aos TAGS do canvas (nao aos ids
    # de item) - no Tkinter, um tag_bind fica associado ao nome do tag na
    # propria widget Canvas, entao continua valendo mesmo depois que
    # build_ui() apaga e recria os itens com aquele mesmo tag. Ou seja: so
    # precisa bindar uma vez, mesmo que a UI seja redesenhada varias vezes.
    def _make_pill_click(nome):
        def _click(event=None):
            if state.is_running():
                return
            strategy_var["value"] = nome
            state.set_strategy(nome)
            refresh_pills()
        return _click

    def _muda_surf_exp(delta):
        def _click(event=None):
            if state.is_running():
                return
            novo = max(1, min(15, surf_exp_var["value"] + delta))
            surf_exp_var["value"] = novo
            state.set_surf_expiracao(novo)
            strategy_var["value"] = "surf"
            state.set_strategy("surf")
            if "surf_exp_label_text" in ui:
                canvas.itemconfig(ui["surf_exp_label_text"], text=f"M{novo}")
            refresh_pills()
        return _click

    def registrar_manual(ganhou):
        """Chamado ao clicar em '+' (ganhou=True) ou '-' (ganhou=False).
        So mexe no placar MANUAL (contadores separados do automatico do
        bot, de proposito, pra comparar depois) - e ja grava a linha na
        planilha (aba "Manual"), igual o placar automatico ja fazia na
        aba "Bot". O saldo agora e sempre automatico (OCR - balance.py),
        entao aqui nao mexe em saldo nenhum."""
        state.add_resultado_manual(ganhou)
        texto, cor = ("+1 GANHO", GREEN_WIN) if ganhou else ("+1 PERDA", RED_LOSS)
        if "manual_status" in ui:
            canvas.itemconfig(ui["manual_status"], text=texto, fill=cor)
        if manual_status_reset["job"] is not None:
            root.after_cancel(manual_status_reset["job"])
        manual_status_reset["job"] = root.after(
            1500, lambda: canvas.itemconfig(ui["manual_status"], text="") if "manual_status" in ui else None
        )

    def toggle(event=None):
        if cfg_state["cfg"] is None:
            return  # sem calibracao ainda - por seguranca, ignora clique
        if state.is_running():
            state.stop()
        else:
            state.start()
        refresh_button()
        refresh_pills()

    def calibrar(event=None):
        # Abre o calibrate.py numa janela de CMD separada, sem travar o
        # overlay. O config.json novo so passa a valer depois que o bot.py
        # for reiniciado (ele so le o config uma vez, no comeco).
        if state.is_running():
            return
        try:
            subprocess.Popen([sys.executable, "calibrate.py"], creationflags=subprocess.CREATE_NEW_CONSOLE)
        except Exception as e:
            print(f"Nao consegui abrir o calibrate.py: {e}")

    def desligar_tudo(event=None):
        # Mata a janela do console pelo NOME que demos a ela no inicio (mais
        # confiavel que tentar adivinhar o PID do processo pai). /T mata os
        # processos filhos junto (o proprio bot.py), /F forca sem confirmar.
        try:
            subprocess.run(["taskkill", "/F", "/T", "/FI", "WINDOWTITLE eq MATHEWIN BOT - console*"],
                            capture_output=True)
        except Exception as e:
            print(f"Nao consegui fechar a janela do CMD sozinho: {e}")
        os._exit(0)

    def pausar_toggle(event=None):
        # so faz sentido pausar/retomar com o robo ligado
        if not state.is_running():
            return
        if state.is_paused():
            state.resume()
        else:
            state.pause()
        refresh_pausa_button()

    def _cursor_pill(event=None):
        canvas.config(cursor="arrow" if state.is_running() else "hand2")

    def _cursor_arrow(event=None):
        canvas.config(cursor="arrow")

    def _cursor_hand(event=None):
        canvas.config(cursor="hand2")

    def _cursor_calibrar(event=None):
        canvas.config(cursor="arrow" if state.is_running() else "hand2")

    def _cursor_pausar(event=None):
        canvas.config(cursor="arrow" if not state.is_running() else "hand2")

    # binda os tags UMA UNICA VEZ - continuam validos pra sempre, mesmo com
    # a UI sendo apagada/recriada a cada resize
    canvas.tag_bind("pill_quadrantes", "<Button-1>", _make_pill_click("quadrantes"))
    canvas.tag_bind("pill_surf", "<Button-1>", _make_pill_click("surf"))
    canvas.tag_bind("pill_quad3", "<Button-1>", _make_pill_click("quad3"))
    for ptag in ("pill_quadrantes", "pill_surf", "pill_quad3"):
        canvas.tag_bind(ptag, "<Enter>", _cursor_pill)
        canvas.tag_bind(ptag, "<Leave>", _cursor_arrow)

    canvas.tag_bind("surf_exp_menos", "<Button-1>", _muda_surf_exp(-1))
    canvas.tag_bind("surf_exp_mais", "<Button-1>", _muda_surf_exp(1))
    for stag in ("surf_exp_menos", "surf_exp_mais"):
        canvas.tag_bind(stag, "<Enter>", _cursor_pill)
        canvas.tag_bind(stag, "<Leave>", _cursor_arrow)

    canvas.tag_bind("btn_manual_win", "<Button-1>", lambda e: registrar_manual(True))
    canvas.tag_bind("btn_manual_win", "<Enter>", _cursor_hand)
    canvas.tag_bind("btn_manual_win", "<Leave>", _cursor_arrow)
    canvas.tag_bind("btn_manual_loss", "<Button-1>", lambda e: registrar_manual(False))
    canvas.tag_bind("btn_manual_loss", "<Enter>", _cursor_hand)
    canvas.tag_bind("btn_manual_loss", "<Leave>", _cursor_arrow)

    canvas.tag_bind("btn_toggle", "<Button-1>", toggle)
    canvas.tag_bind("btn_toggle", "<Enter>", _cursor_hand)
    canvas.tag_bind("btn_toggle", "<Leave>", _cursor_arrow)

    canvas.tag_bind("btn_calibrar", "<Button-1>", calibrar)
    canvas.tag_bind("btn_calibrar", "<Enter>", _cursor_calibrar)
    canvas.tag_bind("btn_calibrar", "<Leave>", _cursor_arrow)

    canvas.tag_bind("btn_desligar", "<Button-1>", desligar_tudo)
    canvas.tag_bind("btn_desligar", "<Enter>", _cursor_hand)
    canvas.tag_bind("btn_desligar", "<Leave>", _cursor_arrow)

    canvas.tag_bind("btn_pausar", "<Button-1>", pausar_toggle)
    canvas.tag_bind("btn_pausar", "<Enter>", _cursor_pausar)
    canvas.tag_bind("btn_pausar", "<Leave>", _cursor_arrow)

    # -------------------------------------------------------- refresh_* ----
    def refresh_pills():
        travado = state.is_running()
        for nome, (rect, text) in ui.get("pill_items", {}).items():
            ativo = nome == strategy_var["value"]
            if ativo:
                canvas.itemconfig(rect, fill=ACCENT_DIM, outline=ACCENT)
                canvas.itemconfig(text, fill=ACCENT)
            else:
                canvas.itemconfig(rect, fill=PILL_BG, outline=PANEL_EDGE)
                canvas.itemconfig(text, fill="#33373f" if travado else MUTED)
        if "surf_exp_label" in ui:
            label_rect, label_text = ui["surf_exp_label"]
            ativo = strategy_var["value"] == "surf"
            cor = ACCENT if ativo else ("#33373f" if travado else MUTED)
            canvas.itemconfig(label_rect, outline=(ACCENT if ativo else PANEL_EDGE))
            canvas.itemconfig(label_text, fill=cor)
        for stag in ("surf_exp_menos", "surf_exp_mais"):
            for item in canvas.find_withtag(stag):
                if canvas.type(item) == "text":
                    canvas.itemconfig(item, fill="#33373f" if travado else MUTED)

    def refresh_button():
        if "btn_rect" not in ui:
            return
        if cfg_state["cfg"] is None:
            canvas.itemconfig(ui["btn_rect"], fill=AMBER_DIM, outline=AMBER)
            canvas.itemconfig(ui["btn_text"], text="\u26a0  CALIBRE PRIMEIRO", fill=AMBER)
            canvas.itemconfig(ui["btn_glow"], image=ui["btn_glow_imgs"]["amber"])
        elif state.is_running():
            canvas.itemconfig(ui["btn_rect"], fill=DANGER_DIM, outline=DANGER)
            canvas.itemconfig(ui["btn_text"], text="\u23f8  PARAR", fill=DANGER)
            canvas.itemconfig(ui["btn_glow"], image=ui["btn_glow_imgs"]["danger"])
        else:
            canvas.itemconfig(ui["btn_rect"], fill=ACCENT_DIM, outline=ACCENT)
            canvas.itemconfig(ui["btn_text"], text="\u25b6  INICIAR", fill=ACCENT)
            canvas.itemconfig(ui["btn_glow"], image=ui["btn_glow_imgs"]["accent"])

    def refresh_pausa_button():
        if "pausa_rect" not in ui:
            return
        travado = not state.is_running()
        if travado:
            canvas.itemconfig(ui["pausa_rect"], outline="#3a2233")
            canvas.itemconfig(ui["pausa_text"], fill="#3a2233")
        elif state.is_paused():
            canvas.itemconfig(ui["pausa_rect"], fill=PINK, outline=PINK)
            canvas.itemconfig(ui["pausa_text"], text="\u25b6  RETOMAR", fill=BG)
        else:
            canvas.itemconfig(ui["pausa_rect"], fill=PANEL_BG, outline=PINK)
            canvas.itemconfig(ui["pausa_text"], text="\u23f8  PAUSAR", fill=PINK)

    def desenha_ring(fracao, texto_central):
        extent = -360 * max(0.0, min(1.0, fracao))
        canvas.itemconfig(ui["ring_arc"], extent=extent)
        canvas.itemconfig(ui["ring_center"], text=texto_central)

    def desenha_saldo_ring(fracao, texto_central, cor):
        extent = -360 * max(0.0, min(1.0, fracao))
        canvas.itemconfig(ui["saldo_ring_arc"], extent=extent, outline=cor)
        canvas.itemconfig(ui["saldo_ring_center"], text=texto_central, fill=FG)
        chave = "win" if cor == GREEN_WIN else ("loss" if cor == RED_LOSS else "muted")
        canvas.itemconfig(ui["saldo_glow"], image=ui["saldo_glow_imgs"][chave])

    def desenha_fundo():
        canvas.delete("bgline")
        w, h = LY["w"], LY["h"]
        gap = max(6, S(30))
        off = anim["offset"] % gap
        x = -h + off
        while x < w:
            canvas.create_line(x, h, x + h, 0, fill=GRID_LINE, width=1, tags="bgline")
            x += gap
        canvas.tag_lower("bgline")  # sempre atras de todo o resto da UI

    def desenha_vela(quadrant_size, vela_num):
        for item in ui.get("vela_items", []):
            canvas.delete(item)
        ui["vela_items"] = []
        gap = 4
        w = WIDTH - 36
        seg_w = (w - gap * (quadrant_size - 1)) / quadrant_size
        for i in range(quadrant_size):
            bx0 = 18 + i * (seg_w + gap)
            bx1 = bx0 + seg_w
            if i + 1 < vela_num:
                cor = "#1c2a1e"       # ja fechada
            elif i + 1 == vela_num:
                cor = PINK            # formando agora - acento rosa pra destacar do resto (verde=compra, vermelho=venda)
            else:
                cor = "#101a12"       # ainda vai vir
            ui["vela_items"].append(
                _rounded_rect(canvas, X(bx0), Y(ui["vela_y0"]), X(bx1), Y(ui["vela_y1"]),
                              r=S(4), fill=cor, outline="")
            )
        canvas.itemconfig(ui["vela_label"], text=f"vela {vela_num} de {quadrant_size}")
        canvas.tag_raise(ui["vela_label"])

    # ============================================================ BUILD ====
    def build_ui(w, h):
        """(Re)desenha TODA a interface para o tamanho real (w, h) da janela.
        Chamada uma vez no inicio e de novo, com debounce, toda vez que a
        janela e redimensionada (arrastando a borda ou maximizando)."""
        canvas.delete("all")
        ui.clear()

        w = max(1, int(w))
        h = max(1, int(h))
        scale = max(MIN_SCALE, min(MAX_SCALE, min(w / WIDTH, h / HEIGHT)))
        content_w, content_h = WIDTH * scale, HEIGHT * scale
        LY["scale"] = scale
        LY["ox"] = (w - content_w) / 2
        LY["oy"] = (h - content_h) / 2
        LY["w"] = w
        LY["h"] = h

        # degrade vertical de fundo (igual linear-gradient(180deg,...) do
        # site) cobrindo a janela INTEIRA (nao so a area de conteudo) - assim
        # se a janela ficar maior/menor que a proporcao do design, a sobra
        # ao redor tambem fica com o mesmo visual, sem "faixas" cruas.
        ui["bg_img"] = ImageTk.PhotoImage(_make_vertical_gradient(w, h, "#081409", "#050b06"))
        ui["bg_id"] = canvas.create_image(0, 0, anchor="nw", image=ui["bg_img"], tags="bggrad")

        # borda externa fina - da o acabamento "painel de vidro", sempre
        # colada na borda real da janela
        canvas.create_rectangle(1, 1, w - 1, h - 1, outline=PANEL_EDGE, width=1)

        # ============================================================ GLOWS =
        # TODOS os brilhos/glows do painel sao criados AQUI, logo no comeco,
        # ANTES de qualquer pill/botao/campo clicavel. Isso e proposital e
        # IMPORTANTE: no Canvas do Tkinter, quem recebe um clique quando
        # varios itens se sobrepoem e sempre o item criado POR ULTIMO (fica
        # em cima na pilha) - mesmo que a maior parte dele seja transparente
        # (um PNG com alpha baixo nas bordas ainda conta pela caixa
        # retangular INTEIRA da imagem pra fins de clique, nao so pelo
        # pixel visivel). O glow do botao INICIAR, por exemplo, e uma
        # imagem BEM maior que o botao em si (de proposito, pra dar um
        # brilho suave e largo) - se ele fosse criado DEPOIS das pills
        # (como estava antes), a caixa dele cobria a linha inteira das
        # pills e "roubava" o clique, mesmo aparecendo soh como uma sombra
        # verde fraca. Criando os glows primeiro, eles ficam sempre no
        # fundo da pilha e nunca atrapalham nenhum clique depois.
        ring_bcx, ring_bcy, ring_br = 56, 232, 34
        saldo_bcx, saldo_bcy, saldo_br = WIDTH - 56, 232, 34
        btn_y0, btn_y1 = 282, 326

        ui["dot_glow_img"] = ImageTk.PhotoImage(_make_glow(S(28), ACCENT, alpha=130))
        canvas.create_image(X(22), Y(28), image=ui["dot_glow_img"], tags="dotglow")

        ui["ring_glow_img"] = ImageTk.PhotoImage(_make_glow(S(ring_br * 2.3), ACCENT, alpha=90))
        canvas.create_image(X(ring_bcx), Y(ring_bcy), image=ui["ring_glow_img"], tags="ringglow")

        ui["saldo_glow_imgs"] = {
            "win": ImageTk.PhotoImage(_make_glow(S(saldo_br * 2.3), GREEN_WIN, alpha=90)),
            "loss": ImageTk.PhotoImage(_make_glow(S(saldo_br * 2.3), RED_LOSS, alpha=90)),
            "muted": ImageTk.PhotoImage(_make_glow(S(saldo_br * 2.3), MUTED, alpha=50)),
        }
        ui["saldo_glow"] = canvas.create_image(X(saldo_bcx), Y(saldo_bcy), image=ui["saldo_glow_imgs"]["muted"], tags="saldoglow")

        ui["btn_glow_imgs"] = {
            "accent": ImageTk.PhotoImage(_make_glow(S((WIDTH - 36) * 0.7), ACCENT, alpha=70, blur=round(S(26)))),
            "danger": ImageTk.PhotoImage(_make_glow(S((WIDTH - 36) * 0.7), DANGER, alpha=70, blur=round(S(26)))),
            "amber": ImageTk.PhotoImage(_make_glow(S((WIDTH - 36) * 0.7), AMBER, alpha=70, blur=round(S(26)))),
        }
        ui["btn_glow"] = canvas.create_image(X(WIDTH / 2), Y((btn_y0 + btn_y1) / 2), image=ui["btn_glow_imgs"]["accent"], tags="btnglow")

        # botaozinho que abre/fecha o painel GR, colado na borda direita
        _rounded_rect(canvas, w - S(20), h / 2 - S(18), w - S(2), h / 2 + S(18), r=S(6),
                      fill=PANEL_BG, outline=ACCENT, width=SW(1), tags=("btn_gr_toggle",))
        ui["gr_toggle_arrow"] = canvas.create_text(w - S(11), h / 2, text="\u203a", fill=ACCENT,
                                                     font=F(("JetBrains Mono", 10, "bold")), tags=("btn_gr_toggle",))

        # avatar do personagem Mathewin, cantinho superior direito do
        # cabecalho - redimensionado com PIL pra acompanhar a escala atual
        if _avatar_src is not None:
            try:
                diam = max(8, round(S(48)))
                resized = _avatar_src.resize((diam, diam), Image.LANCZOS)
                ui["avatar_img"] = ImageTk.PhotoImage(resized)
                canvas.create_image(X(WIDTH - 44), Y(30), image=ui["avatar_img"])
            except Exception:
                pass

        # ================================================== cabecalho =====
        canvas.create_rectangle(X(0), Y(0), X(WIDTH), Y(96), fill="", outline="")
        ui["accent_dot"] = canvas.create_oval(X(18), Y(24), X(26), Y(32), fill=MUTED, outline="")
        canvas.create_text(X(36), Y(28), text="MATHEWIN BOT", fill=FG, anchor="w", font=F(FONT_TITLE))
        canvas.create_text(X(36), Y(46), text="SISTEMA DE DECISAO AUTOMATIZADA", fill=MUTED, anchor="w", font=F(FONT_SUB))
        # linha do cabecalho em degrade verde -> rosa -> azul, na paleta do Mathewin
        canvas.create_line(X(18), Y(62), X(WIDTH * 0.5), Y(62), fill=ACCENT, width=SW(1))
        canvas.create_line(X(WIDTH * 0.5), Y(62), X(WIDTH * 0.78), Y(62), fill=PINK, width=SW(1))
        canvas.create_line(X(WIDTH * 0.78), Y(62), X(WIDTH - 18), Y(62), fill=BLUE, width=SW(1))

        ui["clock_text"] = canvas.create_text(X(WIDTH - 74), Y(30), text="", fill=FG, anchor="e",
                                               font=F(("JetBrains Mono", 13, "bold")))

        # ================================================== pills de estrategia
        pill_defs = [("quadrantes", "QUADRANTES"), ("surf", "SURF"), ("quad3", "QUAD3")]
        ui["pill_items"] = {}

        pills_y0, pills_y1 = 108, 136
        pill_gap = 8
        pill_w = (WIDTH - 36 - pill_gap * 2) / 3
        for i, (nome, rotulo) in enumerate(pill_defs):
            bx0 = 18 + i * (pill_w + pill_gap)
            bx1 = bx0 + pill_w
            tag = f"pill_{nome}"
            rect = _rounded_rect(canvas, X(bx0), Y(pills_y0), X(bx1), Y(pills_y1), r=S(8),
                                  fill=PILL_BG, outline=PANEL_EDGE, width=SW(1), tags=(tag, "pill"))
            # a caixa do "surf" reserva espaco a direita pro toggle 1/2min, entao
            # o texto sai da esquerda ali, e continua centralizado nas outras
            rotulo_bx = bx0 + 24 if nome == "surf" else (bx0 + bx1) / 2
            rotulo_anchor = "w" if nome == "surf" else "center"
            text = canvas.create_text(X(rotulo_bx), Y((pills_y0 + pills_y1) / 2), text=rotulo,
                                       fill=MUTED, font=F(FONT_PILL), anchor=rotulo_anchor, tags=(tag, "pill"))
            ui["pill_items"][nome] = (rect, text)

            if nome == "surf":
                # contador pequeno "< M2 >" dentro da propria caixa do surf, pra
                # escolher a expiracao/timeframe (1 a 15 minutos - ver bot.py).
                step_w, step_h = 13, 16
                label_w = 26
                bgap = 2
                tgl_bx1 = bx1 - 6
                tgl_bx0 = tgl_bx1 - (step_w * 2 + label_w + bgap * 2)
                tgl_by0 = (pills_y0 + pills_y1) / 2 - step_h / 2
                tgl_by1 = tgl_by0 + step_h

                menos_bx0, menos_bx1 = tgl_bx0, tgl_bx0 + step_w
                label_bx0, label_bx1 = menos_bx1 + bgap, menos_bx1 + bgap + label_w
                mais_bx0, mais_bx1 = label_bx1 + bgap, label_bx1 + bgap + step_w

                _rounded_rect(canvas, X(menos_bx0), Y(tgl_by0), X(menos_bx1), Y(tgl_by1), r=S(4),
                              fill=PANEL_BG, outline=PANEL_EDGE, width=SW(1), tags=("surf_exp_menos",))
                canvas.create_text(X((menos_bx0 + menos_bx1) / 2), Y((tgl_by0 + tgl_by1) / 2), text="\u2039",
                                    fill=MUTED, font=F(("JetBrains Mono", 9, "bold")), tags=("surf_exp_menos",))

                label_rect = _rounded_rect(canvas, X(label_bx0), Y(tgl_by0), X(label_bx1), Y(tgl_by1), r=S(4),
                                            fill=PANEL_BG, outline=PANEL_EDGE, width=SW(1))
                label_text = canvas.create_text(X((label_bx0 + label_bx1) / 2), Y((tgl_by0 + tgl_by1) / 2),
                                                 text=f"M{surf_exp_var['value']}",
                                                 fill=MUTED, font=F(("JetBrains Mono", 8, "bold")))
                ui["surf_exp_label"] = (label_rect, label_text)
                ui["surf_exp_label_text"] = label_text

                _rounded_rect(canvas, X(mais_bx0), Y(tgl_by0), X(mais_bx1), Y(tgl_by1), r=S(4),
                              fill=PANEL_BG, outline=PANEL_EDGE, width=SW(1), tags=("surf_exp_mais",))
                canvas.create_text(X((mais_bx0 + mais_bx1) / 2), Y((tgl_by0 + tgl_by1) / 2), text="\u203a",
                                    fill=MUTED, font=F(("JetBrains Mono", 9, "bold")), tags=("surf_exp_mais",))

        # ================================================== barra da vela =
        ui["vela_y0"], ui["vela_y1"] = 148, 168
        ui["vela_items"] = []
        ui["vela_label"] = canvas.create_text(X(18), Y(168 + 10), text="", fill=MUTED, anchor="w", font=F(FONT_MONO_SM))
        vela_visible["on"] = False

        # ================================================== placar manual ==
        # O saldo agora e SEMPRE automatico (lido por OCR pelo bot.py - ver
        # balance.py) - nao existe mais campo de saldo digitado aqui. O que
        # fica e o placar MANUAL (ganhas/perdidas contadas por voce, so pra
        # comparar com o placar automatico do bot depois), atualizado
        # clicando direto em "+" (ganhou) ou "-" (perdeu).
        manual_y = 180
        canvas.create_text(X(18), Y(manual_y), text="PLACAR MANUAL", fill=MUTED, anchor="w", font=F(FONT_MONO_SM))

        plus_bx0, plus_bx1 = 200, 236
        _rounded_rect(canvas, X(plus_bx0), Y(manual_y - 10), X(plus_bx1), Y(manual_y + 10), r=S(6),
                      fill=GREEN_WIN_BG, outline=GREEN_WIN, width=SW(1), tags=("btn_manual_win",))
        canvas.create_text(X((plus_bx0 + plus_bx1) / 2), Y(manual_y), text="+", fill=GREEN_WIN,
                            font=F(FONT_BTN_SM), tags=("btn_manual_win",))

        minus_bx0, minus_bx1 = 246, 282
        _rounded_rect(canvas, X(minus_bx0), Y(manual_y - 10), X(minus_bx1), Y(manual_y + 10), r=S(6),
                      fill=RED_LOSS_BG, outline=RED_LOSS, width=SW(1), tags=("btn_manual_loss",))
        canvas.create_text(X((minus_bx0 + minus_bx1) / 2), Y(manual_y), text="\u2212", fill=RED_LOSS,
                            font=F(FONT_BTN_SM), tags=("btn_manual_loss",))

        ui["manual_status"] = canvas.create_text(X(WIDTH - 18), Y(manual_y), text="", fill=MUTED, anchor="e", font=F(FONT_MONO_SM))

        # ================================================== anel de contagem
        # (ring_bcx/bcy/br ja foram definidos la em cima, junto do glow)
        canvas.create_oval(X(ring_bcx - ring_br - 6), Y(ring_bcy - ring_br - 6),
                            X(ring_bcx + ring_br + 6), Y(ring_bcy + ring_br + 6),
                            outline=BLUE, width=SW(1))
        canvas.create_oval(X(ring_bcx - ring_br), Y(ring_bcy - ring_br), X(ring_bcx + ring_br), Y(ring_bcy + ring_br),
                            outline="#182018", width=SW(5))
        ui["ring_arc"] = canvas.create_arc(X(ring_bcx - ring_br), Y(ring_bcy - ring_br),
                                            X(ring_bcx + ring_br), Y(ring_bcy + ring_br),
                                            start=90, extent=0, style="arc", outline=ACCENT, width=SW(5))
        ui["ring_center"] = canvas.create_text(X(ring_bcx), Y(ring_bcy), text="--", fill=FG, font=F(("JetBrains Mono", 10, "bold")))

        ui["ring_titulo"] = canvas.create_text(X(104), Y(ring_bcy - 22), text="", fill=MUTED, anchor="w", font=F(FONT_LABEL), width=S(190))
        ui["ring_valor"] = canvas.create_text(X(104), Y(ring_bcy - 4), text="", fill=FG, anchor="w", font=F(("Chakra Petch", 11, "bold")), width=S(190))
        ui["ring_extra"] = canvas.create_text(X(104), Y(ring_bcy + 14), text="", fill=ACCENT, anchor="w", font=F(FONT_LABEL), width=S(190))

        # ================================================== anel de saldo ==
        # (saldo_bcx/bcy/br e os glow_imgs ja foram definidos la em cima)
        canvas.create_oval(X(saldo_bcx - saldo_br - 6), Y(saldo_bcy - saldo_br - 6),
                            X(saldo_bcx + saldo_br + 6), Y(saldo_bcy + saldo_br + 6),
                            outline=BLUE, width=SW(1))
        canvas.create_oval(X(saldo_bcx - saldo_br), Y(saldo_bcy - saldo_br),
                            X(saldo_bcx + saldo_br), Y(saldo_bcy + saldo_br),
                            outline="#182018", width=SW(5))
        ui["saldo_ring_arc"] = canvas.create_arc(X(saldo_bcx - saldo_br), Y(saldo_bcy - saldo_br),
                                                  X(saldo_bcx + saldo_br), Y(saldo_bcy + saldo_br),
                                                  start=90, extent=0, style="arc", outline=MUTED, width=SW(5))
        ui["saldo_ring_center"] = canvas.create_text(X(saldo_bcx), Y(saldo_bcy), text="--",
                                                      fill=FG, font=F(("JetBrains Mono", 10, "bold")))
        canvas.create_text(X(saldo_bcx - saldo_br - 12), Y(saldo_bcy), text="SALDO",
                            fill=MUTED, anchor="e", font=F(("Chakra Petch", 11, "bold")))
        ui["saldo_ring_pct"] = canvas.create_text(X(saldo_bcx), Y(saldo_bcy - saldo_br - 16),
                                                   text="", fill=MUTED, font=F(FONT_MONO_SM))
        # campo separado, travado: mostra sempre o saldo INICIAL da rodada
        # atual, vindo pronto do SharedState (state.saldo_inicial - ver
        # bot.py/shared_state.py: e travado no valor da primeira leitura
        # apos cada Iniciar e reseta sozinho a cada novo Iniciar). Fica
        # separado do numero de dentro do anel (saldo ATUAL, sempre
        # atualizando) de proposito, pra dar pra comparar os dois.
        ui["saldo_ring_inicial"] = canvas.create_text(X(saldo_bcx), Y(saldo_bcy + saldo_br + 16),
                                                        text="", fill=MUTED, font=F(FONT_MONO_SM))

        # ================================================== botao iniciar/parar
        # (btn_y0/y1 e o btn_glow ja foram definidos la em cima, junto dos glows)
        ui["btn_rect"] = _rounded_rect(canvas, X(18), Y(btn_y0), X(WIDTH - 18), Y(btn_y1), r=S(10),
                                        fill=ACCENT_DIM, outline=ACCENT, width=SW(1), tags=("btn_toggle",))
        ui["btn_text"] = canvas.create_text(X(WIDTH / 2), Y((btn_y0 + btn_y1) / 2), text="", fill=FG,
                                             font=F(FONT_BTN), tags=("btn_toggle",))

        # ============================================ botoes secundarios ===
        sec_y0, sec_y1 = 336, 368
        sec_gap = 8
        sec_w = (WIDTH - 36 - sec_gap * 2) / 3

        bx0c, bx1c = 18, 18 + sec_w
        ui["calib_rect"] = _rounded_rect(canvas, X(bx0c), Y(sec_y0), X(bx1c), Y(sec_y1), r=S(8),
                                          fill=PANEL_BG, outline=AMBER, width=SW(1), tags=("btn_calibrar",))
        ui["calib_text"] = canvas.create_text(X((bx0c + bx1c) / 2), Y((sec_y0 + sec_y1) / 2), text="\U0001f3af  CALIBRAR",
                                               fill=AMBER, font=F(FONT_BTN_SM), tags=("btn_calibrar",))

        # PAUSAR: robo continua ligado (calculando/mostrando sinal da proxima
        # vela), mas para de CLICAR pra abrir operacao. Diferente de PARAR, que
        # desliga tudo.
        bx0p, bx1p = bx1c + sec_gap, bx1c + sec_gap + sec_w
        ui["pausa_rect"] = _rounded_rect(canvas, X(bx0p), Y(sec_y0), X(bx1p), Y(sec_y1), r=S(8),
                                          fill=PANEL_BG, outline=PINK, width=SW(1), tags=("btn_pausar",))
        ui["pausa_text"] = canvas.create_text(X((bx0p + bx1p) / 2), Y((sec_y0 + sec_y1) / 2), text="\u23f8  PAUSAR",
                                               fill=PINK, font=F(FONT_BTN_SM), tags=("btn_pausar",))

        bx0d, bx1d = bx1p + sec_gap, WIDTH - 18
        _rounded_rect(canvas, X(bx0d), Y(sec_y0), X(bx1d), Y(sec_y1), r=S(8),
                      fill=PANEL_BG, outline=DANGER, width=SW(1), tags=("btn_desligar",))
        canvas.create_text(X((bx0d + bx1d) / 2), Y((sec_y0 + sec_y1) / 2), text="\u23fb  SAIR",
                            fill=DANGER, font=F(FONT_BTN_SM), tags=("btn_desligar",))

        # ================================================== painel de status
        panel_y0, panel_y1 = 378, HEIGHT - 14
        _rounded_rect(canvas, X(18), Y(panel_y0), X(WIDTH - 18), Y(panel_y1), r=S(10), fill=PANEL_BG, outline=PANEL_EDGE, width=SW(1))

        ui["info_text"] = canvas.create_text(X(32), Y(panel_y0 + 8), text="", fill=FG, anchor="nw",
                                              font=F(FONT_MONO_SM), width=S(WIDTH - 64), justify="left")

        placar_gap = 10
        placar_w = (WIDTH - 36 - placar_gap) / 2

        bot_label_y = panel_y0 + 34
        canvas.create_text(X(18), Y(bot_label_y), text="AUTOMATICO (BOT)", fill=AMBER, anchor="w", font=F(FONT_MONO_SM))

        placar_y0 = bot_label_y + 12
        placar_y1 = placar_y0 + 46

        bx0w, bx1w = 18, 18 + placar_w
        ui["win_card"] = _rounded_rect(canvas, X(bx0w), Y(placar_y0), X(bx1w), Y(placar_y1), r=S(8),
                                        fill=AMBER_DIM, outline=AMBER, width=SW(1))
        ui["win_num"] = canvas.create_text(X((bx0w + bx1w) / 2), Y(placar_y0 + (placar_y1 - placar_y0) * 0.42),
                                            text="0", fill=AMBER, font=F(("JetBrains Mono", 17, "bold")))
        canvas.create_text(X((bx0w + bx1w) / 2), Y(placar_y1 - 9), text="GANHAS", fill=AMBER, font=F(FONT_MONO_SM))

        bx0l, bx1l = bx1w + placar_gap, WIDTH - 18
        ui["loss_card"] = _rounded_rect(canvas, X(bx0l), Y(placar_y0), X(bx1l), Y(placar_y1), r=S(8),
                                         fill=AMBER_DIM, outline=AMBER, width=SW(1))
        ui["loss_num"] = canvas.create_text(X((bx0l + bx1l) / 2), Y(placar_y0 + (placar_y1 - placar_y0) * 0.42),
                                             text="0", fill=AMBER, font=F(("JetBrains Mono", 17, "bold")))
        canvas.create_text(X((bx0l + bx1l) / 2), Y(placar_y1 - 9), text="PERDIDAS", fill=AMBER, font=F(FONT_MONO_SM))

        ui["taxa_text"] = canvas.create_text(X(32), Y(placar_y1 + 6), text="", fill=FG, anchor="nw", font=F(FONT_MONO_SM))
        ui["seq_text"] = canvas.create_text(X(WIDTH - 32), Y(placar_y1 + 6), text="", fill=MUTED, anchor="ne", font=F(FONT_MONO_SM))

        manual_label_y = placar_y1 + 26
        canvas.create_text(X(18), Y(manual_label_y), text="MANUAL (voce)", fill=FG, anchor="w", font=F(FONT_MONO_SM))

        placar_m_y0 = manual_label_y + 12
        placar_m_y1 = placar_m_y0 + 46

        ui["win_m_card"] = _rounded_rect(canvas, X(bx0w), Y(placar_m_y0), X(bx1w), Y(placar_m_y1), r=S(8),
                                          fill=GREEN_WIN_BG, outline=GREEN_WIN, width=SW(1))
        ui["win_m_num"] = canvas.create_text(X((bx0w + bx1w) / 2), Y(placar_m_y0 + (placar_m_y1 - placar_m_y0) * 0.42),
                                              text="0", fill=GREEN_WIN, font=F(("JetBrains Mono", 17, "bold")))
        canvas.create_text(X((bx0w + bx1w) / 2), Y(placar_m_y1 - 9), text="GANHAS", fill=GREEN_WIN, font=F(FONT_MONO_SM))

        ui["loss_m_card"] = _rounded_rect(canvas, X(bx0l), Y(placar_m_y0), X(bx1l), Y(placar_m_y1), r=S(8),
                                           fill=RED_LOSS_BG, outline=RED_LOSS, width=SW(1))
        ui["loss_m_num"] = canvas.create_text(X((bx0l + bx1l) / 2), Y(placar_m_y0 + (placar_m_y1 - placar_m_y0) * 0.42),
                                               text="0", fill=RED_LOSS, font=F(("JetBrains Mono", 17, "bold")))
        canvas.create_text(X((bx0l + bx1l) / 2), Y(placar_m_y1 - 9), text="PERDIDAS", fill=RED_LOSS, font=F(FONT_MONO_SM))

        ui["taxa_m_text"] = canvas.create_text(X(32), Y(placar_m_y1 + 6), text="", fill=FG, anchor="nw", font=F(FONT_MONO_SM))
        ui["seq_m_text"] = canvas.create_text(X(WIDTH - 32), Y(placar_m_y1 + 6), text="", fill=MUTED, anchor="ne", font=F(FONT_MONO_SM))

        margem_y = placar_m_y1 + 26
        ui["margem_text"] = canvas.create_text(X(WIDTH / 2), Y(margem_y), text="", fill=MUTED, font=F(FONT_MONO_SM), justify="center")

        # reaplica o estado atual (ligado/parado, pausado, estrategia
        # selecionada etc.) na UI que acabou de ser recriada
        refresh_button()
        refresh_pills()
        refresh_pausa_button()

    # ------------------------------------------- redimensionar (debounce) --
    resize_job = {"id": None}
    last_built = {"w": None, "h": None}

    def _agenda_rebuild(w, h):
        if resize_job["id"] is not None:
            root.after_cancel(resize_job["id"])
        resize_job["id"] = root.after(120, lambda: _executa_rebuild(w, h))

    def _executa_rebuild(w, h):
        resize_job["id"] = None
        # o proprio build_ui() muda o tamanho de itens do canvas, o que as
        # vezes dispara outro <Configure> "fantasma" com o mesmo tamanho -
        # ignora se nada mudou de verdade, pra nao ficar redesenhando a toa
        if last_built["w"] == w and last_built["h"] == h:
            return
        last_built["w"], last_built["h"] = w, h
        build_ui(w, h)

    def _on_configure(event):
        if event.widget is not root:
            return
        w, h = root.winfo_width(), root.winfo_height()
        if w < 2 or h < 2:
            return
        _agenda_rebuild(w, h)
        # reposiciona o painel GR JUNTO, sem debounce (precisa ser instantaneo
        # pra "grudar" no principal enquanto arrasta, nao só depois de soltar)
        _gr_segue_root()

    root.bind("<Configure>", _on_configure)

    def _on_root_unmap(event):
        # dispara quando o painel principal e MINIMIZADO. Como o painel GR e
        # uma janela Toplevel separada (overrideredirect, sem botao proprio
        # de minimizar), ele NAO minimiza sozinho quando o principal minimiza
        # - sem isso, ele ficava "orfao" flutuando na tela, sem como fechar.
        if event.widget is not root:
            return
        gr_win.withdraw()

    def _on_root_map(event):
        # dispara quando o painel principal e RESTAURADO (desminimizado).
        # Traz o GR de volta, ja na posicao certa (do lado, se estava aberto;
        # atras, se estava fechado).
        if event.widget is not root:
            return
        gr_win.deiconify()
        _gr_segue_root()
        if not gr_hover["expanded"]:
            gr_win.lower(root)

    root.bind("<Unmap>", _on_root_unmap)
    root.bind("<Map>", _on_root_map)

    def _log_clique(event):
        # DIAGNOSTICO: mostra TODOS os itens que realmente cobrem o ponto
        # clicado, do mais de cima (o que recebe o clique de verdade) pro
        # mais de baixo - find_overlapping reflete a pilha real de
        # cliques; find_closest (usado numa versao anterior) so pega o
        # item mais PROXIMO geometricamente, o que pode enganar.
        itens = canvas.find_overlapping(event.x - 1, event.y - 1, event.x + 1, event.y + 1)
        pilha = [(i, canvas.gettags(i)) for i in reversed(itens)]
        print(f"[clique] x={event.x} y={event.y}  janela={root.winfo_width()}x{root.winfo_height()}  pilha_no_ponto={pilha}")

    canvas.bind("<Button-1>", _log_clique, add="+")

    # construcao inicial, no tamanho de referencia (o "tamanho que ja abre")
    root.update_idletasks()
    build_ui(root.winfo_width(), root.winfo_height())
    last_built["w"], last_built["h"] = root.winfo_width(), root.winfo_height()

    # Registra o callback que deixa o bot.py avisar o overlay NA HORA (sem
    # esperar o proximo refresh periodico) sempre que um clique de verdade
    # acontece - ver SharedState.notify_click() e o comentario em
    # bot.py:execute(). root.after(0, ...) agenda a chamada pra rodar na
    # thread principal do Tkinter (a unica que pode mexer na UI com
    # seguranca), mesmo sendo chamado a partir da thread do bot.
    def _reafirma_topmost_e_ordem():
        root.attributes("-topmost", True)
        # o passo acima pode bagunçar a ordem de empilhamento do painel GR
        # (no Windows, reafirmar -topmost de UMA janela pode jogar ela pra
        # frente de OUTRAS janelas tambem -topmost, mesmo que a ordem certa
        # ja tivesse sido definida antes) - por isso, sempre que isso
        # acontece, reforçamos tambem se o GR deve estar na frente (aberto)
        # ou atras (fechado), pra nunca deixar ele "vazando" no lugar errado.
        if gr_hover["expanded"]:
            gr_win.lift(root)
        else:
            gr_win.lower(root)

    state.set_on_click_callback(lambda: root.after(0, _reafirma_topmost_e_ordem))

    def _atualiza_intencao():
        """
        Atualiza SO o texto/cor da intencao (ui["ring_extra"], o
        "analisando: COMPRA/VENDA" embaixo do cronometro) - nao redesenha
        mais nada (fundo animado, aneis, placar etc). Chamado de dois
        jeitos:
          1) todo tick do refresh() normal (a cada 150ms), como sempre foi;
          2) NA HORA, via state.set_on_signal_callback logo abaixo - toda
             vez que a intencao (live_signal) ou a decisao oficial
             (last_decision) mudam de verdade (SharedState so dispara esse
             callback quando o valor novo e diferente do anterior).
        Isso fecha o atraso entre "o bot decidiu" e "o painel mostra" pra
        praticamente zero, sem precisar rodar o resto do painel (fundo
        animado, placar, aneis pesados) mais rapido - o resto continua nos
        mesmos 150ms de sempre, sem pesar mais nada.

        Mesma logica de sempre (ver bloco correspondente dentro de refresh
        mais abaixo) - so extraida pra funcao separada, reaproveitavel.
        """
        if "ring_extra" not in ui:
            return
        s = state.snapshot()
        if not s["running"] or not s["next_entry_time"]:
            # nesses casos quem decide o texto e o refresh() normal (mostra
            # "PARADO" ou o texto de status) - essa funcao so cuida do
            # texto de intencao durante a espera normal de entrada.
            return
        sinal = s.get("live_signal")
        if sinal == "buy":
            canvas.itemconfig(ui["ring_extra"], text="analisando: COMPRA", fill=ACCENT)
        elif sinal == "sell":
            canvas.itemconfig(ui["ring_extra"], text="analisando: VENDA", fill=DANGER)
        else:
            canvas.itemconfig(ui["ring_extra"], text="analisando...", fill=MUTED)

    # Mesma ideia do callback de clique acima, mas pra intencao/decisao -
    # dispara so quando o VALOR muda de verdade (ver SharedState), entao
    # nao fica chamando root.after a toa numa leitura repetida.
    state.set_on_signal_callback(lambda: root.after(0, _atualiza_intencao))

    # ============================== PAINEL GR (aba escondida ATRAS do painel
    # principal, controlada por um BOTAO) ===================================
    # Os dois paineis (principal e GR) sao tratados como UM SO bloco: a
    # posicao do GR NUNCA e um numero fixo guardado - ela e sempre CALCULADA
    # em cima da posicao/tamanho ATUAL de verdade do painel principal
    # (root.winfo_x()/y()/width()/height()), a cada instante - inclusive a
    # LARGURA, que agora pode mudar (janela redimensionavel). Isso garante
    # que o GR fica sempre grudado atras do principal e do tamanho certo,
    # nao importa se o principal foi redimensionado ou movido.
    #
    # O CONTEUDO abaixo ja usa os valores DECODIFICADOS da sua planilha
    # (Investimento, % Utilizada, Pay Out, % de cada Gale) como padrao pra
    # cada nivel (2, 3 ou 4), TUDO editavel pelo usuario aqui no painel.
    # Cada nivel guarda sua PROPRIA configuracao separada - trocar o
    # Nivel 4 nao mexe no que foi configurado no Nivel 2.
    #
    # IMPORTANTE: por enquanto isso e so PAINEL - os valores ficam
    # guardados aqui no overlay (gr_config), ainda NAO controlam as
    # entradas reais do bot. Quando o bot.py tiver o motor de
    # soros/gale rodando de verdade (lendo esses mesmos numeros via
    # SharedState), e so trocar a fonte dos valores aqui por
    # state.get(...), sem mudar a tela.
    # Valores padrao EXATOS das suas 3 abas do GR__1_.xlsx (aba 22=nivel2,
    # 23=nivel3, 24=nivel4) - Investimento, %Util, Payout e %Gale de cada
    # nivel de gale, extraidos direto das celulas B1/B2/B3/B12../B15.. do
    # arquivo. Continuam 100% editaveis aqui no painel.
    # Puxa os valores iniciais do MESMO lugar que o bot.py usa (state) - em
    # vez de manter uma segunda copia fixa aqui, que ja causou bug antes
    # (painel mostrando um valor "correto" enquanto o bot operava com outro,
    # desatualizado, guardado so no shared_state.py). Editar um campo aqui
    # continua salvando de volta no state normalmente (ver _gr_confirma_campo).
    gr_config = state.get_gr_config_all()
    gr_nivel_var = {"value": 2}
    gr_ativo_var = {"value": False}
    gr_ciclo_var = {"value": "simples"}

    SLIDE_STEP_MS = 12  # intervalo entre cada quadro da animacao de deslizar do GR
    GR_PEEK = 14  # largura (px) da bordinha do GR que ja fica espiando pra fora, colada
                  # no lado direito do painel principal, mesmo escondido - assim, ao
                  # clicar, ele so precisa deslizar o restante (nao a largura toda),
                  # aparecendo bem mais rapido, sem "atravessar" o painel principal.

    def _gr_largura_atual():
        """O painel GR agora e SEMPRE do mesmo tamanho do painel principal -
        largura E altura - tanto aberto do lado quanto escondido atras dele.
        Nunca um numero fixo: le a largura ATUAL do principal (que pode ter
        sido redimensionado pelo usuario) toda vez que e chamada."""
        return max(1, root.winfo_width())

    def _gr_altura_atual():
        """Mesma altura do painel principal, sempre - sem folga extra (ver
        docstring de _gr_largura_atual acima)."""
        return max(1, root.winfo_height())

    def _gr_x_atras():
        """Posicao X de descanso (escondido): ONDE QUER QUE O PRINCIPAL ESTEJA
        e do tamanho que ele estiver AGORA, ja deslocado pra deixar uma
        bordinha (GR_PEEK) espiando pra fora, colada no lado direito dele -
        nunca um valor fixo (ver comentario acima do bloco)."""
        return root.winfo_x() + root.winfo_width() + GR_PEEK - _gr_largura_atual()

    def _gr_x_ao_lado():
        """Posicao X totalmente aberto, encostado saindo pela lateral DIREITA
        do painel principal (crescendo pra direita na tela) - tambem
        calculada em cima da posicao/tamanho ATUAL dele."""
        return root.winfo_x() + root.winfo_width()

    def _gr_y():
        """Mesmo Y do painel principal, lido ao vivo."""
        return root.winfo_y()

    gr_win = tk.Toplevel(root)
    gr_win.overrideredirect(True)  # sem barra de titulo - parece parte do mesmo conjunto visual
    gr_win.attributes("-topmost", True)
    gr_win.attributes("-alpha", 0.97)
    gr_win.configure(bg=BG)
    gr_win.geometry(f"{_gr_largura_atual()}x{_gr_altura_atual()}+{_gr_x_atras()}+{_gr_y()}")

    gr_canvas = tk.Canvas(gr_win, bg=BG, highlightthickness=0)
    gr_canvas.pack(fill="both", expand=True)
    gr_ui = {"entries": {}}

    def _gr_selecionar_nivel(nivel):
        def _click(event=None):
            gr_nivel_var["value"] = nivel
            state.set_gr_nivel(nivel)
            _gr_desenha_conteudo()
        return _click

    def _gr_toggle_ativo(event=None):
        gr_ativo_var["value"] = not gr_ativo_var["value"]
        state.set_gr_ativo(gr_ativo_var["value"])
        _gr_desenha_conteudo()

    def _gr_toggle_ciclo(event=None):
        gr_ciclo_var["value"] = "composto" if gr_ciclo_var["value"] == "simples" else "simples"
        state.set_gr_ciclo(gr_ciclo_var["value"])
        _gr_desenha_conteudo()

    def _gr_toggle_fonte_investimento(event=None):
        """Alterna a origem do Investimento base entre 'manual' (valor
        digitado na celula, fica fixo) e 'saldo' (puxa o saldo real da conta
        e trava esse valor a cada ciclo novo - ver reiniciar_ciclo em
        gerenciamento.py)."""
        cfg = gr_config[gr_nivel_var["value"]]
        cfg["investimento_fonte"] = "saldo" if cfg.get("investimento_fonte", "manual") == "manual" else "manual"
        state.set_gr_config_campo(gr_nivel_var["value"], "investimento_fonte", cfg["investimento_fonte"])
        _gr_desenha_conteudo()

    def _gr_campo_editavel(y, rotulo, texto_inicial, chave, largura=8):
        """Desenha um rotulo + campinho editavel (tk.Entry de verdade,
        embutido no canvas) numa linha do painel GR. 'chave' identifica o
        campo pra confirmar o valor depois (ver _gr_confirma_campo)."""
        gr_canvas.create_text(20, y, anchor="w", text=rotulo, font=FONT_LABEL, fill=MUTED)
        entry = tk.Entry(gr_canvas, width=largura, font=FONT_MONO_SM, bg=PANEL_BG, fg=FG,
                          insertbackground=FG, relief="flat", justify="right",
                          highlightthickness=1, highlightbackground=PANEL_EDGE, highlightcolor=ACCENT)
        entry.insert(0, texto_inicial)
        entry.bind("<Return>", lambda e: _gr_confirma_campo(chave))
        entry.bind("<FocusOut>", lambda e: _gr_confirma_campo(chave))
        gr_canvas.create_window(_gr_largura_atual() - 24, y, window=entry, anchor="e")
        gr_ui["entries"][chave] = entry

    def _gr_confirma_campo(chave):
        """Le o texto digitado no campo 'chave', valida/converte pro tipo
        certo, salva em gr_config[nivel atual] e redesenha o painel pra
        mostrar o valor ja formatado (e o equilibrio recalculado)."""
        entry = gr_ui["entries"].get(chave)
        if entry is None:
            return
        texto = entry.get().strip().replace(",", ".").replace("%", "").replace("R$", "")
        if not texto:
            return
        try:
            valor = float(texto)
        except ValueError:
            _gr_desenha_conteudo()  # descarta e volta a mostrar o valor valido anterior
            return
        cfg = gr_config[gr_nivel_var["value"]]
        if chave == "nivel":
            try:
                novo_nivel = int(round(valor))
            except (ValueError, OverflowError):
                _gr_desenha_conteudo()
                return
            novo_nivel = min(4, max(2, novo_nivel))
            gr_nivel_var["value"] = novo_nivel
            state.set_gr_nivel(novo_nivel)
            _gr_desenha_conteudo()
            return
        if chave == "investimento":
            cfg["investimento"] = max(0.01, valor)
            valor_final = cfg["investimento"]
        elif chave == "pct_util":
            cfg["pct_util"] = max(0.01, min(0.99, valor / 100 if valor > 1 else valor))
            valor_final = cfg["pct_util"]
        elif chave == "payout":
            cfg["payout"] = max(0.01, min(0.99, valor / 100 if valor > 1 else valor))
            valor_final = cfg["payout"]
        elif chave.startswith("gale_"):
            idx = int(chave.split("_")[1])
            cfg["gale"][idx] = max(0.01, min(3.0, valor / 100 if valor > 1 else valor))
            valor_final = cfg["gale"][idx]
        else:
            valor_final = None
        if valor_final is not None:
            state.set_gr_config_campo(gr_nivel_var["value"], chave, valor_final)
        _gr_desenha_conteudo()

    def _gr_desenha_conteudo():
        """Redesenha todo o conteudo do GR (fundo, borda, seletor de nivel,
        campos editaveis) no tamanho ATUAL da janela GR - chamado no
        inicio, ao trocar de nivel, ao confirmar um campo, e de novo
        sempre que a altura muda junto com o painel principal (mesmo
        esquema de redesenho total usado no painel principal, ver
        build_ui())."""
        for entry in gr_ui["entries"].values():
            try:
                entry.destroy()
            except Exception:
                pass
        gr_ui["entries"] = {}
        gr_canvas.delete("all")

        w, h = gr_win.winfo_width() or _gr_largura_atual(), gr_win.winfo_height() or _gr_altura_atual()
        gr_bg_img = ImageTk.PhotoImage(_make_vertical_gradient(w, h, "#081409", "#050b06"))
        gr_ui["bg_img"] = gr_bg_img  # mantem referencia viva (Tkinter descarta imagem sem isso)
        gr_canvas.create_image(0, 0, anchor="nw", image=gr_bg_img)
        _rounded_rect(gr_canvas, 10, 10, w - 10, h - 10, r=14,
                      outline=PANEL_EDGE, fill=PANEL_BG, width=1)
        gr_canvas.create_text(w // 2, 32, text="GERENCIAMENTO DE RISCO", font=FONT_TITLE, fill=ACCENT)

        # ================= PLANILHA DE SIMULACAO (grade estilo Excel) ==========
        # Painel reduzido a UMA UNICA planilha: celulas com borda de verdade,
        # organizadas em linhas e colunas. As celulas de PARAMETRO (fundo mais
        # claro) sao editaveis igual uma celula de Excel - clica, digita, aperta
        # Enter ou clica fora que confirma. As demais sao so RESULTADO calculado
        # (nao editaveis), igual formula de planilha - mudam sozinhas quando um
        # parametro muda.
        cfg = gr_config[gr_nivel_var["value"]]
        nivel_atual = gr_nivel_var["value"]
        ciclo = gr_ciclo_var["value"]  # nao tem mais botao pra isso no painel;
                                        # fica sempre "simples" internamente - nao
                                        # muda nenhum numero mostrado aqui (so
                                        # afeta o bot DEPOIS que o ciclo fecha).

        # 6 colunas (A a F), IGUAL a planilha original: coluna A mais larga
        # (rotulos como "INVESTIMENTO", "SOROS GALE 1"), B-F do mesmo
        # tamanho (valores). Tudo calculado em cima da largura ATUAL da
        # janela GR (que agora e sempre igual a do painel principal - ver
        # _gr_largura_atual()), entao a grade se ajusta sozinha se a janela
        # for redimensionada.
        x0 = 20
        largura_util = _gr_largura_atual() - 40
        A_W = largura_util * 0.30
        OUT_W = (largura_util - A_W) / 5
        xs = [x0]
        xs.append(x0 + A_W)
        for i in range(1, 6):
            xs.append(x0 + A_W + OUT_W * i)

        def celula_texto(col_i0, col_i1, y0, y1, texto, cor=FG, fundo=None, negrito=False):
            fundo = fundo or PANEL_BG
            gr_canvas.create_rectangle(xs[col_i0], y0, xs[col_i1], y1, outline=PANEL_EDGE, fill=fundo, width=1)
            gr_canvas.create_text((xs[col_i0] + xs[col_i1]) / 2, (y0 + y1) / 2, text=texto,
                                   font=(FONT_LABEL if negrito else FONT_MONO_SM), fill=cor)

        def celula_editavel(col_i0, col_i1, y0, y1, texto_inicial, chave):
            gr_canvas.create_rectangle(xs[col_i0], y0, xs[col_i1], y1, outline=ACCENT, fill="#0e1f10", width=1)
            entry = tk.Entry(gr_canvas, font=FONT_MONO_SM, bg="#0e1f10", fg=FG, insertbackground=FG,
                              relief="flat", justify="center", highlightthickness=0, bd=0)
            entry.insert(0, texto_inicial)
            entry.bind("<Return>", lambda e: _gr_confirma_campo(chave))
            entry.bind("<FocusOut>", lambda e: _gr_confirma_campo(chave))
            largura_px = max(10, xs[col_i1] - xs[col_i0] - 6)
            altura_px = max(10, y1 - y0 - 6)
            gr_canvas.create_window((xs[col_i0] + xs[col_i1]) / 2, (y0 + y1) / 2, window=entry,
                                     width=largura_px, height=altura_px)
            gr_ui["entries"][chave] = entry

        y = 56

        # --- ABAS: uma por planilha/nivel (2, 3, 4) - clicar troca qual
        # config esta sendo mostrada/editada. Se o GR estiver ATIVO, a aba
        # selecionada tambem e a que o bot.py usa AGORA (troca ao vivo,
        # sem precisar reiniciar nada - ver _sincroniza_gr no bot.py). ---
        TAB_GAP = 6
        tab_w = (_gr_largura_atual() - 40 - 2 * TAB_GAP) / 3
        tab_y0, tab_y1 = y, y + 28
        for i, niv in enumerate((2, 3, 4)):
            tx0 = x0 + i * (tab_w + TAB_GAP)
            tx1 = tx0 + tab_w
            selecionada = niv == nivel_atual
            tag = f"gr_tab_{niv}"
            _rounded_rect(gr_canvas, tx0, tab_y0, tx1, tab_y1, r=8,
                          fill=("#123018" if selecionada else PANEL_BG),
                          outline=(ACCENT if selecionada else PANEL_EDGE),
                          width=(2 if selecionada else 1), tags=(tag,))
            gr_canvas.create_text((tx0 + tx1) / 2, (tab_y0 + tab_y1) / 2 - 6,
                                   text=f"NIVEL {niv}", font=FONT_LABEL,
                                   fill=(ACCENT if selecionada else MUTED), tags=(tag,))
            gr_canvas.create_text((tx0 + tx1) / 2, (tab_y0 + tab_y1) / 2 + 8,
                                   text=f"planilha {20 + niv}", font=FONT_MONO_SM,
                                   fill=(FG if selecionada else MUTED), tags=(tag,))
        y = tab_y1 + 8

        # --- botao ATIVAR/DESATIVAR: liga/desliga o GR de verdade no bot,
        # usando SEMPRE a aba selecionada acima como config ativa. ---
        ativo = gr_ativo_var["value"]
        ativar_y0, ativar_y1 = y, y + 26
        _rounded_rect(gr_canvas, x0, ativar_y0, x0 + (_gr_largura_atual() - 40), ativar_y1, r=8,
                      fill=("#123018" if ativo else "#2a0d10"),
                      outline=(GREEN_WIN if ativo else RED_LOSS), width=2,
                      tags=("gr_tab_ativar",))
        texto_ativar = f"\u25cf GR ATIVO NO BOT - NIVEL {nivel_atual}  (clique p/ desligar)" if ativo \
            else f"\u25cb GR DESLIGADO - clique p/ usar NIVEL {nivel_atual} no bot"
        gr_canvas.create_text(x0 + (_gr_largura_atual() - 40) / 2, (ativar_y0 + ativar_y1) / 2,
                               text=texto_ativar, font=FONT_LABEL,
                               fill=(GREEN_WIN if ativo else RED_LOSS), tags=("gr_tab_ativar",))
        y = ativar_y1 + 10

        # --- STATUS AO VIVO do motor de verdade (bot.py) - fase atual
        # (soros/gale), valor da proxima entrada e lucro acumulado do ciclo
        # em andamento. ISSO E O QUE FALTAVA: o resto da grade abaixo e uma
        # SIMULACAO (calculada aqui no overlay, so pra conferir os numeros
        # contra a planilha) - ela nunca muda sozinha com o bot operando.
        # Essa linha aqui, sim, e atualizada em tempo real (ver refresh_gr()
        # mais abaixo), puxando state.get_gr_status() - e como voce confere
        # se o bot esta REALMENTE seguindo o nivel selecionado.
        status_y0, status_y1 = y, y + 22
        gr_ui["status_ao_vivo"] = gr_canvas.create_text(
            x0 + (_gr_largura_atual() - 40) / 2, (status_y0 + status_y1) / 2,
            text="status ao vivo: --", font=FONT_MONO_SM, fill=MUTED,
        )
        y = status_y1 + 8

        # ============ GRADE IDENTICA A PLANILHA DO EXCEL (GR__1_.xlsx) ======
        # Mesmas 6 colunas (A-F), mesma quantidade de linhas e nas MESMAS
        # posicoes/mescagens das 3 abas originais (22=nivel2, 23=nivel3,
        # 24=nivel4), calculadas com as MESMAS formulas de celula da sua
        # planilha (extraidas direto do arquivo, celula por celula):
        #   A4=B1*B2 (1a entrada) | B4=A4*B3 (lucro) | A5=A4+B4 (2a entrada,
        #   soros) ... RESULTADO=ultima entrada+ultimo lucro | cada bloco
        #   "SOROS GALE i" = B(cumulativo ate ali)*B(gale% daquele nivel).
        # (obs: normalizei a linha de "LUCRO DO DIA"/"RESULTADO" pra cair
        # sempre na mesma posicao relativa nas 3 abas - no seu arquivo
        # original ela variava 1 linha de aba pra aba por causa de uma
        # celula extra que so a aba do nivel 3 tinha - o resto e identico.)
        INV = cfg["investimento"]
        PU = cfg["pct_util"]
        PAY = cfg["payout"]
        gale_pcts = cfg["gale"]
        while len(gale_pcts) < nivel_atual:
            gale_pcts.append(gale_pcts[-1] if gale_pcts else 0.5)

        # --- linha A4 em diante: escada do SOROS (compounding: v, v+v*pay, ...) ---
        stakes = [INV * PU]
        profits = [stakes[0] * PAY]
        for _ in range(1, nivel_atual):
            nxt = stakes[-1] + profits[-1]
            stakes.append(nxt)
            profits.append(nxt * PAY)
        resultado = stakes[-1] + profits[-1]
        lucro_dia = resultado - stakes[0]

        # --- blocos "SOROS GALE i": cada um usa o INVESTIDO ACUMULADO ate
        # ali (1a entrada + gales anteriores) vezes o %gale daquele nivel -
        # exatamente igual as formulas B10=A4*B12, B13=(A4+B10)*B15, etc. ---
        blocos = []
        cum = stakes[0]
        for i in range(nivel_atual):
            pct = gale_pcts[i]
            g_stake = cum * pct
            g_profit = g_stake * PAY
            val2 = g_stake + g_profit
            prof2 = val2 * PAY
            d_row1 = INV - (cum + g_stake)      # "L/P" se perder aqui
            e_row1 = INV - d_row1                # "LOSS/DIA" acumulado ate aqui
            f_row1 = (lucro_dia / e_row1) if e_row1 else 0.0  # "R:R"
            c_row2 = val2 + prof2
            d_row2 = (INV - (cum + g_stake)) + c_row2
            blocos.append({
                "g_stake": g_stake, "g_profit": g_profit,
                "val2": val2, "prof2": prof2,
                "d_row1": d_row1, "e_row1": e_row1, "f_row1": f_row1,
                "pct": pct, "c_row2": c_row2, "d_row2": d_row2,
            })
            cum += g_stake

        def R(v):
            return f"{v:,.2f}".replace(",", "\u00b7").replace(".", ",").replace("\u00b7", ".")

        def linha_y(r):
            return grid_y0 + (r - 1) * ROW_H

        # numero de linhas totais dessa aba (nivel), pra saber a altura da
        # grade inteira e distribuir o espaco disponivel na janela
        total_linhas = 6 + 4 * nivel_atual
        espaco_disp = max(200, h - y - 34)
        ROW_H = max(13, min(20, espaco_disp / total_linhas))
        grid_y0 = y

        # --- linhas 1-3: INVESTIMENTO / % UTILIZADA / PAY OUT (celulas de
        # entrada - iguais as celulas B1/B2/B3 da planilha, editaveis) ---
        r = 1
        celula_texto(0, 1, linha_y(r), linha_y(r + 1), "INVESTIMENTO", cor=MUTED)
        celula_editavel(1, 3, linha_y(r), linha_y(r + 1), f"{INV:.2f}", "investimento")
        # Fonte do investimento (MANUAL = valor fixo digitado acima; SALDO =
        # puxa o saldo real da conta e trava a cada ciclo novo - ver
        # reiniciar_ciclo em gerenciamento.py). Cabe nas colunas livres (3-6)
        # dessa mesma linha, sem precisar de espaco extra na grade.
        fonte = cfg.get("investimento_fonte", "manual")
        fy0, fy1 = linha_y(r), linha_y(r + 1)
        f_area_w = xs[6] - xs[3]
        f_w = f_area_w / 2 - 3
        for i, (rotulo, val) in enumerate((("MANUAL", "manual"), ("SALDO", "saldo"))):
            fx0 = xs[3] + i * (f_w + 6) + 2
            fx1 = fx0 + f_w
            ativo = fonte == val
            tag = f"gr_fonte_{val}"
            _rounded_rect(gr_canvas, fx0, fy0 + 2, fx1, fy1 - 2, r=6,
                          fill=(ACCENT_DIM if ativo else PANEL_BG),
                          outline=(ACCENT if ativo else PANEL_EDGE), width=1, tags=(tag,))
            gr_canvas.create_text((fx0 + fx1) / 2, (fy0 + fy1) / 2, text=rotulo,
                                   fill=(ACCENT if ativo else MUTED), font=FONT_MONO_SM, tags=(tag,))
        r += 1
        celula_texto(0, 1, linha_y(r), linha_y(r + 1), "% UTILIZADA", cor=MUTED)
        celula_editavel(1, 3, linha_y(r), linha_y(r + 1), f"{PU * 100:.0f}", "pct_util")
        r += 1
        celula_texto(0, 1, linha_y(r), linha_y(r + 1), "PAY OUT", cor=MUTED)
        celula_editavel(1, 3, linha_y(r), linha_y(r + 1), f"{PAY * 100:.0f}", "payout")
        r += 1

        # --- linhas 4..(3+nivel): escada do soros, coluna A=entrada, B=lucro
        # (igual A4/B4, A5/B5, ... da planilha) ---
        for idx in range(nivel_atual):
            celula_texto(0, 1, linha_y(r), linha_y(r + 1), R(stakes[idx]), cor=FG)
            celula_texto(1, 2, linha_y(r), linha_y(r + 1), R(profits[idx]), cor=GREEN_WIN)
            r += 1

        # --- LUCRO DO DIA (rotulo, coluna C) + RESULTADO (linha seguinte) ---
        celula_texto(2, 3, linha_y(r), linha_y(r + 1), "LUCRO DO DIA", cor=AMBER, negrito=True)
        r += 1
        celula_texto(0, 1, linha_y(r), linha_y(r + 1), "RESULTADO", cor=FG, fundo="#123018", negrito=True)
        celula_texto(1, 2, linha_y(r), linha_y(r + 1), R(resultado), cor=GREEN_WIN, fundo="#123018", negrito=True)
        celula_texto(2, 3, linha_y(r), linha_y(r + 1), R(lucro_dia), cor=GREEN_WIN, fundo="#123018", negrito=True)
        r += 1
        r += 1  # linha em branco, igual a planilha (separa do bloco de gale)

        # --- blocos "SOROS GALE 1".."SOROS GALE nivel" - 3 linhas cada,
        # nas MESMAS posicoes/mesclagens de A10:A12, E11:E12, F11:F12 etc. ---
        for i, b in enumerate(blocos):
            r0 = r
            fundo_bloco = "#1a140a"
            # coluna A mesclada nas 3 linhas do bloco - rotulo "SOROS GALE i"
            celula_texto(0, 1, linha_y(r0), linha_y(r0 + 3), f"SOROS\nGALE {i + 1}",
                         cor=AMBER, fundo=fundo_bloco, negrito=True)
            # linha 0 do bloco: valor do gale + lucro se ganhar (+ cabecalhos
            # L/P, LOSS/DIA, R:R, so no 1o bloco, igual a planilha original)
            celula_texto(1, 2, linha_y(r0), linha_y(r0 + 1), R(b["g_stake"]), cor=FG, fundo=fundo_bloco)
            celula_texto(2, 3, linha_y(r0), linha_y(r0 + 1), R(b["g_profit"]), cor=GREEN_WIN, fundo=fundo_bloco)
            if i == 0:
                celula_texto(3, 4, linha_y(r0), linha_y(r0 + 1), "L/P", cor=MUTED, fundo=fundo_bloco)
                celula_texto(4, 5, linha_y(r0), linha_y(r0 + 1), "LOSS/DIA", cor=MUTED, fundo=fundo_bloco)
                celula_texto(5, 6, linha_y(r0), linha_y(r0 + 1), "R:R", cor=MUTED, fundo=fundo_bloco)
            else:
                celula_texto(3, 4, linha_y(r0), linha_y(r0 + 1), "", fundo=fundo_bloco)
                celula_texto(4, 6, linha_y(r0), linha_y(r0 + 1), "", fundo=fundo_bloco)
            # linha 1 do bloco: valor reinvestido/lucro, D=L/P, E+F mesclados
            # verticalmente com a linha 2 (igual E11:E12/F11:F12 no excel)
            celula_texto(1, 2, linha_y(r0 + 1), linha_y(r0 + 2), R(b["val2"]), cor=FG, fundo=fundo_bloco)
            celula_texto(2, 3, linha_y(r0 + 1), linha_y(r0 + 2), R(b["prof2"]), cor=GREEN_WIN, fundo=fundo_bloco)
            celula_texto(3, 4, linha_y(r0 + 1), linha_y(r0 + 2), R(b["d_row1"]), cor=RED_LOSS, fundo=fundo_bloco)
            celula_texto(4, 5, linha_y(r0 + 1), linha_y(r0 + 3), R(b["e_row1"]), cor=RED_LOSS, fundo=fundo_bloco)
            celula_texto(5, 6, linha_y(r0 + 1), linha_y(r0 + 3), f"{b['f_row1']:.2f}", cor=AMBER, fundo=fundo_bloco)
            # linha 2 do bloco: %gale (EDITAVEL - igual B12/B15/B18/B21 no
            # excel, que sao valores digitados, nao formula) + totais finais
            celula_editavel(1, 2, linha_y(r0 + 2), linha_y(r0 + 3), f"{b['pct'] * 100:.0f}", f"gale_{i}")
            celula_texto(2, 3, linha_y(r0 + 2), linha_y(r0 + 3), R(b["c_row2"]), cor=GREEN_WIN, fundo=fundo_bloco)
            celula_texto(3, 4, linha_y(r0 + 2), linha_y(r0 + 3), R(b["d_row2"]), cor=GREEN_WIN, fundo=fundo_bloco)
            r = r0 + 3

        y = linha_y(r) + 16
        breakeven = 1 / (1 + PAY) * 100
        gr_canvas.create_text(w // 2, y, text=f"equilibrio: {breakeven:.1f}%",
                               font=FONT_MONO_SM, fill=(GREEN_WIN if breakeven < 55 else AMBER))

    # liga os cliques das abas (2/3/4) e do botao ativar/desativar - o bind
    # e feito por TAG (nao por item), entao continua funcionando mesmo
    # depois de _gr_desenha_conteudo() apagar e redesenhar tudo (o mesmo
    # nome de tag e recriado a cada redesenho)
    for _niv in (2, 3, 4):
        gr_canvas.tag_bind(f"gr_tab_{_niv}", "<Button-1>", _gr_selecionar_nivel(_niv))
        gr_canvas.tag_bind(f"gr_tab_{_niv}", "<Enter>", lambda e: gr_canvas.config(cursor="hand2"))
        gr_canvas.tag_bind(f"gr_tab_{_niv}", "<Leave>", lambda e: gr_canvas.config(cursor="arrow"))
    gr_canvas.tag_bind("gr_tab_ativar", "<Button-1>", _gr_toggle_ativo)
    gr_canvas.tag_bind("gr_tab_ativar", "<Enter>", lambda e: gr_canvas.config(cursor="hand2"))
    gr_canvas.tag_bind("gr_tab_ativar", "<Leave>", lambda e: gr_canvas.config(cursor="arrow"))
    for _val in ("manual", "saldo"):
        gr_canvas.tag_bind(f"gr_fonte_{_val}", "<Button-1>", _gr_toggle_fonte_investimento)
        gr_canvas.tag_bind(f"gr_fonte_{_val}", "<Enter>", lambda e: gr_canvas.config(cursor="hand2"))
        gr_canvas.tag_bind(f"gr_fonte_{_val}", "<Leave>", lambda e: gr_canvas.config(cursor="arrow"))

    _gr_desenha_conteudo()

    def refresh_gr():
        """
        Atualiza a linha "status ao vivo" (ver _gr_desenha_conteudo acima)
        com o estado REAL do motor rodando em bot.py (fase soros/gale,
        proxima entrada, lucro do ciclo) - via state.get_gr_status(). Isso
        que faltava: sem isso, o painel so mostrava a simulacao estatica
        (que so muda quando VOCE edita um campo), nunca o que o bot estava
        realmente fazendo - dando a impressao de que o nivel escolhido nao
        estava sendo seguido, mesmo quando estava (o motor em bot.py ja
        aplicava certinho, so o painel nunca contava pra voce).
        """
        item = gr_ui.get("status_ao_vivo")
        if item is None:
            return
        ativo = state.is_gr_ativo()
        if not ativo:
            gr_canvas.itemconfig(item, text="status ao vivo: GR desligado", fill=MUTED)
            return
        status = state.get_gr_status()
        fase = status.get("fase")
        valor_atual = status.get("valor_atual")
        lucro_ciclo = status.get("lucro_ciclo")
        if fase is None:
            gr_canvas.itemconfig(item, text="status ao vivo: aguardando 1a entrada...", fill=MUTED)
            return
        sinal = "+" if (lucro_ciclo or 0) >= 0 else ""
        cor = GREEN_WIN if (lucro_ciclo or 0) >= 0 else RED_LOSS
        texto = f"status ao vivo: {fase}  |  proxima: R${valor_atual:.2f}  |  ciclo: {sinal}R${lucro_ciclo:.2f}"
        gr_canvas.itemconfig(item, text=texto, fill=cor)

    # inicia atras do painel principal (mesma posicao dele, oculta na pilha)
    gr_win.lower(root)

    gr_hover = {
        "expanded": False,
        "current_x": _gr_x_atras(),
        "last_h": _gr_altura_atual(),
    }

    def _gr_aplica_posicao(x):
        h = _gr_altura_atual()
        if h != gr_hover["last_h"]:
            gr_hover["last_h"] = h
            gr_win.geometry(f"{_gr_largura_atual()}x{h}+{int(x)}+{_gr_y()}")
            _gr_desenha_conteudo()  # altura mudou - redesenha o conteudo no tamanho novo
        else:
            gr_win.geometry(f"{_gr_largura_atual()}x{h}+{int(x)}+{_gr_y()}")

    def _gr_segue_root():
        """
        Reposiciona o painel GR na hora (sem animacao), na posicao que ele
        deveria estar AGORA dado o estado atual (aberto do lado, ou fechado
        atras) - chamada pelo <Configure>/<Map> do principal, pra o GR
        "grudar" nele ao arrastar, redimensionar ou restaurar da minimizada,
        em vez de ficar pra tras no lugar antigo.
        """
        alvo = _gr_x_ao_lado() if gr_hover["expanded"] else _gr_x_atras()
        gr_hover["current_x"] = alvo
        _gr_aplica_posicao(alvo)

    def _gr_anima_deslizar():
        # o alvo e recalculado a CADA QUADRO em cima da posicao/tamanho
        # atual do painel principal (nunca um numero guardado de antes) -
        # assim, se o principal se mexer OU for redimensionado no meio da
        # animacao do GR, o GR corrige o rumo sozinho, sem ficar pra tras
        # nem se desgrudar dele.
        alvo = _gr_x_ao_lado() if gr_hover["expanded"] else _gr_x_atras()
        cur = gr_hover["current_x"]
        if abs(alvo - cur) < 1:
            gr_hover["current_x"] = alvo
            _gr_aplica_posicao(alvo)
            if not gr_hover["expanded"]:
                gr_win.lower(root)  # terminou de voltar - garante que fica atras de novo
            return
        novo = cur + (alvo - cur) / 3
        gr_hover["current_x"] = novo
        _gr_aplica_posicao(novo)
        gr_win.after(SLIDE_STEP_MS, _gr_anima_deslizar)

    def _gr_toggle(event=None):
        """Chamado pelo clique no botaozinho na borda direita do painel
        principal (ver dentro de build_ui). Alterna: se estava escondido,
        sai de tras e mostra pro lado direito; se estava mostrando, volta e
        se guarda atras do principal de novo."""
        gr_hover["expanded"] = not gr_hover["expanded"]
        if gr_hover["expanded"]:
            gr_win.lift(root)  # precisa vir pra FRENTE agora pra aparecer saindo de lado
            if "gr_toggle_arrow" in ui:
                canvas.itemconfig(ui["gr_toggle_arrow"], text="\u2039")  # seta aponta pra dentro = "fechar"
        else:
            if "gr_toggle_arrow" in ui:
                canvas.itemconfig(ui["gr_toggle_arrow"], text="\u203a")  # seta aponta pra fora = "abrir"
        _gr_anima_deslizar()

    def _gr_gruda_quando_escondido():
        """Roda em loop leve o tempo todo: enquanto o GR estiver escondido
        (nao expandido), mante-lo GRUDADO na posicao/tamanho atual do
        principal - cobre o caso do principal se mexer ou ser redimensionado
        por qualquer motivo enquanto o GR nao esta fazendo nada. So
        reposiciona/redimensiona, sem nenhuma logica de hover/mouse
        envolvida."""
        if not gr_hover["expanded"]:
            alvo_atras = _gr_x_atras()
            if abs(gr_hover["current_x"] - alvo_atras) >= 1 or _gr_altura_atual() != gr_hover["last_h"]:
                gr_hover["current_x"] = alvo_atras
                _gr_aplica_posicao(alvo_atras)
        root.after(80, _gr_gruda_quando_escondido)

    _gr_gruda_quando_escondido()

    canvas.tag_bind("btn_gr_toggle", "<Button-1>", _gr_toggle)
    canvas.tag_bind("btn_gr_toggle", "<Enter>", lambda e: canvas.config(cursor="hand2"))
    canvas.tag_bind("btn_gr_toggle", "<Leave>", lambda e: canvas.config(cursor="arrow"))

    def salvar_print_painel(caminho="print_painel.png"):
        """
        Salva um print SO da area do(s) painel(is) direto em disco, usando
        mss (a mesma lib que o bot.py ja usa pra ler saldo/velas) - que
        captura o que esta REALMENTE desenhado na tela.

        Isso existe porque o painel GR usa "overrideredirect" (sem barra de
        titulo) + leve transparencia, e esse tipo de janela costuma ser
        IGNORADO pelas ferramentas de print do proprio Windows (ex: o modo
        "Janela" da Ferramenta de Captura, ou Alt+PrintScreen), porque elas
        so enxergam janelas "normais" registradas no sistema - o mss ignora
        essa distincao e captura os pixels da tela diretamente, entao
        funciona certinho independente disso.

        Atalho: aperte F12 (com o painel em foco) pra chamar isso a
        qualquer momento - inclui a largura do GR automaticamente se ele
        estiver aberto/saindo naquele instante.
        """
        left = root.winfo_x()
        top = root.winfo_y()
        largura = root.winfo_width()
        if gr_hover["expanded"]:
            largura += _gr_largura_atual()  # inclui o GR, que nesse momento esta encostado do lado
        altura = root.winfo_height()
        try:
            with mss.mss() as sct:
                regiao = {"left": left, "top": top, "width": largura, "height": altura}
                raw = sct.grab(regiao)
                img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                img.save(caminho)
            print(f"Print salvo em: {caminho}")
        except Exception as e:
            print(f"Nao consegui salvar o print: {e}")

    root.bind("<F12>", lambda e: salvar_print_painel())

    # ================================================================ loop =
    def refresh():
        # Reforca "sempre por cima" a cada ciclo (nao so uma vez no
        # inicio). No Windows, quando o robo clica na pagina da corretora
        # (levando aquela janela pra frente), o painel flutuante pode
        # perder a prioridade de ficar por cima mesmo com -topmost
        # configurado - reafirmar isso a cada atualizacao evita que o
        # painel va parar atras do navegador.
        root.attributes("-topmost", True)
        refresh_gr()

        if "clock_text" in ui:
            canvas.itemconfig(ui["clock_text"], text=datetime.now().strftime("%H:%M:%S"))
        _tenta_carregar_cfg()

        anim["offset"] += 0.5
        desenha_fundo()

        s = state.snapshot()

        # ponto de status pulsando (so pulsa de verdade quando ligado)
        if "accent_dot" in ui:
            if s["running"]:
                if pulse["grow"]:
                    pulse["r"] += 0.5
                    if pulse["r"] >= 6:
                        pulse["grow"] = False
                else:
                    pulse["r"] -= 0.5
                    if pulse["r"] <= 3:
                        pulse["grow"] = True
                cx, cy = X(22), Y(28)
                r = S(pulse["r"])
                canvas.coords(ui["accent_dot"], cx - r, cy - r, cx + r, cy + r)
                canvas.itemconfig(ui["accent_dot"], fill=ACCENT)
            else:
                canvas.coords(ui["accent_dot"], X(18), Y(24), X(26), Y(32))
                canvas.itemconfig(ui["accent_dot"], fill=MUTED)

        # vela do quadrante - so mostra se a estrategia tiver esse conceito
        info = _quadrant_info(strategy_var["value"])
        if info:
            quadrant_size, candle_seconds = info
            offset = cfg_state["cfg"].get("clock_offset_seconds", 0) if cfg_state["cfg"] else 0
            vela = _vela_atual(quadrant_size, candle_seconds, offset)
            desenha_vela(quadrant_size, vela)
            vela_visible["on"] = True
        else:
            if vela_visible["on"]:
                for item in ui.get("vela_items", []):
                    canvas.delete(item)
                ui["vela_items"] = []
                if "vela_label" in ui:
                    canvas.itemconfig(ui["vela_label"], text="")
                vela_visible["on"] = False

        # anel de contagem regressiva
        if "ring_titulo" in ui:
            if s["running"]:
                if s["next_entry_time"]:
                    restante = (s["next_entry_time"] - datetime.now()).total_seconds()
                    restante = max(0.0, restante)
                    fracao = 1 - min(restante / RING_MAX_SECONDS, 1.0)
                    desenha_ring(fracao, f"{restante:0.0f}s" if restante > 0 else "ja!")
                    canvas.itemconfig(ui["ring_titulo"], text="proxima entrada")
                    canvas.itemconfig(ui["ring_valor"], text="entrando agora..." if restante <= 0 else f"em {restante:0.1f}s", fill=FG)
                    _atualiza_intencao()
                else:
                    desenha_ring(0, "...")
                    canvas.itemconfig(ui["ring_titulo"], text="status")
                    canvas.itemconfig(ui["ring_valor"], text=s["status"], fill=FG)
                    canvas.itemconfig(ui["ring_extra"], text="")
            else:
                desenha_ring(0, "--")
                canvas.itemconfig(ui["ring_titulo"], text="")
                canvas.itemconfig(ui["ring_valor"], text="PARADO", fill=MUTED)
                canvas.itemconfig(ui["ring_extra"], text="")

        # anel de saldo - % de ganho/perda desde que o bot foi ligado.
        # O "ponto zero" (saldo_inicial) vem pronto do SharedState (nao e
        # mais calculado aqui no overlay) - ja fica travado no valor da
        # primeira leitura apos cada Iniciar e reseta sozinho a cada novo
        # Iniciar (ver shared_state.py).
        if "saldo_ring_pct" in ui:
            saldo_atual = s.get("saldo")
            saldo_inicial = s.get("saldo_inicial")
            if s["running"]:
                base = saldo_inicial
                if base and saldo_atual is not None:
                    pct = (saldo_atual - base) / base * 100
                    fracao = min(abs(pct) / 100.0, 1.0)
                    cor = GREEN_WIN if pct >= 0 else RED_LOSS
                    sinal = "+" if pct >= 0 else ""
                    desenha_saldo_ring(fracao, f"R${saldo_atual:.2f}", cor)
                    canvas.itemconfig(ui["saldo_ring_pct"], text=f"{sinal}{pct:.1f}%", fill=cor)
                else:
                    desenha_saldo_ring(0, "R$--" if saldo_atual is None else f"R${saldo_atual:.2f}", MUTED)
                    canvas.itemconfig(ui["saldo_ring_pct"], text="aguardando...", fill=MUTED)
            else:
                desenha_saldo_ring(0, "--", MUTED)
                canvas.itemconfig(ui["saldo_ring_pct"], text="", fill=MUTED)
            if "saldo_ring_inicial" in ui:
                texto_inicial = f"inicial: R${saldo_inicial:.2f}" if saldo_inicial is not None else "inicial: --"
                canvas.itemconfig(ui["saldo_ring_inicial"], text=texto_inicial, fill=MUTED)

        if "info_text" in ui:
            linhas = []
            if cfg_state["cfg"] is None:
                linhas.append("Sem calibracao ainda.")
                linhas.append("Clique em CALIBRAR para comecar.")
            if s.get("pausado"):
                linhas.append("\u23f8 PAUSADO - calculando sinais, sem abrir operacao")
            if s["last_decision"] == "buy":
                linhas.append("Ultima direcao: COMPRA")
            elif s["last_decision"] == "sell":
                linhas.append("Ultima direcao: VENDA")
            tipo_res = s.get("ultimo_resultado_tipo")
            valor_res = s.get("ultimo_resultado_valor")
            if tipo_res is not None and valor_res is not None:
                rotulo = {"gain": "GAIN", "loss": "LOSS", "tie": "EMPATE"}[tipo_res]
                sinal = "+" if valor_res >= 0 else ""
                linhas.append(f"Ultimo: {rotulo} ({sinal}{valor_res:.2f})")
            canvas.itemconfig(ui["info_text"], text="\n".join(linhas))

        # placar AUTOMATICO (bot) - sempre amarelo, so o card "acende" mais
        # forte quando tem numero, fica esmaecido quando esta zerado
        if "win_num" in ui:
            ganhas, perdidas = s["ganhas"], s["perdidas"]
            canvas.itemconfig(ui["win_num"], text=str(ganhas))
            canvas.itemconfig(ui["loss_num"], text=str(perdidas))
            canvas.itemconfig(ui["win_card"], outline=(AMBER if ganhas else PANEL_EDGE))
            canvas.itemconfig(ui["win_num"], fill=(AMBER if ganhas else MUTED))
            canvas.itemconfig(ui["loss_card"], outline=(AMBER if perdidas else PANEL_EDGE))
            canvas.itemconfig(ui["loss_num"], fill=(AMBER if perdidas else MUTED))

            total = ganhas + perdidas
            taxa = (100 * ganhas / total) if total else 0
            payout_gr_atual = gr_config[gr_nivel_var["value"]]["payout"]
            breakeven = 1 / (1 + payout_gr_atual) * 100
            if total:
                cor_taxa = GREEN_WIN if taxa >= breakeven else RED_LOSS
                texto_taxa = f"acerto: {taxa:.0f}%  |  equilibrio: {breakeven:.1f}%"
            else:
                cor_taxa = MUTED
                texto_taxa = f"equilibrio p/ payout do GR {gr_nivel_var['value']}: {breakeven:.1f}%"
            canvas.itemconfig(ui["taxa_text"], text=texto_taxa, fill=cor_taxa)

            seq = s["sequencia_atual"]
            if seq > 0:
                canvas.itemconfig(ui["seq_text"], text=f"{seq} ganha(s) seguida(s)", fill=AMBER)
            elif seq < 0:
                canvas.itemconfig(ui["seq_text"], text=f"{-seq} perdida(s) seguida(s)", fill=AMBER)
            else:
                canvas.itemconfig(ui["seq_text"], text="", fill=MUTED)

            # placar MANUAL (voce) - verde/vermelho, igual o placar antigo
            ganhas_m, perdidas_m = s["ganhas_manual"], s["perdidas_manual"]
            canvas.itemconfig(ui["win_m_num"], text=str(ganhas_m))
            canvas.itemconfig(ui["loss_m_num"], text=str(perdidas_m))
            canvas.itemconfig(ui["win_m_card"], outline=(GREEN_WIN if ganhas_m else PANEL_EDGE))
            canvas.itemconfig(ui["win_m_num"], fill=(GREEN_WIN if ganhas_m else MUTED))
            canvas.itemconfig(ui["loss_m_card"], outline=(RED_LOSS if perdidas_m else PANEL_EDGE))
            canvas.itemconfig(ui["loss_m_num"], fill=(RED_LOSS if perdidas_m else MUTED))

            total_m = ganhas_m + perdidas_m
            taxa_m = (100 * ganhas_m / total_m) if total_m else 0
            cor_taxa_m = GREEN_WIN if taxa_m >= 50 and total_m else (RED_LOSS if total_m else MUTED)
            canvas.itemconfig(ui["taxa_m_text"], text=f"taxa de acerto: {taxa_m:.0f}%", fill=cor_taxa_m)

            seq_m = s["sequencia_manual"]
            if seq_m > 0:
                canvas.itemconfig(ui["seq_m_text"], text=f"{seq_m} ganha(s) seguida(s)", fill=GREEN_WIN)
            elif seq_m < 0:
                canvas.itemconfig(ui["seq_m_text"], text=f"{-seq_m} perdida(s) seguida(s)", fill=RED_LOSS)
            else:
                canvas.itemconfig(ui["seq_m_text"], text="", fill=MUTED)

            # margem de erro entre os dois placares (bot vs manual) - diferenca
            # de operacoes contadas e diferenca na taxa de acerto de cada um
            if total or total_m:
                dif_operacoes = abs(total - total_m)
                dif_taxa = abs(taxa - taxa_m)
                cor_margem = MUTED if dif_operacoes == 0 and dif_taxa < 1 else PINK
                canvas.itemconfig(
                    ui["margem_text"],
                    text=f"margem de erro (bot vs manual): {dif_operacoes} operacao(oes) | {dif_taxa:.0f}pp de taxa",
                    fill=cor_margem,
                )
            else:
                canvas.itemconfig(ui["margem_text"], text="")

        refresh_button()
        refresh_pills()
        refresh_pausa_button()

        if "calib_rect" in ui:
            travado = s["running"]
            canvas.itemconfig(ui["calib_rect"], outline=(AMBER_DIM if travado else AMBER))
            canvas.itemconfig(ui["calib_text"], fill=("#5b5030" if travado else AMBER))

        root.after(150, refresh)

    refresh()
    root.mainloop()


if __name__ == "__main__":
    # Permite testar o overlay isolado (sem bot.py), com estado vazio.
    from shared_state import SharedState
    run_overlay(SharedState())
