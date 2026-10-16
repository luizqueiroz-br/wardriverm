"""Endpoints JSON: progresso das tarefas, pontos do mapa, importação por upload."""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

from . import config, db, importer, queries
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


@api.get("/mapa/pontos")
def mapa_pontos():
    conn = db.get_db()
    tipos = [t for t in request.args.getlist("tipo") if t]
    file_ids = [i for i in request.args.getlist("sessao") if i.isdigit()]
    modo = request.args.get("modo", "observacoes")
    pontos = queries.map_points(
        conn,
        file_ids=file_ids,
        types=tipos,
        date_from=request.args.get("de", ""),
        date_to=request.args.get("ate", ""),
        limit=request.args.get("limite", type=int),
        network_mode=(modo == "redes"),
    )
    limite = int(request.args.get("limite", 0) or config.load_settings()["map"]["max_points"])
    return jsonify({
        "pontos": pontos,
        "truncado": len(pontos) >= limite,
        "total": len(pontos),
        "limite": limite,
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
            res = importer.import_bytes(conn, Path(storage.filename).name, data, source="upload")
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
