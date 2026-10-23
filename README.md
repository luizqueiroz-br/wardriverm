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
.venv/Scripts/python.exe cli.py categorizar --baixar   # classifica as redes por tipo de objeto
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
* **Kismet e app WiGLE Android**: importa `.netxml` (um registro por snapshot com GPS, a
  estação `client` fica de fora) e o `wiglewifi.sqlite` do app Android (lido em memória e
  somente leitura, colunas achadas por nome). Os dois são convertidos para o **formato
  WiGLE** antes de gravar, então estatísticas, dedupe e exportação seguem iguais — e o
  CSV que sai continua aceito no WiGLE e no WDGoWars.
* **Puxar do dispositivo**: `cli.py puxar --porta COM5` (ou o botão em Importar) lista o
  cartão SD pela serial, baixa só os arquivos novos e importa. `--diagnostico` mostra a
  resposta crua do firmware, para descobrir o protocolo do seu fork; arquivo binário
  (`.gz`) continua pelo cartão. Comando de listagem/leitura é configurável em
  `wardrive/dispositivo.py` (`sd list` / `sd read <arquivo>` por padrão).
* **No WiGLE**: na ficha de uma rede Wi-Fi, o botão consulta o BSSID na API do WiGLE
  (`/api/v2/network/search?netid=`) com a mesma credencial do upload e mostra quantas
  aparições globais a rede tem, onde, com que canal e criptografia. O resultado fica em
  `wigle_consultas` por 30 dias — a consulta gasta cota, então só "atualizar" repete.
  Erro de chave (401) e cota estourada (429) aparecem com texto próprio e **não** viram
  cache.
* **Suspeitos (SSID duplicado)**: o mesmo nome anunciado por placas de **fabricantes
  diferentes** na mesma célula de ~100 m, com sinal parecido — indício de *evil twin*.
  Ficam de fora o mesmo OUI (mesh e 2,4 + 5 GHz da mesma caixa), mais de 4 BSSIDs na
  célula (hotel) e MAC aleatório (hotspot de celular). Cada achado mostra os BSSIDs, o
  sinal, o canal, a hora e o grau do indício; a ficha da rede avisa quando ela aparece
  num par.
* **O que mudou**: página **Mudanças** compara duas janelas de tempo (e opcionalmente um
  raio) e devolve três listas — **novas**, **sumiram** e **mudaram** (mesmo MAC com outro
  SSID ou outra segurança, com as duas mudanças separadas), cada linha com atalho para o
  mapa. Avisa quando as janelas têm volumes muito diferentes, porque aí "novas" infla.
* **Qual canal usar**: em Config, informe o SSID/MAC da sua rede (ou a coordenada) e um
  raio; o sistema soma a **energia** dos vizinhos por canal — um AP a −40 dBm pesa muito
  mais que um a −75, e canal vizinho (4, 5, 8…) também invade — e ranqueia os candidatos:
  1/6/11 no 2,4 GHz e só os sem DFS (36–48, 149–165) no 5 GHz. Na ficha de uma rede há o
  atalho para analisar aquele ponto.
* **Mapa de risco**: modo `risco` no mapa pinta **uma bolinha por rede Wi-Fi** pela
  fragilidade — aberta (vermelho), WEP (laranja), WPA (amarelo), misto, WPA2 (azul),
  WPA3 (verde), 802.1X — sem agrupar, e a pior fica por cima. A página **Segurança**
  traz o resumo, o ranking de células (~100 m) com mais redes fracas e a lista de
  **SSID de fábrica** (nome do roteador como veio = senha do manual), com CSV
  normal ou mascarado.
* **Cobertura**: modo `cobertura` no mapa desenha a grade de ~100 m com as passagens
  (azul 1, verde 2–5, amarelo 6+); o vazio é onde você nunca passou. A mesma consulta
  devolve km rodados e km² cobertos da seleção, com o corte de salto do percurso.
* **Métricas de percurso**: cada sessão mostra **km rodados**, duração, velocidade média,
  redes por km e área coberta; o painel soma o acervo ("217,7 km em 26,5 h"). Sessão
  sem GPS aparece como "—", nunca 0 km. Trechos de GPS que "pulam" (> 2 km entre duas
  leituras) são descartados para não inventar quilometragem. Colunas calculadas na
  importação e recalculáveis por `cli.py percursos`.
* **Áreas privadas**: raios (casa, trabalho) onde **nada é publicado** — a linha sai
  do CSV, ou fica sem coordenada, no modo "sem GPS". Vale para exportação manual,
  automática e upload (o filtro roda dentro do gerador de lote, no caminho único).
  Antes de gerar, uma prévia obrigatória mostra quantas linhas serão omitidas e por
  qual área, com opção de cancelar; o manifesto do lote registra a contagem.
* **Rastreadores**: lista os dispositivos BLE vistos em muitas sessões **e** em muitos
  lugares diferentes ao longo de horas — o padrão de quem acompanha o percurso (tag,
  fone, carro). Aparelho fixo cai fora por construção: aparece em várias sessões, mas
  sempre no mesmo ponto. Cada candidato mostra sessões, locais distintos, extensão em
  km, período e RSSI, com "ver no mapa" e "é meu" (marca como conhecido e some da lista).
* **Classificação por tipo de objeto** (26 tipos): roteador/AP, celular/tablet,
  computador, veículo, patinete/bike compartilhada, drone, rastreador de frota,
  smart tag, vestível, beacon BLE, casa conectada, medidor de energia, robô aspirador,
  áudio, impressora, impressora 3D, maquininha (POS), câmera, TV/mídia, videogame,
  VR/AR, telefone IP, internet via satélite, placa de dev (ESP32), MAC aleatório e
  não identificado. Sai do **fabricante** (prefixo do MAC na base
  oficial OUI do IEEE) e do **nome da rede** — "VIVOFIBRA-1A2B" é roteador, "VW_MIB_6343"
  é carro, "SmartTag" é rastreador. Cada rede mostra o fabricante e a confiança da
  classificação (alta quando o nome confirma, média quando o fabricante faz de tudo,
  como Apple e Samsung); o que não dá para afirmar fica como "não identificado" em
  vez de chutar. Filtra na lista de Redes e no mapa, com quebra por categoria no painel.
* Mapa (Leaflet + OpenStreetMap) com pontos por observação ou uma bolinha por rede,
  coloridos por tipo e dimensionados pelo RSSI. A partir do **zoom 17** cada ponto
  vira o **ícone do objeto** (📶 roteador, 🚗 veículo, 📱 celular, 🏷️ smart tag…), sem
  agrupamento — ali só os pontos da tela são desenhados, então vale mesmo com 20 mil
  no filtro. De longe fica a bolinha agrupada. No zoom máximo (19) nada é agrupado. — mais uma **camada de calor** opcional
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
