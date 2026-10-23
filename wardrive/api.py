"""Endpoints JSON: progresso das tarefas, pontos do mapa, importação por upload."""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

from . import canais, config, db, importer, mudancas, origens, queries, seguranca, wigle
from .jobs import runner

api = Blueprint("api", __name__, url_prefix="/api")


@api.get("/jobs")
def jobs():
    return jsonify({"jobs": [j.to_dict(with_log=False) for j in runner.list(20)],
                    "busy": runner.busy()})


@api.get("/jobs/<job_id>")
def job_detail(job_id: str):
    job = runner.get(job_id)
    if not job:
        return jsonify({"erro": "tarefa não encontrada"}), 404
    return jsonify(job.to_dict())


@api.get("/resumo")
def resumo():
    conn = db.get_db()
    return jsonify(queries.overview(conn))


@api.get("/mudancas")
def mudancas_endpoint():
    """Diferença entre duas janelas: novas, sumidas e mudadas (mesma API da tela)."""
    conn = db.get_db()
    de_a = request.args.get("de_a", "")
    ate_a = request.args.get("ate_a", "")
    de_b = request.args.get("de_b", "")
    ate_b = request.args.get("ate_b", "")
    if not all((de_a, ate_a, de_b, ate_b)):
        padrao = mudancas.janelas_sugeridas(conn)
        de_a = de_a or padrao["de_a"]
        ate_a = ate_a or padrao["ate_a"]
        de_b = de_b or padrao["de_b"]
        ate_b = ate_b or padrao["ate_b"]
    area = None
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)
    if lat is not None and lon is not None:
        area = {"lat": lat, "lon": lon,
                "raio_m": max(20, min(5000, request.args.get("raio_m", 200, type=int)))}
    resultado = mudancas.comparar(conn, de_a, ate_a, de_b, ate_b, area=area,
                                  tipo=request.args.get("tipo", "WIFI"),
                                  limite=request.args.get("limite", 200, type=int))
    return jsonify(resultado)


@api.get("/wigle/rede/<mac>")
def wigle_rede(mac: str):
    """Histórico global de um BSSID no WiGLE. Consulta sob demanda, com cache de 30 dias."""
    conn = db.get_db()
    forcar = request.args.get("forcar") in ("1", "true", "sim")
    return jsonify(wigle.consultar(conn, mac, forcar=forcar,
                                   base=config.load_settings()["upload"].get("wigle_base")))


@api.get("/canais")
def canais_do_ponto():
    """Ocupação de canais em volta de um ponto (ou da coordenada da própria rede)."""
    conn = db.get_db()
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)
    rede = (request.args.get("rede") or "").strip()
    excluir = (request.args.get("mac") or "").strip() or None
    nome = None

    if rede:
        linha = conn.execute(
            """SELECT mac, ssid, best_lat, best_lon FROM networks
                WHERE type = 'WIFI' AND best_lat IS NOT NULL
                  AND (UPPER(mac) = ? OR ssid LIKE ?)
                ORDER BY obs_count DESC LIMIT 1""",
            (rede.upper(), f"%{rede}%")).fetchone()
        if linha is None:
            return jsonify({"erro": f"nenhuma rede Wi-Fi com GPS para “{rede}”"}), 404
        lat, lon = linha["best_lat"], linha["best_lon"]
        excluir = excluir or linha["mac"]
        nome = linha["ssid"] or linha["mac"]

    if lat is None or lon is None:
        return jsonify({"erro": "informe a rede ou a latitude/longitude"}), 400

    raio = max(10, min(2000, request.args.get("raio_m", 100, type=int)))
    relatorio = canais.relatorio(conn, lat, lon, raio, excluir_mac=excluir)
    relatorio["rede"] = nome
    return jsonify(relatorio)


@api.get("/mapa/risco")
def mapa_risco():
    """Células de ~100 m com quantas redes Wi-Fi fracas (aberta/WEP) foram vistas."""
    conn = db.get_db()
    file_ids = [i for i in request.args.getlist("sessao") if i.isdigit()]
    celulas = seguranca.por_celula(conn, file_ids=file_ids,
                                   de=request.args.get("de", ""),
                                   ate=request.args.get("ate", ""),
                                   limite=request.args.get("limite", 2000, type=int))
    return jsonify({
        "celulas": celulas,
        "grau_celula": 0.001,
        "total_celulas": len(celulas),
        "redes": sum(c["redes"] for c in celulas),
        "fracas": sum(c["fracas"] for c in celulas),
        "riscos": seguranca.RISCO,
    })


@api.get("/mapa/cobertura")
def mapa_cobertura():
    conn = db.get_db()
    tipos = [t for t in request.args.getlist("tipo") if t]
    file_ids = [i for i in request.args.getlist("sessao") if i.isdigit()]
    dados = queries.cobertura(
        conn,
        file_ids=file_ids,
        types=tipos,
        date_from=request.args.get("de", ""),
        date_to=request.args.get("ate", ""),
        limite=request.args.get("limite", 20000, type=int),
    )
    return jsonify(dados)


@api.get("/mapa/pontos")
def mapa_pontos():
    conn = db.get_db()
    tipos = [t for t in request.args.getlist("tipo") if t]
    file_ids = [i for i in request.args.getlist("sessao") if i.isdigit()]
    modo = request.args.get("modo", "observacoes")
    mac = (request.args.get("mac") or "").strip()
    categoria = (request.args.get("categoria") or "").strip()
    data_from = request.args.get("de", "")
    data_to = request.args.get("ate", "")
    limite_arg = request.args.get("limite", type=int)
    pontos = queries.map_points(
        conn,
        file_ids=file_ids,
        types=tipos,
        date_from=data_from,
        date_to=data_to,
        limit=limite_arg,
        network_mode=(modo == "redes"),
        mac=mac,
        categoria=categoria,
    )
    limite = int(limite_arg or config.load_settings()["map"]["max_points"])
    # o mapa de risco colore o ponto pelo auth_mode: resolver aqui evita repetir
    # a regra no JavaScript
    for ponto in pontos:
        ponto["risco"] = seguranca.risco_de(ponto.get("auth_mode"))
    resumo = queries.map_resumo(conn, file_ids=file_ids, types=tipos, date_from=data_from,
                                date_to=data_to, mac=mac, categoria=categoria)
    return jsonify({
        "pontos": pontos,
        "truncado": len(pontos) >= limite,
        "total": len(pontos),
        "limite": limite,
        "mac": mac,
        "categoria": categoria,
        "resumo": resumo,
    })


@api.post("/importar")
def importar_arquivos():
    arquivos = request.files.getlist("arquivos") or request.files.getlist("file")
    if not arquivos:
        return jsonify({"erro": "nenhum arquivo recebido"}), 400
    resultados = []
    with db.session() as conn:
        for storage in arquivos:
            if not storage or not storage.filename:
                continue
            data = storage.read()
            # SQLite precisa de caminho (é lido em memória, sem cópia do arquivo morto)
            caminho = None
            if origens.detectar(storage.filename, data) == "sqlite":
                import tempfile
                from pathlib import Path as _Path
                temporario = tempfile.NamedTemporaryFile(delete=False, suffix=".sqlite")
                temporario.write(data)
                temporario.close()
                caminho = temporario.name
            try:
                res = importer.import_bytes(conn, Path(storage.filename).name, data,
                                            source="upload", path=caminho)
            finally:
                if caminho:
                    from pathlib import Path as _P
                    _P(caminho).unlink(missing_ok=True)
            resultados.append(res)
    return jsonify({
        "resultados": resultados,
        "novos": sum(1 for r in resultados if r["status"] == "ok"),
        "ignorados": sum(1 for r in resultados if r["status"] == "ignorado"),
        "erros": sum(1 for r in resultados if r["status"] == "erro"),
    })


@api.get("/redes")
def redes_json():
    conn = db.get_db()
    itens, total = queries.networks(
        conn,
        search=request.args.get("q", ""),
        type_=request.args.get("tipo", ""),
        auth=request.args.get("auth", ""),
        date_from=request.args.get("de", ""),
        date_to=request.args.get("ate", ""),
        only_no_ssid=bool(request.args.get("sem_ssid")),
        sort=request.args.get("ordem", "obs"),
        limit=min(request.args.get("limite", 200, type=int) or 200, 2000),
        offset=request.args.get("offset", 0, type=int) or 0,
    )
    return jsonify({"redes": itens, "total": total})


@api.get("/exportacoes/<int:export_id>/manifesto")
def manifesto(export_id: int):
    conn = db.get_db()
    export = conn.execute("SELECT * FROM exports WHERE id = ?", (export_id,)).fetchone()
    if not export:
        return jsonify({"erro": "exportação não encontrada"}), 404
    manifest = Path(export["dir"]) / "manifest.json"
    if not manifest.exists():
        return jsonify({"erro": "manifesto ausente"}), 404
    from flask import send_file

    return send_file(manifest, mimetype="application/json", as_attachment=True,
                     download_name="manifest.json")
