/* Wardrive Manager — utilitários de interface (tarefas em segundo plano,
   upload por arrastar-e-soltar e mapa). Sem dependências externas. */
(function () {
  "use strict";

  const csrf = document.body.dataset.csrf || "";

  async function api(url, options) {
    const opts = Object.assign({ headers: {} }, options || {});
    if (opts.method && opts.method !== "GET") {
      opts.headers["X-CSRF-Token"] = csrf;
    }
    const resp = await fetch(url, opts);
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
    return resp.json();
  }

  /* ------------------------------------------------------------- jobbar */
  const bar = document.getElementById("jobbar");
  const rotulo = document.getElementById("job-rotulo");
  const preenche = document.getElementById("job-barra");
  const numeros = document.getElementById("job-numeros");
  const log = document.getElementById("job-log");
  const toggle = document.getElementById("job-toggle");
  let ultimoJob = null;

  if (toggle) {
    toggle.addEventListener("click", () => {
      log.hidden = !log.hidden;
      if (!log.hidden && ultimoJob) renderLog(ultimoJob);
    });
  }

  function renderLog(job) {
    if (!job || !job.log) return;
    log.textContent = job.log.map((l) => `${l.ts}  ${l.msg}`).join("\n");
    log.scrollTop = log.scrollHeight;
  }

  async function acompanhar() {
    let dados;
    try {
      dados = await api("/api/jobs");
    } catch (err) {
      return;
    }
    const ativo = (dados.jobs || []).find((j) => j.status === "rodando" || j.status === "na fila");
    const alvo = ativo || (dados.jobs || [])[0];
    if (!alvo || (alvo.status === "ok" && !bar.dataset.finalizado)) {
      if (!ativo) {
        bar.hidden = true;
        return;
      }
    }
    bar.hidden = false;
    ultimoJob = alvo;
    rotulo.textContent = `${alvo.label} — ${alvo.status}`;
    const p = alvo.progress || {};
    preenche.style.width = (p.percent || 0) + "%";
    numeros.textContent = p.total ? `${p.done}/${p.total} ${p.unit}` : "";
    if (!log.hidden) renderLog(alvo);
    if (alvo.status === "ok") {
      const recarregar = sessionStorage.getItem("job-visto") !== alvo.id;
      sessionStorage.setItem("job-visto", alvo.id);
      if (recarregar && p.total) {
        setTimeout(() => location.reload(), 900);
      }
    }
  }
  if (bar) {
    acompanhar();
    setInterval(acompanhar, 2000);
  }

  /* ------------------------------------------------- upload arrastar/soltar */
  const zona = document.querySelector(".dropzone");
  const entrada = document.getElementById("entrada-arquivos");
  if (zona && entrada) {
    const enviar = async (arquivos) => {
      if (!arquivos.length) return;
      const dados = new FormData();
      for (const arq of arquivos) dados.append("arquivos", arq, arq.name);
      zona.textContent = `Enviando ${arquivos.length} arquivo(s)…`;
      try {
        const resp = await api("/api/importar", { method: "POST", body: dados });
        zona.textContent = `${resp.novos} importado(s), ${resp.ignorados} ignorado(s), ${resp.erros} erro(s).`;
        setTimeout(() => location.reload(), 1200);
      } catch (err) {
        zona.textContent = "Falha no envio: " + err.message;
      }
    };
    zona.addEventListener("click", () => entrada.click());
    entrada.addEventListener("change", () => enviar(Array.from(entrada.files)));
    ["dragenter", "dragover"].forEach((ev) =>
      zona.addEventListener(ev, (e) => {
        e.preventDefault();
        zona.classList.add("arrastando");
      })
    );
    ["dragleave", "drop"].forEach((ev) =>
      zona.addEventListener(ev, (e) => {
        e.preventDefault();
        zona.classList.remove("arrastando");
      })
    );
    zona.addEventListener("drop", (e) => enviar(Array.from(e.dataTransfer.files)));
  }

  /* ------------------------------------------------------------ confirmações */
  document.querySelectorAll("form[data-confirmar]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      if (!confirm(form.dataset.confirmar)) e.preventDefault();
    });
  });

  /* -------------------------------------------------------------- mapa */
  const alvoMapa = document.getElementById("mapa");
  let wardriveMapa = null;
  if (alvoMapa && window.L) {
    const centro = [-15.79, -47.88];
    const mapa = L.map("mapa", { preferCanvas: true, minZoom: 3, maxZoom: 19 })
      .setView(centro, 11);
    wardriveMapa = mapa;
    window.wardriveMapa = mapa;   // exposto para depuração e para o ajuste de tamanho abaixo
    // o contêiner pode ser medido antes do CSS/Layout terminar: sem isso o
    // fitBounds calcula o zoom com um tamanho errado e o mapa fica afastado demais
    mapa.whenReady(() => setTimeout(() => mapa.invalidateSize(), 250));
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      // sem o teto no MAPA, clicar num agrupamento ia a z=21 e o Leaflet esticava
      // os tiles de 19 — o mapa ficava borrado
      maxZoom: 19,
      maxNativeZoom: 19,
      attribution: '© OpenStreetMap',
    }).addTo(mapa);

    const cores = { WIFI: "#4ad295", BLE: "#7aa2ff", BT: "#a98bff" };

    // camada de calor (densidade das coletas): criada antes dos marcadores para
    // ficar embaixo deles; ligar/desligar só alterna a visibilidade do canvas
    const controleCalor = document.getElementById("calor");
    const controlePeso = document.getElementById("calor-peso");
    const controleIcones = document.getElementById("icones");
    const camadaCalor = (window.L && L.heatLayer)
      ? L.heatLayer([], {
          radius: 22,
          blur: 16,
          maxZoom: 17,
          max: 1,
          minOpacity: 0.22,
          gradient: { 0.2: "#1f3b73", 0.4: "#2e9bd6", 0.6: "#48d295", 0.8: "#ffd166", 1: "#ff5252" },
        })
      : null;
    if (camadaCalor) camadaCalor.addTo(mapa);

    // trajeto do item selecionado: criado antes dos marcadores para ficar por baixo
    const linhaTrajeto = L.polyline([], {
      color: "#1e5f9e", weight: 3, opacity: 0, dashArray: "7 5", interactive: false,
    }).addTo(mapa);

    const grupo = L.markerClusterGroup ? L.markerClusterGroup({
      maxClusterRadius: 45,
      // no zoom máximo nada é agrupado: o usuário quer ver ponto por ponto.
      // Lido do mapa para não desencontrar se o maxZoom mudar.
      disableClusteringAtZoom: mapa.getMaxZoom(),
    }) : L.layerGroup();
    mapa.addLayer(grupo);
    // modo ícone usa camada própria, SEM agrupamento: o ícone existe para ser
    // visto, e ali só entram os pontos da tela (bounded), então não pesa
    const camadaIcones = L.layerGroup().addTo(mapa);
    // cobertura: um <path> SVG por célula. Testei canvas (próprio e via L.rectangle)
    // e o bitmap não aparece nesta máquina; SVG é verificável e some/aparece igual.
    // Paleta sequencial: azul (1 passagem) → verde (2-5) → amarelo (6+). Sem
    // preenchimento opaco, porque a grade precisa deixar a rua aparecer embaixo.
    const CORES_COBERTURA = { "1": "#2f6f9e", "2-5": "#2f9e63", "6+": "#e8b31d" };
    const LIMITE_CELULAS_DESENHO = 6000;
    let camadaCobertura = null;
    let modoCobertura = false;
    let ultimosPontos = [];

    function elementoCalor() {
      if (!camadaCalor) return null;
      if (typeof camadaCalor.getElement === "function") return camadaCalor.getElement();
      return camadaCalor._canvas || null;
    }

    function pesoCalor(ponto) {
      if (!controlePeso || controlePeso.value !== "sinal") return 1;
      const rssi = Number(ponto.rssi);
      if (!isFinite(rssi)) return 0.4;
      // -100 dBm → 0,15 (frio) | -40 dBm → 1,0 (quente)
      return Math.max(0.15, Math.min(1, (rssi + 100) / 60));
    }

    function escalaCalor(pontos) {
      // O leaflet.heat acumula os pontos em células de r/2 px na tela, com o valor
      // somado limitado a `max`. A escala certa é, então, o 90º percentil dessas
      // MESMAS células para a vista atual — senão tudo satura (ou fica invisível).
      if (pontos.length < 12) return 1;
      const celula = Math.max(4, Math.floor((camadaCalor.options.radius || 22) / 2));
      const somas = new Map();
      for (const p of pontos) {
        const ponto = mapa.latLngToContainerPoint([p.lat, p.lon]);
        const chave = Math.floor(ponto.x / celula) + ":" + Math.floor(ponto.y / celula);
        // a escala é sempre calculada em contagem de coletas (peso 1), de propósito:
        // se ela acompanhasse o peso, trocar "densidade" por "sinal" normalizaria tudo
        // de novo e as duas vistas ficariam iguais
        somas.set(chave, (somas.get(chave) || 0) + 1);
      }
      const valores = Array.from(somas.values()).sort((a, b) => a - b);
      if (!valores.length) return 1;
      const p90 = valores[Math.min(valores.length - 1, Math.floor(valores.length * 0.9))];
      return Math.max(1, Math.round(p90));
    }

    function desenharCalor() {
      if (!camadaCalor) return;
      const ligado = !controleCalor || controleCalor.checked;
      const validos = ultimosPontos.filter((p) => p.lat != null && p.lon != null);
      camadaCalor.setOptions({ max: escalaCalor(validos) });
      camadaCalor.setLatLngs(
        validos.map((p) => [p.lat, p.lon, pesoCalor(p)])
      );
      const elemento = elementoCalor();
      if (elemento) elemento.style.display = ligado ? "" : "none";
    }

    if (controleIcones) controleIcones.addEventListener("change", aoMudarVista);
    if (controleCalor) controleCalor.addEventListener("change", desenharCalor);
    if (controlePeso) controlePeso.addEventListener("change", desenharCalor);
    // a densidade por célula muda com o zoom: reescala o calor e, no zoom de
    // inspeção, troca bolinha por ícone
    mapa.on("zoomend", aoMudarVista);
    // no arrasto, o modo ícone refaz a camada com o que está na tela; nos modos de
    // grade/risco os pontos vêm de outra consulta e não devem ser redesenhados
    mapa.on("moveend", () => {
      if (modoCobertura) return;
      if (querIcones()) desenharPontos();
    });

    function cor(tipo) {
      return cores[tipo] || "#ffb454";
    }

    function objeto(categoria) {
      const tabela = window.WARDRIVE_OBJETOS || {};
      return tabela[categoria] || tabela.desconhecido ||
        { nome: categoria || "não identificado", cor: "#55607a", icone: "❓" };
    }

    function marcadorComIcone(ponto) {
      const info = objeto(ponto.categoria);
      const elemento = L.divIcon({
        className: "marcador-objeto",
        html: `<span style="--cor:${escapar(info.cor)}">${escapar(info.icone)}</span>`,
        iconSize: [26, 26],
        iconAnchor: [13, 13],
        popupAnchor: [0, -13],
      });
      return L.marker([ponto.lat, ponto.lon], { icon: elemento, riseOnHover: true });
    }

    function escapar(valor) {
      // SSID vem de rede de terceiro: não pode entrar cru no innerHTML do popup
      return String(valor == null ? "" : valor).replace(/[&<>"']/g, (c) => (
        { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
      ));
    }

    function enquadrar(limites) {
      // quando todos os pontos estão no mesmo quarteirão, o fitBounds pediria um
      // zoom absurdo: 17 é o limite útil para leitura
      const caixa = L.latLngBounds(limites);
      mapa.setView(caixa.getCenter(), Math.min(17, mapa.getBoundsZoom(caixa)));
    }

    function desenharTrajeto(pontos) {
      // a ordem de chegada é por data decrescente; o traçado precisa ser cronológico
      const ordenados = pontos
        .filter((p) => p.lat != null && p.lon != null)
        .slice()
        .sort((a, b) => String(a.first_seen || "").localeCompare(String(b.first_seen || "")));
      // pontos repetidos no mesmo lugar não acrescentam nada ao desenho
      const unicos = [];
      for (const p of ordenados) {
        const ultimo = unicos[unicos.length - 1];
        if (!ultimo || ultimo[0] !== p.lat || ultimo[1] !== p.lon) unicos.push([p.lat, p.lon]);
      }
      if (unicos.length > 1) {
        linhaTrajeto.setLatLngs(unicos);
        linhaTrajeto.setStyle({ opacity: 0.5 });
      } else {
        linhaTrajeto.setLatLngs([]);
      }
    }

    function mostrarResumo(dados) {
      const faixa = document.getElementById("faixa-rede");
      const tabela = document.getElementById("resumo-rede");
      const r = dados.resumo || {};
      const rede = r.rede || null;
      const selecionado = !!dados.mac && (r.redes || 0) === 1;
      if (faixa) faixa.hidden = !selecionado;
      if (tabela) tabela.hidden = !selecionado;
      if (!selecionado) return;
      const nome = rede && rede.ssid ? rede.ssid : "(SSID oculto)";
      document.getElementById("resumo-titulo").textContent = nome;
      document.getElementById("resumo-mac").textContent =
        dados.pontos[0] ? "· " + dados.pontos[0].mac : "";
      const ficha = document.getElementById("resumo-detalhe");
      if (ficha && dados.pontos[0]) {
        ficha.href = "/redes/" + encodeURIComponent(dados.pontos[0].mac);
      }
      const partes = [
        `${r.pontos || 0} observação(ões)`,
        `${r.locais || 0} local(is) distinto(s)`,
        `${r.sessoes || 0} sessão(ões)`,
        r.rssi_medio != null ? `RSSI médio ${r.rssi_medio} dBm (máx ${r.rssi_max})` : "",
        r.primeira ? `de ${r.primeira} a ${r.ultima}` : "",
        rede && rede.auth_mode ? rede.auth_mode : "",
      ].filter(Boolean);
      document.getElementById("resumo-linha").textContent = partes.join(" · ");
      const corpo = document.querySelector("#resumo-sessoes tbody");
      if (corpo) {
        corpo.innerHTML = (r.por_sessao || []).map((s) => (
          `<tr><td class="mono">${escapar(s.name)}</td><td class="num">${s.obs}</td>` +
          `<td>${escapar(s.primeira || "")}</td><td>${escapar(s.ultima || "")}</td>` +
          `<td class="num">${s.rssi_max ?? ""}</td></tr>`
        )).join("") || `<tr><td colspan="5" class="texto-fraco">sem observações com GPS</td></tr>`;
      }
    }

    function raio(rssi) {
      const r = Number(rssi);
      if (!isFinite(r)) return 4;
      return Math.max(3, Math.min(9, 4 + (r + 100) / 12));
    }

    // A partir deste zoom o mapa está em modo de inspeção: o ponto vira ícone do
    // objeto, mesmo que a consulta tenha 20 mil pontos — só o que está na tela é
    // desenhado (é o que mantém o mapa rápido).
    const ZOOM_ICONES = 17;
    const LIMITE_ICONES_VISAO = 1200;
    let modoDesenhado = "";
    function querIcones() {
      return (!controleIcones || controleIcones.checked) && mapa.getZoom() >= ZOOM_ICONES;
    }

    function popupDe(p) {
      const ssid = p.ssid && p.ssid.length ? p.ssid : "(SSID oculto)";
      const info = objeto(p.categoria);
      return `<b>${escapar(ssid)}</b><br><span style="font-family:monospace">${escapar(p.mac)}</span><br>` +
        `${escapar(info.icone)} ${escapar(info.nome)}<br>` +
        `tipo: ${escapar(p.type)} · RSSI: ${p.rssi ?? "?"} dBm<br>` +
        `canal: ${p.channel ?? "?"} · ${escapar(p.first_seen || "")}<br>` +
        `<span style="opacity:.7">${p.obs_count > 1 ? p.obs_count + " observações" : ""}</span>`;
    }

    let infoBase = "";

    function atualizarInfo(icones) {
      const info = document.getElementById("mapa-info");
      if (!info) return;
      const modo = icones
        ? `ícones a partir do zoom ${ZOOM_ICONES}`
        : `bolinhas — aproxime até o zoom ${ZOOM_ICONES} para ver os ícones`;
      info.textContent = infoBase ? `${infoBase} — ${modo}` : modo;
    }

    function desenharPontos() {
      grupo.clearLayers();
      camadaIcones.clearLayers();
      const icones = querIcones();
      // no modo ícone só entra o que aparece na tela (com folga para o arrasto);
      // no modo bolinha entra tudo, no canvas, que aguenta o volume
      const caixa = icones ? mapa.getBounds().pad(0.3) : null;
      let desenhados = 0;
      for (const p of ultimosPontos) {
        if (p.lat == null || p.lon == null) continue;
        if (caixa && !caixa.contains([p.lat, p.lon])) continue;
        if (desenhados >= LIMITE_ICONES_VISAO) break;
        let marcador;
        if (icones) {
          marcador = marcadorComIcone(p);
        } else {
          marcador = L.circleMarker([p.lat, p.lon], {
            radius: raio(p.rssi),
            color: cor(p.type),
            weight: 1,
            fillColor: cor(p.type),
            fillOpacity: 0.55,
          });
        }
        marcador.bindPopup(popupDe(p));
        (icones ? camadaIcones : grupo).addLayer(marcador);
        desenhados += 1;
      }
      modoDesenhado = icones ? "icones" : "circulos";
      atualizarInfo(icones);
      return { icones, desenhados };
    }

    // troca de modo ao cruzar o zoom; dentro do modo ícone, refaz a cada
    // movimento (só o que está na tela, então é barato)
    function aoMudarVista() {
      if (desenharCalor) desenharCalor();
      if (modoCobertura) return;   // a grade não depende do zoom (célula fixa em graus)
      const modo = querIcones() ? "icones" : "circulos";
      if (modo === "icones" || modo !== modoDesenhado) desenharPontos();
    }

    function desenharCobertura(dados) {
      removerCobertura();
      const renderer = L.svg({ padding: 0.6 });
      camadaCobertura = L.layerGroup().addTo(mapa);
      const grau = (dados.grau_celula || 0.001) / 2;
      const limites = [];
      const celulas = dados.celulas || [];
      for (const celula of celulas.slice(0, LIMITE_CELULAS_DESENHO)) {
        const faixa = celula.n >= 6 ? "6+" : celula.n >= 2 ? "2-5" : "1";
        const canto1 = [celula.lat - grau, celula.lon - grau];
        const canto2 = [celula.lat + grau, celula.lon + grau];
        L.rectangle([canto1, canto2], {
          renderer: renderer,
          stroke: true,
          color: "#0b1220",
          weight: 0.6,
          opacity: 0.35,
          fillColor: CORES_COBERTURA[faixa],
          fillOpacity: 0.3,
          interactive: false,
        }).addTo(camadaCobertura);
        if (limites.length < 800) limites.push(canto1, canto2);
      }
      return limites;
    }

    function removerCobertura() {
      if (camadaCobertura) {
        mapa.removeLayer(camadaCobertura);
        camadaCobertura = null;
      }
    }

    async function carregar() {
      const form = document.getElementById("form-mapa");
      const params = new URLSearchParams(new FormData(form));
      const legendaCobertura = document.getElementById("legenda-cobertura");
      const legendaRisco = document.getElementById("legenda-risco");
      const emCobertura = params.get("modo") === "cobertura";
      const emRisco = params.get("modo") === "risco";
      modoCobertura = emCobertura || emRisco;

      if (emCobertura) {
        // modo cobertura: grade em vez de pontos — some com marcadores e calor
        const dados = await api("/api/mapa/cobertura?" + params.toString());
        camadaIcones.clearLayers();
        grupo.clearLayers();
        removerCobertura();
        ultimosPontos = [];
        desenharCalor();
        desenharTrajeto([]);
        const limites = desenharCobertura(dados);
        if (legendaCobertura) legendaCobertura.hidden = false;
        mostrarResumo({});
        infoBase = `${dados.total_celulas} célula(s) de ~100 m · ` +
          `~${String(dados.km2).replace(".", ",")} km² · ` +
          `${String(dados.km).replace(".", ",")} km rodados · ${dados.obs} leitura(s)` +
          (dados.truncado ? ` — limitado a ${dados.total_celulas}; reduza o período` : "");
        const info = document.getElementById("mapa-info");
        if (info) info.textContent = infoBase;
        if (limites.length) {
          mapa.invalidateSize();
          const lat = parseFloat(alvoMapa.dataset.lat);
          const lon = parseFloat(alvoMapa.dataset.lon);
          if (isFinite(lat) && isFinite(lon)) mapa.setView([lat, lon], 17);
          else enquadrar(limites);
        }
        return;
      }

      if (emRisco) {
        // modo risco: um ponto por rede, colorido pela faixa do auth_mode. A grade
        // por célula ficaria vermelha em todo lugar (todo quarteirão tem uma rede
        // aberta), então aqui o que informa é a rede, não a célula.
        const paramsRisco = new URLSearchParams(params);
        paramsRisco.set("modo", "redes");
        // risco é coisa de Wi-Fi: sem esse filtro, os 20 mil pontos são dominados por
        // BLE e as redes abertas (pouco observadas) ficam fora do corte
        paramsRisco.delete("tipo");
        paramsRisco.append("tipo", "WIFI");
        const dados = await api("/api/mapa/pontos?" + paramsRisco.toString());
        camadaIcones.clearLayers();
        grupo.clearLayers();
        removerCobertura();
        ultimosPontos = [];
        desenharCalor();
        desenharTrajeto([]);
        const riscos = window.WARDRIVE_RISCOS || {};
        const limites = [];
        // canvas do Leaflet fica em branco depois do zoom nesta máquina; SVG desenha
        const renderer = L.svg({ padding: 0.5 });
        let fracas = 0;
        // pinta do melhor para o pior: assim a rede aberta (a que interessa) fica por
        // cima quando duas caem no mesmo pixel. Para o SVG não travar, entram todas as
        // fracas e uma amostra das demais
        const todos = dados.pontos || [];
        const fracos = todos.filter(p => p.risco === "aberta" || p.risco === "wep");
        const outros = todos.filter(p => !(p.risco === "aberta" || p.risco === "wep"));
        const passo = Math.max(1, Math.ceil(outros.length / 3000));
        const amostra = outros.filter((_, i) => i % passo === 0);
        const ordenados = fracos.concat(amostra).sort((a, b) => {
          const ga = (riscos[b.risco] || {}).gravidade ?? 9;
          const gb = (riscos[a.risco] || {}).gravidade ?? 9;
          return ga - gb;
        });
        for (const p of ordenados) {
          if (p.lat == null || p.lon == null) continue;
          const info = riscos[p.risco] || { cor: "#6b7688", rotulo: p.risco || "?" };
          if (p.risco === "aberta" || p.risco === "wep") fracas += 1;
          const marcador = L.circleMarker([p.lat, p.lon], {
            radius: 5, color: info.cor, weight: 1, fillColor: info.cor, fillOpacity: 0.8,
            renderer: renderer,
          });
          const ssid = p.ssid && p.ssid.length ? p.ssid : "(SSID oculto)";
          marcador.bindPopup(
            `<b>${escapar(ssid)}</b><br><span style="font-family:monospace">${escapar(p.mac)}</span><br>` +
            `<span style="color:${escapar(info.cor)}">${escapar(info.rotulo)}</span> · ` +
            `RSSI ${p.rssi ?? "?"} dBm<br>` +
            `<span style="opacity:.7">${escapar(p.first_seen || "")}</span>`
          );
          marcador.addTo(camadaIcones);
          if (limites.length < 800) limites.push([p.lat, p.lon]);
        }
        if (legendaRisco) legendaRisco.hidden = false;
        mostrarResumo({});
        const total = todos.length;
        const pct = total ? (100 * fracas / total).toFixed(1).replace(".", ",") : "0";
        infoBase = `${fracas} rede(s) fraca(s) de ${total} Wi-Fi (${pct}%) — aberta ou WEP`;
        if (passo > 1) infoBase += ` · amostra de ${amostra.length} das demais (${passo}ª de cada)`;
        const infoRisco = document.getElementById("mapa-info");
        if (infoRisco) infoRisco.textContent = infoBase;
        if (limites.length) {
          mapa.invalidateSize();
          const lat = parseFloat(alvoMapa.dataset.lat);
          const lon = parseFloat(alvoMapa.dataset.lon);
          if (isFinite(lat) && isFinite(lon)) mapa.setView([lat, lon], 17);
          else enquadrar(limites);
        }
        return;
      }
      if (legendaCobertura) legendaCobertura.hidden = true;
      if (legendaRisco) legendaRisco.hidden = true;
      removerCobertura();

      const dados = await api("/api/mapa/pontos?" + params.toString());
      ultimosPontos = dados.pontos || [];
      desenharCalor();
      const resumo = dados.resumo || {};
      const filtrado = !!dados.mac && (resumo.redes || 0) === 1;
      desenharTrajeto(filtrado ? dados.pontos || [] : []);
      const { icones } = desenharPontos();
      const filtros = [];
      if (dados.categoria) filtros.push(document.getElementById("categoria")?.selectedOptions[0]?.textContent || dados.categoria);
      if (dados.mac) filtros.push(dados.pontos[0]?.ssid || dados.mac);
      infoBase = (filtros.length ? filtros.join(" · ") + " — " : "") +
        `${dados.total} ponto(s)` + (dados.truncado
        ? ` — limitado a ${dados.limite}; refine os filtros para ver o resto`
        : "");
      atualizarInfo(icones);
      const limites = [];
      for (const p of ultimosPontos) {
        if (p.lat != null && p.lon != null) limites.push([p.lat, p.lon]);
      }
      if (limites.length) {
        const lat = parseFloat(alvoMapa.dataset.lat);
        const lon = parseFloat(alvoMapa.dataset.lon);
        mapa.invalidateSize();
        if (filtrado) {
          // um item selecionado pode ter sido visto ao longo de um trajeto:
          // enquadrar todos os pontos mostra o "onde ele apareceu" de uma vez
          enquadrar(limites);
        } else if (isFinite(lat) && isFinite(lon)) {
          mapa.setView([lat, lon], 17);
        } else {
          enquadrar(limites);
        }
      }
      mostrarResumo(dados);
    }

    const formMapa = document.getElementById("form-mapa");
    if (formMapa) {
      formMapa.addEventListener("submit", (e) => {
        e.preventDefault();
        carregar();
      });
    }
    carregar();
  }
})();

/* ------------------------------------------------------------------ canais */
(() => {
  const form = document.getElementById("form-canais");
  if (!form) return;
  const alvo = document.getElementById("canais-resultado");
  const escapar2 = (texto) => String(texto ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function barras(canais) {
    const maximo = Math.max(1, ...canais.map((c) => c.aps));
    return `<div class="mini-barras">` + canais.map((c) => `
      <div class="linha">
        <span class="rotulo">canal ${c.canal}</span>
        <span class="trilha"><span style="width: ${(100 * c.aps / maximo).toFixed(1)}%"></span></span>
        <span class="num">${c.aps} APs · ${String(c.rssi_medio).replace(".", ",")} dBm</span>
      </div>`).join("") + `</div>`;
  }

  function sugestoes(itens) {
    if (!itens.length) return "";
    // a lista completa (9 no 5 GHz) vira ruído: os três primeiros respondem a pergunta
    return `<div class="sugestao-canais">` + itens.slice(0, 3).map((s) => {
      const rec = s.posicao === 1;
      // acima de 200% a porcentagem vira número de vezes: "+22737%" não diz nada
      const extra = rec ? "menos poluído"
        : s.excesso_pct >= 200 ? `${Math.round(s.excesso_pct / 100)}× mais energia`
        : `+${s.excesso_pct}% de energia`;
      return `<div class="sugestao ${rec ? "melhor" : ""}">
        <b>${s.posicao}º</b>
        <span class="canal-num">${s.canal}</span>
        <span class="detalhe">${s.aps_mesmo_canal} APs no próprio canal · ${extra}</span>
      </div>`;
    }).join("") + `</div>`;
  }

  function desenhar(dados) {
    if (!dados.aps) {
      return `<p class="texto-fraco">Nenhum AP com GPS em ${dados.raio_m} m deste ponto — nada para comparar.</p>`;
    }
    const partes = [];
    if (dados.rede) {
      partes.push(`<p class="texto-fraco">Ponto da rede <strong>${escapar2(dados.rede)}</strong>
        (${dados.lat}, ${dados.lon}) · raio de ${dados.raio_m} m · ${dados.aps} APs vizinhos</p>`);
    } else {
      partes.push(`<p class="texto-fraco">${dados.lat}, ${dados.lon} · raio de ${dados.raio_m} m ·
        ${dados.aps} APs vizinhos</p>`);
    }
    for (const faixa of ["2.4", "5"]) {
      const bloco = dados.faixas[faixa];
      if (!bloco || !bloco.aps) continue;
      partes.push(`<h3 style="margin:14px 0 6px">${escapar2(bloco.rotulo)} — ${bloco.aps} APs</h3>`);
      partes.push(barras(bloco.canais.slice(0, 8)));
      partes.push(sugestoes(bloco.sugestao));
      if (faixa === "5" && bloco.canais.some((c) => c.canal >= 52 && c.canal <= 144)) {
        partes.push(`<p class="texto-fraco" style="margin:6px 0 0">
          Os canais 52–144 aparecem na lista porque existem na região, mas são DFS — o
          roteador pode abandoná-los por 60 s se detectar radar, por isso não entram na sugestão.</p>`);
      }
    }
    return partes.join("");
  }

  async function analisar() {
    const rede = document.getElementById("canal-rede").value.trim();
    const lat = document.getElementById("canal-lat").value.trim();
    const lon = document.getElementById("canal-lon").value.trim();
    const raio = document.getElementById("canal-raio").value || "100";
    const q = new URLSearchParams();
    if (rede) {
      q.set("rede", rede);
    } else if (lat && lon) {
      q.set("lat", lat);
      q.set("lon", lon);
    } else {
      alvo.innerHTML = `<p class="texto-alerta">Informe o SSID da sua rede ou a coordenada.</p>`;
      return;
    }
    q.set("raio_m", raio);
    alvo.innerHTML = `<p class="texto-fraco">analisando…</p>`;
    try {
      const resposta = await fetch("/api/canais?" + q.toString());
      const dados = await resposta.json();
      alvo.innerHTML = resposta.ok
        ? desenhar(dados)
        : `<p class="texto-alerta">${escapar2(dados.erro || "falhou")}</p>`;
    } catch (erro) {
      alvo.innerHTML = `<p class="texto-alerta">Não deu para consultar: ${escapar2(erro)}</p>`;
    }
  }

  form.addEventListener("submit", (evento) => {
    evento.preventDefault();
    analisar();
  });

  // ?rede=... ou ?lat=..&lon=.. na URL já roda a análise (link vindo da ficha da rede)
  const params = new URLSearchParams(location.search);
  const rede = params.get("rede");
  const lat = params.get("lat");
  const lon = params.get("lon");
  if (rede) document.getElementById("canal-rede").value = rede;
  if (lat) document.getElementById("canal-lat").value = lat;
  if (lon) document.getElementById("canal-lon").value = lon;
  if (rede || (lat && lon)) analisar();
})();

/* ------------------------------------------------------------------- wigle */
(() => {
  const cartao = document.getElementById("wigle-card");
  if (!cartao) return;
  const mac = cartao.dataset.mac;
  const botao = document.getElementById("wigle-consultar");
  const alvo = document.getElementById("wigle-resultado");
  const aviso = document.getElementById("wigle-aviso");
  const esc = (texto) => String(texto ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function dataBr(iso) {
    if (!iso) return "—";
    return String(iso).slice(0, 10).split("-").reverse().join("/");
  }

  function desenhar(dados) {
    if (dados.status === "erro") {
      return `<p class="texto-alerta" style="margin:10px 0 0">${esc(dados.erro)}</p>`;
    }
    const quando = dados.consultado_em ? ` · consultado em ${esc(dados.consultado_em)}` : "";
    const doCache = dados.cache ? `<span class="etiqueta">do cache${quando}</span> ` : "";
    if (!dados.encontrada) {
      return `<p style="margin:10px 0 0">${doCache}
        <strong>O WiGLE não tem registro deste MAC.</strong>
        <span class="texto-fraco">Pode ser rede nova, MAC trocado ou equipamento que só você ouviu.</span></p>`;
    }
    const itens = (dados.resultados || []).map((r) => `
      <tr>
        <td>${esc(r.ssid) || "(oculto)"}</td>
        <td class="texto-fraco">${esc([r.cidade, r.regiao, r.pais].filter(Boolean).join(" · "))}</td>
        <td class="num">${r.canal ?? "—"}</td>
        <td class="texto-fraco">${esc(r.criptografia).toUpperCase() || "—"}</td>
        <td class="texto-fraco">${dataBr(r.primeira)} → ${dataBr(r.ultima)}</td>
        <td class="mono texto-fraco">${r.lat != null ? Number(r.lat).toFixed(4) : "—"},
            ${r.lon != null ? Number(r.lon).toFixed(4) : "—"}</td>
      </tr>`).join("");
    const total = dados.total || (dados.resultados || []).length;
    return `<p style="margin:10px 0 6px">${doCache}
        <strong>${total} ${total === 1 ? "aparição" : "aparições"} no mundo</strong>
        <span class="texto-fraco">— este MAC já foi mapeado por outras pessoas</span></p>
      <div class="tabela-rolagem"><table>
        <thead><tr><th>SSID lá</th><th>onde</th><th class="num">canal</th><th>cripto</th>
                   <th>primeira → última</th><th>coordenada</th></tr></thead>
        <tbody>${itens}</tbody></table></div>`;
  }

  async function consultar(forcar) {
    botao.disabled = true;
    aviso.textContent = "consultando…";
    try {
      const resposta = await fetch(`/api/wigle/rede/${encodeURIComponent(mac)}${forcar ? "?forcar=1" : ""}`);
      const dados = await resposta.json();
      alvo.innerHTML = desenhar(dados);
      aviso.textContent = dados.cache ? "resultado guardado — use “atualizar” para consultar de novo"
        : "";
      if (dados.status !== "erro") {
        botao.textContent = "atualizar (gasta cota)";
        botao.dataset.forcar = "1";
      }
    } catch (erro) {
      alvo.innerHTML = `<p class="texto-alerta" style="margin:10px 0 0">Não deu para consultar: ${esc(erro)}</p>`;
      aviso.textContent = "";
    } finally {
      botao.disabled = false;
    }
  }

  botao.addEventListener("click", () => consultar(botao.dataset.forcar === "1"));
})();

/* ---------------------------------------------------------------- ao vivo */
(() => {
  const alvo = document.getElementById("mapa-ao-vivo");
  if (!alvo || typeof L === "undefined") return;
  const csrf = alvo.dataset.csrf || window.AO_VIVO_CSRF || "";
  const seletor = document.getElementById("fonte");
  const campoArquivo = document.getElementById("campo-arquivo");
  const entradaArquivo = document.getElementById("arquivo");
  const btnIniciar = document.getElementById("btn-iniciar");
  const btnParar = document.getElementById("btn-parar");
  const rotulo = document.getElementById("ao-estado");
  const kpiObs = document.getElementById("kpi-obs");
  const kpiRedes = document.getElementById("kpi-redes");
  const kpiPontos = document.getElementById("kpi-pontos");
  const kpiSessao = document.getElementById("kpi-sessao");

  // sem preferCanvas: nesta máquina o canvas do Leaflet fica branco depois do zoom
  const mapa = L.map(alvo).setView([-23.28, -47.29], 13);
  const rendererSvg = L.svg({ padding: 0.5 });
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    { maxZoom: 19, maxNativeZoom: 19, attribution: "&copy; OpenStreetMap" }).addTo(mapa);
  const camada = L.layerGroup().addTo(mapa);
  // o mapa nasce antes do layout assentar: sem isso, metade do retângulo fica cinza
  setTimeout(() => mapa.invalidateSize(), 300);
  window.addEventListener("resize", () => mapa.invalidateSize());
  new IntersectionObserver((entradas) => {
    if (entradas.some((e) => e.isIntersecting)) mapa.invalidateSize();
  }, { threshold: 0.2 }).observe(alvo);
  window.aoVivoMapa = mapa;      // útil no console e nos testes de tela
  let tracado = null;
  let ultimo = null;
  let ajustado = false;
  let fonteSSE = null;
  let pontos = 0;

  function pintar(lista) {
    for (const p of lista) {
      const cor = p.tipo === "WIFI" ? "#4ad295" : p.tipo === "BLE" ? "#7aa2ff" : "#ffb454";
      L.circleMarker([p.lat, p.lon], {
        radius: 4, color: cor, weight: 1, fillColor: cor, fillOpacity: 0.85,
        renderer: rendererSvg,
      }).bindPopup(`<b>${p.ssid || "(oculto)"}</b><br>${p.mac}<br>${p.rssi ?? "?"} dBm · canal ${p.canal ?? "?"}`)
        .addTo(camada);
      if (ultimo) {
        const ponto = [[ultimo[0], ultimo[1]], [p.lat, p.lon]];
        tracado = tracado ? tracado.addLatLng([p.lat, p.lon]) : L.polyline(ponto, { color: "#56a8ff", weight: 3 }).addTo(camada);
      }
      ultimo = [p.lat, p.lon];
      pontos += 1;
    }
    kpiPontos.textContent = pontos;
    if (!ajustado && ultimo) {           // enquadra o primeiro ponto, depois respeita o zoom do usuário
      ajustado = true;
      mapa.setView(ultimo, 17);
    }
  }

  function pintarEstado(estado) {
    if (!estado) return;
    kpiObs.textContent = estado.obs ?? 0;
    kpiRedes.textContent = estado.redes ?? 0;
    kpiSessao.textContent = estado.sessao || "—";
    if (estado.erro) {
      rotulo.textContent = "erro: " + estado.erro;
      rotulo.classList.add("texto-alerta");
    } else {
      rotulo.textContent = estado.ativo
        ? `coletando… ${estado.obs} obs`
        : `parado — ${estado.obs} observação(ões)${estado.ignoradas ? `, ${estado.ignoradas} linha(s) ignorada(s)` : ""}`;
      rotulo.classList.remove("texto-alerta");
    }
    btnIniciar.disabled = !!estado.ativo;
    btnParar.disabled = !estado.ativo;
  }

  function escutar(desde) {
    if (fonteSSE) fonteSSE.close();
    fonteSSE = new EventSource(`/api/ao-vivo/eventos?desde=${desde}&parar_ao_terminar=1`);
    fonteSSE.onmessage = (evento) => {
      const dados = JSON.parse(evento.data);
      if (dados.pontos && dados.pontos.length) pintar(dados.pontos);
      pintarEstado(dados.estado);
    };
    fonteSSE.addEventListener("fim", () => {
      fonteSSE.close();
      fonteSSE = null;
      // ao terminar, enquadra o traçado inteiro: o percurso todo é a informação
      if (tracado && pontos > 2) {
        try { mapa.fitBounds(tracado.getBounds(), { padding: [30, 30], maxZoom: 17 }); } catch (e) {}
      }
      fetch("/api/ao-vivo/estado").then((r) => r.json()).then(pintarEstado);
    });
    fonteSSE.onerror = () => { /* reconexão automática do EventSource */ };
  }

  function iniciar() {
    const valor = seletor.value || "replay";
    const corpo = { csrf_token: csrf };
    if (valor.startsWith("serial:")) corpo.porta = valor.slice(7);
    else {
      corpo.arquivo = entradaArquivo.value.trim();
      if (!corpo.arquivo) { rotulo.textContent = "informe o arquivo"; return; }
    }
    camada.clearLayers();
    ajustado = false;
    tracado = null;
    ultimo = null;
    pontos = 0;
    kpiPontos.textContent = "0";
    rotulo.textContent = "abrindo…";
    fetch("/api/ao-vivo/iniciar", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify(corpo),
    }).then((r) => r.json()).then((dados) => {
      if (dados.erro) { rotulo.textContent = dados.erro; return; }
      kpiSessao.textContent = dados.sessao;
      btnIniciar.disabled = true;
      btnParar.disabled = false;
      escutar(0);
    }).catch((erro) => { rotulo.textContent = "falhou: " + erro; });
  }

  seletor?.addEventListener("change", () => {
    campoArquivo.hidden = !seletor.value.startsWith("replay");
  });
  btnIniciar?.addEventListener("click", iniciar);
  btnParar?.addEventListener("click", () => {
    fetch("/api/ao-vivo/parar", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: "{}",
    }).then((r) => r.json()).then(pintarEstado);
  });

  fetch("/api/ao-vivo/estado").then((r) => r.json()).then((estado) => {
    pintarEstado(estado);
    if (estado.ativo) escutar(estado.sequencia || 0);
  });
})();

/* ------------------------------------------- escolher a área em Mudanças */
(() => {
  const alvo = document.getElementById("mapa-mudancas");
  if (!alvo || typeof L === "undefined") return;
  const campoLat = document.getElementById("lat");
  const campoLon = document.getElementById("lon");
  const campoRaio = document.getElementById("raio_m");
  const slider = document.getElementById("raio-slider");
  const rotuloRaio = document.getElementById("raio-valor");
  const resumo = document.getElementById("area-resumo");
  const limpar = document.getElementById("limpar-area");
  const caixaPercurso = document.getElementById("mostrar-percurso");

  const mapa = L.map(alvo, { zoomControl: true }).setView([-23.28, -47.29], 13);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    { maxZoom: 19, maxNativeZoom: 19, attribution: "&copy; OpenStreetMap" }).addTo(mapa);
  const renderer = L.svg({ padding: 0.6 });
  const camadaPercurso = L.layerGroup().addTo(mapa);
  window.mapaMudancas = mapa;

  setTimeout(() => mapa.invalidateSize(), 250);
  new IntersectionObserver((entradas) => {
    if (entradas.some((e) => e.isIntersecting)) mapa.invalidateSize();
  }, { threshold: 0.2 }).observe(alvo);

  let marcador = null;
  let circulo = null;
  let raio = parseInt(alvo.dataset.raio, 10) || 200;

  function escreverCampos(lat, lon) {
    const lat6 = Number(lat).toFixed(6);
    const lon6 = Number(lon).toFixed(6);
    if (campoLat) campoLat.value = lat6;
    if (campoLon) campoLon.value = lon6;
    if (campoRaio) campoRaio.value = raio;
    const texto = `${lat6}, ${lon6} · raio ${raio} m`;
    if (resumo) resumo.textContent = texto;
  }

  const CORES_PERCURSO = { "1": "#4a7f9e", "2-5": "#3f6f8c", "6+": "#335c76" };
  const OPACIDADE = { "1": 0.22, "2-5": 0.34, "6+": 0.46 };

  async function desenharPercurso() {
    camadaPercurso.clearLayers();
    if (!caixaPercurso || !caixaPercurso.checked) return;
    const de = alvo.dataset.de, ate = alvo.dataset.ate;
    if (!de || !ate) return;
    try {
      const resposta = await fetch(`/api/mapa/cobertura?de=${de}&ate=${ate}&tipo=WIFI`);
      const dados = await resposta.json();
      for (const celula of dados.celulas || []) {
        const faixa = celula.n >= 6 ? "6+" : celula.n >= 2 ? "2-5" : "1";
        L.rectangle([[celula.lat - 0.0005, celula.lon - 0.0005],
                     [celula.lat + 0.0005, celula.lon + 0.0005]], {
          renderer, stroke: false, fillColor: CORES_PERCURSO[faixa],
          fillOpacity: OPACIDADE[faixa], interactive: false,
        }).addTo(camadaPercurso);
      }
    } catch (erro) {
      if (resumo) resumo.textContent = "não deu para carregar o percurso";
    }
  }

  function porArea(lat, lon, opcoes = {}) {
    const moverMapa = opcoes.moverMapa === true;
    if (marcador) { mapa.removeLayer(marcador); marcador = null; }
    if (circulo) { mapa.removeLayer(circulo); circulo = null; }
    circulo = L.circle([lat, lon], {
      radius: raio, renderer, color: "#4ad295", weight: 2,
      fillColor: "#4ad295", fillOpacity: 0.12,
    }).addTo(mapa);
    marcador = L.marker([lat, lon], { draggable: true }).addTo(mapa);
    marcador.on("drag", () => {
      const pos = marcador.getLatLng();
      circulo.setLatLng(pos);
      escreverCampos(pos.lat, pos.lng);
    });
    marcador.on("dragend", () => {
      const pos = marcador.getLatLng();
      escreverCampos(pos.lat, pos.lng);
    });
    escreverCampos(lat, lon);
    // animação de zoom não completa nesta máquina (some sem erro): vai direto
    if (moverMapa) mapa.setView([lat, lon], 16, { animate: false });
  }

  function limparArea() {
    if (marcador) { mapa.removeLayer(marcador); marcador = null; }
    if (circulo) { mapa.removeLayer(circulo); circulo = null; }
    if (campoLat) campoLat.value = "";
    if (campoLon) campoLon.value = "";
    if (resumo) resumo.textContent = "sem área: comparando o acervo inteiro";
  }

  mapa.on("click", (evento) => {
    // clique em zoom de cidade não deixa escolher a esquina: aproxima na primeira vez
    if (mapa.getZoom() < 14) mapa.setView(evento.latlng, 15, { animate: false });
    porArea(evento.latlng.lat, evento.latlng.lng);
  });

  slider?.addEventListener("input", () => {
    raio = parseInt(slider.value, 10);
    rotuloRaio.textContent = `${raio} m`;
    if (circulo) circulo.setRadius(raio);
    if (campoRaio) campoRaio.value = raio;
    if (marcador) {
      const pos = marcador.getLatLng();
      escreverCampos(pos.lat, pos.lng);
    }
  });

  limpar?.addEventListener("click", limparArea);
  caixaPercurso?.addEventListener("change", desenharPercurso);

  // chegando com ?lat&lon&raio na URL (ou depois de comparar), reconstrói a área
  const lat = parseFloat(alvo.dataset.lat);
  const lon = parseFloat(alvo.dataset.lon);
  if (isFinite(lat) && isFinite(lon)) {
    if (slider) slider.value = raio;
    rotuloRaio.textContent = `${raio} m`;
    porArea(lat, lon, { moverMapa: true });
  } else {
    rotuloRaio.textContent = `${raio} m`;
    if (resumo) resumo.textContent = "clique no mapa para escolher a área";
  }
  desenharPercurso();
})();

/* se escolher "personalizado", abre as datas sem o usuário ter que adivinhar */
(() => {
  const seletor = document.getElementById("periodo");
  const avancado = document.querySelector("details.avancado");
  if (!seletor || !avancado) return;
  seletor.addEventListener("change", () => {
    if (seletor.value === "personalizado") avancado.open = true;
  });
})();
