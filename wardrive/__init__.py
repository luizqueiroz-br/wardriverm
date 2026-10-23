"""Aplicação Flask do Wardrive Manager."""
from __future__ import annotations

import secrets
from datetime import datetime

from flask import Flask, abort, request, session

from . import config, db
from .jobs import runner

__all__ = ["create_app"]


def create_app(settings_overrides: dict | None = None) -> Flask:
    config.ensure_dirs()
    db.init_db()

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = config.flask_secret_key()
    app.config.update(
        APP_NAME=config.APP_NAME,
        APP_VERSION=config.APP_VERSION,
        MAX_CONTENT_LENGTH=256 * 1024 * 1024,   # upload de arquivos grandes pelo navegador
        JSON_SORT_KEYS=False,
        # ferramenta local: sem cache de template/estático (senão editar a interface
        # exige reiniciar o serviço para ver a mudança)
        TEMPLATES_AUTO_RELOAD=True,
        SEND_FILE_MAX_AGE_DEFAULT=0,
    )

    if settings_overrides:
        config.save_settings(settings_overrides)

    # ---------------------------------------------------------------- filtros
    @app.template_filter("duracao")
    def _duracao(segundos):
        """1234 → '20 min'; 3720 → '1 h 02'; None → '—'."""
        if segundos is None:
            return "—"
        segundos = int(segundos)
        if segundos < 60:
            return f"{segundos} s"
        if segundos < 3600:
            return f"{segundos // 60} min"
        return f"{segundos // 3600} h {segundos % 3600 // 60:02d}"

    @app.template_filter("km")
    def _km(valor):
        """Sem valor vira '—' (sessão sem GPS não pode aparecer como 0 km)."""
        if valor is None:
            return "—"
        return f"{valor:.1f}".replace(".", ",")

    @app.template_filter("pct")
    def _pct(valor):
        """7.4 → '7,4' (o app inteiro fala português: vírgula decimal)."""
        if valor is None:
            return "—"
        return f"{valor:.1f}".replace(".", ",")

    @app.template_filter("data_curta")
    def _data_curta(valor):
        """'2026-10-15' → '15/10' (o usuário pensa em dia/mês, não em ISO)."""
        texto = (valor or "")[:10]
        if len(texto) == 10 and texto[4] == "-" and texto[7] == "-":
            return f"{texto[8:10]}/{texto[5:7]}"
        return texto

    from .views import ui
    from .api import api

    app.register_blueprint(ui)
    app.register_blueprint(api)
    app.teardown_appcontext(db.close_db)

    # ---------------------------------------------------------------- CSRF
    @app.before_request
    def _csrf_protect():
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            token = session.get("csrf")
            sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            if not sent and request.mimetype == "application/json":
                sent = (request.get_json(silent=True) or {}).get("csrf_token")
            if not token or not sent or not secrets.compare_digest(str(token), str(sent)):
                abort(400, "token CSRF ausente ou inválido — recarregue a página")

    @app.context_processor
    def _inject():
        token = session.get("csrf")
        if not token:
            token = secrets.token_urlsafe(24)
            session["csrf"] = token
        settings = config.load_settings()
        return {
            "csrf_token": token,
            "app_name": config.APP_NAME,
            "app_version": config.APP_VERSION,
            "settings": settings,
            "targets": config.TARGETS,
            "now": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "has_wigle_key": config.has_key("wigle"),
            "has_wdgwars_key": config.has_key("wdgwars"),
            "jobs_active": runner.list(5),
        }

    # ------------------------------------------ importação automática (1x)
    state = {"auto_import": False}

    @app.before_request
    def _auto_import():
        if state["auto_import"] or request.path.startswith("/static"):
            return None
        state["auto_import"] = True
        settings = config.load_settings()
        if settings.get("auto_import_on_start") and settings.get("ward_dirs"):
            from .importer import scan_and_import

            def _task(job, dirs):
                with db.session() as conn:
                    return scan_and_import(conn, dirs, job=job)

            runner.submit("importação", "Varredura automática das pastas de wardriving",
                          _task, list(settings["ward_dirs"]))
        return None

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "app": config.APP_NAME, "version": config.APP_VERSION}

    @app.errorhandler(404)
    def _404(_exc):
        from flask import render_template

        return render_template("erro.html", codigo=404,
                               mensagem="Página não encontrada."), 404

    @app.errorhandler(413)
    def _413(_exc):
        from flask import render_template

        return render_template("erro.html", codigo=413,
                               mensagem="Arquivo grande demais para este upload."), 413

    return app
