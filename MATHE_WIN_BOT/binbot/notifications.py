"""
NOTIFICACOES - manda um aviso no celular quando uma operacao e executada e
quando o resultado dela sai. Hoje so tem Telegram implementado, mas a funcao
notify() abaixo e o unico lugar que bot.py chama - se um dia voce quiser
adicionar WhatsApp (ou trocar por outra coisa), e so mexer aqui dentro,
sem tocar no resto do bot.

Nao trava o bot se a notificacao falhar (sem internet, token errado, etc):
so imprime um aviso no terminal e segue o loop normalmente.

--------------------------------------------------------------------------
COMO CONFIGURAR O TELEGRAM (leva uns 2 minutos):

1. No Telegram, procure o contato "BotFather" (e o bot oficial que cria
   outros bots) e abra uma conversa com ele.
2. Mande o comando /newbot e siga as instrucoes (ele pede um nome e um
   "username" pro seu bot, que precisa terminar em "bot").
3. No final, o BotFather te manda uma mensagem com um token, parecido com:
   123456789:ABCdefGHIjklMNOpqrsTUVwxyz
   Esse e o seu "bot_token".
4. Agora procure o seu bot novo pelo username que voce escolheu e mande
   qualquer mensagem pra ele (ex: "oi") - isso e necessario pra ele saber
   pra quem responder.
5. Abra esse link no navegador, trocando SEU_TOKEN pelo token do passo 3:
   https://api.telegram.org/botSEU_TOKEN/getUpdates
6. Na resposta (um JSON), procure por "chat":{"id": NUMERO ... - esse
   NUMERO e o seu "chat_id".
7. Coloque os dois valores no config.json:
   "telegram_bot_token": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
   "telegram_chat_id": "987654321"
8. Pronto - o bot ja vai comecar a mandar notificacao sozinho.

Se voce deixar os dois campos em branco ("") no config.json, a notificacao
fica desligada (nao da erro, so nao manda nada).
--------------------------------------------------------------------------
"""
import requests


def _send_telegram(cfg, message):
    if not cfg.get("telegram_enabled", True):
        return  # notificacao pausada de proposito (telegram_enabled: false no config.json)

    token = cfg.get("telegram_bot_token", "")
    chat_id = cfg.get("telegram_chat_id", "")
    if not token or not chat_id:
        return  # notificacao desligada (campos vazios no config.json)

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.get(url, params={"chat_id": chat_id, "text": message}, timeout=5)
        if resp.status_code != 200:
            print(f"   [notificacao] Telegram recusou o envio: {resp.text[:200]}")
    except Exception as e:
        print(f"   [notificacao] falha ao enviar Telegram: {e}")


def notify(cfg, message):
    """
    Ponto unico chamado pelo bot.py. Hoje so manda Telegram; se um dia
    voce adicionar WhatsApp (CallMeBot ou outro), e so chamar a funcao
    nova aqui dentro tambem, igual a linha do Telegram abaixo.
    """
    _send_telegram(cfg, message)

    # --- futuro: WhatsApp via CallMeBot, quando/se ativar a API key ---
    # _send_whatsapp(cfg, message)
