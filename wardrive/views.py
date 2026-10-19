"""Rotas das páginas (HTML)."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   send_file, url_for)

from . import (categorias, config, db, exporter, importer, queries, uploaders,
               upload_service)
from .jobs import runner

ui = Blueprint("ui", __name__)


def _page_args(**overrides) -> dict:
    args = {k: request.args.get(k, "") for k in ("q", "tipo", "auth", "de", "ate", "ordem",
                                                 "pagina", "sem_ssid", "categoria")}
    args["pagina"] = request.args.get("pagina", type=int) or 1
    args.update(overrides)
    return args


def _paginate(total: int, per_page: int, pagina: int) -> dict:
    pages = max(1, (total + per_page - 1) // per_page)
    pagina = min(max(1, pagina), pages)
    return {"pagina": pagina, "paginas": pages, "total": total,
            "offset": (pagina - 1) * per_page, "per_page": per_page}


# --------------------------------------------------------------------------- #
# dashboard
# --------------------------------------------------------------------------- #
@ui.get("/")
def dashboard():
    conn = db.get_db()
    context = {
        "resumo": queries.overview(conn),
        "serie": queries.daily_series(conn, 45),
        "top_ssids": queries.top_ssids(conn, 12),
        "top_auth": queries.top_auth_modes(conn, 8),
        "canais": queries.channel_histogram(conn, 18),
        "sessoes": queries.sessions(conn, limit=8),
        "uploads": queries.recent_uploads(conn, 10),
        "eventos": queries.events(conn, 12),
        "qualidade": queries.quality_report(conn),
        "objetos": categorias.resumo_categorias(conn),
        "nomes_categoria": categorias.CATEGORIAS,
        "cores_categoria": categorias.CORES,
        "jobs": runner.list(6),
    }
    return render_template("dashboard.html", **context)


@ui.get("/atividade")
def atividade():
    conn = db.get_db()
    return render_template("atividade.html", eventos=queries.events(conn, 200), jobs=runner.list(25))


# --------------------------------------------------------------------------- #
# sessões (arquivos)
# --------------------------------------------------------------------------- #
@ui.get("/sessoes")
def sessoes():
    conn = db.get_db()
    args = _page_args()
    per_page = 25
    total = queries.session_count(conn, args["q"])
    page = _paginate(total, per_page, args["pagina"])
    itens = queries.sessions(conn, search=args["q"], limit=per_page, offset=page["offset"])
    return render_template("sessoes.html", sessoes=itens, page=page, args=args)


@ui.get("/sessoes/<int:file_id>")
def sessao_detalhe(file_id: int):
    conn = db.get_db()
    sessao = queries.session(conn, file_id)
    if not sessao:
        abort(404)
    pagina = request.args.get("pagina", type=int) or 1
    per_page = 50
    total = db.scalar(conn, "SELECT COUNT(*) FROM observations WHERE file_id = ?", (file_id,))
    if request.args.get("filtro") == "sem_gps":
        total = db.scalar(conn, "SELECT COUNT(*) FROM observations WHERE file_id = ? AND has_gps = 0", (file_id,))
        where_extra = "AND has_gps = 0"
    else:
        where_extra = ""
    page = _paginate(total, per_page, pagina)
    obs = conn.execute(
        f"""SELECT * FROM observations WHERE file_id = ? {where_extra}
             ORDER BY first_seen LIMIT ? OFFSET ?""",
        (file_id, per_page, page["offset"]),
    ).fetchall()
    uploads = conn.execute(
        """SELECT DISTINCT u.* FROM uploads u
             JOIN obs_uploads ou ON ou.upload_id = u.id
             JOIN observations o ON o.id = ou.obs_id
            WHERE o.file_id = ? ORDER BY u.id DESC LIMIT 10""",
        (file_id,),
    ).fetchall()
    return render_template("sessao_detalhe.html", sessao=sessao, observacoes=[dict(o) for o in obs],
                           page=page, uploads=[dict(u) for u in uploads])


@ui.post("/sessoes/<int:file_id>/reimportar")
def sessao_reimportar(file_id: int):
    conn = db.get_db()
    sessao = queries.session(conn, file_id)
    if not sessao:
        abort(404)
    path = sessao.get("path")
    if not path or not Path(path).exists():
        flash("O arquivo original não está mais acessível nesse caminho — importe novamente pela tela de importação.", "erro")
        return redirect(url_for("ui.sessao_detalhe", file_id=file_id))
    resultado = importer.import_path(conn, Path(path), source="reimportação", force=True)
    flash(f"Reimportado: {sessao['name']} — {resultado.get('stats', {}).get('obs_count', 0)} linhas.", "ok")
    return redirect(url_for("ui.sessao_detalhe", file_id=file_id))


@ui.post("/sessoes/<int:file_id>/excluir")
def sessao_excluir(file_id: int):
    conn = db.get_db()
    resultado = importer.delete_file(conn, file_id)
    flash(f"Sessão removida: {resultado.get('file', '?')}. Os arquivos de export já gerados não são apagados.", "ok")
    return redirect(url_for("ui.sessoes"))


# --------------------------------------------------------------------------- #
# redes
# --------------------------------------------------------------------------- #
@ui.get("/redes")
def redes():
    conn = db.get_db()
    args = _page_args()
    per_page = 50
    filtros = dict(search=args["q"], type_=args["tipo"], auth=args["auth"], date_from=args["de"],
                   date_to=args["ate"], only_no_ssid=bool(args["sem_ssid"]),
                   sort=args["ordem"] or "obs", categoria=args["categoria"])
    itens, total = queries.networks(conn, limit=per_page, offset=(args["pagina"] - 1) * per_page,
                                    **filtros)
    page = _paginate(total, per_page, args["pagina"])
    itens, _ = queries.networks(conn, limit=per_page, offset=page["offset"], **filtros)
    return render_template("redes.html", redes=itens, page=page, args=args,
                           meta=queries.filters_meta(conn),
                           categorias=categorias.resumo_categorias(conn),
                           nomes_categoria=categorias.CATEGORIAS,
                           cores_categoria=categorias.CORES)


@ui.post("/redes/recategorizar")
def redes_recategorizar():
    """Refaz a classificação (útil depois de importar mais dados)."""
    conn = db.get_db()
    resultado = categorias.categorizar_redes(conn, forcar=True)
    flash(f"Recategorizado: {resultado['redes']} rede(s) — "
          f"{resultado['oui']} prefixos de fabricante na base.", "ok")
    return redirect(url_for("ui.redes", **{k: v for k, v in request.args.items() if v}))


@ui.get("/redes/<mac>")
def rede_detalhe(mac: str):
    conn = db.get_db()
    dados = queries.network_detail(conn, mac.upper())
    if not dados:
        abort(404)
    return render_template("rede_detalhe.html", **dados)


# --------------------------------------------------------------------------- #
# mapa
# --------------------------------------------------------------------------- #
@ui.get("/mapa")
def mapa():
    conn = db.get_db()
    sessoes_lista = queries.sessions(conn, limit=200)
    return render_template("mapa.html", sessoes=sessoes_lista, args=_page_args(),
                           categorias=categorias.resumo_categorias(conn),
                           nomes_categoria=categorias.CATEGORIAS)


# --------------------------------------------------------------------------- #
# exportação
# --------------------------------------------------------------------------- #
@ui.get("/exportar")
def exportar():
    conn = db.get_db()
    return render_template("exportar.html", exports=queries.exports(conn, 60),
                           sessoes=queries.sessions(conn, limit=300),
                           resumo=queries.overview(conn), args=_page_args())


@ui.post("/exportar")
def exportar_criar():
    conn = db.get_db()
    form = request.form
    target = form.get("target", "wigle")
    if target not in config.TARGETS:
        abort(400)
    selection = _selection_from_form(form)
    settings = config.load_settings()

    def _task(job, selection, target, label):
        with db.session() as task_conn:
            job.set_total(len(selection.get("file_ids") or []) or 1, "etapas")
            created = exporter.create_export(task_conn, target=target, selection=selection,
                                             settings=settings, label=label)
            job.tick(f"export #{created['export_id']} criado: {created['rows']} linhas, "
                     f"{created['chunks']} arquivo(s)")
            return created

    job = runner.submit("export", f"Exportação {config.TARGETS[target]['label']}", _task,
                        selection, target, form.get("label") or f"{target}-{selection['mode']}")
    flash(f"Exportação iniciada (tarefa {job.id}). O progresso aparece no topo da página.", "ok")
    return redirect(url_for("ui.exportar"))


def _selection_from_form(form) -> dict:
    mode = form.get("mode", "all")
    file_ids = [int(i) for i in form.getlist("file_ids") if i.strip().isdigit()]
    return {
        "mode": mode,
        "file_ids": file_ids,
        "date_from": form.get("date_from", "").strip(),
        "date_to": form.get("date_to", "").strip(),
        "types": [t for t in form.getlist("types") if t],
        "with_gps_only": bool(form.get("with_gps_only")),
        "dedupe": form.get("dedupe", "exact"),
        "header_mode": form.get("header_mode", "source"),
    }


@ui.get("/exportar/<int:export_id>")
def exportar_detalhe(export_id: int):
    conn = db.get_db()
    dados = queries.export_detail(conn, export_id)
    if not dados["export"]:
        abort(404)
    return render_template("export_detalhe.html", **dados)


@ui.get("/exportar/<int:export_id>/arquivo/<int:chunk_id>")
def exportar_arquivo(export_id: int, chunk_id: int):
    conn = db.get_db()
    chunk = conn.execute(
        "SELECT * FROM export_chunks WHERE id = ? AND export_id = ?", (chunk_id, export_id)
    ).fetchone()
    if not chunk:
        abort(404)
    path = Path(chunk["path"])
    if not path.exists():
        abort(404, "arquivo do lote não existe mais no disco")
    return send_file(path, as_attachment=True, download_name=chunk["filename"])


@ui.get("/exportar/<int:export_id>/zip")
def exportar_zip(export_id: int):
    conn = db.get_db()
    chunks = conn.execute(
        "SELECT * FROM export_chunks WHERE export_id = ? ORDER BY idx", (export_id,)
    ).fetchall()
    export = conn.execute("SELECT * FROM exports WHERE id = ?", (export_id,)).fetchone()
    if not export:
        abort(404)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for chunk in chunks:
            path = Path(chunk["path"])
            if path.exists():
                zf.write(path, arcname=f"{path.parent.name}/{chunk['filename']}")
        out_dir = Path(export["dir"])
        for extra in ("manifest.json", "upload.sh", "upload.ps1"):
            p = out_dir / extra
            if p.exists():
                zf.write(p, arcname=f"{out_dir.name}/{extra}")
    buffer.seek(0)
    name = f"{Path(export['dir']).name}.zip"
    return send_file(buffer, mimetype="application/zip", as_attachment=True, download_name=name)


@ui.post("/exportar/<int:export_id>/excluir")
def exportar_excluir(export_id: int):
    conn = db.get_db()
    export = conn.execute("SELECT dir FROM exports WHERE id = ?", (export_id,)).fetchone()
    if export:
        conn.execute("DELETE FROM exports WHERE id = ?", (export_id,))
        flash("Registro de exportação removido (os arquivos continuam em disco).", "ok")
    return redirect(url_for("ui.exportar"))


# --------------------------------------------------------------------------- #
# uploads
# --------------------------------------------------------------------------- #
@ui.get("/uploads")
def uploads_page():
    conn = db.get_db()
    secrets_info = config.load_secrets()
    lista, total = queries.uploads(conn, target=request.args.get("target", ""),
                                   status=request.args.get("status", ""), limit=200)
    return render_template(
        "uploads.html",
        uploads=lista, total=total,
        exports=queries.exports(conn, 40),
        sessoes=queries.sessions(conn, limit=300),
        resumo=queries.overview(conn),
        chaves={
            "wigle_name": config.mask(secrets_info.get("wigle_api_name", "")),
            "wigle_token": config.mask(secrets_info.get("wigle_api_token", "")),
            "wdgwars": config.mask(secrets_info.get("wdgwars_api_key", "")),
            "de_env": secrets_info.get("_from_env", False),
        },
    )


@ui.post("/uploads/chaves")
def uploads_salvar_chaves():
    patch = {}
    if request.form.get("wigle_api_name", "").strip():
        patch["wigle_api_name"] = request.form["wigle_api_name"].strip()
    if request.form.get("wigle_api_token", "").strip():
        patch["wigle_api_token"] = request.form["wigle_api_token"].strip()
    if request.form.get("wdgwars_api_key", "").strip():
        patch["wdgwars_api_key"] = request.form["wdgwars_api_key"].strip()
    if request.form.get("apagar_wigle"):
        patch.update({"wigle_api_name": "", "wigle_api_token": ""})
    if request.form.get("apagar_wdgwars"):
        patch["wdgwars_api_key"] = ""
    if patch:
        config.save_secrets(patch)
        flash("Chaves salvas em data/secrets.json (fora do banco e do navegador).", "ok")
    else:
        flash("Nada para salvar — os campos de chave vieram vazios.", "aviso")
    return redirect(url_for("ui.uploads_page"))


@ui.post("/uploads/validar")
def uploads_validar():
    target = request.form.get("target", "")
    settings = config.load_settings()
    if target == "wigle":
        creds = uploaders.creds_for("wigle")
        resultado = uploaders.wigle_validate(creds["name"], creds["token"],
                                             base=settings["upload"].get("wigle_base"), timeout=20)
    elif target == "wdgwars":
        creds = uploaders.creds_for("wdgwars")
        resultado = uploaders.wdgwars_validate(creds["key"],
                                               base=settings["upload"].get("wdgwars_base"), timeout=20)
    else:
        abort(400)
    flash(("✓ " if resultado.get("ok") else "✗ ") + f"{target}: {resultado.get('message', '')}",
          "ok" if resultado.get("ok") else "erro")
    return redirect(url_for("ui.uploads_page"))


@ui.post("/uploads/enviar")
def uploads_enviar():
    form = request.form
    target = form.get("target", "wigle")
    if target not in config.TARGETS:
        abort(400)
    settings = config.load_settings()
    dry_run = bool(form.get("dry_run"))
    mode = form.get("mode", "new")
    selection = {
        "mode": mode,
        "file_ids": [int(i) for i in form.getlist("file_ids") if i.strip().isdigit()],
        "date_from": form.get("date_from", "").strip(),
        "date_to": form.get("date_to", "").strip(),
        "types": [t for t in form.getlist("types") if t],
        "with_gps_only": bool(form.get("with_gps_only", "1")),
        "dedupe": form.get("dedupe", settings["export"].get("dedupe", "exact")),
        "header_mode": form.get("header_mode", settings["export"].get("header_mode", "source")),
    }
    label = form.get("label") or f"{target}-{mode}"
    export_id = form.get("export_id")
    if export_id and export_id.isdigit():
        export_id = int(export_id)

        def _resend(job):
            with db.session() as task_conn:
                return upload_service.upload_export(task_conn, export_id, target, settings,
                                                    dry_run=dry_run, job=job)

        job = runner.submit("upload", f"Reenvio do lote #{export_id} para {config.TARGETS[target]['label']}",
                            _resend)
        flash(f"Reenvio iniciado (tarefa {job.id}).", "ok")
        return redirect(url_for("ui.uploads_page"))

    def _task(job):
        with db.session() as task_conn:
            return upload_service.export_and_upload(task_conn, target=target, selection=selection,
                                                    settings=settings, label=label, dry_run=dry_run, job=job)

    rotulo = "Simulação" if dry_run else "Envio"
    job = runner.submit("upload", f"{rotulo} para {config.TARGETS[target]['label']}", _task)
    flash(f"{rotulo} iniciada (tarefa {job.id}). Acompanhe o progresso aqui na página.", "ok")
    return redirect(url_for("ui.uploads_page"))


# --------------------------------------------------------------------------- #
# importação e configurações
# --------------------------------------------------------------------------- #
@ui.get("/importar")
def importar():
    conn = db.get_db()
    return render_template("importar.html", pastas=config.load_settings()["ward_dirs"],
                           sessoes=queries.sessions(conn, limit=40),
                           qualidade=queries.quality_report(conn))


@ui.post("/importar/scan")
def importar_scan():
    settings = config.load_settings()
    dirs = [d.strip() for d in request.form.getlist("dirs") if d.strip()] or settings["ward_dirs"]
    force = bool(request.form.get("force"))

    def _task(job, dirs, force):
        with db.session() as task_conn:
            return importer.scan_and_import(task_conn, dirs, force=force, job=job)

    job = runner.submit("importação", "Varredura das pastas de wardriving", _task, dirs, force)
    flash(f"Varredura iniciada (tarefa {job.id}).", "ok")
    return redirect(url_for("ui.importar"))


@ui.post("/importar/purgar-vazios")
def importar_purgar():
    conn = db.get_db()
    total = importer.purge_empty(conn)
    flash(f"{total} registro(s) sem observações removido(s).", "ok")
    return redirect(url_for("ui.importar"))


@ui.get("/config")
def config_page():
    return render_template("config.html", cfg=config.load_settings(),
                           pastas=config.load_settings()["ward_dirs"])


@ui.post("/config")
def config_salvar():
    form = request.form
    patch = {
        "ward_dirs": [d.strip() for d in form.get("ward_dirs", "").splitlines() if d.strip()],
        "auto_import_on_start": bool(form.get("auto_import_on_start")),
        "device": {
            "appRelease": form.get("device_appRelease", "").strip(),
            "model": form.get("device_model", "").strip(),
            "release": form.get("device_release", "").strip(),
            "device": form.get("device_device", "").strip(),
            "display": form.get("device_display", "").strip(),
            "board": form.get("device_board", "").strip(),
            "brand": form.get("device_brand", "").strip(),
        },
        "export": {
            "rows_per_chunk": int(form.get("rows_per_chunk") or 10000),
            "wdgwars_rows_per_chunk": int(form.get("wdgwars_rows_per_chunk") or 10000),
            "wigle_max_bytes": int(float(form.get("wigle_max_mb") or 38) * 1024 * 1024),
            "wdgwars_max_bytes": int(float(form.get("wdgwars_max_mb") or 14) * 1024 * 1024),
            "gzip_wigle": bool(form.get("gzip_wigle")),
            "include_no_gps": bool(form.get("include_no_gps")),
            "dedupe": form.get("dedupe", "exact"),
            "header_mode": form.get("header_mode", "source"),
        },
        "upload": {
            "wigle_donate": bool(form.get("wigle_donate")),
            "timeout": int(form.get("timeout") or 300),
            "dry_run_default": bool(form.get("dry_run_default")),
            "wigle_base": form.get("wigle_base", "").strip() or config.WIGLE_API_BASE,
            "wdgwars_base": form.get("wdgwars_base", "").strip() or config.WDGWARS_API_BASE,
        },
        "map": {"max_points": int(form.get("map_max_points") or 40000)},
    }
    config.save_settings(patch)
    flash("Configurações salvas.", "ok")
    return redirect(url_for("ui.config_page"))
