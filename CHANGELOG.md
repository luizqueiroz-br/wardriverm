# Mudanças

Tudo que mudou no Wardrive Manager, na ordem em que foi publicado. As versões
seguem o que está em `wardrive/config.py` (`APP_VERSION`) e aparecem no rodapé
e em `/healthz`.

## 1.2.0 — análise

Fecha seis das doze propostas abertas como issue. Nada aqui inventa dado: tudo
sai do acervo local (`data/wardrive.db`) e continua funcionando offline.

### Mapa

- **Risco** (`modo=risco`): uma bolinha por rede Wi-Fi, colorida pela
  fragilidade do `auth_mode` — aberta, WEP, WPA, misto, WPA2, WPA3, 802.1X. Sem
  agrupamento, com a rede mais fraca por cima e o resto em amostra para o SVG
  não travar. Desenhado em SVG porque o canvas do Leaflet fica em branco depois
  do zoom.
- **Cobertura** (`modo=cobertura`): grade de ~100 m com as passagens (azul 1,
  verde 2–5, amarelo 6+), km rodados e km² cobertos da seleção.
- **Ícone por objeto** no zoom de inspeção (26 categorias), sem agrupamento no
  zoom máximo; acima de 1.500 pontos volta para bolinha.

### Análise

- **Segurança**: 902 de 12.270 redes Wi-Fi abertas ou com WEP (7,4%) e 795 com
  SSID de fábrica. Ranking por célula de ~100 m e CSV normal ou mascarado.
- **Qual canal usar**: soma a energia dos vizinhos (dBm → escala relativa) com
  sobreposição de 20 MHz e ranqueia 1/6/11 no 2,4 GHz e os canais sem DFS no
  5 GHz.
- **Mudanças**: duas janelas de tempo na mesma área devolvem novas, sumidas e
  mudadas — com troca de SSID e troca de segurança em colunas separadas.
- **Rastreadores**: BLE/BT que acompanha o percurso (≥ 3 sessões, ≥ 3 locais e
  ≥ 12 h).
- **Rede filtrada**: escolher um MAC/SSID mostra só onde aquela rede apareceu,
  com traçado cronológico e resumo por sessão.

### Privacidade e exportação

- **Áreas privadas**: raios onde nada é publicado, aplicados no caminho único de
  geração de lote (manual, automático e upload), com prévia obrigatória.
- **Máscara** de SSID/MAC nos CSVs de análise.
- Hook de pré-commit bloqueia `data/`, `*.db`, `secrets.json`, exports, logs e
  qualquer texto com MAC/coordenadas nos arquivos versionados.

### Infra

- `cli.py` com `serve`, `parar`, `status`, `importar`, `categorizar`, `exportar`,
  `enviar`, `percursos`.
- 135 testes (`python -m unittest discover -s tests -t .`), incluindo os que
  rodam contra os logs reais em `tests/test_dados_reais.py`.

## 1.1.0

- Mapa com calor por densidade ou por sinal (escala adaptativa pelo 90º
  percentil), filtro por item/rede e ícones por categoria de objeto.
- 26 categorias de objeto (roteador, celular, veículo, drone, rastreador,
  beacon, medidor, impressora 3D, pagamento, VR, satélite…).
- Exportação em lotes compatível com WiGLE 1.6 e WDGoWars, com `upload.sh` e
  `upload.ps1`.

## 1.0.0

- Importação dos formatos Marauder 1.4, warrior/WDGoWars 1.6 e WiGLE/Kismet,
  com deduplicação por SHA-256 e cobertura de arquivos repetidos.
- SQLite com sessões, observações, redes, exportações e uploads; envio para
  WiGLE e WDGoWars pelo caminho incremental.
