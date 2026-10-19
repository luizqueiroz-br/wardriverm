"""Classificação dos dispositivos em tipos de objeto (roteador, veículo, smart tag…).

A identificação sai de duas fontes complementares:

1. **OUI** — os 3 primeiros bytes do MAC identificam o fabricante (base oficial do
   IEEE, guardada em ``data/oui/``). É o sinal mais direto: um MAC da Tesla é um
   carro, um da Espressif é uma placa ESP32.
2. **Nome da rede (SSID)** — quando existe, é mais específico que o fabricante:
   "VIVO-1A2B" é roteador da operadora, "iPhone de Luiz" é celular, mesmo que o
   fabricante (Apple) fabrique várias coisas.

Quando as duas fontes não bastam, o resultado fica como "desconhecido" — é melhor
não chutar: Apple e Samsung, por exemplo, fabricam celular, computador, relógio e TV
com a mesma faixa de MACs, então esses casos recebem confiança média/baixa.
"""
from __future__ import annotations

import csv
import re
import urllib.request
from pathlib import Path

from . import config

# --------------------------------------------------------------------------- #
# Catálogo de categorias (id → como aparece na tela)
# --------------------------------------------------------------------------- #
CATEGORIAS: dict[str, str] = {
    "roteador": "roteador / AP",
    "celular": "celular / tablet",
    "computador": "computador",
    "veiculo": "veículo",
    "drone": "drone",
    "smart_tag": "smart tag / rastreador",
    "vestivel": "vestível",
    "casa": "casa conectada",
    "audio": "áudio",
    "impressora": "impressora",
    "camera": "câmera",
    "tv": "TV / mídia",
    "console": "videogame",
    "devkit": "placa de dev (ESP32)",
    "aleatorio": "MAC aleatório",
    "desconhecido": "não identificado",
}

#: ordem usada nas listas e gráficos (mais útil primeiro)
ORDEM = list(CATEGORIAS)

#: cor de cada categoria (usada nas etiquetas da interface)
CORES: dict[str, str] = {
    "roteador": "#4ad295",
    "celular": "#7aa2ff",
    "computador": "#61d4d0",
    "veiculo": "#ffb454",
    "drone": "#ff5fa2",
    "smart_tag": "#ff8fb1",
    "vestivel": "#c58bff",
    "casa": "#9fd356",
    "audio": "#ffd166",
    "impressora": "#b0b7c3",
    "camera": "#ff7a6b",
    "tv": "#8ecae6",
    "console": "#e07a5f",
    "devkit": "#00d1b2",
    "aleatorio": "#6b7688",
    "desconhecido": "#55607a",
}

# --------------------------------------------------------------------------- #
# Regras por fabricante (Organization Name do IEEE, em minúsculas)
# --------------------------------------------------------------------------- #
# (regex, categoria) — a primeira que casar vence, então as mais específicas
# precisam vir antes das genéricas.
REGRAS_VENDOR: list[tuple[str, str]] = [
    # --- redes / infraestrutura ------------------------------------------------
    (r"roteador|roteadores|router|roteamento", "roteador"),
    (r"tp-link|mercusys", "roteador"),
    (r"d-link", "roteador"),
    (r"netgear|netcomm|sercomm", "roteador"),
    (r"asustek computer|asustek", "roteador"),
    (r"zte corporation|zte ", "roteador"),
    (r"huawei|honor device", "roteador"),
    (r"mikrotik|routerboard", "roteador"),
    (r"ubiquiti|aruba networks|ruckus|cisco systems|cisco meraki", "roteador"),
    (r"intelbras", "roteador"),
    (r"sagemcom|technicolor|vantiva|arris|commscope", "roteador"),
    (r"fiberhome|calix|zyxel|tenda|cambium|actiontec|nokia shanghai bell", "roteador"),
    (r"mitrastar|tellescom|humax|kaonmedia|kaon |dasan|greenpacket|summitek|parks s/a", "roteador"),
    (r"datacom|zhonggou|shenzhen gongjin|sdmc|gemtek|askey|wistron neeb|wistron neweb", "roteador"),
    (r"pirelli|jiuzhou|skyworth|fogconn|sagemcom broadband", "roteador"),
    (r"arcadyan|mercury communication|taicang t&w|vsolution|bilian|fortinet|shenzhen sdmc",
     "roteador"),
    (r"linksys|belkin", "roteador"),
    (r"parkervision|allied telesis|edgecore|extreme networks", "roteador"),
    # --- celulares / tablets ---------------------------------------------------
    (r"^apple", "celular"),
    (r"samsung electronics|samsung electro", "celular"),
    (r"xiaomi|beijing xiaomi", "celular"),
    (r"motorola mobility|motorola solutions", "celular"),
    (r"lg electronics", "celular"),
    (r"oppo|oneplus|realme|vivo mobile|guangdong vivo", "celular"),
    (r"sony mobile|sony corporation", "celular"),
    (r"google", "celular"),
    (r"tcl |positivo|multilaser|hmd global|nokia mobile", "celular"),
    (r"transsion|tecno|infinix|itel", "celular"),
    # --- computadores ----------------------------------------------------------
    (r"intel corporate|intel corporation", "computador"),
    (r"dell|lenovo|acer|micro-star|asustek.*notebook|clevo", "computador"),
    (r"hewlett packard|hp inc|compal|quantal|pegatron|wiwynn|tonfunk", "computador"),
    (r"microsoft|raspberry pi|framework computer|system76", "computador"),
    (r"realtek semiconductor|broadcom", "computador"),
    # --- automotivo ------------------------------------------------------------
    (r"tesla", "veiculo"),
    (r"ford motor|general motors|gm global|onstar", "veiculo"),
    (r"volkswagen|audi ag|porsche|bentley|lamborghini", "veiculo"),
    (r"toyota|honda motor|nissan|mazda|subaru|suzuki", "veiculo"),
    (r"hyundai|kia motors|genesis", "veiculo"),
    (r"bayerische motoren|bmw|mercedes|daimler|volvo car|jaguar|land rover", "veiculo"),
    (r"stellantis|fiat|peugeot|citroen|renault|dacia|byd auto|great wall|chery|geely", "veiculo"),
    (r"continental automotive|bosch|denso|valeo|aptiv|magna|harman automotive|lear corp",
     "veiculo"),
    # --- drones -----------------------------------------------------------------
    # vem antes de "veiculo"/"camera" porque o nome do drone é a pista mais forte
    # (Tello-XXXXXX, DJI-..., MavicMini-...); "spark"/"phantom" só com sufixo,
    # senão qualquer rede chamada "Spark" viraria drone
    (r"^sz dji|dji technology|ryze tech|autel robotics|parrot sa|parrot drones|skydio|"
     r"hubsan|yuneec|walkera|eachine|holy stone|zero zero robotics", "drone"),
    (r"tile, inc|tile inc|chipolo|pebblebee|life360", "smart_tag"),
    (r"garmin|fitbit|withings|polar electro|suunto|whoop|oura health", "vestivel"),
    (r"huami|amazfit|zepp", "vestivel"),
    # --- casa conectada --------------------------------------------------------
    (r"espressif", "devkit"),
    (r"nordic semiconductor|texas instruments|silicon labs|microchip technology", "devkit"),
    (r"tuya|itead|sonoff|allterco|shelly|aqara|lumi united|broadlink", "casa"),
    (r"midea|gree electric|hisense electric|haier|electrolux|whirlpool", "casa"),
    (r"signify|philips lighting|hue |lifx|yeelight|nanoleaf|wemo", "casa"),
    (r"netatmo|ecobee|ring |amazon technologies|arlo", "casa"),
    (r"ohsung|humax.*(video|set)|kaonmedia.*(video)", "tv"),
    (r"sonos", "audio"),
    (r"bose|sennheiser|harman|jbl|jabra|gn audio|marshall|bang & olufsen", "audio"),
    (r"anker|soundcore|edifier|shure", "audio"),
    # --- periféricos de escritório --------------------------------------------
    (r"seiko epson|epson|brother industries|canon|kyocera|ricoh|xerox|lexmark|zebra tech",
     "impressora"),
    (r"hikvision|dahua|axis communic|uniview|reolink|wyze|ezviz|hanwha", "camera"),
    (r"roku|vizio|hisense|skyworth|arris.*video|technicolor.*video", "tv"),
    (r"nintendo|sony interactive|microsoft.*(xbox)", "console"),
]

# --------------------------------------------------------------------------- #
# Regras por nome de rede (SSID) — valem mais que o fabricante
# --------------------------------------------------------------------------- #
def _marca(*nomes: str) -> str:
    """Casa a marca só quando ela está isolada.

    Sem isso, "Claudio" casa "audi" (viraria veículo) e "Stile Papelaria" casa
    "tile" (viraria smart tag). Underscore/hífen contam como separador, porque os
    nomes de fábrica usam "Toyota_2477" e "VW_MIB_6343".
    """
    alternativas = "|".join(re.escape(nome) for nome in nomes)
    return rf"(?<![a-z0-9])(?:{alternativas})(?![a-z0-9])"


REGRAS_SSID: list[tuple[str, str]] = [
    # roteadores de operadora e de fábrica: o nome é praticamente uma assinatura
    (r"^(vivo|claro|net[_\-]|oi[ _\-]|tim[ _\-]|algar|sercomtel|desktop-)", "roteador"),
    (r"^(tp-link|dlink|d-link|netgear|mercusys|intelbras|tenda|zyxel|asus)", "roteador"),
    (r"^(geek|starlink|mega[_\-]|flash[_\-]|wi-?fi|rede[_\-]|casa-|brisanet|unifique)",
     "roteador"),
    # aparelhos pessoais
    (r"(iphone|ipad|ipod)", "celular"),
    (rf"{_marca('galaxy', 'redmi', 'poco')}|\bmoto [a-z]\b|\bsm-[agsn]\d|\bpixel \d", "celular"),
    # impressão
    (r"(officejet|deskjet|laserjet|epson|pixma|dcp-|mfc-|hl-l)", "impressora"),
    (r"^direct-.*(hp|epson|brother|canon|pixma)", "impressora"),
    # rastreadores e vestíveis
    (rf"{_marca('airtag', 'tile', 'chipolo', 'smarttag', 'pebblebee')}|smart ?tag|\bfind my\b",
     "smart_tag"),
    (rf"{_marca('garmin', 'fenix', 'forerunner', 'fitbit', 'amazfit', 'whoop')}|\bmi band\b",
     "vestivel"),
    # drones (nome do próprio drone costuma ser a única pista)
    (rf"{_marca('tello', 'mavic', 'anafi', 'skydio', 'hubsan', 'yuneec', 'bebop', 'autel')}"
     r"|\bdji[-_ ]|\bphantom \d|\bspark[-_][0-9a-z]{4}", "drone"),
    # carros (o SSID costuma ser o nome do sistema embarcado)
    (rf"{_marca('tesla', 'toyota', 'honda', 'bmw', 'audi', 'volkswagen', 'chevrolet')}"
     r"|\bmodel [3syx]\b|^vw_|onstar\d|carplay|android ?auto|\bford ?sync\b|\bsync ?gen", "veiculo"),
    # casa / áudio / tv
    (rf"{_marca('hue', 'tuya', 'sonoff', 'shelly', 'aqara', 'lifx', 'tapo', 'kasa', 'wemo')}",
     "casa"),
    (rf"{_marca('sonos', 'bose', 'jbl', 'soundbar', 'marshall')}|\bsrs-|\bflip \d|\bcharge \d",
     "audio"),
    (rf"{_marca('chromecast', 'roku', 'bravia', 'directv')}|fire ?tv|smart-?tv|sky-?box", "tv"),
    (rf"{_marca('hikvision', 'dahua', 'reolink', 'ezviz', 'ipcam')}|intelbras ?cam", "camera"),
    (rf"{_marca('esp32', 'esp8266', 'nodemcu', 'marauder')}|esp-[0-9a-f]{4}", "devkit"),
    (rf"{_marca('playstation', 'xbox')}|\bps[45]\b|^switch[_\-][0-9a-f]{4}", "console"),
]

#: prefixo do MAC que indica endereço aleatório (bit localmente administrado)
_RANDOMIZADO = 0x02


def _primeiro_octeto(mac: str) -> int | None:
    try:
        return int(mac.replace("-", ":").split(":")[0], 16)
    except (ValueError, IndexError):
        return None


def mac_aleatorio(mac: str) -> bool:
    """True para MAC localmente administrado (BLE/Android embaralham assim)."""
    octeto = _primeiro_octeto(mac or "")
    return bool(octeto is not None and octeto & _RANDOMIZADO)


def normalizar_mac(mac: str) -> str:
    return (mac or "").strip().replace("-", ":").upper()


# --------------------------------------------------------------------------- #
# Base de OUI (fabricante pelo prefixo do MAC)
# --------------------------------------------------------------------------- #
OUI_ARQUIVOS = {
    "ma-l.csv": "https://standards-oui.ieee.org/oui/oui.csv",
    "ma-m.csv": "https://standards-oui.ieee.org/oui28/mam.csv",
    "ma-s.csv": "https://standards-oui.ieee.org/oui36/oui36.csv",
}


def pasta_oui() -> Path:
    return Path(config.DATA_DIR) / "oui"


def baixar_oui(destino: Path | None = None, timeout: int = 60) -> list[Path]:
    """Baixa a base pública do IEEE (uma vez; ~5 MB no total)."""
    destino = destino or pasta_oui()
    destino.mkdir(parents=True, exist_ok=True)
    baixados = []
    for nome, url in OUI_ARQUIVOS.items():
        alvo = destino / nome
        if alvo.is_file() and alvo.stat().st_size > 10_000:
            baixados.append(alvo)
            continue
        with urllib.request.urlopen(url, timeout=timeout) as resposta:
            alvo.write_bytes(resposta.read())
        baixados.append(alvo)
    return baixados


def ler_oui(pasta: Path | None = None) -> dict[str, str]:
    """Lê os CSVs do IEEE para {prefixo_hex: fabricante}. Prefixo de 6, 7 ou 9 dígitos."""
    pasta = pasta or pasta_oui()
    tabela: dict[str, str] = {}
    for nome in OUI_ARQUIVOS:
        arquivo = pasta / nome
        if not arquivo.is_file():
            continue
        with arquivo.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            for linha in csv.DictReader(fh):
                prefixo = (linha.get("Assignment") or "").strip().upper()
                fabricante = (linha.get("Organization Name") or "").strip()
                if prefixo and fabricante:
                    tabela[prefixo] = fabricante
    return tabela


def fabricante(mac: str, tabela: dict[str, str]) -> str:
    """Consulta o fabricante testando os três tamanhos de prefixo (MA-L, MA-M, MA-S)."""
    limpo = re.sub(r"[^0-9A-Fa-f]", "", normalizar_mac(mac))
    for tamanho in (9, 7, 6):
        if len(limpo) >= tamanho:
            achado = tabela.get(limpo[:tamanho])
            if achado:
                return achado
    return ""


# --------------------------------------------------------------------------- #
# Classificação
# --------------------------------------------------------------------------- #
def classificar(tipo: str, mac: str, ssid: str, vendor: str) -> tuple[str, str, str]:
    """Devolve (categoria, confiança, origem). Confiança: alta | media | baixa."""
    nome = (ssid or "").strip().lower()
    if nome:
        for padrao, categoria in REGRAS_SSID:
            if re.search(padrao, nome):
                return categoria, "alta", "ssid"
    if vendor:
        alvo = vendor.lower()
        for padrao, categoria in REGRAS_VENDOR:
            if re.search(padrao, alvo):
                # Apple/Samsung/Xiaomi/Google fabricam de tudo: o fabricante só
                # sugere o tipo mais provável, não confirma
                generico = re.match(r"^(apple|samsung|xiaomi|google|sony|huawei|lg)", alvo)
                return categoria, "media" if generico else "alta", "oui"
    if mac_aleatorio(mac):
        return "aleatorio", "media", "mac"
    if tipo == "BLE":
        return "desconhecido", "baixa", "tipo"
    return "desconhecido", "baixa", "tipo"


def categorizar_redes(conn, *, forcar: bool = False, baixar: bool = False) -> dict:
    """Preenche fabricante/categoria de todas as redes do banco."""
    tabela = _carregar_tabela_oui(conn, baixar=baixar)
    consulta = "SELECT mac, COALESCE(ssid,'') AS ssid, type, vendor, categoria FROM networks"
    if not forcar:
        consulta += " WHERE categoria IS NULL"
    linhas = conn.execute(consulta).fetchall()
    contagem: dict[str, int] = {}
    for linha in linhas:
        mac = linha["mac"]
        vendor = fabricante(mac, tabela) if tabela else ""
        vendor = vendor or (linha["vendor"] or "")
        categoria, confianca, origem = classificar(linha["type"] or "", mac, linha["ssid"], vendor)
        conn.execute(
            "UPDATE networks SET vendor = ?, categoria = ?, categoria_conf = ?, "
            "categoria_origem = ? WHERE mac = ?",
            (vendor or None, categoria, confianca, origem, mac),
        )
        contagem[categoria] = contagem.get(categoria, 0) + 1
    conn.commit()
    return {"redes": len(linhas), "por_categoria": contagem, "oui": len(tabela)}


def _carregar_tabela_oui(conn, *, baixar: bool = False) -> dict[str, str]:
    """Usa a tabela ``oui`` do banco; popula a partir dos CSVs quando vazia."""
    total = conn.execute("SELECT COUNT(*) FROM oui").fetchone()[0]
    if total and not baixar:
        return {r[0]: r[1] for r in conn.execute("SELECT prefix, vendor FROM oui")}
    try:
        if baixar:
            baixar_oui()
        tabela = ler_oui()
    except OSError:
        tabela = {}
    if not tabela:
        # sem base local e sem internet: segue só com as regras de nome
        return {r[0]: r[1] for r in conn.execute("SELECT prefix, vendor FROM oui")}
    conn.executemany(
        "INSERT OR REPLACE INTO oui (prefix, vendor) VALUES (?, ?)", tabela.items()
    )
    conn.commit()
    return tabela


def resumo_categorias(conn) -> list[dict]:
    """Quantas redes/observações em cada categoria (para painel e listas)."""
    linhas = conn.execute(
        """SELECT COALESCE(n.categoria,'desconhecido') AS categoria,
                  COUNT(*) AS redes,
                  COALESCE(SUM(n.obs_count), 0) AS obs
             FROM networks n GROUP BY categoria"""
    ).fetchall()
    dados = {r["categoria"]: {"redes": r["redes"], "obs": r["obs"]} for r in linhas}
    saida = []
    for categoria in ORDEM:
        info = dados.pop(categoria, None)
        if info:
            saida.append({"categoria": categoria, **info})
    for categoria, info in sorted(dados.items(), key=lambda par: -par[1]["redes"]):
        saida.append({"categoria": categoria, **info})
    return saida


def nome_categoria(categoria: str) -> str:
    return CATEGORIAS.get(categoria or "", CATEGORIAS["desconhecido"])
