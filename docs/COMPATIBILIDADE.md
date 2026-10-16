# Compatibilidade com WiGLE e WDGoWars

Documento de referência do que o Wardrive Manager grava e envia. Tudo aqui foi
verificado contra as fontes citadas e coberto por testes automatizados
(`tests/test_exporter.py`, `tests/test_upload.py`).

---

## 1. Formato do arquivo (os dois serviços usam o mesmo)

### Pre-header (primeira linha)

```
WigleWifi-1.6,appRelease=<versão do app>,model=<modelo>,release=<versão do firmware>,device=<aparelho>,display=<tela>,board=<placa>,brand=<marca>
```

* Sempre sai `WigleWifi-1.6`, mesmo quando o arquivo de origem era 1.0/1.2/1.4
  (subir de versão é aceito; descer não é).
* Campos com vírgula são colocados entre aspas conforme a RFC 4180 — o parser do
  WiGLE usa um leitor CSV de verdade, então `brand=Moog, LLC` precisa vir como
  `"brand=Moog, LLC"`.
* Quando o lote tem uma única sessão de origem, o pre-header vem do arquivo
  original (atribuição correta: `brand=JustCallMeKoko` do Marauder, `brand=WDGoWars`
  do warrior). Em lotes mistos, usa o perfil configurado.

### Cabeçalho (segunda linha) — 14 colunas, fixas

```
MAC,SSID,AuthMode,FirstSeen,Channel,Frequency,RSSI,CurrentLatitude,CurrentLongitude,AltitudeMeters,AccuracyMeters,RCOIs,MfgrId,Type
```

Escrevemos sempre as 14 colunas, inclusive quando a origem tinha 11 ou 12: os
importadores dos dois serviços leem **por nome de coluna**, e um cabeçalho
incompleto muda o significado das posições.
**Cada parte de um lote dividido repete as duas linhas de cabeçalho** — fragmento
sem cabeçalho faz o importador do WiGLE usar o layout antigo e ler RSSI como
latitude.

### Linhas

| Coluna | Como sai |
| --- | --- |
| `MAC` | maiúsculo; aceita também chave de célula (`310410_56967_4118917`) |
| `SSID` | como veio (pode ser vazio em BLE) |
| `AuthMode` | capacidades como capturadas: `[WPA2-PSK-CCMP+TKIP][ESS]`, `[BLE]`, `Misc [LE]` |
| `FirstSeen` | `AAAA-MM-DD HH:MM:SS` (o formato “SQL seconds” que o WiGLE exige) |
| `Channel` | inteiro; vazio quando não se aplica |
| `Frequency` | MHz inteiro; **derivado do canal** quando a origem não tem a coluna (1.4); vazio em BLE/BT |
| `RSSI` | inteiro (dBm) |
| `CurrentLatitude` / `CurrentLongitude` | 8 casas decimais; **vazio quando não há GPS válido** |
| `AltitudeMeters` | inteiro (o WiGLE espera metros inteiros) |
| `AccuracyMeters` | até 2 casas |
| `RCOIs`, `MfgrId` | como capturados (normalmente vazios) |
| `Type` | `WIFI`, `BLE`, `BT`, `GSM`, `CDMA`, `WCDMA`, `LTE`, `NR` |

Frequência derivada do canal: `1–13 → 2407+5·c`, `14 → 2484`,
`32–177 → 5000+5·c` (5 GHz), `178–233 → 5950+5·c` (6 GHz).

### Codificação e quebras de linha

UTF-8 sem BOM, linhas terminadas em CRLF (RFC 4180). Campos com vírgula, aspas ou
quebra de linha são escapados com aspas duplas, exatamente como manda o padrão.

Fonte: <https://api.wigle.net/csvFormat.html> (WiGLE CSV Format v1.6).

---

## 2. Upload no WiGLE

| Item | Valor |
| --- | --- |
| Endpoint | `POST https://api.wigle.net/api/v2/file/upload` |
| Autenticação | `Authorization: Basic base64(<api_name>:<api_token>)` — também aceitamos o texto pronto “Encoded for use” de wigle.net/account |
| Corpo | `multipart/form-data`: campo `file` (nome do arquivo, tipo `text/csv`) + campo `donate` = `true`/`false` |
| Validação de credencial | `GET /api/v2/profile/uploads?pagestart=0&pageend=1` (lista o último envio) |
| Sucesso | `{"success": true, "results": [{"transid": "...", "networks": N, ...}]}` — guardamos o `transid` |
| Tamanho | ≈40 MB por arquivo (o portal divide acima disso no servidor); `.csv.gz` é aceito e o limite vale para o tamanho **descomprimido** |

Fonte: especificação acima, contrato usado pelo cliente oficial
(`wigle-wifi-wardriving`, `POST /api/v2/file/upload`, Basic auth + multipart) e pelo
plugin pwnagotchi (`data={"donate": "false"}, files={"file": ...}`).

---

## 3. Upload no WDGoWars

| Item | Valor |
| --- | --- |
| Endpoint | `POST https://wdgwars.pl/endpoint/upload-csv` (a rota também existe em `/api/upload-csv`) |
| Autenticação | cabeçalho `X-API-Key: <chave de 64 hex>` — `Authorization: Bearer` é recusado |
| Corpo | `multipart/form-data`, campo `file` com o CSV `.log`/`.csv` no formato WigleWifi-1.6 |
| Validação de credencial | `GET https://wdgwars.pl/api/me` (usuário, gangue, contadores, badges) |
| Sucesso | `{"ok":true,"imported":N,"captured":N,"duplicates":N,"no_gps":N,"bad_rows":N,"cooldown":S,"merged_samples":N,"total":N}` |

Erros tratados pelo cliente (e testados):

| HTTP | Corpo | O que fazemos |
| --- | --- | --- |
| 400 | `{"error":"Invalid data format"}` | erro registrado (CSV ausente/malformado) |
| 401 | `{"error":"Invalid API key"}` | erro registrado, com a mensagem do servidor |
| 409 | `{"error":"duplicate_upload"}` | tratado como **sucesso** (o arquivo já estava lá) |
| 413 | `payload-too-large` (máx. 15 728 640 bytes) | divide o lote em duas metades e reenvia |
| 429 | fila por conta, com `retry_after` | espera o tempo pedido e tenta de novo (até 3 vezes) |
| 524 | timeout do Cloudflare | divide o lote e reenvia (as linhas podem já estar entrando na origem — por isso a divisão, nunca o reenvio do mesmo arquivo inteiro) |

Limites aplicados na geração de cada parte: **≤10 000 linhas** e **≤14 MB**
(o origin processa o CSV de forma síncrona e o Cloudflare corta em 120 s; com
10 000 linhas cada parte leva dezenas de segundos).

Fontes: documentação pública da plataforma
(<https://www.wdgwars.pl/press>, seção *Developer API*) e a engenharia reversa
mantida pelo ecossistema de uploaders
(<https://github.com/Yggdrasil-AI-labs/wigle-to-wdgwars>, seções *WDGWars API
reference* e *Cloudflare 524 footgun*), confirmada pelos firmwares que já sobem
direto do aparelho (Bruce WDGoWars, WDGWMarauderV8, ESP32 Marauder v1.18+).

---

## 4. Como o modo incremental funciona

1. Ao gerar um lote, o sistema grava ao lado de cada parte um arquivo
   `partNN.ids.jsonl` (as observações enviadas) e, quando o dedupe descarta
   linhas repetidas, um `partNN.cover.txt.gz` com **todas** as observações
   equivalentes àquelas linhas.
2. Quando o destino responde sucesso (ou “duplicado”), essas observações entram
   na tabela `obs_uploads` como enviadas **para aquele destino**.
3. Na próxima exportação no modo “somente o que ainda não subiu”, essas linhas
   ficam de fora — inclusive as duplicatas descartadas, que sem esse cuidado
   reapareceriam como um lote pequeno a cada rodada.
4. Cada destino tem sua própria fila: o que já subiu para o WiGLE continua
   pendente para o WDGoWars.

## 5. Privacidade

* O servidor escuta apenas `127.0.0.1`.
* Chaves em `data/secrets.json` (permissão restrita quando o sistema permite) ou em
  variáveis de ambiente; nunca em log, nem de volta na interface.
* Nenhuma chamada de rede acontece além dos endpoints configurados — e o primeiro
  envio é sempre uma simulação, que não faz POST nenhum.
* Os dados (MACs, SSIDs, coordenadas) só saem da sua máquina quando você manda,
  e os dois serviços recebem exatamente as mesmas colunas que os firmwares enviam
  hoje direto do aparelho.
