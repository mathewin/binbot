"""
Leitura do SALDO da conta via OCR (reconhecimento de texto), pra apurar o
resultado (gain/loss/empate) de cada operacao comparando o saldo ANTES do
clique com o saldo ANTES do PROXIMO clique - ou seja, depois que a corretora
ja creditou/descontou o resultado da operacao anterior, e antes de qualquer
valor novo sair da conta por causa da proxima entrada.

MUDANCA IMPORTANTE (vs versao anterior): a regiao do saldo NAO e mais
calibrada manualmente (saldo_left/top/right/bottom no config.json). Em vez
disso, o bot acha o saldo sozinho, procurando na tela por rotulos comuns
de varias corretoras diferentes ("SALDO", "CONTA", "BALANCE", "ACCOUNT" -
cobre "Conta demo", "Conta real", "CONTA DEMO", "BRL Account", "Conta de
demonstracao" etc, sem diferenciar maiuscula/minuscula) e lendo o numero
do lado, embaixo OU EM CIMA do rotulo (a posicao varia de corretora pra
corretora). Depois de achar uma vez, guarda essa posicao em memoria
(cache) pra nao precisar escanear a tela toda de novo a cada leitura - so
refaz a busca completa se a leitura falhar.

IMPORTANTE sobre timing: capturar a IMAGEM da regiao do saldo e rapido
(grab_saldo_region), mas ler o TEXTO dela com OCR (ocr_saldo_from_image) e
mais lento (pode levar de 100 a 300ms). Por isso, no bot.py, a imagem e
capturada ANTES do clique (pra nao atrasar o clique, que precisa cair bem
na cabeca da vela), mas o OCR dela so roda DEPOIS do clique - a foto ja fica
"congelada" no momento certo, e o processamento mais lento acontece com
calma, sem pressa nenhuma.

A busca completa pela palavra "SALDO" na tela inteira (find_saldo_region)
e mais lenta que uma leitura de regiao ja conhecida, entao ela so roda:
  - na primeira vez que o bot pede o saldo;
  - toda vez que a leitura da regiao em cache falhar (ex: o valor sumiu
    dali, layout mudou, corretora moveu o painel).

Requer o pacote pytesseract e o programa Tesseract-OCR instalado:
  pip install pytesseract
  Tesseract (Windows): https://github.com/UB-Mannheim/tesseract/wiki
Se o instalador nao adicionar ao PATH automaticamente, ajuste a linha
TESSERACT_CMD logo abaixo.
"""
import os
import re
import shutil
import time

import mss
import pytesseract
from PIL import Image, ImageOps

# ---------------------------------------------------------------------
# Localizacao automatica do Tesseract-OCR (sem depender de um caminho
# fixo de um computador especifico). Tenta, nessa ordem:
#   1) o comando "tesseract" ja disponivel no PATH do sistema;
#   2) os locais mais comuns de instalacao no Windows.
# Se nao achar em nenhum desses lugares, avisa no console com uma
# mensagem clara (em vez de simplesmente falhar sem explicar o motivo).
# ---------------------------------------------------------------------
def _localizar_tesseract():
    encontrado = shutil.which("tesseract")
    if encontrado:
        return encontrado

    candidatos = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
        os.path.expandvars(r"%USERPROFILE%\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"),
    ]
    for caminho in candidatos:
        if caminho and os.path.exists(caminho):
            return caminho

    return None


_tesseract_path = _localizar_tesseract()
if _tesseract_path:
    pytesseract.pytesseract.tesseract_cmd = _tesseract_path
else:
    print(
        "AVISO: nao encontrei o Tesseract-OCR instalado neste computador.\n"
        "A leitura do saldo vai falhar ate isso ser resolvido. Baixe e instale em:\n"
        "  https://github.com/UB-Mannheim/tesseract/wiki\n"
        "Depois de instalar, feche e abra o bot de novo."
    )

SALDO_READ_RETRIES = 5
SALDO_READ_INTERVAL = 0.3

# Padding (em pixels, na escala do screenshot original) usado ao redor do
# rotulo do saldo pra definir onde o numero deve estar.
_LARGURA_VALOR = 220   # o quao pra direita/baixo o numero pode se estender
_ALTURA_EXTRA = 10      # folga vertical fixa em volta da caixa do rotulo
# multiplicador de altura: o NUMERO do saldo costuma ser escrito numa
# fonte maior/mais em negrito que o texto do rotulo (ex: "CONTA DEMO"
# pequeno, "R$419.90" bem maior logo abaixo) - se a regiao de busca so
# usar a altura do proprio rotulo, o numero fica cortado e o OCR nao
# consegue ler. Multiplicar por 3 da folga de sobra pra fontes maiores.
_MULT_ALTURA = 3

# Cache da ultima regiao do valor do saldo que funcionou (dict com
# left/top/width/height) - fica em memoria so, nunca vai pro config.json,
# porque a ideia e nao depender de calibracao salva.
_cached_region = None


def invalidate_cache():
    """Forca a proxima leitura a re-escanear a tela toda em vez de usar o
    cache. Util se voce sabe que o layout da corretora mudou."""
    global _cached_region
    _cached_region = None


def _normaliza(texto):
    """Remove tudo que nao for letra e deixa maiusculo, pra comparar
    'Saldo:', 'SALDO', 'saldo ' etc. todos como 'SALDO'."""
    return re.sub(r"[^A-Za-zÀ-ÿ]", "", texto).upper()


def _grab_top_half():
    """
    Print so do CANTO SUPERIOR DIREITO da tela, em vez da tela inteira - o
    usuario confirmou que o saldo, em qualquer corretora que ele usa,
    sempre fica nesse canto (nunca no lado esquerdo, nunca abaixo do topo
    da tela). Restringir a busca a essa area pequena:
      - evita confundir o rotulo/numero do saldo com outros textos ou
        numeros parecidos que aparecem em outros lugares da tela (ex:
        porcentagem de payout do grafico, cotacao do ativo, etc);
      - e bem mais rapido, ja que o OCR processa uma area pequena.
    Pega os ultimos 35% da largura e os primeiros 12% da altura da tela.
    """
    with mss.mss() as sct:
        monitor = sct.monitors[1]  # monitor principal
        largura_area = int(monitor["width"] * 0.35)
        altura_area = int(monitor["height"] * 0.12)
        area = {
            "left": monitor["left"] + monitor["width"] - largura_area,
            "top": monitor["top"],
            "width": largura_area,
            "height": altura_area,
        }
        raw = sct.grab(area)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        return img, area["left"], area["top"]


def _preprocess_busca(img, scale=2):
    """Aumenta e melhora o contraste da imagem ANTES de procurar o rotulo
    do saldo - textos pequenos de interface (modo escuro, fontes miudas)
    saem praticamente ilegiveis pro OCR sem isso, e o OCR acaba lendo
    letras aleatorias em vez do texto de verdade."""
    img = img.convert("L")
    img = ImageOps.autocontrast(img)
    w, h = img.size
    return img.resize((w * scale, h * scale), Image.LANCZOS)


_ESCALA_BUSCA = 2


def debug_dump_deteccoes(caminho_imagem="debug_saldo.png"):
    """
    Ferramenta de diagnostico (rodar manualmente, ex: 'python -c "import
    balance; balance.debug_dump_deteccoes()"' com a tela da corretora
    aberta) - salva um print (ja tratado/ampliado do jeito que o OCR
    realmente usa) do canto superior direito da tela em disco e imprime
    no console TODAS as palavras que o OCR conseguiu ler nela, uma por
    linha. Use isso se o bot nao estiver achando o saldo: abra o
    debug_saldo.png e confira se o texto do rotulo aparece legivel, e olhe
    a lista impressa pra ver se o OCR leu o rotulo (SALDO/CONTA/BALANCE/
    ACCOUNT) com alguma variacao que o codigo ainda nao esteja reconhecendo.
    """
    img, _, _ = _grab_top_half()
    img_busca = _preprocess_busca(img, scale=_ESCALA_BUSCA)
    img_busca.save(caminho_imagem)
    try:
        data = pytesseract.image_to_data(img_busca, output_type=pytesseract.Output.DICT, config="--psm 11")
    except Exception as e:
        print(f"Erro ao rodar OCR: {e}")
        return
    print(f"Print salvo em: {caminho_imagem}")
    print("Palavras detectadas no canto superior direito da tela:")
    for texto in data["text"]:
        if texto.strip():
            print(f"  '{texto.strip()}'  (normalizado: '{_normaliza(texto)}')")


# Palavras de rotulo aceitas como indicio de que o saldo esta por perto.
# Corretoras diferentes usam palavras diferentes ("SALDO", "CONTA",
# "BALANCE", "ACCOUNT" etc) - qualquer uma dessas, sozinha ou combinada
# ("Conta demo", "BRL Account", "Conta de demonstracao"...), serve de pista.
_LABELS_ALVO = {"SALDO", "CONTA", "ACCOUNT", "BALANCE"}


def debug_encontrar_saldo():
    """
    Ferramenta de diagnostico mais completa que debug_dump_deteccoes:
    roda o processo INTEIRO (achar a regiao do rotulo + testar a leitura
    do numero em cada uma das 4 posicoes candidatas) e imprime, passo a
    passo, o que deu certo e o que falhou em cada uma. Rode assim (com a
    corretora aberta mostrando o saldo):
        python -c "import balance; balance.debug_encontrar_saldo()"
    Tambem salva debug_valor_encontrado.png com a regiao que funcionou,
    se alguma funcionar.
    """
    img, offset_left, offset_top = _grab_top_half()
    img_busca = _preprocess_busca(img, scale=_ESCALA_BUSCA)
    try:
        data = pytesseract.image_to_data(
            img_busca, output_type=pytesseract.Output.DICT, config="--psm 11"
        )
    except Exception as e:
        print(f"Erro ao rodar OCR na busca do rotulo: {e}")
        return

    candidatos_label = []
    for i, texto in enumerate(data["text"]):
        if _normaliza(texto) in _LABELS_ALVO:
            candidatos_label.append(
                {
                    "texto": texto.strip(),
                    "left": data["left"][i] // _ESCALA_BUSCA,
                    "top": data["top"][i] // _ESCALA_BUSCA,
                    "width": data["width"][i] // _ESCALA_BUSCA,
                    "height": data["height"][i] // _ESCALA_BUSCA,
                }
            )

    if not candidatos_label:
        print("Nao achei nenhum rotulo (SALDO/CONTA/BALANCE/ACCOUNT) na tela.")
        print("Rode debug_dump_deteccoes() pra ver tudo que o OCR esta lendo ali.")
        return

    print(f"Rotulos encontrados: {[c['texto'] for c in candidatos_label]}")

    for idx, label in enumerate(candidatos_label):
        l, t, w, h = label["left"], label["top"], label["width"], label["height"]
        altura_regiao = (h * _MULT_ALTURA) + _ALTURA_EXTRA * 2
        print(f"\nRotulo #{idx+1}: '{label['texto']}' em x={l} y={t} (largura={w} altura={h})")

        regioes = {
            "direita": {
                "left": offset_left + l + w, "top": offset_top + t - _ALTURA_EXTRA,
                "width": _LARGURA_VALOR, "height": altura_regiao,
            },
            "abaixo": {
                "left": offset_left + max(l - 30, 0), "top": offset_top + max(t - _ALTURA_EXTRA, 0),
                "width": _LARGURA_VALOR, "height": altura_regiao,
            },
            "acima": {
                "left": offset_left + max(l - 30, 0), "top": offset_top + max(t - altura_regiao, 0),
                "width": _LARGURA_VALOR, "height": altura_regiao,
            },
            "esquerda": {
                "left": offset_left + max(l - _LARGURA_VALOR, 0), "top": offset_top + t - _ALTURA_EXTRA,
                "width": _LARGURA_VALOR, "height": altura_regiao,
            },
        }

        for nome, regiao in regioes.items():
            if regiao["width"] <= 0 or regiao["height"] <= 0:
                print(f"  [{nome}] regiao invalida (fora da tela)")
                continue
            try:
                with mss.mss() as sct:
                    raw = sct.grab(regiao)
                    candidato_img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            except Exception as e:
                print(f"  [{nome}] erro ao capturar: {e}")
                continue

            nome_arquivo = f"debug_valor_{nome}.png"
            candidato_img.save(nome_arquivo)
            valor = ocr_saldo_from_image(candidato_img)
            if valor is not None:
                print(f"  [{nome}] SUCESSO -> leu o valor {valor}  (imagem salva em {nome_arquivo})")
            else:
                print(f"  [{nome}] nao leu nenhum numero valido  (imagem salva em {nome_arquivo})")


def find_saldo_region():
    """
    Tira um print do CANTO SUPERIOR DIREITO da tela (ver _grab_top_half -
    e onde o saldo sempre aparece) e procura, usando OCR, qualquer palavra de
    _LABELS_ALVO (com as coordenadas de cada palavra reconhecida). Ao
    achar uma, monta ATE QUATRO regioes candidatas pro VALOR do saldo, ja
    que o layout varia bastante de corretora pra corretora:
      1) logo abaixo do rotulo (ex: "CONTA DEMO" em cima, numero embaixo -
         layout mais comum, por isso e tentado primeiro)
      2) mesma linha, um pouco a direita do rotulo (ex: "SALDO: R$ 123,45")
      3) logo acima do rotulo (ex: numero em cima, "Conta BRL" embaixo)
      4) mesma linha, um pouco a esquerda do rotulo
    Tenta ler um numero valido em cada uma (nessa ordem) e retorna a
    primeira regiao que funcionar, ja no formato {"left","top","width","height"}
    pronto pro mss.grab(). Retorna None se nao achar nenhum rotulo conhecido
    ou se nenhuma das regioes candidatas tiver um numero legivel - nesse
    caso, rode debug_dump_deteccoes() pra investigar o motivo.
    """
    img, offset_left, offset_top = _grab_top_half()
    img_busca = _preprocess_busca(img, scale=_ESCALA_BUSCA)
    try:
        data = pytesseract.image_to_data(
            img_busca, output_type=pytesseract.Output.DICT, config="--psm 11"
        )
    except Exception:
        return None

    candidatos_label = []
    for i, texto in enumerate(data["text"]):
        if _normaliza(texto) in _LABELS_ALVO:
            candidatos_label.append(
                {
                    # divide pela escala pra voltar as coordenadas do
                    # tamanho real da tela (a busca roda numa versao
                    # ampliada da imagem, mas a captura final do valor
                    # precisa ser feita nas coordenadas reais da tela)
                    "left": data["left"][i] // _ESCALA_BUSCA,
                    "top": data["top"][i] // _ESCALA_BUSCA,
                    "width": data["width"][i] // _ESCALA_BUSCA,
                    "height": data["height"][i] // _ESCALA_BUSCA,
                }
            )

    for label in candidatos_label:
        l, t, w, h = label["left"], label["top"], label["width"], label["height"]
        altura_regiao = (h * _MULT_ALTURA) + _ALTURA_EXTRA * 2

        regiao_direita = {
            "left": offset_left + l + w,
            "top": offset_top + t - _ALTURA_EXTRA,
            "width": _LARGURA_VALOR,
            "height": altura_regiao,
        }
        regiao_abaixo = {
            "left": offset_left + max(l - 30, 0),
            "top": offset_top + max(t - _ALTURA_EXTRA, 0),
            "width": _LARGURA_VALOR,
            "height": altura_regiao,
        }
        regiao_acima = {
            "left": offset_left + max(l - 30, 0),
            "top": offset_top + max(t - altura_regiao, 0),
            "width": _LARGURA_VALOR,
            "height": altura_regiao,
        }
        regiao_esquerda = {
            "left": offset_left + max(l - _LARGURA_VALOR, 0),
            "top": offset_top + t - _ALTURA_EXTRA,
            "width": _LARGURA_VALOR,
            "height": altura_regiao,
        }

        for regiao in (regiao_abaixo, regiao_direita, regiao_acima, regiao_esquerda):
            if regiao["width"] <= 0 or regiao["height"] <= 0:
                continue
            try:
                with mss.mss() as sct:
                    raw = sct.grab(regiao)
                    candidato_img = Image.frombytes(
                        "RGB", raw.size, raw.bgra, "raw", "BGRX"
                    )
            except Exception:
                continue
            if ocr_saldo_from_image(candidato_img) is not None:
                return regiao

    return None


def grab_saldo_region(cfg=None):
    """
    Screenshot RAPIDO (sem OCR) so da area do saldo.

    Usa a regiao em cache (achada antes via find_saldo_region). Se ainda
    nao tiver cache, tenta achar agora. Se nao conseguir achar "SALDO" em
    lugar nenhum da tela, retorna None (saldo fica "desligado" nesse ciclo,
    sem travar o resto do bot).

    O parametro cfg e mantido so por compatibilidade com chamadas antigas -
    nao e mais usado pra calibracao manual do saldo.
    """
    global _cached_region

    if _cached_region is not None:
        try:
            with mss.mss() as sct:
                raw = sct.grab(_cached_region)
                img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            return img
        except Exception:
            _cached_region = None  # cache invalido, cai pra busca completa

    nova_regiao = find_saldo_region()
    if nova_regiao is None:
        return None

    _cached_region = nova_regiao
    try:
        with mss.mss() as sct:
            raw = sct.grab(_cached_region)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        return img
    except Exception:
        return None


def _parse_valor(texto):
    """
    Extrai um numero decimal de um texto tipo 'R$ 1.234,56', '1234.56',
    'US$ 1,234.56' etc. Assume que o ULTIMO separador (virgula ou ponto)
    encontrado e o decimal, e os anteriores sao separador de milhar.
    Retorna float ou None se nao achar nada reconhecivel.
    """
    match = re.search(r"[\d.,]+", texto.strip())
    if not match:
        return None
    bruto = match.group(0).strip(".,")
    if not bruto:
        return None

    if "," in bruto and "." in bruto:
        if bruto.rfind(",") > bruto.rfind("."):
            bruto = bruto.replace(".", "").replace(",", ".")
        else:
            bruto = bruto.replace(",", "")
    elif "," in bruto:
        partes = bruto.split(",")
        if len(partes[-1]) == 2:  # ex: "1234,56" -> decimal
            bruto = bruto.replace(",", ".")
        else:  # ex: "1,234" -> separador de milhar
            bruto = bruto.replace(",", "")

    try:
        return float(bruto)
    except ValueError:
        return None


def _preprocess(img, scale=3):
    """Aumenta e converte pra tons de cinza - melhora bastante a taxa de
    acerto do OCR em textos pequenos tipo saldo de painel de corretora."""
    img = img.convert("L")
    img = ImageOps.autocontrast(img)
    w, h = img.size
    img = img.resize((w * scale, h * scale), Image.LANCZOS)
    return img


def ocr_saldo_from_image(img, tentativas_psm=(7, 8, 6, 11)):
    """
    Roda OCR numa imagem JA CAPTURADA (nao tira novo screenshot - ver
    comentario no topo do arquivo sobre por que a captura e o OCR sao
    separados). Retorna float ou None.

    ESTRATEGIA: roda o OCR em modo "livre" (sem forcar so digitos) em
    varios modos (psm) diferentes, pega TODO o texto que sair, e dentro
    dele procura trechos que parecam numero (sequencias de digitos com
    ponto/virgula no meio). Entre todos os trechos encontrados - somando
    todas as tentativas de psm - fica com o que tiver MAIS digitos, ja que
    o valor do saldo normalmente e o numero mais "comprido" da regiao
    (ex: "409.90" tem 5 digitos, enquanto um icone lido errado por acidente
    vira normalmente 1 ou 2 digitos soltos tipo "7" ou "90"). Isso evita
    que um icone/seta vizinho (ex: "+Deposito", seta de menu) confunda a
    leitura e prevaleca sobre o numero de verdade.
    """
    proc = _preprocess(img)

    melhor_texto = None
    melhor_qtd_digitos = 0

    for psm in tentativas_psm:
        try:
            texto = pytesseract.image_to_string(proc, config=f"--psm {psm}")
        except Exception:
            continue
        for trecho in re.findall(r"\d[\d.,]*\d|\d", texto):
            qtd_digitos = sum(c.isdigit() for c in trecho)
            if qtd_digitos > melhor_qtd_digitos:
                melhor_qtd_digitos = qtd_digitos
                melhor_texto = trecho

    if melhor_texto is not None:
        valor = _parse_valor(melhor_texto)
        if valor is not None:
            return valor

    # fallback: metodo antigo, forcando somente digitos em alguns modos
    whitelist = "0123456789.,"
    for psm in tentativas_psm:
        config = f"--psm {psm} -c tessedit_char_whitelist={whitelist}"
        try:
            texto = pytesseract.image_to_string(proc, config=config)
        except Exception:
            continue
        valor = _parse_valor(texto)
        if valor is not None:
            return valor
    return None


def read_saldo(cfg=None, retries=SALDO_READ_RETRIES, interval=SALDO_READ_INTERVAL):
    """
    Leitura "tudo em um": tira o screenshot da regiao do saldo (achada
    automaticamente ou vinda do cache) e ja roda o OCR, tentando de novo
    (novo screenshot a cada tentativa) se nao conseguir reconhecer nada.
    Usada nos momentos em que NAO tem clique acontecendo bem ali (ex: sem
    sinal / sem confirmacao naquele ciclo, ou pra mostrar o saldo no
    overlay) - onde timing nao e critico e da pra gastar esse tempo todo.

    Se falhar em achar a regiao mesmo depois das tentativas, invalida o
    cache pra forcar uma busca completa nova da proxima vez.
    Retorna float ou None.
    """
    for _ in range(retries):
        img = grab_saldo_region(cfg)
        if img is None:
            time.sleep(interval)
            continue
        valor = ocr_saldo_from_image(img)
        if valor is not None:
            return valor
        time.sleep(interval)

    invalidate_cache()
    return None
