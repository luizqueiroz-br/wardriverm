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
    mapa.on("moveend", () => { if (querIcones()) desenharPontos(); });

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
      const emCobertura = params.get("modo") === "cobertura";
      modoCobertura = emCobertura;

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
      if (legendaCobertura) legendaCobertura.hidden = true;
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
