# Wardrive Manager

Sistema local (Flask + SQLite) para organizar, analisar e **exportar seus dados de
wardriving** — com CSVs que sobem tanto no **[WiGLE](https://wigle.net)** quanto no
**[WDGoWars](https://wdgwars.pl)**.

Feito a partir dos seus arquivos reais em
`C:\Users\luizv\OneDrive\Área de Trabalho\ward\send` (ESP32 Marauder 1.4 e
WDGoWars/projectZero 1.6) — nada de dados de exemplo: o sistema já leu, indexou
e conferiu os 33 arquivos que estavam lá.

---

## Começando

```bat
:: na pasta do projeto, com duplo clique:
iniciar.bat
```

Ele cria o ambiente `.venv`, instala as dependências e abre
<http://127.0.0.1:5000> no navegador. O servidor escuta **somente em 127.0.0.1**
(só a sua máquina).

Pela linha de comando:

```bash
.venv/Scripts/python.exe cli.py serve --port 5000     # interface web
.venv/Scripts/python.exe cli.py status                 # resumo do banco
.venv/Scripts/python.exe cli.py importar               # varre as pastas configuradas
.venv/Scripts/python.exe cli.py importar --watch 60    # varre a cada 60 s (deixe rodando)
.venv/Scripts/python.exe cli.py exportar --destino wigle --modo novo
.venv/Scripts/python.exe cli.py enviar --destino wdgwars --modo novo --simular
.venv/Scripts/python.exe cli.py enviar --destino wigle --export 3 --real
.venv/Scripts/python.exe cli.py validar --destino wigle
```

---

## O que o sistema faz

**Importar**
* Varre pastas (recursivo) e importa `.log`, `.csv`, `.txt`, `.gz`; também aceita
  upload pelo navegador (arrastar e soltar).
* Entende `WigleWifi-1.4` (11 colunas, sem Frequency), `1.6` (12 colunas) e o
  layout completo de 14 colunas, mapeando **por nome de coluna** — versões
  diferentes entram sem ajuste manual.
* Deduplica por `sha256 + nome`: rodar o scan duas vezes não duplica nada.
* Deriva a frequência a partir do canal quando a coluna não existe (2,4 / 5 / 6 GHz).
* Guarda também os arquivos sem nenhuma linha, como sessão de controle.

**Analisar**
* Painel com totais, coletas por dia, top SSIDs, segurança dos APs, canais, fila de
  upload e problemas de qualidade (linhas sem GPS, inválidas, arquivos repetidos).
* Lista de redes com busca, filtro por tipo/segurança/período e ordenação.
* Detalhe da rede (todos os nomes vistos naquele MAC, sinal mais forte, coordenada
  de referência) e detalhe da sessão (metadados, pré-header, área coberta, tabela
  completa de observações).
* Escolher uma rede (na lista de Redes ou digitando o MAC/SSID no mapa) filtra o mapa
  para **os lugares onde só ela apareceu**: os pontos dela, o traçado na ordem das
  coletas e um resumo com quantas vezes/sessões/locais, RSSI médio e a lista de
  sessões onde foi vista.
* Mapa (Leaflet + OpenStreetMap) com pontos por observação ou uma bolinha por rede,
  coloridos por tipo e dimensionados pelo RSSI — mais uma **camada de calor** opcional
  (densidade das coletas, com peso por contagem ou pelo RSSI) que se reescala com o zoom.
  As duas camadas convivem: os marcadores/agrupamentos ficam por cima do calor.

**Exportar**
* Seis modos de seleção: tudo, somente o que **ainda não subiu** para o destino,
  sessões escolhidas, período, tipo (Wi-Fi / BLE / BT / celular) e RSSI máximo.
* Três tratamentos de duplicata: remover linhas idênticas (padrão), uma amostra por
  rede e sessão, ou nenhum.
* Pre-header do arquivo de origem (atribuição correta a Marauder/WDGoWars) ou do
  perfil configurado.
* Cada lote sai em `data/exports/<data>_<destino>_<rótulo>/` com as partes `.csv`,
  `manifest.json`, `upload.sh` (curl) e `upload.ps1` (PowerShell) — dá para subir
  tudo sem a interface.

**Enviar**
* WiGLE: `POST /api/v2/file/upload` com `Basic` (nome da API + token) e
  `donate=true|false`.
* WDGoWars: `POST /endpoint/upload-csv` com o cabeçalho `X-API-Key`.
* Fila serial (o WDGoWars recusa uploads simultâneos), com tratamento de
  **429** (espera o `retry_after`), **409** (arquivo repetido = sucesso),
  **413** e **524** — nesses dois últimos o lote é **dividido em metades e
  reenviado automaticamente**, e cada tentativa fica registrada no histórico.
* O primeiro envio de cada lote pode ser uma **simulação**: gera os arquivos e
  mostra exatamente o que iria, sem tocar nos servidores.
* Modo incremental: o que já foi aceito por um destino nunca é reenviado para ele
  (inclusive as linhas descartadas como duplicata, que também saem da fila).

---

## Onde ficam as coisas

```
wardrive-manager/
├─ iniciar.bat                 inicia tudo com dois cliques
├─ cli.py                      linha de comando (serve, importar, exportar, enviar…)
├─ wardrive/                   aplicação Flask
│  ├─ parser.py                leitura dos CSVs WiGLE
│  ├─ exporter.py              geração dos lotes compatíveis
│  ├─ uploaders.py             clientes WiGLE e WDGoWars
│  ├─ upload_service.py        fila, divisão de lote, modo incremental
│  ├─ importer.py              varredura de pastas e importação
│  ├─ queries.py               agregações das telas
│  ├─ views.py / api.py        rotas HTML e JSON
│  └─ templates/ static/       interface
├─ data/
│  ├─ wardrive.db              banco SQLite (o único dado que importa)
│  ├─ secrets.json             suas chaves de API (fora do banco)
│  ├─ settings.json            preferências
│  └─ exports/                 lotes gerados
├─ docs/COMPATIBILIDADE.md     regras exatas dos dois serviços, com fontes
└─ tests/                      41 testes (unittest), incluindo um servidor de mentira
```

## Subir o serviço

**Primeiro plano** (janela aberta, `Ctrl+C` encerra — bom para ver o log na hora):

```bat
cd /d C:\Users\luizv\wardrive-manager
.venv\Scripts\python.exe cli.py serve --port 5000
```

**Segundo plano** (janela minimizada, sobrevive ao fechar o terminal):

```bat
servico.bat            :: porta 5000
servico.bat 8080       :: outra porta
parar.bat              :: encerra
parar.bat 8080         :: encerra a da 8080
```

`servico.bat` espera o serviço responder, mostra a URL e grava a saída em
`data\servidor.log` (`data\servidor.err.log` para erros). O pid fica em
`data\servidor.pid`, e é por ele que `parar.bat` encerra o processo certo — sem
mexer em nenhum outro Python da máquina.

**Conferir se está no ar e quanto falta enviar:**

```bash
.venv/Scripts/python.exe cli.py status
# Serviço:    no ar em http://127.0.0.1:5000 (pid 36432)
# Sessões:    33
# Observações:      8148
# Pendentes:  WiGLE 8148 linhas | WDGoWars 8148 linhas
# Chaves:     WiGLE ausente | WDGoWars ausente
```

Pela linha de comando também dá para importar, exportar e enviar (ver os
exemplos no começo deste arquivo). Para deixar rodando sempre que o Windows
iniciar, agende o `servico.bat`:

```bat
schtasks /Create /TN "Wardrive Manager" /SC ONLOGON /TR "C:\Users\luizv\wardrive-manager\servico.bat"
schtasks /Delete /TN "Wardrive Manager" /F
```

O serviço escuta só em `127.0.0.1`. Se quiser acessar do celular na mesma rede,
troque para `--host 0.0.0.0` ciente de que a interface ficaria exposta na rede
local (sem senha).

## Chaves de API

Preencha na aba **Uploads** (ou em `data/secrets.json`, ou ainda nas variáveis de
ambiente `WIGLE_API_NAME`, `WIGLE_API_TOKEN`, `WDGWARS_API_KEY` — estas têm
prioridade):

| Destino | O que colar |
| --- | --- |
| WiGLE | *API name* e *token* de <https://wigle.net/account> (também aceita o texto “Encoded for use”) |
| WDGoWars | chave de 64 caracteres gerada no seu perfil em wdgwars.pl |

As chaves ficam só na sua máquina, nunca são enviadas para outro lugar senão o
respectivo serviço, e não aparecem de volta na tela (só mascaradas).
Cada botão **Testar credencial** faz uma chamada somente-leitura para confirmar.

## Testes

```bash
.venv/Scripts/python.exe -m unittest discover -s tests -t .           # tudo
.venv/Scripts/python.exe -m unittest tests.test_dados_reais -v         # confere contra seus arquivos
.venv/Scripts/python.exe tools/smoke.py http://127.0.0.1:5000          # navega a interface de ponta a ponta
.venv/Scripts/python.exe tools/verifica_scripts.py                     # roda o upload.sh gerado
.venv/Scripts/python.exe tools/servidor_mentira.py 5099 saida.json     # servidor local que imita as duas APIs
```

Os testes cobrem: leitura dos formatos 1.4/1.6/14 colunas e casos de borda,
ida-e-volta do CSV gerado, limites de tamanho e linhas por parte, importação
idempotente, os números reais dos seus arquivos, e o fluxo de upload completo
contra um servidor de mentira que imita as respostas dos dois serviços
(incluindo 429, 409, 413 e 524).
