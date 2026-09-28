"""
BOT PRINCIPAL - roda em loop, le a tela, decide e executa.

Numa maquina nova, sem calibracao ainda: pode rodar 'python bot.py' direto.
A janela flutuante abre normal e fica esperando - e so clicar em CALIBRAR
nela, fazer a calibracao, e o robo carrega a configuracao sozinho, sem
precisar reiniciar nada.

Uso normal (com janela flutuante e botao Iniciar/Parar):
  python bot.py
  python bot.py --dry-run                 -> abre a janela, mas nunca clica de verdade
  python bot.py --strategy surf           -> abre a janela ja com "Surf" pre-selecionada

Depois de calibrado, e so escolher a estrategia (Quadrantes, Surf ou Quad3) e
clicar em "INICIAR" na janela flutuante - da pra trocar de estrategia e
ligar/desligar o robo quantas vezes quiser sem fechar o programa nem digitar
comando de novo.

Modo avancado, sem janela (roda direto, sempre ligado, sem botao) - esse modo
PRECISA da calibracao ja pronta de antes, ele nao tem como calibrar por dentro:
  python bot.py --no-overlay --strategy quadrantes
  python bot.py --no-overlay --strategy surf --dry-run

Estrategias disponiveis (arquivos strategy_<nome>.py):
  quadrantes -> compara 2 quadrantes de 5 velas, so opera nos minutos 3/8/13/18...
  quad3      -> segue a tendencia do ultimo topo/fundo, quadrante de 3 velas de 5min
  surf       -> segue a cor da ultima vela fechada, opera em todo minuto

Pressione CTRL+C a qualquer momento pra fechar o programa de vez.
"""
import argparse
import csv
import importlib
import json
import os
import threading
import time
from datetime import datetime, timedelta

import pyautogui

import candles
import notifications
import balance
import gerenciamento
from shared_state import SharedState

CONFIG_PATH = "config.json"
LOG_PATH = "historico.csv"

# Quanto tempo ANTES do horario de entrada a leitura da vela em andamento
# comeca. A "ultima vela da lista" nesse periodo e a vela ATUAL, ainda em
# formacao ate fechar de verdade em entry_time - a decisao e baseada nela,
# monitorada repetidamente ate o fim (ver _read_janela_confirmada), pra
# pegar a cor mais proxima possivel do fechamento real e nao travar numa
# leitura antiga que a vela ainda pode reverter antes de fechar.
PRE_CAPTURE_SECONDS = 1.2

# CONFIRMACAO: em vez de confiar numa unica leitura de tela (que pode pegar
# a vela num instante de ruido/flutuacao, ainda formando ou na borda entre
# duas cores), a decisao real de entrada so e aceita quando a MAIORIA das
# leituras feitas durante toda a janela de confirmacao (uma leitura a cada
# CONFIRM_INTERVAL segundos) concordar: pelo menos CONFIRM_READS leituras
# E pelo menos CONFIRM_MAJORITY_RATIO da janela toda. Isso evita que um
# punhado de leituras isoladas e erradas bem no fim (ex: um marcador da
# corretora sobrepondo a vela por uma fracao de segundo) vire uma entrada
# na direcao contraria ao que a vela foi o tempo todo.
CONFIRM_READS = 4
CONFIRM_INTERVAL = 0.05  # leituras bem mais rapidas, cabem mais perto do fechamento real
CONFIRM_MAJORITY_RATIO = 0.75  # fracao minima da janela toda que precisa concordar com a decisao final

# A maioria final NAO considera mais TODAS as leituras feitas desde o inicio
# da janela de captura (que pode ter pego a vela ainda instavel/mudando la
# no comeco). Em vez disso, so entram no calculo as leituras feitas dentro
# desse intervalo final, deslizante, contado pra tras a partir do momento em
# que a confirmacao encerra - refletindo o estado mais recente e real da
# vela, e nao o historico inteiro da espera.
CONFIRM_WINDOW_SECONDS = 1.0

# CAMADA EXTRA DE RIGOR: como a decisao e sempre em cima da vela AO VIVO
# (ainda em formacao, podendo reverter ate o ultimo instante antes de
# fechar), a MAIORIA sozinha (acima) pode nao ser rigorosa o bastante -
# ela permite ate 25% de leituras discordantes (CONFIRM_MAJORITY_RATIO).
# Por isso, nos ultimos CONFIRM_FINAL_STREAK_SECONDS antes do prazo, a
# exigencia sobe de "maioria" pra "UNANIMIDADE": TODAS as leituras desse
# trechinho final tem que concordar com a decisao majoritaria. Uma unica
# leitura discordante bem no fim (sinal de que a vela pode estar
# revertendo bem na hora) e suficiente pra travar a operacao - mais vale
# nao entrar do que entrar com duvida.
CONFIRM_FINAL_STREAK_SECONDS = 0.3

# Mesma ideia, mas pro texto "analisando: COMPRA/VENDA" exibido no overlay
# ENQUANTO espera (live_probe, chamado num ritmo que acelera conforme a
# entrada se aproxima - ver LIVE_PROBE_* logo abaixo) - so atualiza o texto
# depois que a mesma leitura aparecer PROBE_CONFIRM_READS vezes seguidas,
# pra ele parar de "piscar" ou ficar preso numa leitura antiga por causa de
# ruido momentaneo.
PROBE_CONFIRM_READS = 2

# Ritmo do live_probe (palpite provisorio mostrado no overlay ENQUANTO
# espera, antes da decisao oficial): comeca devagar e vai acelerando
# conforme a entrada se aproxima, pra ultima atualizacao do painel ficar bem
# perto da hora real de entrar, e nao desatualizada por ate 2s nesse
# momento critico. Ver wait_with_countdown().
LIVE_PROBE_INTERVAL_NORMAL = 1.0   # ritmo enquanto falta mais de LIVE_PROBE_THRESHOLD_NEAR segundos
LIVE_PROBE_INTERVAL_NEAR = 0.3     # ritmo entre LIVE_PROBE_THRESHOLD_NEAR e LIVE_PROBE_THRESHOLD_MAX segundos
LIVE_PROBE_THRESHOLD_NEAR = 20.0   # a partir daqui acelera pra LIVE_PROBE_INTERVAL_NEAR
LIVE_PROBE_THRESHOLD_MAX = 8.0     # a partir daqui, sem intervalo minimo - o mais rapido que a captura conseguir

# Margem de seguranca ANTES do entry_time onde a leitura de confirmacao para
# de vez (mesmo que ainda desse tempo pra mais uma leitura). Sem essa margem,
# se a ULTIMA chamada de candles.detect_candle_blobs() (captura de tela +
# processamento de imagem) demorar mais que CONFIRM_INTERVAL, a leitura
# terminava DEPOIS do entry_time - e ai o clique saia atrasado, com a vela
# nova ja tendo nascido antes dele. Com essa margem, o loop de confirmacao
# sempre libera o codigo a tempo de chegar no wait_with_countdown final e
# clicar bem na cabeca da vela, mesmo que uma leitura pontual demore mais.
CLICK_SAFETY_MARGIN = 0.03


def _saldo_async(cfg):
    """
    Dispara a leitura do saldo (balance.read_saldo) numa THREAD SEPARADA e
    retorna IMEDIATAMENTE um "holder" (dict com chave 'valor', comecando em
    None, preenchido quando a leitura terminar).

    Isso e essencial agora que o saldo e achado sozinho na tela (sem
    calibracao manual): a busca pode falhar e tentar de novo varias vezes
    por dentro (ver balance.SALDO_READ_RETRIES), e cada tentativa faz um
    print de tela + OCR - bem mais pesado que a leitura antiga de uma
    regiao ja calibrada e pequena. Se isso bloqueasse o ciclo principal,
    atrasaria a exibicao da intencao de compra/venda no painel e, pior,
    poderia empurrar a janela de confirmacao da vela pra cima da hora de
    entrada, sobrando pouco tempo pra confirmar direito (podendo ate
    inverter a decisao). Rodando em background, o saldo demora o tempo que
    precisar sem nunca atrasar a leitura/decisao da vela.
    """
    holder = {"valor": None}

    def _tarefa():
        holder["valor"] = balance.read_saldo(cfg)

    threading.Thread(target=_tarefa, daemon=True).start()
    return holder


def _publica_saldo_continuamente_async(cfg, state, intervalo=4):
    """
    Mantem o SALDO sempre atualizado em segundo plano, sem depender de
    operacoes fechando. Antes, o saldo so era lido em dois momentos (ao
    ligar o robo, e logo depois de cada operacao fechar) - entre um
    momento e outro, o numero mostrado no painel ficava desatualizado
    (ex: mostrando o saldo de duas operacoes atras). Rodando num loop
    continuo, o saldo mostrado sempre reflete (com no maximo alguns
    segundos de atraso) o valor real da conta.

    Roda pra sempre (thread daemon, junto com o programa), lendo o saldo
    a cada 'intervalo' segundos e publicando no state sempre que a
    leitura der certo. Se uma leitura falhar (ex: popup cobrindo a tela
    naquele instante), so ignora e tenta de novo no proximo ciclo - nao
    trava nem precisa de tratamento especial.
    """
    def _tarefa():
        while True:
            # Nao dispara OCR do saldo enquanto o bot estiver na janela
            # critica de confirmacao da vela (perto do fechamento, leituras
            # a cada 0.05s - ver _read_janela_confirmada). O OCR do saldo
            # pode rodar varias tentativas de Tesseract por leitura (ver
            # balance.ocr_saldo_from_image) e, competindo por CPU/captura de
            # tela bem nesse instante, atrasar as leituras da vela e ate
            # mudar a decisao final. Como essa leitura e so pra exibicao no
            # painel (ja tolera alguns segundos de atraso), so espera um
            # pouco e tenta de novo no proximo ciclo - nao perde nada.
            if state is not None and state.is_confirming():
                time.sleep(0.1)
                continue
            valor = balance.read_saldo(cfg)
            if valor is not None and state:
                state.set_saldo(valor)
            time.sleep(intervalo)

    threading.Thread(target=_tarefa, daemon=True).start()


def load_config():
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    if "entry_delay_seconds" not in cfg:
        # compatibilidade com config.json calibrado em versao anterior
        cfg["entry_delay_seconds"] = 0
    return cfg


def _sincroniza_gr(gr_atual, gr_snapshot_anterior, state):
    """
    Confere se a config de GR mudou desde a ultima vez (usuario ligou/
    desligou, trocou de nivel, trocou de ciclo, ou editou algum campo no
    painel) e, se sim, cria um motor NOVO do zero com a config atual -
    nunca edita um motor ja em andamento por baixo dos panos, pra nao
    misturar regras no meio de um ciclo (o unico jeito de mudar de nivel/
    config e comecando um ciclo novo, de proposito).

    Retorna (gr_atual_atualizado, snapshot_usado_agora) - quem chama deve
    guardar os dois de volta nas variaveis locais do loop.
    """
    snap = state.get_gr_snapshot()
    if not snap["ativo"]:
        return None, snap
    mudou = (
        gr_atual is None
        or snap["nivel"] != gr_snapshot_anterior.get("nivel")
        or snap["ciclo"] != gr_snapshot_anterior.get("ciclo")
        or snap["config"] != gr_snapshot_anterior.get("config")
    )
    if mudou:
        gr_atual = gerenciamento.GerenciamentoRisco(snap["nivel"], snap["config"], ciclo=snap["ciclo"])
        print(f"[GR] motor (re)iniciado - nivel {snap['nivel']}, ciclo {snap['ciclo']}, "
              f"proxima entrada: R${gr_atual.valor_atual():.2f}")
    return gr_atual, snap


def _processa_resultado_gr(gr, state, ganhou):
    """
    Chamado logo apos uma operacao fechar (ver _resolve_pendentes), SE o
    gerenciamento de risco estiver ativo. Avanca o motor e aplica o efeito
    de cada evento (ver docstring de GerenciamentoRisco.registrar_resultado
    pra entender as regras completas de cada modo).
    """
    resultado = gr.registrar_resultado(ganhou)
    evento = resultado["evento"]

    if evento == "continua":
        print(f"[GR] {gr.status_label()} - proxima entrada: R${gr.valor_atual():.2f}")

    elif evento == "ciclo_venceu":
        tipo, lucro = resultado["tipo"], resultado["lucro"]
        if gr.ciclo == "composto":
            saldo_real = state.snapshot().get("saldo")
            if saldo_real is not None:
                gr.reiniciar_ciclo(banca=saldo_real)
                print(f"[GR] ciclo venceu ({tipo}, lucro R${lucro:.2f}) - modo COMPOSTO: "
                      f"reiniciando com banca real R${saldo_real:.2f}. "
                      f"Proxima entrada: R${gr.valor_atual():.2f}")
            else:
                # sem saldo real disponivel ainda (raro) - reinicia com o
                # ultimo investimento configurado, pra nao travar o bot
                gr.reiniciar_ciclo()
                print("[GR] ciclo venceu, mas saldo real ainda nao disponivel - "
                      "reiniciando com o investimento configurado.")
        elif tipo == "soros_limpo":
            print(f"[GR] STOP WIN - soros limpo ({gr.nivel}/{gr.nivel}), lucro R${lucro:.2f}. Pausando.")
            state.pause()
            gr.reiniciar_ciclo()
        else:  # recuperou via gale, modo simples - so reinicia e segue
            print(f"[GR] recuperou via gale, ciclo fechado (lucro R${lucro:.2f}). Seguindo operando.")
            gr.reiniciar_ciclo()

    elif evento == "stop_loss":
        prejuizo = resultado["prejuizo"]
        print(f"[GR] STOP LOSS - estourou o gale (nivel {gr.nivel}). Prejuizo do ciclo: R${prejuizo:.2f}. Parando.")
        state.stop()
        gr.reiniciar_ciclo()

    state.set_gr_status(gr.fase, gr.valor_atual(), gr.lucro_ciclo)


def init_log():
    if not os.path.exists(LOG_PATH):
        with open(LOG_PATH, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "estrategia", "decisao", "cor_resultado", "resultado"])


def log_row(timestamp, estrategia, decisao, saldo_diferenca, resultado):
    with open(LOG_PATH, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([timestamp, estrategia, decisao, saldo_diferenca or "", resultado or ""])


def wait_with_countdown(target_dt, state=None, live_probe=None):
    """
    Espera ate target_dt, mostrando um cronometro regressivo no terminal.

    Se 'state' for passado (SharedState) e o usuario clicar em PARAR durante
    a espera, interrompe na hora e retorna False (sinal pra quem chamou: nao
    prosseguir com essa entrada, voltar pro estado parado). Retorna True se
    chegou ate o horario normalmente.

    Se 'live_probe' for passado (funcao sem argumentos que retorna "buy",
    "sell" ou None), ela e chamada periodicamente durante a espera, e o
    resultado e publicado em state.set_live_signal() - isso e o palpite
    PROVISORIO da estrategia, mostrado no overlay, que vai "flutuando"
    conforme mais velas fecham, ate a decisao oficial ser tomada de verdade
    (mais perto da entrada).

    O ritmo dessas chamadas NAO e fixo - fica mais rapido conforme a entrada
    se aproxima (ver LIVE_PROBE_*): a cada LIVE_PROBE_INTERVAL_NORMAL segundos
    enquanto falta bastante tempo, a cada LIVE_PROBE_INTERVAL_NEAR quando
    faltam LIVE_PROBE_THRESHOLD_NEAR segundos ou menos, e o mais rapido que a
    captura de tela conseguir (sem intervalo minimo) quando faltam
    LIVE_PROBE_THRESHOLD_MAX segundos ou menos - assim a ultima atualizacao
    da intencao mostrada no painel fica a poucos instantes da entrada de
    verdade, em vez de poder ficar ate 2s desatualizada nesse momento critico.
    """
    ultimo_probe = 0.0
    while True:
        if state is not None and not state.is_running():
            print("\r   parado pelo usuario.                    ")
            if state:
                state.set_live_signal(None)
            return False
        now = datetime.now()
        remaining = (target_dt - now).total_seconds()
        if remaining <= 0:
            print("\r   entrando agora...              ")
            return True
        if live_probe is not None and state is not None:
            agora_ts = time.time()
            if remaining <= LIVE_PROBE_THRESHOLD_MAX:
                intervalo_probe = 0.0  # o mais rapido possivel, sem esperar entre leituras
            elif remaining <= LIVE_PROBE_THRESHOLD_NEAR:
                intervalo_probe = LIVE_PROBE_INTERVAL_NEAR
            else:
                intervalo_probe = LIVE_PROBE_INTERVAL_NORMAL
            if agora_ts - ultimo_probe >= intervalo_probe:
                ultimo_probe = agora_ts
                try:
                    sinal = live_probe()
                except Exception:
                    sinal = None
                state.set_live_signal(sinal)
        print(f"\r   proxima entrada em {remaining:5.1f}s   ", end="", flush=True)
        time.sleep(min(remaining, 0.2))


def next_entry_time(cfg, is_entry_minute):
    """
    Proximo horario de entrada valido para a estrategia atual (definido por
    is_entry_minute). A entrada acontece 'entry_delay_seconds' segundos DEPOIS
    desse minuto COMECAR (ou seja, logo no primeiro segundo da vela nova, nao
    antes dela abrir).
    """
    now = datetime.now() + timedelta(seconds=cfg["clock_offset_seconds"])
    candidate = now.replace(second=0, microsecond=0)

    for _ in range(180):
        entry = candidate + timedelta(seconds=cfg["entry_delay_seconds"])
        if is_entry_minute(candidate.minute) and entry > now:
            return entry - timedelta(seconds=cfg["clock_offset_seconds"])
        candidate += timedelta(minutes=1)

    raise RuntimeError("Nao encontrei o proximo horario de entrada - confira o relogio do sistema")


def execute(direction, cfg, dry_run, state=None, valor=None):
    label = "COMPRA" if direction == "buy" else "VENDA"
    emoji = "🔼" if direction == "buy" else "🔽"
    ativo = cfg.get("ativo", "").strip()
    hora = datetime.now().strftime("%H:%M:%S")
    print(f"[{hora}] Decisao: {label}" + (f"  (valor: R${valor:.2f})" if valor is not None else ""))

    # Clica PRIMEIRO, o mais proximo possivel do entry_time (cabeca da vela).
    # A notificacao vem depois de propositio - ela pode envolver rede/IO e
    # se rodasse antes do clique, atrasava a entrada de verdade em cima da
    # vela, fazendo o clique cair depois da vela nova ja ter aberto.
    if not dry_run:
        x, y = cfg["buy_button"] if direction == "buy" else cfg["sell_button"]
        pyautogui.click(x, y)
        # Avisa o overlay NA HORA (sem esperar o proximo refresh periodico
        # dele, que roda a cada 150ms) que um clique de verdade acabou de
        # acontecer. O clique costuma levar o foco/z-order do navegador da
        # corretora pra frente por um instante, fazendo o painel flutuante
        # (mesmo com -topmost) parecer "sumir e voltar" ate o overlay
        # reforcar -topmost de novo sozinho - esse aviso fecha essa lacuna
        # na hora, em vez de deixar o usuario ver esse piscar. Ver
        # SharedState.notify_click() e o callback registrado em overlay.py.
        if state is not None:
            state.notify_click()
    else:
        print("   (dry-run: nao clicou de verdade)")

    linha_ativo = f"📈 Ativo: {ativo}\n" if ativo else ""
    notifications.notify(
        cfg,
        f"{emoji} Entrando em {label}\n"
        f"{linha_ativo}"
        f"🕒 Horario: {hora}"
    )




def _agenda_leitura_saldo_fechamento(item, cfg):
    """
    Agenda, numa THREAD SEPARADA, a leitura do saldo que vai fechar essa
    operacao ('saldo depois'). A thread DORME ate item["close_time"] e SO
    ENTAO dispara o OCR - capturando o saldo o mais perto possivel do
    fechamento real, escrito no proprio item (item["saldo_depois_holder"]).

    Por que isso existe: antes, essa leitura so acontecia bem DEPOIS, no
    meio do ciclo seguinte, logo apos o clique da PROXIMA operacao. Com
    entradas rapidas (ex: estrategia surf com expiracao curta), close_time
    dessa operacao e entry_time da proxima ficam bem colados - e o clique
    da proxima ja debita a aposta nova da conta ANTES dessa leitura
    rodar. Resultado: a leitura de fechamento vinha contaminada pelo valor
    da aposta seguinte, o delta de saldo saia errado (saldo "nem sobe nem
    desce") e o placar de ganhas/perdidas contava errado.

    Agendando a leitura pra disparar sozinha, em background, exatamente em
    close_time (antes de qualquer clique novo poder acontecer, ja que
    close_time de uma operacao e sempre um pouco ANTES do entry_time da
    proxima - a diferenca e exatamente entry_delay_seconds), a leitura fica
    limpa - sem depender de quando o ciclo seguinte por acaso chama
    _resolve_pendentes.

    NOTA: se entry_delay_seconds no config.json estiver em 0, close_time
    da operacao anterior colide EXATAMENTE com o entry_time da proxima -
    nesse caso especifico nao ha janela nenhuma pra separar as duas
    leituras (limitacao real, nao da pra corrigir so no codigo). Um
    entry_delay_seconds pequeno mas positivo (1-2s) garante folga.
    """
    holder = {"valor": None, "pronto": False}

    def _tarefa():
        delay = (item["close_time"] - datetime.now()).total_seconds()
        if delay > 0:
            time.sleep(delay)
        holder["valor"] = balance.read_saldo(cfg)
        holder["pronto"] = True

    threading.Thread(target=_tarefa, daemon=True).start()
    item["saldo_depois_holder"] = holder


def _resolve_pendentes(pendentes, cfg, state, contadores, gr=None):
    """
    Resolve a pendencia mais antiga (a operacao ja executada, esperando
    fechar) comparando o SALDO DEPOIS que a operacao fechou com o SALDO DE
    ANTES (guardado em item["saldo_antes_holder"] no momento do clique) -
    em vez de comparar a cor da vela com a direcao operada. O saldo e a
    fonte real de verdade (reflete o resultado de verdade que a corretora
    aplicou), entao nao ha mais necessidade de analisar cor de vela pra
    decidir o placar:
      saldo subiu   -> ganhou
      saldo caiu    -> perdeu
      saldo igual   -> empate (nao conta nem como ganho nem como perda)
    So resolve UMA pendencia por ciclo (a mais antiga que ja passou do
    close_time). Se o saldo nao conseguir ser lido (ex: popup cobrindo a
    tela nesse instante - balance.read_saldo ja tenta de novo internamente
    algumas vezes antes de desistir), marca indeterminado e tenta a
    proxima pendencia no proximo ciclo.
    """
    if not pendentes:
        return pendentes

    agora = datetime.now()
    item = pendentes[0]
    if agora < item["close_time"]:
        return pendentes

    # O saldo DEPOIS agora vem da leitura agendada em background (ver
    # _agenda_leitura_saldo_fechamento, chamada quando esse item foi
    # criado) - capturada o mais perto possivel do close_time de verdade,
    # sem risco de vir contaminada por uma entrada nova que abriu logo em
    # seguida. Se por algum motivo o item nao tiver esse agendamento (ex:
    # pendencia antiga, de antes dessa mudanca), cai no metodo antigo como
    # reserva.
    holder_depois = item.get("saldo_depois_holder")
    if holder_depois is not None:
        if not holder_depois["pronto"]:
            # a leitura agendada ainda esta rodando - nao trava esperando
            # aqui, so tenta de novo no proximo ciclo
            return pendentes
        saldo_atual = holder_depois["valor"]
    else:
        saldo_atual = balance.read_saldo(cfg)

    holder = item.get("saldo_antes_holder")
    saldo_antes = holder["valor"] if holder else None

    if saldo_atual is None or saldo_antes is None:
        print(f"   -> nao consegui confirmar o resultado de {item['timestamp']} (saldo nao disponivel) - marcado indeterminado")
        log_row(item["timestamp"], item["estrategia"], item["decisao"], "", "indeterminado")
        return pendentes[1:]

    if state:
        state.set_saldo(saldo_atual)

    diferenca = saldo_atual - saldo_antes

    if diferenca == 0:
        print(f"   -> resultado de {item['timestamp']} ({item['decisao']}): empate (saldo nao mudou)")
        if state:
            state.set_ultimo_resultado("tie", diferenca)
        log_row(item["timestamp"], item["estrategia"], item["decisao"], f"{diferenca:.2f}", "empate")
        return pendentes[1:]

    ganhou = diferenca > 0
    if ganhou:
        contadores["ganhas"] += 1
    else:
        contadores["perdidas"] += 1
    if state:
        state.add_resultado(ganhou, valor=diferenca)
        state.set_ultimo_resultado("gain" if ganhou else "loss", diferenca)
    if gr is not None:
        _processa_resultado_gr(gr, state, ganhou)
    resultado_txt = "GANHOU" if ganhou else "PERDEU"
    print(f"   -> resultado de {item['timestamp']} ({item['decisao']}): saldo {saldo_antes:.2f} -> {saldo_atual:.2f} "
          f"({'+' if diferenca >= 0 else ''}{diferenca:.2f}) [{resultado_txt}]")
    print(f"   PLACAR: {contadores['ganhas']} ganhas | {contadores['perdidas']} perdidas")
    log_row(item["timestamp"], item["estrategia"], item["decisao"], f"{diferenca:.2f}", "ganhou" if ganhou else "perdeu")
    resultado_emoji = "✅" if ganhou else "❌"
    direcao_txt = "COMPRA" if item["decisao"] == "buy" else "VENDA"
    direcao_emoji = "🔼" if item["decisao"] == "buy" else "🔽"
    hora_entrada = item["timestamp"].split(" ")[-1]  # so o HH:MM:SS
    total_ops = contadores["ganhas"] + contadores["perdidas"]
    assertividade = (100 * contadores["ganhas"] / total_ops) if total_ops else 0
    notifications.notify(
        cfg,
        f"{direcao_emoji} Entrada {hora_entrada} - {direcao_txt}\n"
        f"{resultado_emoji} Resultado: {resultado_txt}\n"
        f"📊 Placar: {contadores['ganhas']}G / {contadores['perdidas']}P\n"
        f"🎯 Assertividade: {assertividade:.1f}%"
    )
    return pendentes[1:]


def _read_janela_confirmada(cfg, strategy, deadline, state=None):
    """
    Le a vela EM ANDAMENTO repetidamente ate perto do fechamento real dela
    (proximo de 'deadline', que e o entry_time - ou seja, o segundo 59 da
    vela atual).

    A decisao final NAO e baseada so na ultima leitura (nem so numa streak
    curta de leituras seguidas): e a MAIORIA das leituras validas feitas
    dentro do ULTIMO CONFIRM_WINDOW_SECONDS antes do fim da janela (janela
    deslizante, recalculada aqui na hora de decidir) - nao mais o historico
    inteiro desde o inicio da captura. Isso evita que uma leitura tirada bem
    no comeco da espera (quando a vela podia ainda estar instavel/mudando)
    dilua uma leitura que ja estava claramente estabilizada no final. So
    confirma se essa maioria for forte (pelo menos CONFIRM_MAJORITY_RATIO das
    leituras da janela final E pelo menos CONFIRM_READS leituras concordando).

    Isso existe porque uma unica checagem por streak curta (poucas leituras
    seguidas bem no fim) e fragil demais: qualquer interferencia rapida na
    tela nesse instante final - por exemplo o marcador/tooltip que a propria
    corretora desenha em cima do grafico pra mostrar uma operacao aberta
    (ex: "10 R$ 00:26") - pode empurrar so essas ultimas leituras pra cor
    errada e fazer o robo "confirmar" uma decisao contraria ao que a vela
    foi de verdade o tempo todo. Usando a maioria da janela inteira, esse
    tipo de ruido pontual de ultima hora deixa de conseguir virar a decisao
    sozinho.

    Se 'state' for passado, o painel e atualizado EM TEMPO REAL a cada
    leitura desse loop (nao so no _live_probe de antes da captura) - assim
    a intencao mostrada no overlay vai mudando junto com o que o bot esta
    de fato enxergando na vela em andamento.

    Se a maioria nao for forte o suficiente (cor "em duvida", sem
    consenso), retorna confirmado=False - quem chamou decide nao operar
    nessa vela, que e o comportamento seguro esperado.

    Retorna (blobs, janela, decisao, confirmado) - blobs e janela sao da
    ULTIMA foto tirada (o mesmo blobs tambem serve pra resolver pendencias);
    decisao e a MAJORITARIA (nao necessariamente a ultima lida).
    """
    precisa = strategy.WINDOW_SIZE + strategy.SKIP_FORMING
    leituras = []  # (timestamp, decisao) de todas as leituras validas (nao-None), em ordem
    ultimos_blobs, ultima_janela, ultima_decisao = [], None, None

    while True:
        blobs = candles.detect_candle_blobs(cfg)
        ultimos_blobs = blobs

        if len(blobs) >= precisa:
            janela = (
                blobs[-precisa:-strategy.SKIP_FORMING]
                if strategy.SKIP_FORMING > 0 else blobs[-strategy.WINDOW_SIZE:]
            )
            decisao = strategy.decide(janela)
            ultima_janela, ultima_decisao = janela, decisao
            leituras.append((time.time(), decisao))
            if state:
                state.set_live_signal(decisao)  # mostra a intencao atual no painel, mesmo antes de confirmar
        else:
            # sem velas suficientes na tela agora - nao conta como leitura valida
            ultima_janela, ultima_decisao = None, None
            if state:
                state.set_live_signal(None)

        if datetime.now() + timedelta(seconds=CONFIRM_INTERVAL) >= deadline:
            confirmado = False
            decisao_final = ultima_decisao
            motivo_falha = None
            if leituras:
                corte = leituras[-1][0] - CONFIRM_WINDOW_SECONDS
                leituras_janela = [d for ts, d in leituras if ts >= corte]
                contagem = {}
                for d in leituras_janela:
                    contagem[d] = contagem.get(d, 0) + 1
                decisao_majoritaria, votos = max(contagem.items(), key=lambda kv: kv[1])
                if votos >= CONFIRM_READS and votos / len(leituras_janela) >= CONFIRM_MAJORITY_RATIO:
                    # Maioria bateu - mas como e vela AO VIVO (pode reverter ate
                    # o ultimo instante), exige uma segunda camada bem mais
                    # rigorosa antes de confirmar de vez: nos ultimos
                    # CONFIRM_FINAL_STREAK_SECONDS, TODAS as leituras (sem
                    # excecao) tem que concordar com a maioria - unanimidade,
                    # nao so maioria. Qualquer leitura destoante bem no fim
                    # trava a operacao.
                    corte_final = leituras[-1][0] - CONFIRM_FINAL_STREAK_SECONDS
                    leituras_finais = [d for ts, d in leituras if ts >= corte_final]
                    if leituras_finais and all(d == decisao_majoritaria for d in leituras_finais):
                        confirmado = True
                        decisao_final = decisao_majoritaria
                    else:
                        motivo_falha = "reversao/instabilidade na reta final"
                else:
                    motivo_falha = "sem maioria clara na janela final"
            if state and not confirmado:
                # decidiu nao operar em cima da hora - deixa isso claro no
                # painel em vez de manter a ultima intencao "flutuando"
                if motivo_falha:
                    state.set_status(f"nao operou ({motivo_falha})")
                else:
                    state.set_status("indefinido no fechamento - nao operou")
                state.set_live_signal(None)
            return ultimos_blobs, ultima_janela, decisao_final, confirmado

        time.sleep(CONFIRM_INTERVAL)


def _do_one_entry(cfg, args, strategy, strategy_name, state, pendentes, contadores, gr=None):
    """
    Um ciclo completo: espera ate pouco antes do horario de entrada, tira a
    foto e decide (com a vela nova ainda fechada pra ela mesma, isto e, ainda
    NAO aberta), espera o resto ate o horario exato e so ai executa o clique.
    Retorna a lista de pendentes atualizada. Se o usuario apertar PARAR
    durante qualquer uma das esperas, retorna None (sinal pra quem chamou
    voltar pro estado parado sem fazer mais nada neste ciclo).

    'gr' (opcional): motor de GerenciamentoRisco ja pronto (ver
    _sincroniza_gr em run_supervised) - se vier preenchido, o VALOR da
    entrada usa gr.valor_atual() em vez do padrao fixo, e o resultado
    (ganhou/perdeu) e repassado a ele em _resolve_pendentes.
    """
    # Pro surf, a entrada acontece a cada N minutos (N = expiracao escolhida
    # no painel, 1 a 15) - so nos minutos multiplos de N - em vez de sempre
    # todo minuto. Com N=1 continua entrando todo minuto (comportamento
    # original). A logica de decisao continua a mesma (olha a ultima vela
    # FECHADA e segue a cor dela) - so muda em quais minutos o bot considera
    # "hora de entrar".
    is_entry_minute = strategy.IS_ENTRY_MINUTE
    if strategy_name == "surf" and state is not None:
        n = state.get_surf_expiracao()
        if n > 1:
            is_entry_minute = lambda minute, _n=n: minute % _n == 0

    entry_time = next_entry_time(cfg, is_entry_minute)
    capture_time = entry_time - timedelta(seconds=PRE_CAPTURE_SECONDS)
    print(f"\nProxima entrada as {entry_time.strftime('%H:%M:%S')}...")
    if state:
        state.set_entry_time(entry_time)
        state.set_live_signal(None)  # comeca zerado a cada novo ciclo

    # Saldo ANTES da operacao (baseline pra depois calcular ganho/perda real
    # comparando com o saldo lido quando ela fechar). Le AQUI, logo no
    # comeco do ciclo - onde sobram varios segundos ate a hora critica de
    # decidir/clicar - de proposito. OCR (pytesseract) pode demorar mais de
    # 1-3s dependendo da maquina, e ler isso perto da hora da entrada
    # atrasava o clique de verdade (bug ja identificado e corrigido: o
    # clique estava saindo alguns segundos depois do horario certo).
    saldo_antes_holder = _saldo_async(cfg)

    # Ajusta o VALOR do investimento (gerenciamento de risco) AQUI, logo no
    # comeco do ciclo - de proposito, igual ao saldo acima. O valor calculado
    # por gr.valor_atual() so depende do resultado da operacao ANTERIOR, nao
    # da vela atual - entao nao ha motivo nenhum pra deixar isso pra ultima
    # hora. Fazer isso dentro de execute() (como era antes) fazia o
    # clique+digitacao no campo de valor competir por tempo bem em cima da
    # janela de confirmacao da vela (que e bem apertada de proposito - ver
    # CONFIRM_INTERVAL/CLICK_SAFETY_MARGIN la em cima) - o suficiente pra
    # empurrar leituras pra fora do prazo e causar "sem confirmacao" toda
    # hora depois da primeira operacao. Ajustando aqui, sobra o ciclo quase
    # inteiro de folga, sem nenhuma chance de atrapalhar a leitura da vela.
    # Resolve o resultado da operacao ANTERIOR primeiro - so DEPOIS disso e
    # que gr.valor_atual() reflete de verdade o resultado mais recente (sem
    # essa ordem, o valor calculado ficava sempre um ciclo ATRASADO, usando
    # o resultado de duas operacoes atras em vez da que acabou de fechar -
    # causando inconsistencia entre o que o painel mostra e o valor real
    # aplicado). A leitura de saldo de fechamento dela ja foi agendada em
    # background no ciclo anterior, entao normalmente isso so pega um
    # resultado que ja estava pronto, sem nenhuma espera aqui.
    pendentes = _resolve_pendentes(pendentes, cfg, state, contadores, gr=gr)

    valor_gr = None
    if gr is not None:
        import math
        valor_gr = gr.valor_atual()
        valor_inteiro = math.ceil(valor_gr)
        if not args.dry_run and cfg.get("valor_field"):
            vx, vy = cfg["valor_field"]
            pyautogui.click(vx, vy)
            pyautogui.hotkey("ctrl", "a")
            pyautogui.press("backspace")
            pyautogui.write(str(valor_inteiro))
            pyautogui.press("tab")
            print(f"   [GR] valor da entrada ajustado para R${valor_inteiro} (calculado: R${valor_gr:.2f})")
        else:
            print(f"   [GR] (dry-run ou sem valor_field calibrado) valor que seria usado: R${valor_inteiro} (calculado: R${valor_gr:.2f})")


    # estado do debounce do live_probe, reiniciado a cada novo ciclo de vela
    probe_state = {"ultima_leitura": None, "streak": 0, "confirmado": None}

    def _live_probe():
        """
        Reavaliacao PROVISORIA, chamada periodicamente enquanto espera - usa
        o que estiver detectavel na tela agora mesmo, sem garantia de ser a
        janela final (pode estar incompleta ou ainda mudar). E so pra dar um
        palpite "flutuando" no overlay ate a decisao oficial de verdade,
        tomada mais perto da entrada (mesma logica de sempre, ali embaixo).

        Pra nao ficar "piscando" ou preso numa leitura antiga por causa de
        ruido/flutuacao momentanea de um unico frame, so atualiza o palpite
        exibido depois que a mesma leitura se repetir PROBE_CONFIRM_READS
        vezes seguidas (contando entre chamadas - essa funcao roda num ritmo
        que acelera conforme a entrada se aproxima, ver LIVE_PROBE_* e
        wait_with_countdown()).
        """
        blobs_provisorio = candles.detect_candle_blobs(cfg)
        precisa_provisorio = strategy.WINDOW_SIZE + strategy.SKIP_FORMING
        if len(blobs_provisorio) < precisa_provisorio:
            probe_state["ultima_leitura"], probe_state["streak"] = None, 0
            return probe_state["confirmado"]

        janela_provisoria = (
            blobs_provisorio[-precisa_provisorio:-strategy.SKIP_FORMING]
            if strategy.SKIP_FORMING > 0 else blobs_provisorio[-strategy.WINDOW_SIZE:]
        )
        leitura = strategy.decide(janela_provisoria)

        if leitura == probe_state["ultima_leitura"]:
            probe_state["streak"] += 1
        else:
            probe_state["ultima_leitura"], probe_state["streak"] = leitura, 1

        if probe_state["streak"] >= PROBE_CONFIRM_READS:
            probe_state["confirmado"] = leitura
        return probe_state["confirmado"]

    # 1) Espera ate pouco ANTES da entrada - nesse instante a vela nova ainda
    #    nao abriu, entao a ultima vela da lista e garantidamente a ultima
    #    ja fechada de verdade. Durante essa espera, o palpite provisorio vai
    #    "flutuando" no overlay conforme mais velas fecham.
    if not wait_with_countdown(capture_time, state, live_probe=_live_probe):
        return None

    if state is not None and not state.is_running():
        return None  # foi parado durante a espera da pre-captura

    # SO AGORA comeca o trecho realmente critico em tempo: a leitura de
    # confirmacao final, que roda a cada CONFIRM_INTERVAL (0.05s) ate o
    # fechamento da vela. A flag 'confirming' fica ligada SO durante esse
    # trecho curto (nao durante a espera de live_probe acima, que e bem
    # mais longa - pode durar quase o ciclo inteiro - e ja roda num ritmo
    # bem mais lento). Deixar a flag ligada tambem durante o live_probe
    # prendia a thread de saldo num loop de checagem apertado por quase
    # todo o ciclo, e a troca de contexto extra disso acabava atrasando ate
    # a atualizacao da intencao provisoria (COMPRA/VENDA) no painel - por
    # isso a janela protegida aqui e a mais estreita possivel.
    if state is not None:
        state.set_confirming(True)
    try:
        # UMA UNICA "rodada" de fotos usada tanto pra resolver a pendencia quanto
        # pra decidir a proxima entrada - evita capturas divergentes quase
        # simultaneas. Agora exige confirmacao (CONFIRM_READS leituras seguidas
        # com a mesma decisao) antes de aceitar a cor como definitiva, em vez de
        # confiar numa unica foto que pode ter pego a vela num instante de ruido.
        blobs, janela, decision, confirmado = _read_janela_confirmada(
            cfg, strategy, deadline=entry_time - timedelta(seconds=CLICK_SAFETY_MARGIN), state=state
        )
    finally:
        if state is not None:
            state.set_confirming(False)

    total_detectado = len(blobs)
    precisa = strategy.WINDOW_SIZE + strategy.SKIP_FORMING
    if total_detectado < precisa:
        print(f"So detectei {total_detectado} velas na tela (preciso de pelo menos {precisa}) - pulando.")
        if state:
            state.set_status(f"so {total_detectado} velas detectadas...")
        # ainda assim espera ate a entrada, pra nao dessincronizar o proximo ciclo
        wait_with_countdown(entry_time, state)
        # resolve a pendencia so AGORA, bem na hora que ela realmente fecha -
        # ver comentario detalhado la embaixo, apos o execute()
        pendentes = _resolve_pendentes(pendentes, cfg, state, contadores, gr=gr)
        return pendentes

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not confirmado:
        # leitura instavel ate o fim do prazo (a cor mudou/oscilou demais pra
        # bater CONFIRM_READS leituras seguidas iguais) - isso e sinal de
        # possivel erro de leitura, entao NAO opera nessa vela por seguranca.
        # Avisa no painel e passa pra proxima vela.
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Leitura sem confirmacao -> nao operou, pulando pra proxima vela.")
        if state:
            state.set_status("sem confirmacao na leitura - nao operou, indo pra proxima vela")
            state.set_live_signal(None)
        log_row(timestamp, strategy_name, "sem_confirmacao", "", "")
        wait_with_countdown(entry_time, state)
        pendentes = _resolve_pendentes(pendentes, cfg, state, contadores, gr=gr)
        return pendentes

    if state:
        state.set_live_signal(decision)  # a partir daqui e a decisao "quase oficial"

    if decision is None:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Sem sinal -> sem operacao.")
        log_row(timestamp, strategy_name, "none", "", "")
        wait_with_countdown(entry_time, state)
        pendentes = _resolve_pendentes(pendentes, cfg, state, contadores, gr=gr)
        return pendentes

    # Saldo ANTES ja comecou a ser lido em BACKGROUND la no comeco do ciclo
    # (ver saldo_antes_holder mais acima) - roda numa thread separada, entao
    # mesmo que o OCR ainda nao tenha terminado, isso nao atrasa nem um
    # pouco o que vem a seguir (nenhuma leitura de tela nem OCR acontecendo
    # aqui, perto da hora critica).

    # 2) So agora espera o restante ate o horario EXATO da entrada, na cabeca
    #    da vela nova, e clica.
    if not wait_with_countdown(entry_time, state):
        return None

    if state is not None and not state.is_running():
        return None  # foi parado bem na hora exata da entrada

    # PAUSADO: o robo continua ligado, calculando e mostrando a intencao da
    # proxima vela normalmente (state.set_decision abaixo), mas NAO clica de
    # verdade nem registra a operacao como pendente (nao ha nada real pra
    # resolver depois) - ver botao PAUSAR/RETOMAR no overlay.
    if state is not None and state.is_paused():
        label = "COMPRA" if decision == "buy" else "VENDA"
        print(f"[{datetime.now().strftime('%H:%M:%S')}] PAUSADO - intencao: {label} (sem clicar)")
        state.set_decision(decision)
        state.set_live_signal(None)
        pendentes = _resolve_pendentes(pendentes, cfg, state, contadores, gr=gr)
        return pendentes

    execute(decision, cfg, args.dry_run, state, valor=valor_gr)
    if state:
        state.set_decision(decision)
        state.set_live_signal(None)  # ja executou - o palpite provisorio nao serve mais

    # Resolve a pendencia anterior LOGO APOS o clique. A leitura de fechamento
    # dela mesma ja foi agendada em background la no ciclo anterior (ver
    # _agenda_leitura_saldo_fechamento), entao normalmente essa chamada so
    # pega o resultado que ja estava pronto - sem nenhuma leitura de tela
    # acontecendo bem aqui.
    pendentes = _resolve_pendentes(pendentes, cfg, state, contadores, gr=gr)

    # entry_time = abertura_da_vela + entry_delay_seconds, entao o fechamento
    # da vela de entrada e (60 * expiracao_min) depois da abertura. Pro surf,
    # a expiracao (1 a 15 minutos) e escolhida no painel - mesma estrategia,
    # so muda quanto tempo a operacao demora pra fechar.
    expiracao_min = 1
    if strategy_name == "surf" and state is not None:
        expiracao_min = state.get_surf_expiracao()
    close_time = entry_time + timedelta(seconds=60 * expiracao_min - cfg["entry_delay_seconds"])
    novo_item = {
        "timestamp": timestamp,
        "estrategia": strategy_name,
        "decisao": decision,
        "close_time": close_time,
        "saldo_antes_holder": saldo_antes_holder,
    }
    # Agenda AGORA a leitura do saldo que vai fechar ESSA operacao - a
    # thread dorme sozinha ate close_time e le naquela hora certa, bem
    # antes de qualquer clique futuro poder contaminar o valor (ver
    # docstring de _agenda_leitura_saldo_fechamento pra entender o problema
    # que isso resolve).
    _agenda_leitura_saldo_fechamento(novo_item, cfg)
    pendentes.append(novo_item)
    return pendentes


def run_loop_legacy(cfg, args, strategy, strategy_name):
    """Modo --no-overlay: roda pra sempre com uma unica estrategia fixa, sem botao de liga/desliga."""
    init_log()
    print(f"Bot iniciado com a estrategia '{strategy_name}'. CTRL+C para parar.")
    print(f"Historico sendo salvo em: {os.path.abspath(LOG_PATH)}")
    if args.dry_run:
        print(">>> MODO DRY-RUN: nenhum clique real sera feito <<<")

    pendentes = []
    contadores = {"ganhas": 0, "perdidas": 0}
    while True:
        pendentes = _do_one_entry(cfg, args, strategy, strategy_name, None, pendentes, contadores)
        if pendentes is None:
            pendentes = []  # nunca deveria acontecer sem state, mas por seguranca


def run_supervised(cfg, args, state):
    """
    Modo com janela flutuante: fica de olho no botao Iniciar/Parar e na
    estrategia selecionada. So opera de verdade quando state.is_running().
    Troca de estrategia e permitida (o usuario so consegue mexer nos radio
    buttons enquanto o bot esta parado) - ao trocar, zera as pendencias
    antigas pra nao misturar resultado de uma estrategia com a outra.
    """
    init_log()
    print("Bot pronto. Escolha a estrategia e clique em INICIAR na janela flutuante.")
    print(f"Historico sendo salvo em: {os.path.abspath(LOG_PATH)}")
    if args.dry_run:
        print(">>> MODO DRY-RUN: nenhum clique real sera feito <<<")

    _publica_saldo_continuamente_async(cfg, state)

    pendentes = []
    contadores = {"ganhas": 0, "perdidas": 0}
    strategy = None
    strategy_name = None
    gr = None
    gr_snapshot_anterior = {}
    estava_rodando = False

    while True:
        rodando = state.is_running()

        if rodando and not estava_rodando:
            # Acabou de ligar (Iniciar clicado) - reseta TUDO do zero, nao
            # importa como parou da ultima vez (usuario clicou Parar, ou o
            # gerenciamento bateu STOP LOSS/STOP WIN sozinho). Isso evita
            # carregar pendencia antiga (de uma operacao que ficou no ar
            # quando parou) ou um ciclo de gerenciamento pela metade.
            pendentes = []
            gr = None
            gr_snapshot_anterior = {}
            print("\n=== LIGADO - tudo resetado (pendencias e gerenciamento do zero) ===")
        estava_rodando = rodando

        if not rodando:
            state.set_status("parado - selecione a estrategia e aperte Iniciar")
            time.sleep(0.2)
            continue

        nome_selecionado = state.get_strategy()
        if nome_selecionado != strategy_name:
            strategy = importlib.import_module(f"strategy_{nome_selecionado}")
            strategy_name = nome_selecionado
            pendentes = []
            print(f"\n=== Estrategia ativa: {strategy_name} ===")

        gr, gr_snapshot_anterior = _sincroniza_gr(gr, gr_snapshot_anterior, state)

        resultado = _do_one_entry(cfg, args, strategy, strategy_name, state, pendentes, contadores, gr=gr)
        pendentes = resultado if resultado is not None else pendentes


def _run_supervised_safe(cfg, args, state):
    """Roda run_supervised protegido - se der erro fatal, avisa bem claro e encerra
    tudo (inclusive a janela flutuante), em vez de deixar o overlay congelado
    com informacao antiga sem o usuario perceber que o robo parou."""
    try:
        run_supervised(cfg, args, state)
    except Exception as e:
        msg = f"ERRO FATAL: {e}"
        print(f"\n[{msg}]")
        print("Dica comum: config.json esta zerado/errado - rode 'python calibrate.py' de novo.")
        state.set_status(msg)
        state.stop()
        os._exit(1)


def _wait_for_config_and_run(args, state):
    """
    Espera o config.json aparecer antes de ligar o robo de verdade. Isso
    permite abrir o bot.py numa maquina nova, SEM calibracao ainda - a
    janela flutuante abre normal, e o usuario pode calibrar clicando no
    botao CALIBRAR dela. Assim que o config.json e criado, o robo carrega
    ele e passa a funcionar normalmente, sem precisar reiniciar nada.
    """
    avisou = False
    while not os.path.exists(CONFIG_PATH):
        if not avisou:
            state.set_status("sem calibracao ainda - clique em CALIBRAR na janela")
            avisou = True
        time.sleep(0.5)
    try:
        cfg = load_config()
    except Exception as e:
        state.set_status(f"ERRO ao ler config.json: {e}")
        return
    _run_supervised_safe(cfg, args, state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="So mostra a decisao, nao clica")
    parser.add_argument("--strategy", default="quadrantes", help="Estrategia inicial (quadrantes ou surf)")
    parser.add_argument("--no-overlay", action="store_true", help="Roda sem janela flutuante nem botao, direto com a estrategia escolhida")
    args = parser.parse_args()

    cfg = None
    if os.path.exists(CONFIG_PATH):
        try:
            cfg = load_config()
        except Exception as e:
            print(f"config.json invalido ({e}) - rode 'python calibrate.py' de novo.")

    if args.no_overlay:
        if cfg is None:
            print("config.json nao encontrado - rode 'python calibrate.py' primeiro (o modo --no-overlay precisa da calibracao ja pronta, ele nao tem janela pra calibrar por dentro).")
            return
        strategy = importlib.import_module(f"strategy_{args.strategy}")
        run_loop_legacy(cfg, args, strategy, args.strategy)
        return

    state = SharedState(default_strategy=args.strategy)

    trading_thread = threading.Thread(
        target=_wait_for_config_and_run, args=(args, state), daemon=True
    )
    trading_thread.start()

    try:
        import overlay
        overlay.run_overlay(state, cfg)  # cfg pode vir None numa maquina nova - o overlay se vira sozinho ate a calibracao aparecer
    except Exception as e:
        print(f"Nao consegui abrir a janela flutuante ({e}) - use --no-overlay pra rodar sem ela.")


if __name__ == "__main__":
    main()