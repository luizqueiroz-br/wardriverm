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
    "micromobilidade": "patinete / bike",
    "drone": "drone",
    "rastreador": "rastreador de frota",
    "smart_tag": "smart tag",
    "vestivel": "vestível",
    "beacon": "beacon BLE",
    "casa": "casa conectada",
    "medidor": "medidor de energia",
    "robo": "robô aspirador",
    "audio": "áudio",
    "impressora": "impressora",
    "impressora3d": "impressora 3D",
    "pagamento": "maquininha (POS)",
    "camera": "câmera",
    "tv": "TV / mídia",
    "console": "videogame",
    "vr": "VR / AR",
    "telefone": "telefone IP",
    "satelite": "internet via satélite",
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
    "micromobilidade": "#5ed67f",
    "drone": "#ff5fa2",
    "rastreador": "#ff8c42",
    "smart_tag": "#ff8fb1",
    "vestivel": "#c58bff",
    "beacon": "#b39ddb",
    "casa": "#9fd356",
    "medidor": "#ffd23f",
    "robo": "#9ad5ca",
    "audio": "#ffd166",
    "impressora": "#b0b7c3",
    "impressora3d": "#7bc4ff",
    "pagamento": "#f4a261",
    "camera": "#ff7a6b",
    "tv": "#8ecae6",
    "console": "#e07a5f",
    "vr": "#a3e048",
    "telefone": "#b8c0cc",
    "satelite": "#8fd3ff",
    "devkit": "#00d1b2",
    "aleatorio": "#6b7688",
    "desconhecido": "#55607a",
}

#: emoji de cada categoria (usado nas etiquetas e nos marcadores do mapa)
ICONES: dict[str, str] = {
    "roteador": "📶",
    "celular": "📱",
    "computador": "💻",
    "veiculo": "🚗",
    "micromobilidade": "🛴",
    "drone": "🚁",
    "rastreador": "📍",
    "smart_tag": "🏷️",
    "vestivel": "⌚",
    "beacon": "📡",
    "casa": "🏠",
    "medidor": "⚡",
    "robo": "🤖",
    "audio": "🔊",
    "impressora": "🖨️",
    "impressora3d": "🧵",
    "pagamento": "💳",
    "camera": "📷",
    "tv": "📺",
    "console": "🎮",
    "vr": "🥽",
    "telefone": "☎️",
    "satelite": "🛰️",
    "devkit": "🧩",
    "aleatorio": "🎲",
    "desconhecido": "❓",
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
    # Inventus Power (Manaus) aparece com 489 redes Wi-Fi com SSID de pessoa
    # ("Janaina", "ALESSANDRA") e sinal de rua: é CPE de assinante montada aqui.
    # V-Solution e HDV fabricam ONU/GPON — os nomes do IEEE vêm com grafias
    # diferentes ("V-Solution", "Phoelectron"), por isso as variações.
    (r"inventus power|hdv pho?toelectron|essys|v-?solution", "roteador"),
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
    (r"hon hai precision|byd electronic", "celular"),
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
    # Visteon = painel/infotainment; Positron = alarme automotivo com Bluetooth
    (r"visteon|positron|texa spa", "veiculo"),
    # --- drones -----------------------------------------------------------------
    # vem antes de "veiculo"/"camera" porque o nome do drone é a pista mais forte
    # (Tello-XXXXXX, DJI-..., MavicMini-...); "spark"/"phantom" só com sufixo,
    # senão qualquer rede chamada "Spark" viraria drone
    (r"^sz dji|dji technology|ryze tech|autel robotics|parrot sa|parrot drones|skydio|"
     r"hubsan|yuneec|walkera|eachine|holy stone|zero zero robotics", "drone"),
    (r"tile, inc|tile inc|chipolo|pebblebee|life360", "smart_tag"),
    # beacons de varejo/evento (iBeacon/Eddystone)
    (r"kontakt|estimote|gimbal|radius networks|minew|jaalee|bluecats|blue up", "beacon"),
    # patinetes e bikes compartilhadas (BLE para desbloqueio)
    (r"neutron holdings|bird rides|tembici|grow mobility|grin scooters|whoosh mobility|"
     r"beam mobility|voi technology|zeus scooters|bolt operations", "micromobilidade"),
    # rastreadores de frota/ativo (não são tag de consumidor)
    (r"queclink|teltonika|calamp|concox|ruptela|samsara|sascar|onixsat|autotrac|meitrack|"
     r"gowhere|wanway|\bcastel\b|suntech international", "rastreador"),
    # maquininhas de cartão
    # outras marcas curtas: limite de palavra para não casar dentro de outra palavra
    (r"ingenico|verifone|pax technology|\bpax\b|newland payment|gertec|\belgin\b|bematech|"
     r"diebold nixdorf|\bncr\b|worldline", "pagamento"),
    # robôs aspirador
    (r"irobot|roborock|ecovacs|dreame technology|narwal|neato robotics|sharkninja", "robo"),
    # impressão 3D
    (r"bambu lab|prusa research|creality|elegoo|anycubic|artillery|sovol|flashforge|"
     r"raise3d|markforged|ultimaker|formlabs|makerbot|snapmaker", "impressora3d"),
    # VR / AR
    (r"reality labs|meta platforms|htc corporation.*vive|pico technology|valve corporation|"
     r"vuzix|magic leap", "vr"),
    # telefonia IP — Grandstream faz também AP de hotspot (é o que aparece na rua,
    # com SSID de loja/banco), então fica em rede; os fabricantes abaixo são de
    # telefone mesmo
    (r"grandstream", "roteador"),
    (r"\byealink\b|\bsnom\b|\bpolycom\b|\bavaya\b|\bmitel\b|\bfanvil\b|\bsangoma\b", "telefone"),
    # internet via satélite
    (r"spacex|starlink|viasat|hughes network|inmarsat|iridium|globalstar|kuiper", "satelite"),
    # medidores inteligentes (com limite de palavra: "Hitron" contém "itron" e
    # virava medidor de energia)
    (r"landis\+gyr|\bitron\b|\bxylem\b|\bsensus\b|kaifa|nansen|\beletra\b|\belster\b|\bisw\b",
     "medidor"),
    (r"garmin|fitbit|withings|polar electro|suunto|whoop|oura health", "vestivel"),
    (r"huami|amazfit|zepp", "vestivel"),
    # --- casa conectada --------------------------------------------------------
    (r"espressif", "devkit"),
    (r"nordic semiconductor|texas instruments|silicon labs|microchip technology", "devkit"),
    # módulos/SoCs de rádio: o que carrega um desses costuma ser protótipo ou gadget
    (r"alto ?beam|bouffalo|high-?flying|ai-link|sjit|fn-link|gaoshengda", "devkit"),
    (r"actions semiconductor|mcs logic", "audio"),
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
    (r"^(geek|mega[_\-]|flash[_\-]|wi-?fi|rede[_\-]|casa-|brisanet|unifique)",
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
    # impressão 3D e robô aspirador (nome de fábrica bem característico)
    (rf"{_marca('bambu', 'prusa', 'creality', 'elegoo', 'anycubic')}"
     r"|bambu ?lab|\bprusa ?mini", "impressora3d"),
    (rf"{_marca('roomba', 'roborock', 'deebot', 'ecovacs', 'dreame', 'narwal')}", "robo"),
    # telefonia IP e VR
    (rf"{_marca('grandstream', 'yealink', 'snom', 'polycom', 'fanvil')}", "telefone"),
    # VR: "quest" e "pico" são palavras comuns ("Quest Net", "Pico do Jaraguá"),
    # então só contam com marca explícita ou modelo numerado
    (r"\boculus\b|\bmeta quest\b|\bquest [23]\b|\bpico [0-9]\b|\bhtc vive\b|\bvive[-_ ]", "vr"),
    # internet via satélite
    (rf"{_marca('starlink', 'viasat', 'hughesnet')}", "satelite"),
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
#: fabricantes que produzem de tudo — o OUI sugere o tipo, não confirma
FABRICANTES_GENERICOS = re.compile(r"^(apple|samsung|xiaomi|google|sony|huawei|lg)")
#: fabricantes de componente/módulo: o que está em volta pode ser qualquer coisa
FABRICANTES_COMPONENTE = re.compile(
    r"^(actions semiconductor|mcs logic|alto ?beam|bouffalo|high-?flying|ai-?link|sjit|"
    r"fn-link|gaoshengda|texas instruments|silicon labs|nordic semiconductor|"
    r"realtek|broadcom|hon hai|byd electronic)")


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
                # Apple/Samsung fabricam de tudo; quem faz módulo/rádio vende para
                # qualquer produto — nos dois casos a categoria é dedução, não certeza
                if FABRICANTES_GENERICOS.match(alvo) or FABRICANTES_COMPONENTE.match(alvo):
                    return categoria, "media", "oui"
                return categoria, "alta", "oui"
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
