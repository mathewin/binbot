# Bot de trade automatizado - guia passo a passo

## Estrategias disponiveis
Agora o bot suporta mais de uma estrategia, escolhida na hora de rodar:

- **`quadrantes`** (padrao): compara 2 quadrantes fechados de 5 velas entre si.
  Tendencia manda sempre que clara; alternancia so decide quando esta lateral.
  So opera nos minutos terminados em 3 e 8.
- **`surf`**: olha so a ultima vela fechada e entra na mesma cor pra "surfar"
  a sequencia. Opera em TODO minuto, nao so 3/8. Quando a cor muda, ele ja
  passa a seguir a nova cor automaticamente na proxima entrada.

Pra escolher qual rodar, use `--strategy quadrantes` ou `--strategy surf` nos
comandos do passo 5 e 6 abaixo (se nao passar nada, usa `quadrantes`).

## O que mudou nesta versao
O bot detecta as velas sozinho, escaneando a tela pixel por pixel pra achar
exatamente onde cada corpo de vela esta - nao precisa mais contar "quantas
velas ficam visiveis".

## 1. Instalar o Python (so na primeira vez)
1. Baixe em https://www.python.org/downloads/
2. Ao instalar, marque a caixinha **"Add Python to PATH"** antes de clicar em Install.

## 2. Instalar as bibliotecas
Abra o "Prompt de Comando" (cmd) do Windows, navegue ate a pasta onde estao estes arquivos e rode:
```
pip install -r requirements.txt
```

## 3. Calibrar (toda vez que mudar zoom, resolucao, ou layout da corretora)
1. Abra o navegador com o grafico da corretora visivel, do jeito que voce normalmente opera.
   Deixe visiveis pelo menos umas 15-20 velas na tela (zoom out se precisar).
2. No cmd, rode:
```
python calibrate.py
```
3. Siga as instrucoes: posicione o mouse em cada ponto pedido e aperte ENTER
   (nao clique - so posicione e volte pro teclado). Marque o canto superior
   esquerdo e inferior direito abrangendo TODA a area onde as velas aparecem
   (do topo dos pavios mais altos ate a base dos pavios mais baixos).

## 4. Diagnosticar a deteccao (faca isso sempre depois de calibrar)
```
python debug_read.py
```
Mostra no terminal quantas velas foram detectadas e a cor de cada uma, e salva
`debug.png` com uma linha marcando cada vela encontrada. Abra o debug.png e
confira: uma linha por vela, nenhuma vela sem linha, nenhuma vela com duas linhas.
Se detectar poucas velas ou errar cores, ajuste `color_tolerance` no config.json
ou recalibre com uma area do grafico maior/melhor enquadrada.

## 5. Testar sem clicar de verdade (recomendado antes de rodar por dinheiro)
```
python bot.py --dry-run --strategy quadrantes
python bot.py --dry-run --strategy surf
```
Mostra no terminal o que o bot decidiria a cada entrada, sem clicar em nada.

## 6. Rodar de verdade
```
python bot.py --strategy quadrantes
python bot.py --strategy surf
```
Loop continuo. Com `quadrantes`, entra nos minutos 3/8/13/18... Com `surf`,
entra em todo minuto. CTRL+C para parar.

## Historico automatico (historico.csv)
Criado automaticamente (mesmo em --dry-run), uma linha por quadrante:

| coluna | o que e |
|---|---|
| timestamp | data/hora da decisao |
| decisao | buy, sell ou none |
| cor_vela3 | cor real que a vela3 fechou (resultado da operacao) |
| cor_vela4 | cor da vela seguinte |

## Notificacao no celular (Telegram)
O bot pode te avisar no Telegram toda vez que uma operacao for executada e
quando o resultado dela sair (ganhou/perdeu) - funciona em `--dry-run` tambem.

1. No Telegram, abra uma conversa com o **BotFather** e mande `/newbot`,
   seguindo as instrucoes (escolha um nome e um username terminado em "bot").
2. O BotFather te devolve um **token** parecido com `123456789:ABCdefGHI...`.
3. Procure seu bot novo pelo username escolhido e mande uma mensagem qualquer
   pra ele (ex: "oi").
4. Abra no navegador (trocando SEU_TOKEN pelo token do passo 2):
   `https://api.telegram.org/botSEU_TOKEN/getUpdates`
5. Na resposta, ache `"chat":{"id": NUMERO` - esse NUMERO e o seu **chat_id**.
6. No `config.json`, preencha:
```
"telegram_bot_token": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
"telegram_chat_id": "987654321"
```
7. Pronto - na proxima vez que rodar `python bot.py`, as notificacoes ja
   chegam sozinhas. Se deixar os dois campos em branco, fica desligado.

(WhatsApp: ainda nao implementado nesta versao - o modulo `notifications.py`
ja esta preparado pra receber isso depois, sem precisar mexer no bot.py.)

## Ajustes finos
- **`color_tolerance`** (config.json): se o bot errar cor ou nao detectar vela nenhuma, ajuste (comeca em 35).
- **`clock_offset_seconds`** (config.json): se o clique sair cedo/tarde, ajuste (positivo = atrasa).
- **`MIN_BODY_HEIGHT`** / **`MAX_COLUMN_GAP`** dentro de `candles.py`: altura minima pra contar
  como corpo de vela (filtra pavio fino) e quantas colunas vazias entre um corpo e outro ainda
  contam como a mesma vela. Ajuste se velas muito finas/grossas estiverem sendo mal detectadas.
- **`LATERAL_TOLERANCE_PX`** (strategy.py): define o quao parecido o fechamento precisa
  estar da abertura pra contar como lateral.

## Limitacoes conhecidas
- Le a tela por cor de pixel. Mudar tema, zoom, layout ou posicao da janela exige recalibrar.
- Nao percebe pop-ups, quedas de conexao ou travamentos da corretora - fique de olho.
- Sem limite de perdas automatico embutido.
