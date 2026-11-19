# Triagem de artigos para a RSL (multimodal e agêntico)

Ferramentas para triar os artigos de uma Revisão Sistemática da Literatura sobre arquiteturas,
frameworks, padrões e taxonomias para sistemas multimodais e agênticos baseados em IA.

A triagem tem duas etapas, e cada uma acrescenta colunas à planilha sem alterar o resto:

```
artigos.csv ──► triagem_titulos.py ──► artigos_triagem.csv ──► triagem_resumos.py ──► triagem_resumo.csv
 (Rayyan)        etapa 1: título                                etapa 2: título + resumo
                 (regras, sem IA)                               (Claude)
```

| Arquivo | Função |
|---|---|
| `triagem_titulos.py` | Etapa 1. Triagem por **título**, com expressões regulares. Só Python, sem custo. Adiciona `classification` e `code`. |
| `triagem_resumos.py` | Etapa 2. Triagem por **título + resumo**, enviada ao Claude. Adiciona `abstract_*` e `final_classification`. |
| `triagem_resumos_deterministica.py` | Etapa 2 **sem IA**: mesma triagem por título + resumo, só com regras em Python. Sem Claude, sem custo, sem limite de uso. Menos fiel que o Claude (veja a seção 4). |
| `prompt.md` | Prompt da etapa 2 (critérios IC/EC, arquétipos A–E e códigos de exclusão). |

Este README trata da **etapa 2**, que pode rodar de três formas: com o **login da sua conta Claude** (sem API key),
com a **API da Anthropic** (com API key), ou **sem IA**, só com regras em Python (menos fiel, mas sem custo nem limite).

---

## 1. Etapa 1: triagem por título

```bash
python3 triagem_titulos.py artigos.csv                 # gera artigos_triagem.csv
python3 triagem_titulos.py artigos.csv -o saida.csv
```

Requer só Python 3.8+. A saída é a planilha original com as colunas `classification` (yes/no/maybe)
e `code`. A etapa 2 usa essa coluna para enviar ao Claude apenas os artigos `yes` e `maybe`.

---

## 2. Etapa 2: escolha a configuração

|  | **A. Login da conta** (padrão) | **B. API** |
|---|---|---|
| Flag | `--backend login` (ou omitir) | `--backend api` |
| Autenticação | Login do Claude Code (assinatura Pro/Max) | `ANTHROPIC_API_KEY` |
| Instalação | Claude Code (`claude`) | `pip install anthropic` |
| Cobrança | Consome o limite de uso da assinatura | Por token, na conta da API |
| Modos | Só direto (chamadas em paralelo) | `lote` (50% mais barato, assíncrono) ou `direto` |
| Velocidade | ~3 a 5 s por artigo, 3 em paralelo | Lote: normalmente < 1 h; direto: rápido |
| Grande volume | Pode esbarrar no limite da janela de 5 h; `--esperar-limite` espera e continua sozinho | Sem esse limite (apenas os limites de taxa da API) |
| Reprodutibilidade | Sem controle de `temperature` | Sem controle de `temperature` |

**Quando usar cada uma:** o login serve bem para calibrar o prompt e rodar amostras, sem custo
adicional. Para rodar milhares de artigos de uma vez, a API em modo lote é a opção mais indicada.

### Requisitos comuns

- Python 3.8+
- `prompt.md` na mesma pasta do script (o script procura `prompt_triagem_resumo.md` e, se não existir,
  `prompt.md`; para outro arquivo use `--prompt caminho.md`)
- Uma planilha CSV com colunas `title` e `abstract` (e, idealmente, `key` e `classification`)

---

### Configuração A: login da conta (sem API key)

O script chama o Claude Code em modo headless (`claude -p`) para cada artigo, usando o login da sua
conta. Nenhuma `ANTHROPIC_API_KEY` é necessária.

**1. Instale o Claude Code e faça login** (uma vez):

```bash
npm install -g @anthropic-ai/claude-code    # ou o instalador oficial
claude                                      # abre o Claude Code; digite /login e entre na sua conta
```

Confira que `claude` está no PATH:

```bash
claude --version
```

**2. Rode**:

```bash
# conferir quantos artigos vão e ver o comando que será executado (não chama o Claude)
python3 triagem_resumos.py artigos_triagem.csv --simular

# calibrar com os 50 primeiros artigos; grava só esses 50 no CSV
python3 triagem_resumos.py artigos_triagem.csv --amostra 50 --so-avaliados -o amostra.csv

# rodar tudo
python3 triagem_resumos.py artigos_triagem.csv -o triagem_resumo.csv
```

Como funciona e o que observar:

- O script **remove `ANTHROPIC_API_KEY` do ambiente** ao chamar o `claude`. Mesmo que a variável esteja
  definida no seu terminal, a execução usa o login da conta e não cobra a API.
- Cada chamada roda sem ferramentas, sem carregar o `CLAUDE.md` do projeto e sem salvar sessão.
- Ao final, o script mostra o **uso equivalente na API** (ex.: `~US$ 0.12`). Esse valor **não é cobrado**:
  serve só para dimensionar o consumo do limite da assinatura. Na prática, em torno de US$ 0,03 por
  artigo.
- Se o limite de uso da assinatura acabar, por padrão o script **para e avisa**. Rode o mesmo comando
  mais tarde e ele continua de onde parou. Para ele **esperar a renovação e continuar sozinho**, use
  `--esperar-limite` (veja a próxima seção).
- `--modo lote` **não existe** neste backend (retorna erro), porque o `claude -p` não tem Message Batches.
- Ajuste o paralelismo com `--workers` (padrão: 3). Valores altos aumentam a chance de bater no limite.

#### Esperar o limite de 5 horas renovar (`--esperar-limite`)

Para rodar um volume grande sem acompanhar, acrescente `--esperar-limite`:

```bash
caffeinate -i python3 triagem_resumos.py artigos_triagem.csv -o triagem_resumo.csv --esperar-limite
```

Quando a assinatura atinge o limite, o script:

1. **Pausa todas as chamadas** de uma vez (mesmo que várias estejam em andamento) e imprime o horário
   da retomada, por exemplo `[limite] ... Pausado até 17/11 15:30 (~2h13min)`.
2. **Dorme até o horário de reset** informado pelo Claude Code (mais 1 min de folga) e então tenta de
   novo **o mesmo artigo**, sem perder nenhum.
3. **Retoma sozinho** e segue até terminar. Pode haver várias pausas numa mesma execução; o total é
   mostrado no fim (`pausas por limite de uso`).

Detalhes importantes:

- **O terminal precisa ficar aberto.** O script usa `caffeinate` automaticamente no macOS para o Mac não
  entrar em repouso por inatividade, mas **fechar a tampa do notebook ainda suspende tudo**. Se isso
  acontecer, rode o mesmo comando de novo: o cache retoma de onde parou.
- **Limite semanal:** se a renovação estiver além de `--espera-max` horas (padrão: 6), o script não espera
  e para, avisando a data. Isso evita ficar parado por dias em silêncio (limite de 7 dias).
- **Horário de reset desconhecido:** se o Claude Code não informar o horário, o script testa de novo a
  cada `--espera-sondagem` minutos (padrão: 15).
- **Login expirado não é limite:** se o erro for de autenticação, o script para na hora (não espera).
- `Ctrl+C` durante a pausa encerra em segundos; rodar o mesmo comando depois continua do ponto em que parou.
- Só vale no backend `login`. Na API a opção é ignorada com um aviso.

---

### Configuração B: API da Anthropic

**1. Instale o SDK e defina a chave**:

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...          # chave criada em console.anthropic.com
```

Para não repetir a cada terminal, coloque o `export` no `~/.zshrc`. Não grave a chave em arquivos do
projeto.

**2. Rode**:

```bash
# conferir quantos artigos vão e ver um exemplo da requisição (não chama a API)
python3 triagem_resumos.py artigos_triagem.csv --backend api --simular

# calibrar com 50 artigos, em modo direto (resposta rápida, preço normal)
python3 triagem_resumos.py artigos_triagem.csv --backend api --modo direto \
        --amostra 50 --so-avaliados -o amostra.csv

# rodar tudo em lote (50% mais barato, assíncrono)
python3 triagem_resumos.py artigos_triagem.csv --backend api -o triagem_resumo.csv
```

Como funciona e o que observar:

- **Modo `lote`** (padrão na API): envia os artigos pela Message Batches API, com 50% de desconto. O
  processamento é assíncrono, normalmente leva menos de 1 h e no máximo 24 h. O script consulta o
  andamento a cada `--intervalo` segundos (padrão: 60).
- **Retomada do lote:** se você interromper com `Ctrl+C`, rode **o mesmo comando** e o script retoma o
  lote já enviado, sem reenviar (o estado fica em `<saida>.lotes.json`).
- **Modo `direto`:** chamadas síncronas em paralelo (`--workers`, padrão: 4). Mais caro, bom para
  amostras pequenas. Mostra os tokens usados ao final.
- `--cache-1h` mantém o prompt em cache por 1 h; é útil em lotes grandes.
- Se a chave estiver ausente ou for recusada, o script para com uma mensagem clara.
- Consulte os preços atuais em <https://www.anthropic.com/pricing>. O comando `--simular` estima os
  tokens (prompt do sistema + conteúdo dos artigos) antes de gastar qualquer valor.

---

## 3. Etapa 2 sem IA: `triagem_resumos_deterministica.py`

Faz a mesma triagem por título + resumo **só com regras em Python** (expressões regulares e pontuação). Não usa
Claude, API nem login: não tem custo, não tem limite de uso e dá sempre o mesmo resultado para a mesma entrada. Leva
cerca de 40 s para ~10 mil artigos. Grava as **mesmas colunas** da versão com Claude, e a justificativa
diz qual regra decidiu e quais trechos do título/resumo a acionaram.

```bash
# testar um artigo avulso
python3 triagem_resumos_deterministica.py --titulo "Agent S: An Open Agentic Framework" --resumo "We present Agent S ..."

# calibrar com os 50 primeiros artigos
python3 triagem_resumos_deterministica.py artigos_triagem.csv --amostra 50 --so-avaliados -o amostra.csv

# rodar tudo
python3 triagem_resumos_deterministica.py artigos_triagem.csv -o triagem_resumo.csv
```

Requer só Python 3.8+ e os arquivos `triagem_titulos.py` e `triagem_resumos.py` na mesma pasta (reaproveita o
vocabulário de sinais e a leitura/escrita da planilha). Aceita as mesmas opções de entrada e seleção do
`triagem_resumos.py` (`-o`, `--amostra`, `--so-avaliados`, `--filtro`, `--sem-resumo`, `--coluna-*`); duplicatas (EC3)
e artigos sem resumo são tratados exatamente do mesmo jeito.

### Perfis (`--perfil`)

| Perfil | Comportamento | Quando usar |
|---|---|---|
| `rigoroso` (padrão) | `yes` só com evidência forte; o que não passa nesse crivo vira `maybe` (as exclusões `no` são as do `revocacao`). Ver "O que o `rigoroso` exige" abaixo. | Quando o resultado do `revocacao` traz `yes` demais e você quer uma lista curta e confiável de `yes`. |
| `revocacao` | `yes` mais amplo; só dá `no` com evidência forte; na dúvida, `maybe`. Coerente com o `prompt.md` ("favorável à revocação"). | Triagem de apoio em que **não se pode perder** artigo relevante. |
| `precisao` | Aplica todas as regras de exclusão (EC1, EC2, NIC1, NIC8). Mais `no`, menos leitura, mas perde artigos relevantes. | Quando o tempo de leitura pesa mais do que a revocação. |

**O que o `rigoroso` exige para dar `yes`** (tudo o que não cumprir vira `maybe`, ou `no` se uma exclusão clara valer):

- **A (estudo secundário):** termo forte de revisão **no título** (survey, review, taxonomy, overview, SoK...), o
  resumo também se declara revisão ("this survey", "we review"...), menciona arquitetura/organização (arquitetura,
  framework, taxonomia, paradigma, padrão, componentes, pipeline...), tem multimodal forte, algum sinal de método ou
  avaliação e tema âncora no título (multimodal, LLM, agentes, paradigma cognitivo). Não pode ser revisão de um
  domínio de aplicação nem de avaliação.
- **B (agentes):** LLM no contexto, agente em sentido de LLM (não otimização/RL), "agente" no título e ≥3 menções no
  resumo, ≥2 componentes distintos (ou padrão de projeto/arquitetura no título), pontuação de estrutura ≥3, termo de
  arquitetura no resumo e entrada multimodal forte (ou, em padrões de projeto, arquitetura descrita).
- **D (RAG):** recuperação aumentada de verdade (RAG, "retrieval-augmented"...) com LLM/geração, estrutura descrita,
  ≥2 componentes, ≥4 menções a RAG/recuperação e ≥2 sinais de validação; "image retrieval", "text-video retrieval"
  e hashing não contam.
- **C/E (framework multimodal):** exceção restrita a EC1 do `prompt.md` aplicada à risca: o resumo **declara**
  "propomos/apresentamos um framework/sistema/arquitetura/pipeline" (e não rede, modelo, método, atenção ou perda),
  descreve vários componentes e a organização (fusão, etapas, ramos), cita arquitetura/organização, tem entrada
  multimodal forte, mostra ≥4 sinais de validação (experimento, benchmark, resultados...) e não é dataset/benchmark
  nem procedimento de treino (distilação, aprendizado contrastivo...).

Os limiares ficam no topo do arquivo (`RIG_ESTRUTURA_MIN`, `RIG_TRICK_MAX`, `RIG_IMPL_MIN_CE`, `RIG_IMPL_MIN_D`,
`RIG_RAG_MIN_D`, `RIG_DESIGN_MIN_D`, `RIG_DESIGN_MIN_B`, `RIG_AGENT_MIN_B`, `RIG_ESTRUTURA_MIN_B`). Em C/E o conjunto ouro é o limite: apertar mais faz ao menos um ouro deixar de
ser `yes`. Em A, B e D o ouro tem poucos artigos (5, 5 e 1) e não limita; dá para apertar mais, mas sem como validar.

## 4. O quanto confiar nas regras (medido)

Comparei as regras com os rótulos do Claude em `amostra_1000.csv` (1000 artigos) e com o conjunto ouro
(`artigos_ouro.csv`). Os 1000 artigos foram divididos em 60% para **desenvolvimento** (onde as regras foram
calibradas) e 40% para **teste cego** (não usado para ajustar nada). "Revocação dos `yes`" é a fração dos
artigos que o Claude marcou `yes` e que as regras mantiveram como `yes` ou `maybe`.

| Perfil | Conjunto | Concordância de classe com o Claude | Revocação dos `yes` do Claude | Artigos marcados para leitura (Claude: 49–55%) |
|---|---|---|---|---|
| `revocacao` | desenvolvimento | 42,5% | 94,2% | 80,0% |
| `revocacao` | **teste cego** | 42,2% | **86,1%** | 76,4% |
| `precisao` | desenvolvimento | 53,0% | 63,2% | 47,0% |
| `precisao` | **teste cego** | 54,0% | **55,1%** | 41,3% |

**Conjunto ouro:** os 15 artigos saem `yes` nos três perfis.

Perfil `rigoroso` na mesma amostra (as regras novas foram ajustadas olhando os erros do conjunto de
desenvolvimento; o de teste só foi consultado para medir):

| Conjunto | `yes` das regras (Claude marcou 33–37%) | Dos `yes` das regras, quantos o Claude também marcou `yes` | `yes` do Claude que viraram `yes` | `yes` do Claude mantidos como `yes` ou `maybe` |
|---|---|---|---|---|
| desenvolvimento | 5,0% | 62,1% | 9,5% | 88,4% |
| **teste** | 4,7% | **75,0%** | 9,5% | 74,1% |

Na planilha completa de 16.052 artigos, os `yes` passaram de 4.967 (`revocacao`, 31%) para 485 (`rigoroso`, 3,0%).

Como ler esses números:

- **As regras não substituem o Claude.** A concordância exata de classe fica em torno de 42–54%; para comparação,
  "sempre `no`" acertaria ~46%. Nos artigos do tipo "modelo multimodal de ML" (a maioria do corpus), a fronteira entre
  `E` (framework), `EC1` (modelo de ML puro) e `M` é uma decisão semântica que o vocabulário quase não revela: nem o
  próprio Claude é consistente entre artigos muito parecidos.
- **Não é falta de esforço nas regras.** Um classificador treinado nos mesmos 600 artigos (TF-IDF + regressão
  logística, e até um GBM com todos os meus sinais) também fica em ~52–55% no teste. Com esses dados, esse parece ser o
  teto de qualquer abordagem baseada em texto.
- **O que as regras fazem bem:** descartar "multimodal" em sentido não computacional e artigos sem nenhum sinal de
  multimodalidade/LLM/agentes (`NIC7`, ~92% de acerto), reconhecer surveys, agentes e RAG com vocabulário claro, e
  filtrar benchmarks, segurança e técnicas estreitas.
- **O perfil `rigoroso` troca revocação por confiança:** a lista de `yes` fica curta e ~62–75% deles também são `yes`
  para o Claude, mas só ~10% dos `yes` do Claude chegam como `yes` (a maioria vira `maybe`). Por isso **leia também os
  `maybe`**: ele serve para priorizar a leitura, não para descartar artigos.
- **O perfil `revocacao` filtra pouco:** marca ~76–80% dos artigos para leitura, contra ~50% do Claude; economiza
  cerca de 1 em cada 4 leituras e perde ~6–14% dos `yes` do Claude. O perfil `precisao` marca quase a mesma fração
  que o Claude, mas perde 37–45% dos `yes`.
- **Referência é o Claude, não a verdade.** Os rótulos de comparação são do Claude com o `prompt.md` atual, não de
  revisão humana. Trate o resultado das regras como triagem de apoio e revise `yes` e `maybe`.

---

## 5. Entrada e saída

**Entrada.** CSV com ao menos `title` e `abstract`. Se houver a coluna `classification` (da etapa 1), só
vão ao Claude as linhas `yes` e `maybe` (mude com `--filtro`). Sem essa coluna, **todas** as linhas são
avaliadas.

Tratamento automático, sem chamar o Claude:

- **Duplicatas** (mesmo título normalizado): marcadas `no / EC3`, com a `key` da primeira ocorrência
  em `duplicado_de`.
- **Sem resumo** (vazio ou com menos de 50 caracteres): marcadas `maybe / M`. Use
  `--sem-resumo enviar` para mandar só o título ao Claude.

**Saída.** A mesma planilha, preservando colunas, linhas, aspas e separador, com estas colunas no fim:

| Coluna | Conteúdo |
|---|---|
| `duplicado_de` | `key` da primeira ocorrência do título, se for duplicata (EC3) |
| `abstract_classification` | `yes`, `no` ou `maybe` (vazio se não foi avaliado) |
| `abstract_code` | `A`–`E` (yes) · `M` (maybe) · `N0`, `NIC7`, `NIC1`, `EC1`, `EC2`, `EC5`, `NIC8`, `EC3` (no) |
| `abstract_ic` | IC atendidos, separados por `;` |
| `abstract_ec` | EC aplicados, separados por `;` |
| `abstract_justificativa` | 1 a 2 frases em português |
| `final_classification` | decisão final após as duas etapas |

---

## 6. Opções (versão com Claude)

| Opção | Efeito |
|---|---|
| `-o, --saida` | CSV de saída (padrão: `<entrada>_resumo.csv`) |
| `--backend {login,api}` | Como falar com o Claude (padrão: `login`) |
| `--modo {lote,direto}` | Só na API. Padrão: `lote`. No login é sempre `direto` |
| `--modelo` | Modelo (padrão: `claude-sonnet-5-5`) |
| `--prompt` | Arquivo do prompt (padrão: `prompt_triagem_resumo.md` ou `prompt.md`) |
| `--amostra N` | Avalia só os **N primeiros** artigos elegíveis da planilha, na ordem (sem sorteio) |
| `--so-avaliados` | Grava no CSV só as linhas enviadas ao Claude (com `--amostra 50`, só 50 linhas) |
| `--filtro` | Valores de `classification` a enviar (padrão: `yes,maybe`) |
| `--sem-resumo {maybe,enviar}` | O que fazer com artigo sem resumo (padrão: `maybe`) |
| `--workers` | Chamadas em paralelo (padrão: 3 no login, 4 na API) |
| `--esperar-limite` | Só login. Ao atingir o limite de uso (janela de 5 h), espera a renovação e continua sozinho |
| `--espera-max HORAS` | Só login. Maior espera aceita por vez (padrão: 6). Acima disso, para |
| `--espera-sondagem MIN` | Só login. Sem horário de reset, testa de novo a cada N min (padrão: 15) |
| `--simular` | Mostra contagens e um exemplo, sem chamar o Claude |
| `--tamanho-lote`, `--intervalo` | Só API/lote: artigos por lote (5000) e segundos entre consultas (60) |
| `--cache-1h` | Só API: cache do prompt por 1 h |
| `--coluna-titulo`, `--coluna-resumo`, `--coluna-key`, `--coluna-classificacao` | Nomes de colunas, se forem diferentes do padrão |

---

## 7. Retomada e cache (versão com Claude)

- Cada resposta é gravada em `<saida>.respostas.jsonl`. Rodar de novo **não reenvia** artigos já
  avaliados, o que vale para os dois backends (interrupção, limite de uso, falha de rede).
- Para refazer do zero (por exemplo, depois de mudar o `prompt.md`), **apague** o `.respostas.jsonl`
  daquela saída, ou use outro nome em `-o`.
- Artigos que falharam são reenviados na próxima execução do mesmo comando.

---

## 8. Problemas comuns (versão com Claude)

| Mensagem | Causa e solução |
|---|---|
| `Comando 'claude' não encontrado` | Claude Code não instalado ou fora do PATH. Instale e rode `claude --version`. |
| `Parado: ... login ...` | Sessão expirada. Abra `claude`, rode `/login` e repita o comando. |
| `Parado: ... limite de uso ...` | Limite da assinatura. Repita o comando depois da renovação, ou use `--esperar-limite` para o script esperar sozinho. |
| `... além do máximo de espera` | A renovação está além de `--espera-max` (ex.: limite semanal). Rode de novo depois da data informada, ou aumente `--espera-max`. |
| `--modo lote só existe no backend api` | Use `--backend api`, ou tire `--modo lote`. |
| `Backend api: defina ANTHROPIC_API_KEY` | Faça o `export ANTHROPIC_API_KEY=...`. |
| `Instale o SDK: pip install anthropic` | Falta o SDK (só no backend api). |
| `A API recusou a chave` | Chave inválida ou sem permissão. Gere outra no console. |
| `Coluna de título 'title' não encontrada` | Use `--coluna-titulo` / `--coluna-resumo` com o nome certo. |
| Classificação diferente entre duas execuções | Esperado em casos limítrofes: não há `temperature: 0` em nenhum dos dois backends. |
