"""
Deteccao automatica de velas na tela - sem precisar contar "quantas velas
ficam visiveis". Em vez de dividir a largura do grafico em fatias iguais
(que acumula erro se o numero de velas visiveis for digitado errado), isso
escaneia a imagem de verdade, pixel por pixel, e acha os blocos (blobs) de
cor verde/vermelha que sao os CORPOS das velas.

Um "blob" e um grupo de colunas vizinhas que tem um corpo de vela (um trecho
vertical continuo da cor de alta ou de baixa, alto o suficiente pra nao ser
so o pavio fino). Isso da a posicao real de cada vela, nao uma estimativa.

As cores de alta/baixa NAO precisam ser verde/vermelho - sao as cores exatas
que voce apontou na calibragem (calibrate.py), sejam quais forem (o grafico
pode usar azul/laranja, roxo/rosa, etc). Internamente o codigo ainda chama
os dois grupos de "green"/"red" so como rotulo (= alta/baixa), nao como cor
literal.
"""
import numpy as np
import mss
from PIL import Image

MIN_BODY_HEIGHT = 1   # altura minima (px) de um trecho colorido pra contar como corpo (filtra so ruido de 0px; corpos finos de verdade agora sao detectados)
MAX_COLUMN_GAP = 2    # colunas sem corpo entre uma vela e outra ainda contam como "mesma vela" (anti-aliasing)
MIN_BLOB_WIDTH = 6    # largura minima (colunas) pra um blob contar como vela de verdade - filtra
                      # "velas" fantasmas geradas por elementos da INTERFACE da corretora (textos,
                      # bordas de balao "10 R$ HH:MM", marcadores de entrada etc) que caem dentro
                      # da tolerancia de cor calibrada mas sao finos de mais (poucos px de largura)
                      # pra ser um corpo de vela de verdade - mesmo velas de corpo BEM fino na
                      # ALTURA (tipo doji) ainda ocupam a largura inteira da vela, entao esse
                      # filtro nao arrisca descartar velas legitimas.
MAX_BLOB_WIDTH_RATIO = 2.2   # blobs MAIS LARGOS que isso vezes a largura TIPICA das velas (a
                             # MEDIANA de largura de todos os blobs detectados naquele instante)
                             # sao descartados - normalmente sao elementos de interface (ex: o
                             # balao de preco/hora tipo "10 R$ HH:MM", que e bem mais largo que
                             # uma vela por ter texto dentro), nao velas de verdade. Calcular em
                             # cima da MEDIANA (em vez de um numero fixo de pixels) faz esse
                             # filtro funcionar em qualquer zoom de grafico ou corretora, sem
                             # precisar recalibrar nada - a largura "normal" de vela e sempre
                             # relativa ao que esta sendo detectado ali, na hora.
MIN_BLOB_HEIGHT_RATIO = 0.15   # blobs bem mais BAIXOS (em altura) que isso vezes a altura TIPICA
                                # das velas (a MEDIANA de altura de todos os blobs detectados
                                # naquele instante) sao descartados - esse e o filtro que pega o
                                # MARCADOR DE OPERACAO ABERTA que a corretora desenha por cima do
                                # grafico (a linha pontilhada de preco de entrada + balao "10 R$
                                # HH:MM"): essa linha e bem mais FINA (poucos px de altura) que o
                                # corpo de qualquer vela real, mas pode ter largura parecida com
                                # uma vela (por isso o filtro de largura MIN_BLOB_WIDTH sozinho nao
                                # pega ela) e a cor dela costuma bater com a tolerancia calibrada,
                                # ja que a corretora pinta esse marcador na MESMA cor da decisao
                                # (compra=verde, venda=vermelho) - o que e perigoso: como o
                                # marcador fica parado (nao muda nem oscila), ele pode "confirmar"
                                # uma leitura com unanimidade total mesmo sendo a cor errada, bem
                                # no momento critico da decisao (ver _read_janela_confirmada em
                                # bot.py). Igual ao filtro de largura, calcula em cima da MEDIANA
                                # (nao um numero fixo de pixels) pra funcionar em qualquer zoom/
                                # corretora sem precisar recalibrar. Valor BEM baixo (0.15) de
                                # proposito: uma vela de verdade pode legitimamente ter corpo
                                # baixo logo apos abrir (preco ainda perto da abertura), entao o
                                # corte so pega casos extremos (linha de poucos px), evitando
                                # descartar vela real por engano.


def grab_chart(cfg):
    """Tira um screenshot so da area do grafico calibrada."""
    region = {
        "left": cfg["chart_left"],
        "top": cfg["chart_top"],
        "width": cfg["chart_right"] - cfg["chart_left"],
        "height": cfg["chart_bottom"] - cfg["chart_top"],
    }
    with mss.mss() as sct:
        raw = sct.grab(region)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    return img


def _color_grid(img, cfg):
    """
    Retorna uma matriz (altura x largura) de inteiros: 1 = pixel verde,
    2 = pixel vermelho, 0 = nem um nem outro (dentro da tolerancia calibrada).
    """
    arr = np.asarray(img).astype(np.int32)  # (h, w, 3)
    # "up_rgb"/"down_rgb" sao os nomes novos (qualquer cor); "green_rgb"/"red_rgb"
    # sao mantidos so por compatibilidade com config.json calibrado em versao anterior.
    green = np.array(cfg.get("up_rgb", cfg.get("green_rgb")), dtype=np.int32)
    red = np.array(cfg.get("down_rgb", cfg.get("red_rgb")), dtype=np.int32)
    tol = cfg["color_tolerance"]

    dist_g = np.sqrt(((arr - green) ** 2).sum(axis=2))
    dist_r = np.sqrt(((arr - red) ** 2).sum(axis=2))

    is_green = (dist_g <= tol) & (dist_g <= dist_r)
    is_red = (dist_r <= tol) & (dist_r < dist_g)

    grid = np.zeros(arr.shape[:2], dtype=np.uint8)
    grid[is_green] = 1
    grid[is_red] = 2
    return grid


def _longest_run_in_column(col, min_height):
    """
    col: array 1D de 0/1/2 (uma coluna de pixels, de cima pra baixo).
    Acha o maior trecho continuo do MESMO valor nao-zero. Retorna
    (color_code, top, bottom) ou None se nada atingir min_height.
    """
    changes = np.flatnonzero(np.diff(col)) + 1
    bounds = np.concatenate(([0], changes, [len(col)]))

    best = None
    for i in range(len(bounds) - 1):
        start, end = bounds[i], bounds[i + 1]
        val = col[start]
        if val == 0:
            continue
        length = end - start
        if length < min_height:
            continue
        if best is None or length > (best[2] - best[1]):
            best = (val, start, end)

    if best is None:
        return None
    val, top, bottom = best
    return ("green" if val == 1 else "red"), int(top), int(bottom)


def detect_candle_blobs(cfg, min_body_height=MIN_BODY_HEIGHT, max_gap=MAX_COLUMN_GAP, min_blob_width=MIN_BLOB_WIDTH):
    """
    Escaneia a area do grafico inteira e retorna a lista de velas detectadas,
    da mais antiga (esquerda) para a mais recente (direita). Cada vela:
      {"color": "green"/"red", "top": int, "bottom": int, "x_center": float}
    top/bottom sao coordenadas ABSOLUTAS de tela (relativas ao monitor todo).

    Blobs mais finos que min_blob_width colunas sao descartados - sao quase
    sempre elementos da INTERFACE da corretora (texto, bordas de balao tipo
    "10 R$ HH:MM", marcadores de entrada) cuja cor caiu dentro da tolerancia
    calibrada, e nao velas de verdade (ver MIN_BLOB_WIDTH acima).
    """
    img = grab_chart(cfg)
    grid = _color_grid(img, cfg)
    width = grid.shape[1]

    per_column = []  # (x, color, top, bottom) ou None
    for x in range(width):
        run = _longest_run_in_column(grid[:, x], min_body_height)
        if run is None:
            per_column.append(None)
        else:
            color, top, bottom = run
            per_column.append((x, color, top, bottom))

    blobs = []
    current = []
    gap = 0
    for entry in per_column:
        if entry is not None:
            cor_atual = entry[1]
            if current and current[-1][1] != cor_atual:
                # a cor mudou de uma coluna pra outra SEM gap de background
                # entre elas (vela nova nasceu colada na anterior, sem
                # espaco vazio de separacao) - fecha o blob atual aqui e
                # comeca um novo, mesmo sem gap nenhum. Sem isso, uma vela
                # pequena colada numa vela grande de cor diferente virava
                # UM SO blob mesclado, com a cor decidida por qual lado tem
                # mais colunas - normalmente a vela grande "engolia" a
                # pequena, escondendo justamente as reversoes mais recentes.
                blobs.append(current)
                current = []
            current.append(entry)
            gap = 0
        elif current:
            gap += 1
            if gap > max_gap:
                blobs.append(current)
                current = []
                gap = 0
    if current:
        blobs.append(current)

    blobs = [b for b in blobs if len(b) >= min_blob_width]

    # SEPARA BLOBS MESCLADOS - quando uma vela fechada e a vela em formacao
    # (ou duas velas seguidas quaisquer) tem a MESMA cor e ficam coladas com
    # um gap de fundo <= max_gap entre elas, o loop acima (que so fecha um
    # blob quando a COR muda ou o gap estoura) nao consegue separa-las: elas
    # viram UM SO blob, com o dobro (ou mais) da largura normal de uma vela.
    # Isso e especialmente grave pra estrategias que olham a ultima vela
    # fechada (ex: surf): bem no instante em que a vela em formacao inverte
    # de cor, o pedaco novo pode nascer fino de mais e ser descartado pelo
    # filtro de min_blob_width acima, deixando blobs[-1] presa no blob
    # mesclado ANTIGO (cor desatualizada) - o bot "trava" na cor errada e
    # decide contrario ao que a vela realmente fechou.
    #
    # Correcao: calcula a largura mediana das velas de verdade (as que ja
    # sobreviveram ao filtro de min_blob_width acima) e, se um blob for bem
    # mais largo que isso (indicio de que sao N velas coladas viradas uma
    # so), divide ele em N pedacos de largura igual - cada pedaco vira uma
    # vela separada, com sua propria cor/top/bottom recalculados so daquelas
    # colunas. So roda com blobs suficientes pra uma mediana fazer sentido
    # (mesmo criterio dos outros filtros por mediana abaixo).
    if len(blobs) >= 3:
        larguras_base = sorted(len(b) for b in blobs)
        meio = len(larguras_base) // 2
        if len(larguras_base) % 2 == 1:
            largura_mediana_base = larguras_base[meio]
        else:
            largura_mediana_base = (larguras_base[meio - 1] + larguras_base[meio]) / 2
        if largura_mediana_base > 0:
            blobs_separados = []
            for b in blobs:
                n_partes = round(len(b) / largura_mediana_base)
                if n_partes >= 2 and len(b) > largura_mediana_base * 1.5:
                    tamanho = len(b) / n_partes
                    for i in range(n_partes):
                        inicio = round(i * tamanho)
                        fim = round((i + 1) * tamanho) if i < n_partes - 1 else len(b)
                        if fim > inicio:
                            blobs_separados.append(b[inicio:fim])
                else:
                    blobs_separados.append(b)
            blobs = blobs_separados

    # Filtro de largura MAXIMA - ver MAX_BLOB_WIDTH_RATIO no topo do arquivo.
    # Calcula a largura tipica (mediana) em cima do que sobrou ate aqui, e
    # descarta qualquer blob bem mais largo que isso (normalmente o balao de
    # preco/hora da corretora, nao uma vela de verdade). So roda se tiver
    # blobs suficientes pra uma mediana fazer sentido (com 1 ou 2 blobs so,
    # nao da pra saber qual e o "tipico" - nesse caso nao filtra nada por
    # largura, so os outros filtros ja aplicados continuam valendo).
    if len(blobs) >= 3:
        larguras = sorted(len(b) for b in blobs)
        meio = len(larguras) // 2
        if len(larguras) % 2 == 1:
            largura_mediana = larguras[meio]
        else:
            largura_mediana = (larguras[meio - 1] + larguras[meio]) / 2
        limite_largura = largura_mediana * MAX_BLOB_WIDTH_RATIO
        blobs = [b for b in blobs if len(b) <= limite_largura]

    # Filtro de altura MINIMA - ver MIN_BLOB_HEIGHT_RATIO no topo do arquivo.
    # A altura de um blob e a do trecho colorido MAIS ALTO entre suas colunas
    # (nao a media) - assim, mesmo que o marcador da corretora encoste numa
    # ponta da vela real e "herde" colunas altas dela, a altura maxima real da
    # vela ainda aparece corretamente medida. Calcula a mediana em cima do que
    # sobrou ate aqui e descarta blobs bem mais baixos que isso - normalmente
    # a linha/balao do marcador de operacao aberta, nao uma vela de verdade.
    # So roda com blobs suficientes pra uma mediana fazer sentido (mesmo
    # criterio do filtro de largura acima).
    if len(blobs) >= 3:
        alturas = sorted(max(bottom - top for (_x, _c, top, bottom) in b) for b in blobs)
        meio = len(alturas) // 2
        if len(alturas) % 2 == 1:
            altura_mediana = alturas[meio]
        else:
            altura_mediana = (alturas[meio - 1] + alturas[meio]) / 2
        limite_altura = altura_mediana * MIN_BLOB_HEIGHT_RATIO
        blobs_filtrados = [
            b for b in blobs
            if max(bottom - top for (_x, _c, top, bottom) in b) >= limite_altura
        ]
        # SEGURANCA: se esse filtro fosse derrubar a contagem de velas abaixo
        # de 3 (o minimo que fez a gente confiar na mediana em primeiro
        # lugar), alguma coisa esta estranha (calibragem ruim, tolerancia de
        # cor errada, etc) - nesse caso e mais seguro NAO filtrar nada por
        # altura do que arriscar deixar o chamador (bot.py/estrategia) com
        # velas de menos pra decidir, o que pode gerar erro mais adiante.
        if len(blobs_filtrados) >= 3:
            blobs = blobs_filtrados

    result = []
    for blob in blobs:
        xs = [e[0] for e in blob]
        colors = [e[1] for e in blob]
        tops = [e[2] for e in blob]
        bottoms = [e[3] for e in blob]
        color = max(set(colors), key=colors.count)
        result.append({
            "color": color,
            "top": cfg["chart_top"] + min(tops),
            "bottom": cfg["chart_top"] + max(bottoms),
            "x_center": sum(xs) / len(xs) + cfg["chart_left"],
        })
    return result
