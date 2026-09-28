"""
Estado compartilhado entre a thread que opera (bot.py) e a thread do overlay
(janela flutuante). Protegido por um lock simples, ja que duas threads
diferentes leem/escrevem nele ao mesmo tempo.

Agora tambem controla LIGA/DESLIGA e qual ESTRATEGIA usar - isso e definido
pelo usuario clicando na janela flutuante, e a thread que opera fica de olho
nesse estado pra saber se deve rodar ou ficar parada, e com qual estrategia.

NOVO: existem DOIS placares separados agora:
  - ganhas/perdidas          -> contagem AUTOMATICA (feita pelo bot.py, do
                                 jeito que ja era antes).
  - ganhas_manual/perdidas_manual -> contagem MANUAL (feita pelo usuario, ao
                                 informar o saldo no overlay - ver overlay.py).
Cada resultado (dos dois placares) tambem fica registrado numa planilha
Excel (resultados.xlsx), em abas separadas "Bot" e "Manual", pra dar pra
comparar depois.

REQUER: pip install openpyxl (so pra gravar a planilha).
"""
import os
import threading
from datetime import datetime

PLANILHA_PATH = "resultados.xlsx"


def _log_planilha(aba, tipo, valor=None, saldo=None, extra_ganhas=None, extra_perdidas=None):
    """
    Adiciona uma linha na planilha resultados.xlsx, na aba indicada ("Bot"
    ou "Manual"). Cria o arquivo e as duas abas se ainda nao existirem.
    Qualquer erro aqui so imprime um aviso no console e segue o jogo - a
    planilha e um registro extra, nao pode travar o bot se der problema
    (arquivo aberto no Excel ao mesmo tempo, por exemplo).
    """
    try:
        import openpyxl
    except ImportError:
        print("AVISO: pacote 'openpyxl' nao instalado - rode 'pip install openpyxl' "
              "para habilitar o registro em planilha. Resultado NAO foi salvo na planilha.")
        return

    try:
        if os.path.exists(PLANILHA_PATH):
            wb = openpyxl.load_workbook(PLANILHA_PATH)
        else:
            wb = openpyxl.Workbook()
            wb.remove(wb.active)  # tira a aba padrao "Sheet" vazia

        for nome_aba in ("Bot", "Manual"):
            if nome_aba not in wb.sheetnames:
                ws = wb.create_sheet(nome_aba)
                ws.append(["data/hora", "resultado", "diferenca saldo (R$)",
                           "saldo apos", "ganhas (total)", "perdidas (total)"])

        ws = wb[aba]
        ws.append([
            datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            tipo,
            round(valor, 2) if valor is not None else None,
            round(saldo, 2) if saldo is not None else None,
            extra_ganhas,
            extra_perdidas,
        ])
        wb.save(PLANILHA_PATH)
    except Exception as e:
        print(f"AVISO: nao consegui gravar na planilha ({e}). O placar em tela continua normal.")


class SharedState:
    def __init__(self, default_strategy="quadrantes"):
        self.lock = threading.Lock()
        self.strategy_name = default_strategy
        self.surf_expiracao_min = 1     # 1 a 15 - so usado quando strategy_name == "surf"
        self.running = False  # comeca PARADO - so roda quando o usuario clicar em Iniciar
        self.pausado = False  # PAUSADO = continua calculando/mostrando sinais, mas NAO abre operacao
        self.confirming = False  # True durante a janela critica de leitura/confirmacao da vela
                                  # (ver _read_janela_confirmada em bot.py) - usado pra avisar a
                                  # thread de leitura continua do saldo (_publica_saldo_continuamente_async)
                                  # pra NAO disparar OCR nesse periodo, evitando que ela compita por
                                  # CPU/captura de tela bem na hora em que o timing e critico.
        self.status = "parado - selecione a estrategia e aperte Iniciar"
        self.next_entry_time = None
        self.last_decision = None       # 'buy' ou 'sell'
        self.live_signal = None         # palpite PROVISORIO da estrategia, atualizado
                                         # periodicamente enquanto espera a proxima entrada -
                                         # so vira "last_decision" de verdade quando executa
        self.live_valor = None          # valor PROVISORIO da proxima entrada (GR), recalculado
                                         # junto com live_signal a cada leitura - mostra no painel
                                         # o efeito de soros/gale que o resultado teria AGORA, antes
                                         # da confirmacao oficial. Vira None de novo quando trava
                                         # (mesmo momento em que last_decision passa a valer).

        # placar AUTOMATICO (bot)
        self.ganhas = 0
        self.perdidas = 0
        self.sequencia_atual = 0        # positivo = ganhas seguidas, negativo = perdidas seguidas

        # placar MANUAL (usuario, via campo de saldo no overlay)
        self.ganhas_manual = 0
        self.perdidas_manual = 0
        self.sequencia_manual = 0

        self.saldo = None               # saldo atual da conta (None = ainda nao informado/lido) - atualiza sempre
        self.saldo_inicial = None       # saldo TRAVADO no valor da primeira leitura desde o ultimo Iniciar -
                                         # nao muda mais depois disso ate a proxima vez que apertar Iniciar
                                         # (ver start() acima e set_saldo() abaixo)
        self.ultimo_resultado_tipo = None   # 'gain', 'loss' ou 'tie' (empate) - placar automatico
        self.ultimo_resultado_valor = None  # diferenca de saldo (pode ser negativa) - placar automatico

        # callback opcional que o overlay registra (ver set_on_click_callback)
        # pra ser avisado NA HORA (sem esperar o proximo refresh periodico)
        # sempre que bot.py clica de verdade pra abrir uma operacao. Usado
        # pra reforcar "-topmost" imediatamente apos o clique - ver
        # overlay.py e o comentario em notify_click() logo abaixo.
        self.on_click_callback = None

        # segundo callback, SEPARADO do de cima - pra avisar o overlay NA
        # HORA sempre que a INTENCAO (live_signal) ou a decisao oficial
        # (last_decision) mudam de verdade (ver set_live_signal/
        # set_decision abaixo, que so disparam esse callback quando o
        # valor novo e DIFERENTE do anterior - uma leitura repetida nao
        # dispara nada, pra nao ficar chamando o overlay a toa). Usado pra
        # atualizar so o textinho "analisando: COMPRA/VENDA" na hora certa,
        # sem esperar o proximo tick do polling (150ms) do overlay - ver
        # overlay.py:_atualiza_intencao().
        self.on_signal_callback = None

        # --- GERENCIAMENTO DE RISCO (GR): soros + gale por nivel ---
        # Configurado pelo usuario no painel GR (overlay.py) - o bot.py so
        # LE isso (nunca escreve), e usa pra criar/atualizar o motor de
        # verdade (ver gerenciamento.GerenciamentoRisco). A logica de
        # calculo em si NAO mora aqui - aqui e so o "o que o usuario
        # configurou", que e comunicacao entre as duas threads.
        self.gr_ativo = False
        self.gr_nivel = 2                # 2, 3 ou 4
        self.gr_ciclo = "simples"        # "simples" ou "composto"
        self.gr_config = {
            2: {"investimento": 50.0, "pct_util": 0.20, "payout": 0.80, "gale": [0.50] * 30,
                "gale_ativos": 2, "investimento_fonte": "manual"},
            3: {"investimento": 50.0, "pct_util": 0.20, "payout": 0.80, "gale": [0.50, 0.50, 0.50], "investimento_fonte": "manual"},
            4: {"investimento": 500.0, "pct_util": 0.01, "payout": 0.85, "gale": [0.50, 0.50, 0.50, 0.50], "investimento_fonte": "manual"},
        }
        # status ao vivo do ciclo atual (so leitura, atualizado pelo
        # bot.py a cada entrada) - pra mostrar no painel GR o que esta
        # rolando agora (fase soros/gale, proximo valor, lucro do ciclo).
        self.gr_status = {"fase": None, "valor_atual": None, "lucro_ciclo": None}

    # --- controle liga/desliga ---
    def start(self):
        with self.lock:
            self.running = True
            self.pausado = False
            self.status = "iniciando..."
            self.saldo_inicial = None  # recalibra a cada Iniciar - ver set_saldo() abaixo

    def stop(self):
        with self.lock:
            self.running = False
            self.pausado = False
            self.status = "parado"
            self.next_entry_time = None

    def is_running(self):
        with self.lock:
            return self.running

    # --- pausa: robo continua ligado e calculando sinais, mas NAO deve
    # clicar pra abrir operacao nenhuma enquanto pausado=True. O bot.py
    # precisa checar state.is_paused() logo antes de clicar comprar/vender
    # (e pode seguir normalmente com o resto: contagem, calculo do sinal
    # provisorio via set_live_signal, etc - so o clique real que para).
    def pause(self):
        with self.lock:
            self.pausado = True

    def resume(self):
        with self.lock:
            self.pausado = False

    def is_paused(self):
        with self.lock:
            return self.pausado

    # --- janela critica de confirmacao da vela: ver comentario no __init__.
    # bot.py chama set_confirming(True) logo antes de comecar a ler a vela
    # em ritmo rapido (perto do fechamento) e set_confirming(False) assim
    # que essa leitura critica termina (num finally, pra nunca ficar preso
    # em True se algo der erro no meio do caminho).
    def set_confirming(self, valor):
        with self.lock:
            self.confirming = valor

    def is_confirming(self):
        with self.lock:
            return self.confirming

    # --- selecao de estrategia (so pode trocar enquanto estiver parado) ---
    def set_strategy(self, name):
        with self.lock:
            if not self.running:
                self.strategy_name = name

    def get_strategy(self):
        with self.lock:
            return self.strategy_name

    # --- expiracao do surf: 1 a 15 minutos, mesma estrategia so muda o tempo ---
    def set_surf_expiracao(self, minutos):
        with self.lock:
            if not self.running:
                self.surf_expiracao_min = max(1, min(15, minutos))

    def get_surf_expiracao(self):
        with self.lock:
            return self.surf_expiracao_min

    # --- resto do estado (cronometro, decisao, placar) ---
    def set_entry_time(self, dt):
        with self.lock:
            self.next_entry_time = dt

    def set_status(self, texto):
        with self.lock:
            self.status = texto

    def set_decision(self, decisao):
        mudou = False
        with self.lock:
            if self.last_decision != decisao:
                self.last_decision = decisao
                mudou = True
        if mudou:
            self._notify_signal_change()

    def set_live_signal(self, sinal):
        mudou = False
        with self.lock:
            if self.live_signal != sinal:
                self.live_signal = sinal
                mudou = True
        if mudou:
            self._notify_signal_change()

    def set_live_valor(self, valor):
        mudou = False
        with self.lock:
            if self.live_valor != valor:
                self.live_valor = valor
                mudou = True
        if mudou:
            self._notify_signal_change()

    def add_resultado(self, ganhou, valor=None):
        """Placar AUTOMATICO (chamado pelo bot.py). 'valor' e opcional
        (diferenca de saldo da operacao), so usado pra enriquecer a
        planilha - se nao for passado, a linha fica sem esse dado."""
        with self.lock:
            if ganhou:
                self.ganhas += 1
                self.sequencia_atual = self.sequencia_atual + 1 if self.sequencia_atual >= 0 else 1
            else:
                self.perdidas += 1
                self.sequencia_atual = self.sequencia_atual - 1 if self.sequencia_atual <= 0 else -1
            ganhas, perdidas, saldo_atual = self.ganhas, self.perdidas, self.saldo
        _log_planilha("Bot", "GANHO" if ganhou else "PERDA", valor=valor, saldo=saldo_atual,
                       extra_ganhas=ganhas, extra_perdidas=perdidas)

    def add_resultado_manual(self, ganhou, valor=None, saldo=None):
        """Placar MANUAL (chamado pelo overlay quando o usuario confirma um
        novo saldo). 'valor' = diferenca em relacao ao saldo anterior."""
        with self.lock:
            if ganhou:
                self.ganhas_manual += 1
                self.sequencia_manual = self.sequencia_manual + 1 if self.sequencia_manual >= 0 else 1
            else:
                self.perdidas_manual += 1
                self.sequencia_manual = self.sequencia_manual - 1 if self.sequencia_manual <= 0 else -1
            ganhas, perdidas = self.ganhas_manual, self.perdidas_manual
        _log_planilha("Manual", "GANHO" if ganhou else "PERDA", valor=valor, saldo=saldo,
                       extra_ganhas=ganhas, extra_perdidas=perdidas)

    def set_saldo(self, valor):
        with self.lock:
            self.saldo = valor
            if self.saldo_inicial is None:
                # primeira leitura valida desde o ultimo Iniciar - trava
                # como "saldo inicial" e nao mexe mais nele ate reiniciar
                self.saldo_inicial = valor

    def set_ultimo_resultado(self, tipo, valor):
        """tipo: 'gain', 'loss' ou 'tie'; valor: diferenca de saldo (float, pode ser negativa/zero)."""
        with self.lock:
            self.ultimo_resultado_tipo = tipo
            self.ultimo_resultado_valor = valor

    # --- callback de clique (ver comentario no __init__) ---
    def set_on_click_callback(self, fn):
        with self.lock:
            self.on_click_callback = fn

    def notify_click(self):
        """
        Chamado pelo bot.py logo apos um clique de verdade (ver execute()
        em bot.py). Roda o callback (se o overlay tiver registrado um) FORA
        do lock, pra nao segurar o lock enquanto executa codigo de outro
        modulo. Qualquer erro no callback so e ignorado - avisar o overlay
        e um bonus, nunca pode travar nem atrasar o bot de verdade.
        """
        with self.lock:
            cb = self.on_click_callback
        if cb:
            try:
                cb()
            except Exception:
                pass

    # --- callback de mudanca de sinal/decisao (ver comentario no __init__) ---
    def set_on_signal_callback(self, fn):
        with self.lock:
            self.on_signal_callback = fn

    def _notify_signal_change(self):
        """
        Chamado de dentro de set_live_signal/set_decision, SO quando o
        valor novo e diferente do anterior (ver os dois metodos acima) -
        roda o callback (se o overlay tiver registrado um) FORA do lock,
        mesma logica de notify_click() acima. Qualquer erro no callback so
        e ignorado - e um bonus visual, nunca pode travar nem atrasar o
        bot de verdade.
        """
        with self.lock:
            cb = self.on_signal_callback
        if cb:
            try:
                cb()
            except Exception:
                pass

    # --- GERENCIAMENTO DE RISCO (GR) - config vinda do painel ---
    def set_gr_ativo(self, valor):
        with self.lock:
            self.gr_ativo = bool(valor)

    def is_gr_ativo(self):
        with self.lock:
            return self.gr_ativo

    def set_gr_nivel(self, nivel):
        with self.lock:
            if nivel in self.gr_config:
                self.gr_nivel = nivel

    def set_gr_ciclo(self, ciclo):
        with self.lock:
            if ciclo in ("simples", "composto"):
                self.gr_ciclo = ciclo

    def set_gr_config_campo(self, nivel, chave, valor):
        """Atualiza UM campo da config de UM nivel (ex: nivel=2,
        chave='payout', valor=0.9), sem mexer no resto. 'chave' pode ser
        'investimento', 'pct_util', 'payout', 'investimento_fonte'
        ('manual' ou 'saldo'), ou 'gale_<indice>' (ex: 'gale_0' pro
        primeiro nivel de gale)."""
        with self.lock:
            cfg = self.gr_config.get(nivel)
            if cfg is None:
                return
            if chave.startswith("gale_") and chave != "gale_ativos":
                idx = int(chave.split("_")[1])
                if 0 <= idx < len(cfg["gale"]):
                    cfg["gale"][idx] = valor
            elif chave in ("investimento", "pct_util", "payout"):
                cfg[chave] = valor
            elif chave == "investimento_fonte" and valor in ("manual", "saldo"):
                cfg["investimento_fonte"] = valor
            elif chave == "gale_ativos":
                cfg["gale_ativos"] = max(1, min(len(cfg["gale"]), int(valor)))

    def get_gr_snapshot(self):
        """Copia de tudo que o bot.py precisa pra montar/atualizar o motor
        de GR - nivel, ciclo e a config completa do nivel selecionado.
        A lista "gale" que vai pro motor real (gerenciamento.py) e cortada
        em "gale_ativos" itens (quantos niveis de gale o usuario escolheu
        no painel) - o resto do pool (ate 30, no nivel 2) fica guardado,
        pronto, mas so entra em jogo se o usuario selecionar mais depois."""
        with self.lock:
            nivel = self.gr_nivel
            cfg_nivel = self.gr_config[nivel]
            gale_completo = cfg_nivel["gale"]
            gale_ativos = max(1, min(len(gale_completo), cfg_nivel.get("gale_ativos", len(gale_completo))))
            return {
                "ativo": self.gr_ativo,
                "nivel": nivel,
                "ciclo": self.gr_ciclo,
                "config": dict(cfg_nivel, gale=list(gale_completo[:gale_ativos])),
            }

    def get_gr_config_all(self):
        """Copia completa da config dos 3 niveis (2, 3 e 4) - usado pelo
        overlay.py pra desenhar o painel GR inicial a partir da MESMA fonte
        de verdade que o bot.py usa (em vez de manter um segundo dicionario
        de valores padrao duplicado e sujeito a ficar desatualizado)."""
        with self.lock:
            return {
                nivel: dict(cfg, gale=list(cfg["gale"]))
                for nivel, cfg in self.gr_config.items()
            }

    def set_gr_status(self, fase, valor_atual, lucro_ciclo):
        with self.lock:
            self.gr_status = {"fase": fase, "valor_atual": valor_atual, "lucro_ciclo": lucro_ciclo}

    def get_gr_status(self):
        with self.lock:
            return dict(self.gr_status)

    def snapshot(self):
        with self.lock:
            return {
                "strategy_name": self.strategy_name,
                "surf_expiracao_min": self.surf_expiracao_min,
                "running": self.running,
                "pausado": self.pausado,
                "status": self.status,
                "next_entry_time": self.next_entry_time,
                "last_decision": self.last_decision,
                "live_signal": self.live_signal,
                "live_valor": self.live_valor,
                "ganhas": self.ganhas,
                "perdidas": self.perdidas,
                "sequencia_atual": self.sequencia_atual,
                "ganhas_manual": self.ganhas_manual,
                "perdidas_manual": self.perdidas_manual,
                "sequencia_manual": self.sequencia_manual,
                "saldo": self.saldo,
                "saldo_inicial": self.saldo_inicial,
                "ultimo_resultado_tipo": self.ultimo_resultado_tipo,
                "ultimo_resultado_valor": self.ultimo_resultado_valor,
                "gr_ativo": self.gr_ativo,
                "gr_nivel": self.gr_nivel,
                "gr_ciclo": self.gr_ciclo,
                "gr_status": dict(self.gr_status),
            }
