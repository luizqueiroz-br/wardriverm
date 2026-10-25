"""Interface de linha de comando do Wardrive Manager.

Exemplos:
    python cli.py serve --port 5000
    python cli.py importar --dirs "C:/Users/luizv/OneDrive/Área de Trabalho/ward/send"
    python cli.py importar --watch 60
    python cli.py exportar --destino wigle --modo novo
    python cli.py enviar --destino wdgwars --modo novo --simular
    python cli.py enviar --destino wigle --export 3 --real
    python cli.py status
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

from wardrive import (categorias, config, db, exporter, importer, queries, upload_service,
                      uploaders)

TARGETS = tuple(config.TARGETS)


def _settings() -> dict:
    return config.load_settings()


def _conn():
    db.init_db()
    return db.connect()


class _ConsoleJob:
    """Objeto mínimo que imita a interface de Job usada pelos serviços."""

    def write(self, message, level="info"):
        print(f"  · {message}")

    def set_total(self, total, unit="itens"):
        self.total = total

    def tick(self, message=None):
        if message:
            print(f"  · {message}")


def _pid_file():
    return config.DATA_DIR / "servidor.pid"


def _servico_no_ar(porta: int = 5000) -> tuple[bool, int | None]:
    """(está respondendo?, pid registrado)."""
    import urllib.error
    import urllib.request

    pid = None
    arquivo = _pid_file()
    if arquivo.exists():
        try:
            pid = int(arquivo.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pid = None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{porta}/healthz", timeout=3) as resposta:
            return resposta.status == 200, pid
    except (urllib.error.URLError, OSError):
        return False, pid


def cmd_status(args) -> int:
    with db.session() as conn:
        resumo = queries.overview(conn)
    no_ar, pid = _servico_no_ar(args.port)
    print(f"Serviço:    {'no ar em http://127.0.0.1:%d' % args.port if no_ar else 'parado'}"
          + (f" (pid {pid})" if no_ar and pid else ""))
    print(f"Banco:      {config.DB_PATH}")
    print(f"Sessões:    {resumo['arquivos']}")
    print(f"Observações:{resumo['observacoes']:>10}")
    print(f"Redes:      {resumo['redes']}  (wifi {resumo['redes_wifi']}, ble/bt {resumo['redes_ble']})")
    print(f"Período:    {resumo['inicio']} → {resumo['fim']}")
    print(f"Pendentes:  WiGLE {resumo['pendentes']['wigle']} linhas | WDGoWars {resumo['pendentes']['wdgwars']} linhas")
    print(f"Chaves:     WiGLE {'ok' if config.has_key('wigle') else 'ausente'} | "
          f"WDGoWars {'ok' if config.has_key('wdgwars') else 'ausente'}")
    uploads = resumo["uploads"]
    if uploads:
        print("Uploads:")
        for u in uploads:
            print(f"  {u['target']:<8} {u['status']:<10} {u['n']:>4}  último {u['ultimo']}")
    return 0


def cmd_percursos(args) -> int:
    with db.session() as conn:
        resultado = queries.recalcular_percursos(conn, forcar=args.forcar)
        total = queries.progresso_geral(conn)
    print(f"{resultado['sessoes']} sessão(ões) calculada(s) — +{resultado['km']} km")
    print(f"Acervo: {total['km']} km em {total['horas']} h de coleta"
          f"{f' ({total['velocidade_kmh']} km/h médio)' if total['velocidade_kmh'] else ''}")
    return 0


def cmd_categorizar(args) -> int:
    with db.session() as conn:
        if args.baixar:
            print("Baixando a base OUI do IEEE…")
        resultado = categorias.categorizar_redes(conn, forcar=args.forcar, baixar=args.baixar)
    print(f"\n{resultado['redes']} rede(s) classificada(s) · {resultado['oui']} prefixos na base\n")
    total = sum(resultado["por_categoria"].values()) or 1
    for categoria, quantidade in sorted(resultado["por_categoria"].items(),
                                        key=lambda par: -par[1]):
        barra = "#" * max(1, round(28 * quantidade / total))
        print(f"  {categorias.nome_categoria(categoria):30} {quantidade:6}  {barra}")
    return 0


def cmd_puxar(args) -> int:
    from wardrive import dispositivo

    if args.diagnostico:
        alvo = f" e enviar “{args.comando}”" if args.comando else ""
        print(f"Abrindo {args.porta} a {args.baud} baud por {args.segundos}s{alvo} — a saída crua vem abaixo.")
        try:
            linhas = dispositivo.diagnostico(args.porta, baud=args.baud, segundos=args.segundos,
                                             comando=args.comando)
        except dispositivo.ErroDispositivo as erro:
            print(f"✗ {erro}")
            return 1
        for linha in linhas:
            print(f"  | {linha}")
        print(f"\n{len(linhas)} linha(s). Se o cartão não listou nada, guarde esta saída: "
              "o protocolo do seu firmware é diferente do assumido (sd list / sd read).")
        return 0

    destino = Path(args.destino or (_settings()["ward_dirs"][0] if _settings()["ward_dirs"] else "data/puxados"))
    job = _ConsoleJob()
    with db.session() as conn:
        resumo = dispositivo.puxar(conn, args.porta, destino, importar=not args.sem_importar,
                                   baud=args.baud, job=job)
    if resumo["erro"]:
        print(f"✗ {resumo['erro']}")
    print(f"{len(resumo['baixados'])} baixado(s), {len(resumo['ignorados'])} já estava(m) aqui.")
    for nome in resumo["baixados"]:
        print(f"  + {nome}")
    if resumo["importacao"]:
        imp = resumo["importacao"]
        print(f"  importação: {imp['novos']} sessão(ões), {imp['obs']} linha(s), {imp['erros']} erro(s)")
    return 0 if not resumo["erro"] else 1


def cmd_portas(args) -> int:
    from wardrive import dispositivo

    portas = dispositivo.portas_disponiveis()
    if not portas:
        print("Nenhuma porta serial encontrada. O ESP32 aparece como COMx no Windows.")
        return 1
    for item in portas:
        print(f"  {item['porta']:<10} {item['descricao']}")
    return 0


def cmd_importar(args) -> int:
    dirs = args.dirs or _settings()["ward_dirs"]
    job = _ConsoleJob()
    with db.session() as conn:
        if args.watch:
            print(f"Vigiando {len(dirs)} pasta(s) a cada {args.watch}s — Ctrl+C para sair.")
            try:
                while True:
                    resumo = importer.scan_and_import(conn, dirs, force=args.forcar)
                    if resumo["novos"] or resumo["erros"]:
                        print(f"[{time.strftime('%H:%M:%S')}] {resumo['novos']} novo(s), "
                              f"{resumo['obs']} linhas, {resumo['erros']} erro(s)")
                    time.sleep(args.watch)
            except KeyboardInterrupt:
                print("Encerrado.")
                return 0
        resumo = importer.scan_and_import(conn, dirs, force=args.forcar, job=job)
    print(f"\n{resumo['novos']} arquivo(s) importado(s), {resumo['ignorados']} já existente(s), "
          f"{resumo['vazios']} vazio(s), {resumo['erros']} erro(s) — {resumo['obs']} linhas novas.")
    for detalhe in resumo["detalhes"]:
        if detalhe["status"] == "ok":
            s = detalhe["stats"]
            print(f"  + {detalhe['file']}: {s['obs_count']} linhas ({s['wifi_count']} wifi, "
                  f"{s['ble_count']} ble), {s['network_count']} redes")
        else:
            print(f"  ! {detalhe['file']}: {detalhe.get('message', detalhe['status'])}")
    return 0


def _selection_from_args(args) -> dict:
    return {
        "mode": args.modo,
        "file_ids": [int(i) for i in (args.sessoes or [])],
        "date_from": args.de or "",
        "date_to": args.ate or "",
        "types": args.tipos or ["WIFI", "BLE"],
        "with_gps_only": not args.com_sem_gps,
        "dedupe": args.duplicatas,
        "header_mode": args.preheader,
    }


def cmd_exportar(args) -> int:
    settings = _settings()
    job = _ConsoleJob()
    selection = _selection_from_args(args)
    with db.session() as conn:
        criado = exporter.create_export(conn, target=args.destino, selection=selection,
                                        settings=settings, label=args.rotulo)
    print(f"Lote #{criado['export_id']}: {criado['rows']} linhas em {criado['chunks']} arquivo(s), "
          f"{criado['bytes'] / 1024:.0f} KB")
    priv = criado.get("privacidade") or {}
    if priv.get("omitidas") or priv.get("zeradas"):
        print(f"  áreas privadas: {priv.get('omitidas', 0)} linha(s) omitida(s), "
              f"{priv.get('zeradas', 0)} sem coordenada "
              f"(de {priv.get('total_selecionado', 0)} na seleção)")
    print(f"Pasta: {criado['dir']}")
    for chunk in criado["manifest"]["chunks"]:
        print(f"  {chunk['file']}: {chunk['rows']} linhas, {chunk['bytes'] / 1024:.0f} KB")
    return 0


def cmd_enviar(args) -> int:
    settings = _settings()
    job = _ConsoleJob()
    if args.export:
        with db.session() as conn:
            resumo = upload_service.upload_export(conn, args.export, args.destino, settings,
                                                 dry_run=args.simular, job=job)
    else:
        selection = _selection_from_args(args)
        with db.session() as conn:
            resposta = upload_service.export_and_upload(
                conn, target=args.destino, selection=selection, settings=settings,
                label=args.rotulo, dry_run=args.simular, job=job)
        resumo = resposta.get("upload") or {}
        if not resumo:
            print(resposta.get("message", "nada a enviar"))
            return 0
    print(f"\nenviados {resumo['enviados']} lote(s) — ok {resumo['ok']}, duplicados "
          f"{resumo['duplicados']}, simulação {resumo['simulados']}, erros {resumo['erros']} "
          f"({resumo['linhas']} linhas)")
    for item in resumo["resultados"]:
        print(f"  {item['chunk']}: {item['status']} — {(item.get('message') or '')[:120]}")
    return 1 if resumo["erros"] else 0


def cmd_validar(args) -> int:
    settings = _settings()
    if args.destino == "wigle":
        creds = uploaders.creds_for("wigle")
        if not creds["name"] or not creds["token"]:
            print("credenciais WiGLE ausentes (data/secrets.json ou variáveis de ambiente)")
            return 2
        resultado = uploaders.wigle_validate(creds["name"], creds["token"],
                                             base=settings["upload"]["wigle_base"])
    else:
        creds = uploaders.creds_for("wdgwars")
        if not creds["key"]:
            print("chave WDGoWars ausente (data/secrets.json ou WDGWARS_API_KEY)")
            return 2
        resultado = uploaders.wdgwars_validate(creds["key"], base=settings["upload"]["wdgwars_base"])
    print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))
    return 0 if resultado.get("ok") else 1


def cmd_exportacoes(args) -> int:
    with db.session() as conn:
        lotes = queries.exports(conn, args.limite)
    for lote in lotes:
        print(f"#{lote['id']:<4} {lote['created_at']}  {lote['target']:<8} {lote['row_count']:>6} linhas  "
              f"{lote['arquivos']} arquivo(s)  envios ok={lote['enviados']} falhas={lote['falhas']}  "
              f"{lote['label'] or ''}")
        print(f"      {lote['dir']}")
    return 0


def cmd_serve(args) -> int:
    from wardrive import create_app

    app = create_app()
    arquivo_pid = _pid_file()
    arquivo_pid.write_text(str(os.getpid()), encoding="utf-8")
    print(f"{config.APP_NAME} em http://127.0.0.1:{args.port} (Ctrl+C para encerrar)")
    try:
        app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False, threaded=True)
    finally:
        try:
            arquivo_pid.unlink(missing_ok=True)
        except OSError:
            pass
    return 0


def cmd_parar(args) -> int:
    """Encerra o serviço iniciado por `serve`, usando o pid registrado."""
    arquivo = _pid_file()
    no_ar, pid = _servico_no_ar(args.port)
    if not no_ar:
        print("O serviço não está respondendo — nada a encerrar.")
        arquivo.unlink(missing_ok=True)
        return 0
    if not pid:
        print("O serviço está no ar, mas sem pid registrado (foi iniciado de outra forma).\n"
              "Encerre pela janela do terminal ou com: taskkill /F /PID <número>")
        return 2
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        print(f"Não foi possível encerrar o pid {pid}: {exc}")
        return 1
    time.sleep(1.5)
    ainda, _ = _servico_no_ar(args.port)
    arquivo.unlink(missing_ok=True)
    print(f"Serviço encerrado (pid {pid})." if not ainda else f"O pid {pid} ainda responde; tente novamente.")
    return 0 if not ainda else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Wardrive Manager — gestão e upload de dados de wardriving")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("serve", help="inicia a interface web")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--debug", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("parar", help="encerra o serviço iniciado por serve")
    p.add_argument("--port", type=int, default=5000)
    p.set_defaults(func=cmd_parar)

    p = sub.add_parser("status", help="resumo do banco e das pendências de envio")
    p.add_argument("--port", type=int, default=5000)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("importar", help="varre pastas e importa arquivos novos")
    p.add_argument("--dirs", nargs="*", help="pastas (padrão: as das configurações)")
    p.add_argument("--forcar", action="store_true", help="reimporta mesmo que já exista")
    p.add_argument("--watch", type=int, default=0, help="repetir a varredura a cada N segundos")
    p.set_defaults(func=cmd_importar)

    p = sub.add_parser("portas", help="lista as portas seriais (para achar o COM do ESP32)")
    p.set_defaults(func=cmd_portas)

    p = sub.add_parser("puxar", help="baixa os logs do cartão SD do ESP32 pela serial")
    p.add_argument("--porta", required=True, help="ex.: COM5 (veja com `cli.py portas`)")
    p.add_argument("--destino", help="pasta onde salvar (padrão: a primeira das configurações)")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--sem-importar", action="store_true", help="só baixar, não importar")
    p.add_argument("--diagnostico", action="store_true",
                   help="não baixa nada: mostra a resposta crua do firmware")
    p.add_argument("--comando", help="com --diagnostico, envia esta linha antes de ler (ex.: help)")
    p.add_argument("--segundos", type=float, default=5.0,
                   help="tempo de escuta no diagnóstico (padrão: 5)")
    p.set_defaults(func=cmd_puxar)

    p = sub.add_parser("percursos", help="recalcula km/duração/velocidade das sessões")
    p.add_argument("--forcar", action="store_true", help="recalcula todas, não só as que faltam")
    p.set_defaults(func=cmd_percursos)

    p = sub.add_parser("categorizar", help="classifica as redes em tipos de objeto (roteador, veículo, tag…)")
    p.add_argument("--forcar", action="store_true", help="reclassifica todas (não só as sem categoria)")
    p.add_argument("--baixar", action="store_true", help="baixa a base OUI do IEEE antes de classificar")
    p.set_defaults(func=cmd_categorizar)

    def _add_selection(p):
        p.add_argument("--destino", choices=TARGETS, required=True)
        p.add_argument("--modo", choices=("all", "new", "files"), default="new")
        p.add_argument("--sessoes", nargs="*", type=int, help="ids de sessão (modo files)")
        p.add_argument("--de", default="", help="data inicial AAAA-MM-DD")
        p.add_argument("--ate", default="", help="data final AAAA-MM-DD")
        p.add_argument("--tipos", nargs="*", default=["WIFI", "BLE"], help="WIFI BLE BT GSM CDMA WCDMA LTE NR")
        p.add_argument("--com-sem-gps", action="store_true", help="inclui linhas sem GPS")
        p.add_argument("--duplicatas", choices=("exact", "network", "none"), default="exact")
        p.add_argument("--preheader", choices=("source", "profile"), default="source")
        p.add_argument("--rotulo", default=None)

    p = sub.add_parser("exportar", help="gera um lote de CSVs sem enviar")
    _add_selection(p)
    p.set_defaults(func=cmd_exportar)

    p = sub.add_parser("enviar", help="gera (ou reaproveita) um lote e envia")
    _add_selection(p)
    p.add_argument("--export", type=int, help="reenvia um lote já existente")
    p.add_argument("--simular", action="store_true", default=True, help="não envia (padrão)")
    p.add_argument("--real", dest="simular", action="store_false", help="envia de verdade")
    p.set_defaults(func=cmd_enviar)

    p = sub.add_parser("validar", help="testa a credencial de um destino")
    p.add_argument("--destino", choices=TARGETS, required=True)
    p.set_defaults(func=cmd_validar)

    p = sub.add_parser("lotes", help="lista os lotes exportados")
    p.add_argument("--limite", type=int, default=20)
    p.set_defaults(func=cmd_exportacoes)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config.ensure_dirs()
    db.init_db()          # cria/atualiza o banco antes de qualquer comando
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
