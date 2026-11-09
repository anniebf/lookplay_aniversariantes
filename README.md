# Lookplay Aniversariantes

RPA que publica na TV corporativa (sistema **Lookplay**) os aniversariantes de cada filial,
com um dashboard para acompanhar e repetir as execuções.

Toda semana, para cada filial, o robô:

1. consulta no Oracle quem faz aniversário de **vida** (data de nascimento) e de **empresa**
   (data de admissão, chamada de "casa" no código);
2. gera um CSV por filial e tipo;
3. envia o CSV ao conteúdo daquela filial no Lookplay, por requisições HTTP;
4. registra o resultado no Oracle, que alimenta o dashboard.

Ele substitui o projeto anterior, que fazia o mesmo com Selenium: abria um
Chrome, clicava em cada filial e anexava o arquivo. O antigo levava cerca de uma hora e não
deixava rastro do que foi enviado. Este roda em segundos por filial, confirma cada envio e mostra
tudo numa tela.

---

## Sumário

- [Arquitetura](#arquitetura)
- [Como foi feito: decisões técnicas](#como-foi-feito-decisões-técnicas)
- [Integração com o Lookplay](#integração-com-o-lookplay)
- [Banco de dados](#banco-de-dados)
- [Configuração (.env)](#configuração-env)
- [Rodar localmente](#rodar-localmente)
- [Deploy com Docker](#deploy-com-docker)
- [API](#api)
- [Testes](#testes)
- [Operação e problemas comuns](#operação-e-problemas-comuns)
- [Pontos de atenção](#pontos-de-atenção)
- [Como estender](#como-estender)

---

## Arquitetura

```
                    ┌──────────────── processo único (uvicorn) ────────────────┐
                    │                                                          │
 Oracle             │  RepositorioOracle ──┐                                   │
 view RH    ───────►│  (2 consultas)       │                                   │
                    │                      ▼                                   │
                    │  Agendador ───► Orquestrador ───► ClienteLookplay ───────┼──► Lookplay
                    │  (seg 06:00)     │    ▲            (requests + cookie)    │    (HTTP)
                    │                  │    │                                  │
 Navegador ◄────────┤  Dashboard ◄── API ───┘                                  │
 (HTML/JS)          │  (index.html)  (FastAPI)                                 │
                    │                  │                                       │
                    │                  ▼                                       │
 Oracle  ◄──────────┤             Historico  (RPA.LOOKPLAY_ANIVERSARIANTES*)   │
 schema RPA         └──────────────────────────────────────────────────────────┘
```

Um único processo serve a API, o dashboard e o agendamento. O container não guarda estado:
o histórico e a trava de execução ficam no Oracle.

### Fluxo de uma execução (`app/orquestrador.py`)

```
consulta o Oracle (todas as filiais de uma vez)
para cada tipo (empresa, vida) e cada filial:
    sem aniversariantes          -> registra "sem_aniversariantes", não acessa o Lookplay
    com aniversariantes:
        abre a sessão no Lookplay (só na primeira vez que precisar)
        gera o CSV em memória e envia
        confirma que o arquivo mudou no Lookplay
        registra "enviado" + quem foi enviado
    conteúdo não existe no Lookplay -> "sem_conteudo"
    erro de uma filial           -> "erro", segue para a próxima
    sessão expirada              -> "erro" nesta e nas próximas, sem novas tentativas
```

### Módulos

| Arquivo | Responsabilidade |
|---|---|
| `app/config.py` | Lê o `.env` / variáveis de ambiente. Nenhum segredo nem dado da empresa no código |
| `app/dominio.py` | Regras puras: semana ISO, 29/02, formato do CSV. Sem I/O, fácil de testar |
| `app/repositorio_oracle.py` | Consultas de filiais e aniversariantes na view configurada; aplica as regras do `.env` |
| `app/cliente_lookplay.py` | Sessão, listagem, leitura do formulário e upload no Lookplay |
| `app/orquestrador.py` | Fluxo de uma execução (acima) e log por filial |
| `app/historico.py` | Histórico no Oracle (produção) ou SQLite (exemplo e testes) |
| `app/agendador.py` | Dispara o envio automático (padrão: segunda 06:00) |
| `app/painel.py` | Monta os dados do dashboard: junta Oracle, Lookplay e histórico, com cache |
| `app/api.py` | Rotas REST e autenticação HTTP Basic |
| `app/static/index.html` | Dashboard (HTML + JS puro, sem build) |
| `app/montagem.py` | Liga as peças conforme o modo (exemplo ou produção) |
| `app/main.py` | Entrada do servidor (`uvicorn app.main:app`) |
| `app/executar.py`, `main.py` | Envio pela linha de comando (cron, Airflow, manual) |
| `app/diagnostico.py` | Confere a conexão com o Lookplay **sem enviar nada** |
| `app/modo_exemplo.py` | Oracle e Lookplay falsos, para rodar sem acesso a nada |

As dependências entram pelo construtor (injeção de dependência): o mesmo `Orquestrador` roda
com o Lookplay real, com o modo exemplo ou com os dublês dos testes.

---

## Como foi feito: decisões técnicas

**Python + requests em vez de Selenium.** O Lookplay é um sistema PHP com formulários HTML.
Repetir as requisições que o navegador faz é mais rápido, não depende de Chrome nem de
ChromeDriver e cabe numa imagem Docker pequena. O HTML é lido com BeautifulSoup.

**Banco consultado antes do Lookplay.** O script antigo abria a filial no Lookplay e só depois
consultava o Oracle, e registrava como "sem aniversariantes" filiais que na verdade não tinham
conteúdo cadastrado. Agora as duas situações são distintas, e quem não tem aniversariante nem
abre sessão no Lookplay.

**Duas consultas ao Oracle em vez de uma por filial.** As consultas de vida e de empresa trazem
todas as filiais de uma vez e o Python agrupa. Os critérios de período são os mesmos do projeto anterior.

**Nada da empresa no código.** Endereço e usuário do Lookplay, a view de funcionários, as empresas e
seções excluídas, as seções renomeadas e a filial em destaque vêm do `.env`. O SQL só filtra o período;
as regras da empresa são aplicadas no Python (`aplicar_regras`). Em produção essas variáveis são
obrigatórias, mesmo vazias, para uma regra nunca sumir sem aviso.

**CSV em memória.** O arquivo não é gravado em disco: dados pessoais não ficam espalhados pelo
servidor. O formato é o mesmo aceito hoje (`;`, UTF-8 sem BOM, CRLF).

**Envio confirmado, não presumido.** O Lookplay responde HTTP 200 até quando a sessão expirou.
Por isso, depois do upload o cliente abre a tela de novo e só considera "enviado" se o
"Arquivo Atual" mudou.

**Histórico no Oracle, com SQLite para exemplo e testes.** O SQL é escrito uma vez, com
parâmetros nomeados (`:nome`) que os dois bancos aceitam. Uma classe "dialeto" resolve só o que
muda: conexão (pool no Oracle), id gerado (`RETURNING ... INTO`), limite de linhas e criação das
tabelas. As tabelas são criadas na primeira subida, se não existirem.

**Trava de execução no banco.** Um índice único só aceita uma execução `em_andamento`. Vale entre
processos e servidores: o dashboard, o agendamento e o terminal nunca enviam ao mesmo tempo. Uma
execução sem atividade há 10 minutos (queda do processo) é marcada como interrompida.

**Agendamento interno em vez de cron/Airflow.** O servidor que fica no ar verifica o horário a
cada 30 s. A checagem "já rodou nesta janela?" consulta o histórico no Oracle, então mesmo com
dois servidores ligados só um envia.

**Dashboard sem framework.** Um HTML com JS puro, servido pela própria API: não há build nem
`node_modules`. Todo dado entra por `textContent`, nunca como HTML, para impedir injeção.

**LGPD.** A API e o histórico guardam só nome, dia/mês e seção. O ano de nascimento nunca sai do
Oracle de origem. O log não leva nomes de pessoas.

---

## Integração com o Lookplay

O Lookplay não tem API documentada; o HTML é a interface. Se ele mudar, o único arquivo a
ajustar é `app/cliente_lookplay.py`. Para checar sem enviar nada: `python -m app.diagnostico`.

1. **Sessão:** usa o cookie de `LOOKPLAY_COOKIES`. Se estiver vazio ou expirado e `LOOKPLAY_SENHA`
   estiver preenchida, faz login no formulário de `index.php` (uma única tentativa, para não
   bloquear o usuário).
2. **Listagem:** `GET /modules/conteudo/_list.php?filtro=<FILTRO>`. Cada linha da tabela
   `#table-1` é um conteúdo; o título (2ª coluna) é o nome da filial e tem um formulário que
   aponta para `_edit.php`.
3. **Tela de edição:** `POST _edit.php` com o `id` da linha.
4. **Upload:** reenvia **todos** os campos do formulário (tipo, título, região, dias, semanas,
   status...) junto com o arquivo. Reenviar tudo evita apagar a configuração do conteúdo.

**Detalhe importante:** os campos `arquivo` e `texto` **não existem no HTML** da tela de edição.
O JavaScript do Lookplay os cria a partir de um modelo `<script id="<TIPO>-template">`. O cliente
lê esse modelo para montar esses campos (`texto=SEMANA`). Se o modelo ganhar um campo novo, o
envio é cancelado com erro, em vez de apagar um dado sem perceber.

Outras proteções: listagem vazia é erro (provável mudança no HTML), título repetido no mesmo
filtro cancela o envio daquela filial, e tela de login no meio do caminho vira "sessão expirada".

| Tipo | Filtro (padrão) | Configuração |
|---|---|---|
| Vida | `ANIVERSARIANTES_3` | `LOOKPLAY_FILTRO_VIDA` |
| Empresa | `TEMPO_CASA_3` | `LOOKPLAY_FILTRO_CASA` |

Os conteúdos `ANIVERSARIANTES_2` e `TEMPO_CASA_2` estão inativos e servem para testar sem afetar
a TV.

---

## Banco de dados

### Origem: view de funcionários (`ORACLE_VIEW`)

Consultas em `app/repositorio_oracle.py`, com os mesmos critérios do projeto anterior. A view
precisa ter as colunas `nome`, `dtnascimento`, `dataadmissao`, `secao`, `unidade` (filial),
`codsituacao` e `emp_protheus` (código da empresa).

- **Vida:** nascimento na **semana ISO** atual (`to_char(..., 'iw')`), 29/02 vira 28/02.
- **Empresa:** admissão no **mês** atual, com pelo menos 1 ano de casa.
- Exclui demitidos (`codsituacao = 'D'`).
- Empresas excluídas, seções excluídas e seções renomeadas: `ORACLE_EMPRESAS_EXCLUIDAS`,
  `ORACLE_SECOES_EXCLUIDAS` e `ORACLE_SECOES_RENOMEADAS` no `.env`.

### Histórico: schema `RPA`

Criado automaticamente na primeira subida (usuário precisa de `CREATE TABLE`):

| Tabela | Conteúdo |
|---|---|
| `RPA.LOOKPLAY_ANIVERSARIANTES` | Uma linha por execução: origem, tipo, filial, status, início, fim |
| `RPA.LOOKPLAY_ANIVERSARIANTES_ITENS` | Resultado de cada filial e tipo na execução |
| `RPA.LOOKPLAY_ANIVERSARIANTES_PESSOAS` | Quem foi enviado: nome, dia/mês, seção (sem ano) |
| `RPA.LOOKPLAY_ANIVERSARIANTES_EVENTOS` | Linhas do log mostradas abaixo da filial no dashboard |

Índice `LOOKPLAY_ANIVERSARIANTES_UQ_ANDAMENTO`: único sobre
`CASE WHEN status = 'em_andamento' THEN 1 END`, a trava de execução.

O DDL completo está em `DDL_ORACLE`, em `app/historico.py`. Datas ficam em `TIMESTAMP(0)` na hora
local do servidor; por isso o `TZ` precisa estar configurado.

Valores de `status` na execução: `em_andamento`, `concluida`, `concluida_com_erros`, `falhou`,
`interrompida`. Nos itens: `enviado`, `sem_aniversariantes`, `sem_conteudo`, `erro`. Valores de
`origem`: `dashboard`, `agendamento`, `agendador` (terminal).

---

## Configuração (.env)

Copie `.env.example` para `.env`. O `.env` nunca vai para o Git nem para a imagem Docker.

| Variável | Padrão | Descrição |
|---|---|---|
| `MODO` | `exemplo` | `exemplo` (dados fictícios, nada é enviado) ou `producao`. Em produção, as variáveis de Lookplay e `ORACLE_*` são obrigatórias |
| `usernameBd`, `passwordBd`, `dsn` | | Oracle (mesmos nomes do projeto antigo) |
| `LOOKPLAY_URL` | | Endereço do Lookplay, ex.: `https://SUA-EMPRESA.lookplay.tv/sistema` |
| `LOOKPLAY_USUARIO` | | Usuário do robô no Lookplay |
| `LOOKPLAY_COOKIES` | | Cookie da sessão, ex.: `PHPSESSID=abc` |
| `LOOKPLAY_SENHA` | | Senha do Lookplay; com ela, o login é refeito sozinho |
| `ORACLE_VIEW` | | View de funcionários, ex.: `SCHEMA.VIEW` |
| `ORACLE_EMPRESAS_EXCLUIDAS` | | Códigos de empresa que não entram, separados por vírgula: `75,80` |
| `ORACLE_SECOES_EXCLUIDAS` | | Seções que não entram: `EMPRESA\|SEÇÃO;EMPRESA\|SEÇÃO` |
| `ORACLE_SECOES_RENOMEADAS` | | Seções exibidas com outro nome: `EMPRESA\|SEÇÃO=NOVO NOME;...` |
| `FILIAL_MATRIZ` | | Filial em destaque e no topo do dashboard (nome exato) |
| `LOOKPLAY_FILTRO_VIDA` / `_CASA` | `ANIVERSARIANTES_3` / `TEMPO_CASA_3` | Filtros do Lookplay |
| `DASHBOARD_USUARIO` / `DASHBOARD_SENHA` | `admin` / | Login do dashboard. Obrigatório em produção |
| `HISTORICO` | `oracle` em produção, `sqlite` no exemplo | Onde fica o histórico |
| `HISTORICO_SCHEMA` / `HISTORICO_TABELA` | `RPA` / `LOOKPLAY_ANIVERSARIANTES` | Nome das tabelas |
| `AGENDA_ATIVA` | `1` | `0` desliga o envio automático |
| `AGENDA_DIA` / `AGENDA_HORA` | `segunda` / `06:00` | Quando enviar |
| `AGENDA_TOLERANCIA_MINUTOS` | `120` | Até quanto tempo depois ainda envia, se o servidor subir atrasado |
| `CACHE_CONSULTA_SEGUNDOS` | `300` | Cache das consultas ao Oracle/Lookplay no dashboard |
| `PAUSA_ENTRE_ENVIOS` | `0.5` | Pausa entre uploads, para não sobrecarregar o Lookplay |
| `TZ` | `America/Cuiaba` | Fuso dos horários (Docker) |
| `LOG_EM_ARQUIVO` | `1` | `0` no Docker: log só na saída padrão |

---

## Rodar localmente

Requer Python 3.12.

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
copy .env.example .env          # MODO=exemplo: não precisa de Oracle nem de Lookplay
.venv\Scripts\python -m uvicorn app.main:app --port 8765
```

Abra http://127.0.0.1:8765. Documentação interativa da API: http://127.0.0.1:8765/docs.

**Passar a usar o Oracle e o Lookplay reais**, com segurança:

1. Preencha o `.env` com Oracle, Lookplay e `DASHBOARD_SENHA`.
2. Rode `python -m app.diagnostico` (só leitura) e confira se a sessão e os campos estão certos.
3. Teste no filtro inativo, sem afetar a TV:
   ```powershell
   $env:MODO = "producao"; $env:LOOKPLAY_FILTRO_VIDA = "ANIVERSARIANTES_2"
   .venv\Scripts\python main.py --filial "Matriz" --tipo vida
   ```
4. Só então use `MODO=producao` com os filtros padrão.

---

## Deploy com Docker

A imagem leva só o código (`app/`, `main.py`). O servidor precisa apenas do `.env`.

**1. Construir e publicar (máquina de desenvolvimento)**

```bash
docker build -t <usuario-dockerhub>/lookplay-aniversariantes:latest .
docker push <usuario-dockerhub>/lookplay-aniversariantes:latest
```

**2. Rodar no servidor**

```bash
docker pull <usuario-dockerhub>/lookplay-aniversariantes:latest
docker run -d --name lookplay-aniversariantes --env-file /caminho/do/.env -e TZ=America/Cuiaba -e LOG_EM_ARQUIVO=0 -p 8765:8765 --restart unless-stopped --log-opt max-size=10m --log-opt max-file=5 <usuario-dockerhub>/lookplay-aniversariantes:latest
docker logs -f lookplay-aniversariantes
```

O log deve mostrar `Uvicorn running on http://0.0.0.0:8765` e
`Agendamento ativo. Próximo envio automático: ...`. O `--restart unless-stopped` religa o container
depois de reiniciar o servidor, o que garante o envio de segunda.

**Atualizar:** `docker pull`, `docker rm -f lookplay-aniversariantes` e o mesmo `docker run`.

Também há um `docker-compose.yml` para quem preferir `docker compose up -d --build` com o código.

**O que a imagem faz:** Python 3.12 slim, `tzdata` para o fuso, usuário sem privilégios,
`HEALTHCHECK` (o 401 da API conta como saudável) e driver Oracle em modo *thin* (sem Instant Client).

**O servidor precisa:** resolver o host do Oracle (`dsn`), acessar o Lookplay pela internet e ter a
porta 8765 liberada no firewall para os usuários do dashboard.

**Comandos úteis no servidor:**

```bash
docker exec lookplay-aniversariantes python main.py                       # envia todas as filiais agora
docker exec lookplay-aniversariantes python main.py --filial "Matriz" --tipo vida
docker exec lookplay-aniversariantes python -m app.diagnostico           # confere o Lookplay sem enviar
```

`main.py` devolve código 1 quando há erro ou filial fora do Lookplay, útil para cron ou Airflow.

---

## API

Todas as rotas exigem HTTP Basic (`DASHBOARD_USUARIO` / `DASHBOARD_SENHA`).

| Rota | Função |
|---|---|
| `GET /` | Dashboard |
| `GET /api/status` | Modo, execução em andamento (com a filial atual) e próximo envio automático |
| `GET /api/filiais` | Situação da semana por filial, resumo e filiais fora do Lookplay |
| `GET /api/filiais/{filial}/aniversariantes` | Aniversariantes da filial; `?tipo=vida\|casa` |
| `GET /api/filiais/{filial}/enviados` | Quem foi no último envio de cada tipo |
| `GET /api/filiais/{filial}/log` | Passos da execução atual ou da última da filial |
| `GET /api/execucoes` | Histórico (`?limite=20`) |
| `GET /api/execucoes/{id}` | Resultado filial por filial |
| `POST /api/execucoes` | Roda agora. Corpo: `{"tipo": "ambos\|vida\|casa", "filial": null}` |

`POST` com outra execução em andamento responde **409** dizendo o que está rodando.

```bash
curl -u admin:SENHA -X POST http://servidor:8765/api/execucoes \
  -H "Content-Type: application/json" -d '{"tipo":"vida","filial":"Matriz"}'
```

**Situação de uma filial na semana** (`app/painel.py`), da mais urgente para a menos:
`erro` → `sem_conteudo` (fora do Lookplay) → `nao_rodou` → `enviado` → `sem_aniversariantes`.
A filial assume a pior entre vida e empresa. Envios de semanas anteriores não contam.

---

## Testes

```powershell
.venv\Scripts\python -m pytest
```

Por padrão nada acessa Oracle ou Lookplay: o Lookplay é simulado com a biblioteca `responses`
(HTML igual ao real, inclusive o modelo JavaScript) e o histórico usa SQLite.

Para rodar também o histórico no **Oracle real**, em tabelas temporárias `LOOKPLAY_TESTE_<pid>`
que são apagadas no fim (as de produção não são tocadas):

```powershell
$env:ORACLE_TESTE = "1"; .venv\Scripts\python -m pytest
```

| Arquivo | Cobre |
|---|---|
| `tests/test_dominio.py` | Semana ISO, 29/02, formato do CSV |
| `tests/test_cliente_lookplay.py` | Cookie, login, campos do modelo JS, confirmação, proteções |
| `tests/test_orquestrador.py` | Fluxo, erros por filial, sessão expirada, trava, log |
| `tests/test_api.py` | Rotas, autenticação, situações, LGPD, conflito 409 |
| `tests/test_agendador.py` | Janela de segunda, não duplicar, adiar se ocupado |

---

## Operação e problemas comuns

| Sintoma no dashboard | Causa | O que fazer |
|---|---|---|
| Filial em "Fora do Lookplay" | Tem aniversariante, mas não há conteúdo com o **nome exato** da filial no filtro | Cadastrar o conteúdo no Lookplay e rodar a filial |
| Vários "erro" com "sessão expirou" | Cookie vencido e sem `LOOKPLAY_SENHA` | Atualizar `LOOKPLAY_COOKIES` no `.env` e reiniciar o container |
| "Não foi possível consultar o banco" | Oracle fora do ar ou `dsn` errado | `docker logs` mostra o erro completo |
| "Já está rodando: ..." | Outra execução em andamento | Esperar; o dashboard mostra o progresso |
| Execução "interrompida" | O processo caiu no meio | Rodar de novo a filial ou todas |
| "O modelo tem campos desconhecidos" | O Lookplay mudou a tela de edição | Ajustar `CAMPOS_ESPERADOS_NO_MODELO` em `cliente_lookplay.py` |

Mudanças no `.env` só valem depois de reiniciar o servidor (`docker restart lookplay-aniversariantes`).

---

## Pontos de atenção

- **Vida é por semana, empresa é por mês.** O CSV de empresa leva o mês inteiro, como no projeto
  antigo. O dashboard mostra a semana e lista o resto do mês à parte.
- **Filial que ficou sem aniversariantes não recebe upload**, então o CSV anterior continua no
  Lookplay (mesmo comportamento do script antigo).
- **Virada de ano:** o critério `iw` usa o número da semana; aniversários de 1 a 3 de janeiro podem
  cair na semana errada quando o ano vira no meio da semana.
- **Segurança:** a API expõe nomes e dia/mês de aniversário e permite disparar envios. Use senha
  forte, só na rede interna, e coloque HTTPS na frente se o acesso sair dela (HTTP Basic não
  criptografa).
- **Um processo só:** não suba com `--workers` > 1; o cache do painel fica em memória.
- **Script antigo:** o agendamento do projeto anterior (Selenium) deve ficar desligado, senão a TV
  recebe dois envios.

---

## Como estender

- **Mudou o Lookplay:** ajuste `app/cliente_lookplay.py`, atualize o HTML simulado em
  `tests/test_cliente_lookplay.py` e valide com `python -m app.diagnostico`.
- **Mudou o critério de aniversariante:** edite o SQL em `app/repositorio_oracle.py`. Se a regra de
  semana mudar, ajuste também `app/dominio.py`, que replica o critério para o dashboard.
- **Outro banco para o histórico:** crie um dialeto em `app/historico.py` com `nomes`, `sessao`,
  `parametros`, `primeiros`, `inserir` e `criar_estrutura`, e escolha-o em `app/montagem.py`.
- **Novo campo no dashboard:** a rota em `app/api.py`, os dados em `app/painel.py` e a tela em
  `app/static/index.html`.
