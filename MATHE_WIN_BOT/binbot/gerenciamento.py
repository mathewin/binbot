"""
Motor de GERENCIAMENTO DE RISCO (GR): soros + gale, por nivel (2, 3 ou 4),
em ciclo SIMPLES ou COMPOSTO.

Isso e SO CALCULO DE ESTADO - nao mexe em tela, nao clica em nada, nao sabe
nada sobre pyautogui/corretora. O bot.py consulta valor_atual() pra saber
quanto entrar AGORA, e chama registrar_resultado(ganhou) depois que uma
operacao fecha, pra avancar o ciclo e saber o que fazer em seguida (seguir,
parar, ou reiniciar).

REGRAS (decodificadas da planilha do usuario + confirmadas em conversa):

SOROS (fase de tentar ganhar "limpo"): a entrada de cada nivel = a anterior
+ o lucro da anterior. O nivel escolhido (2, 3 ou 4) e quantas dessas
entradas seguidas precisam ganhar pra fechar o ciclo com STOP WIN. Perder
QUALQUER entrada do soros (a primeira ou uma no meio) abre a escada de GALE.

GALE (fase de tentar recuperar uma perda): a entrada de cada nivel do gale
= (soma de tudo que ja foi investido nessa sequencia de perda) x uma %
propria daquele nivel (configuravel). Se ganhar em qualquer nivel do gale,
o ciclo fecha RECUPERADO (nao e "stop win" - so fecha e reinicia). Se
perder ate o ULTIMO nivel de gale configurado, e STOP LOSS.

CICLO SIMPLES: o valor de Investimento fica FIXO (nao muda saldo). Ao
fechar com STOP WIN (soros limpo), o bot.py deve PAUSAR sozinho (o usuario
decide se continua ou para - ver SharedState.pause()). Ao recuperar via
gale, so reinicia e segue operando normal, sem pausar. Ao bater STOP LOSS,
o bot.py deve PARAR de vez (ver SharedState.stop()).

CICLO COMPOSTO: nao existe "stop win" que pausa - TODA vitoria (seja soros
limpo OU recuperacao via gale) reinicia o ciclo usando o SALDO REAL DA
CONTA (lido por OCR, vindo de fora - este modulo nao le saldo nenhum,
quem chama que precisa passar o valor pra reiniciar_ciclo(banca=...)) como
novo Investimento, e o bot SEGUE operando sem parar. So um STOP LOSS
encerra de vez.
"""


class GerenciamentoRisco:
    def __init__(self, nivel, config, ciclo="simples", banca_inicial=None):
        """
        nivel: 2, 3 ou 4 - quantas entradas de soros seguidas fecham o
            ciclo com stop win, e quantos niveis de gale existem.
        config: dict com "investimento" (R$), "pct_util" (0-1), "payout"
            (0-1), "gale" (lista de fracoes 0-1) e "investimento_fonte"
            ("manual" ou "saldo" - ver reiniciar_ciclo abaixo). E uma copia
            PROPRIA deste objeto (nao compartilhada com o dict do painel) -
            mudar o painel depois de criar o motor nao afeta um ciclo ja em
            andamento.
        ciclo: "simples" ou "composto" (ver regras acima)
        banca_inicial: saldo real da conta, se ja disponivel no momento de
            criar o motor - so tem efeito se investimento_fonte=="saldo"
            (ver reiniciar_ciclo).
        """
        self.nivel = nivel
        self.config = dict(config)
        self.config["gale"] = list(config["gale"])
        self.config.setdefault("investimento_fonte", "manual")
        self.ciclo = ciclo
        self.reiniciar_ciclo(banca=banca_inicial)

    def reiniciar_ciclo(self, banca=None):
        """Comeca um ciclo novo do zero, na fase de soros.

        O valor de Investimento que vale para o ciclo INTEIRO e definido
        AQUI, uma unica vez, e so muda na PROXIMA vez que reiniciar_ciclo
        for chamado de novo (nunca no meio de um ciclo em andamento):
          - investimento_fonte == "manual": mantem o valor configurado no
            painel, INTOCADO - nunca e sobrescrito, nem no modo composto.
          - investimento_fonte == "saldo": puxa o saldo REAL da conta (o
            parametro 'banca', vindo de fora - este modulo nao le tela nem
            saldo nenhum) e trava esse valor como Investimento deste ciclo.
            Se 'banca' nao foi passado ainda (ex: saldo nao lido a tempo),
            mantem o ultimo valor conhecido em vez de travar o bot.
        """
        if banca is not None and self.config.get("investimento_fonte", "manual") == "saldo":
            self.config["investimento"] = banca
        self.fase = "soros"            # "soros" ou "gale"
        self.indice = 0                # indice dentro da fase atual
        self.cumulativo_perda = 0.0    # soma investida na sequencia de perda atual (usado no gale)
        self.lucro_ciclo = 0.0         # lucro/prejuizo acumulado DESTE ciclo ate agora

    def valor_atual(self):
        """Valor (R$, sempre positivo) da PROXIMA entrada a ser feita agora."""
        if self.fase == "soros":
            stake = self.config["investimento"] * self.config["pct_util"]
            for _ in range(self.indice):
                stake = stake + stake * self.config["payout"]
            return round(stake, 2)
        gale_pct = self.config["gale"][self.indice]
        return round(self.cumulativo_perda * gale_pct, 2)

    def status_label(self):
        """Texto curto pra mostrar no painel/console (ex: 'soros 2/3' ou 'gale 1/2')."""
        if self.fase == "soros":
            return f"soros {self.indice + 1}/{self.nivel}"
        return f"gale {self.indice + 1}/{len(self.config['gale'])}"

    def registrar_resultado(self, ganhou):
        """
        Chamado depois que UMA operacao fecha (ja sabendo se ganhou ou
        perdeu). Atualiza o estado interno e devolve um dict descrevendo o
        que aconteceu - quem chama (bot.py) decide o que fazer com isso:

          {"evento": "continua"}
              -> segue no mesmo ciclo, sem parar nada. valor_atual() ja
                 reflete a proxima entrada.

          {"evento": "ciclo_venceu", "tipo": "soros_limpo" | "gale", "lucro": X}
              -> o ciclo fechou COM VITORIA. "tipo" diz se foi um soros
                 limpo (bateu o nivel escolhido sem nunca precisar de
                 gale) ou uma recuperacao via gale.
                 - simples + soros_limpo -> bot.py deve PAUSAR (stop win)
                 - simples + gale        -> so reinicia e segue operando
                 - composto (qualquer tipo) -> bot.py deve ler o saldo
                   real da conta e chamar reiniciar_ciclo(banca=saldo),
                   e SEGUIR operando (sem pausar nem parar)

          {"evento": "stop_loss", "prejuizo": X}
              -> estourou o ultimo nivel de gale configurado. bot.py deve
                 PARAR de vez, nos dois modos (simples e composto).
        """
        stake = self.valor_atual()

        if self.fase == "soros":
            if ganhou:
                self.lucro_ciclo += stake * self.config["payout"]
                self.indice += 1
                if self.indice >= self.nivel:
                    lucro = self.lucro_ciclo
                    return {"evento": "ciclo_venceu", "tipo": "soros_limpo", "lucro": lucro}
                return {"evento": "continua"}
            else:
                self.lucro_ciclo -= stake
                self.fase = "gale"
                self.cumulativo_perda = stake
                self.indice = 0
                return {"evento": "continua"}

        # fase == "gale"
        if ganhou:
            self.lucro_ciclo += stake * self.config["payout"]
            lucro = self.lucro_ciclo
            return {"evento": "ciclo_venceu", "tipo": "gale", "lucro": lucro}
        else:
            self.lucro_ciclo -= stake
            self.cumulativo_perda += stake
            self.indice += 1
            if self.indice >= len(self.config["gale"]):
                prejuizo = self.lucro_ciclo
                return {"evento": "stop_loss", "prejuizo": prejuizo}
            return {"evento": "continua"}


def simular_tabela(nivel, config, ciclo="simples"):
    """
    Simula um ciclo INTEIRO (sem tela, sem clique) e devolve uma lista de
    linhas pra conferir os valores contra a planilha, ANTES de ligar o bot
    de verdade:

      [{"fase": "Soros 1", "valor": 10.0, "investido_acumulado": 10.0,
        "lucro_se_ganhar": 9.0, "prejuizo_se_perder": -10.0}, ...]

    A ordem das linhas e: primeiro todos os passos do SOROS (na sequencia
    em que aconteceriam se fosse ganhando toda vez), depois todos os passos
    do GALE (na sequencia em que aconteceriam se fosse perdendo toda vez a
    partir do primeiro soros). Isso mostra as DUAS escadas completas de uma
    vez, sem precisar realmente ganhar/perder nada de verdade.
    """
    linhas = []

    # --- Escada do SOROS: simula ganhando toda vez, do inicio ao fim ---
    gr_soros = GerenciamentoRisco(nivel, config, ciclo=ciclo)
    for i in range(nivel):
        valor = gr_soros.valor_atual()
        lucro_se_ganhar = round(valor * gr_soros.config["payout"], 2)
        linhas.append({
            "fase": f"Soros {i + 1}/{nivel}",
            "valor": valor,
            "lucro_se_ganhar": lucro_se_ganhar,
            "prejuizo_se_perder": -valor,
        })
        gr_soros.registrar_resultado(ganhou=True)

    # --- Escada do GALE: simula perdendo no primeiro soros, depois
    #     perdendo em cada nivel do gale ate o ultimo ---
    gr_gale = GerenciamentoRisco(nivel, config, ciclo=ciclo)
    gr_gale.registrar_resultado(ganhou=False)  # perde o soros -> abre o gale
    n_niveis_gale = len(gr_gale.config["gale"])
    for i in range(n_niveis_gale):
        valor = gr_gale.valor_atual()
        lucro_se_ganhar = round(valor * gr_gale.config["payout"] + gr_gale.lucro_ciclo, 2)
        prejuizo_se_perder = round(gr_gale.lucro_ciclo - valor, 2)
        linhas.append({
            "fase": f"Gale {i + 1}/{n_niveis_gale}",
            "valor": valor,
            "lucro_se_ganhar": lucro_se_ganhar,
            "prejuizo_se_perder": prejuizo_se_perder,
        })
        gr_gale.registrar_resultado(ganhou=False)

    return linhas


def imprimir_tabela(nivel, config, ciclo="simples"):
    """Mostra a tabela formatada no terminal - util pra conferir a mao contra a planilha."""
    linhas = simular_tabela(nivel, config, ciclo)
    print(f"\n{'FASE':<12} {'VALOR (R$)':>12} {'LUCRO SE GANHAR':>18} {'PREJUIZO SE PERDER':>20}")
    print("-" * 64)
    for linha in linhas:
        print(f"{linha['fase']:<12} {linha['valor']:>12.2f} {linha['lucro_se_ganhar']:>18.2f} {linha['prejuizo_se_perder']:>20.2f}")


if __name__ == "__main__":
    config_exemplo = {"investimento": 10.0, "pct_util": 1.0, "payout": 0.90, "gale": [0.28, 0.39]}
    print("=== Nivel 2, ciclo simples ===")
    imprimir_tabela(2, config_exemplo, ciclo="simples")
