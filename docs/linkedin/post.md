# Post para o LinkedIn — Wardrive Manager

Arquivos de imagem em `docs/linkedin/final/` (ordem sugerida: 01 → 02 → 03 → 04 → 05;
`00-montagem.png` é a alternativa de imagem única). Todos os MACs e SSIDs de terceiros
estão tarjados, o mapa está em escala regional e nenhuma chave de API aparece.

---

## Versão principal (copiar e colar)

Terminei um projeto que juntou duas coisas que eu gosto: rádio e código. 🛰️

Faz um tempo que eu saio coletando redes Wi-Fi e dispositivos BLE por aí — wardriving com ESP32 (Marauder e o firmware WDGoWars/projectZero). O problema nunca foi coletar: era organizar. Os logs saem em CSVs de formatos diferentes (WigleWifi 1.4 com 11 colunas, 1.6 com 12), ficam espalhados no cartão SD e no OneDrive, e subir tudo à mão para o WiGLE e para o WDGoWars era um trabalho chato e repetitivo.

Então construí o **Wardrive Manager**: um sistema local em Flask + SQLite que cuida disso de ponta a ponta.

**O que ele faz**

📥 Importa os logs de uma pasta (recursivo), entende os três layouts de CSV WiGLE mapeando por nome de coluna, deriva a frequência a partir do canal quando a coluna não existe e deduplica por hash — rodar a varredura duas vezes não duplica nada.

📊 Painel com coletas por dia, top redes, segurança dos APs, canais e fila de upload. Lista de redes com busca e filtros, detalhe por sessão e por rede, relatório de qualidade (linhas sem GPS, inválidas, arquivos repetidos) e mapa interativo.

📤 Exporta lotes compatíveis com os dois serviços: cabeçalho completo de 14 colunas repetido em cada parte (isso evita o clássico erro de o importador ler o layout antigo e trocar RSSI por latitude), pre-header do aparelho de origem, e cada parte respeitando o limite do destino. Junto de cada lote saem `manifest.json`, um `upload.sh` e um `upload.ps1` prontos.

☁️ Envia direto — WiGLE via Basic auth e WDGoWars via `X-API-Key` — com fila serial (o WDGoWars recusa uploads simultâneos), modo incremental por destino (o que já subiu não sobe de novo) e tratamento de 429, 409, 413 e 524, com divisão automática do lote quando ele passa do limite.

**Números reais do meu primeiro uso:** 33 sessões, 8.148 observações, 6.278 redes únicas (2.446 Wi-Fi e 3.832 BLE), 100% processado na minha máquina.

**Testes:** 41 automatizados, cobrindo leitura dos formatos, ida-e-volta do CSV gerado, limites por parte, importação idempotente e o fluxo de upload inteiro contra um servidor local que imita as duas APIs — inclusive os erros. Nada de testar em produção com dados de verdade.

🔐 **Sobre privacidade** — wardriving mexe com dado que identifica pessoas: MAC, nome de rede de terceiros e coordenada. Três cuidados aqui: o sistema roda só em 127.0.0.1, as chaves ficam em arquivo fora do banco, e nos prints abaixo os MACs e SSIDs de terceiros estão tarjados (o mapa está em escala regional, de propósito). Vale lembrar o óbvio: wardriving registra apenas o que o rádio transmite abertamente — não é quebra de criptografia nem captura de conteúdo.

Código aberto no GitHub: https://github.com/luizqueiroz-br/wardriverm

Se você também brinca de wardriving (ou quer começar) e quer um lugar decente para guardar seus dados, o repositório está lá. Sugestões, issues e PRs são bem-vindos.

#wardriving #WiGLE #WDGoWars #ESP32 #Python #Flask #SQLite #ciberseguranca #segurancadainformacao #OSINT #hardware #projetopessoal

---

## Versão curta (se preferir um post mais direto)

Meus logs de wardriving estavam espalhados em CSVs de formatos diferentes e subir tudo à mão para o WiGLE e para o WDGoWars era chato. Resolvi com código: **Wardrive Manager**, um sistema local em Flask + SQLite que importa os logs, mostra tudo em painel e mapa, e exporta lotes prontos para os dois serviços — com upload incremental e tratamento dos limites de cada API.

No primeiro uso: 33 sessões, 8.148 observações e 6.278 redes únicas processadas na minha máquina. 41 testes automatizados cobrindo do parser ao upload (com um servidor local que imita as duas APIs).

Nos prints, MACs e SSIDs de terceiros estão tarjados: dado de wardriving identifica pessoas, e isso não precisa ir para o feed.

Código aberto: https://github.com/luizqueiroz-br/wardriverm

#wardriving #WiGLE #WDGoWars #Python #ciberseguranca #ESP32

---

## Textos alternativos para as imagens (acessibilidade)

1. `01-capa.png` — Cartão do projeto "Wardrive Manager — WiGLE + WDGoWars" com os números: 8.148 observações, 6.278 redes únicas, 41 testes.
2. `02-painel.png` — Painel do sistema com sessões importadas, redes únicas, Wi-Fi/BLE, gráfico de coletas por dia e a fila de upload de cada serviço.
3. `03-mapa.png` — Mapa com as áreas de coleta em escala regional.
4. `04-redes-mascarado.png` — Lista de redes descobertas; as colunas de SSID e MAC estão tarjadas, os demais campos (tipo, segurança, RSSI, datas) aparecem.
5. `05-uploads.png` — Tela de uploads com os campos de chave de API e o envio incremental por destino.
