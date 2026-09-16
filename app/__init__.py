"""
app 패키지 — create_app() 팩토리.

§3-8: errorhandler (403/404/500) + 로깅 초기화 추가.
"""

from markupsafe import Markup, escape
from flask import Flask, jsonify, render_template, request

from . import config
from .logging_config import configure_logging, get_logger


def _nl2br_filter(value):
    """§3-5 XSS 안전 nl2br. 자동 이스케이프 후 개행만 <br> 로 변환."""
    if value is None:
        return ""
    escaped = str(escape(value))
    return Markup(escaped.replace("\n", "<br>\n"))


def _is_api_request():
    return request.path.startswith("/api/")


def create_app():
    """Flask 애플리케이션 팩토리."""
    configure_logging()
    log = get_logger(__name__)

    application = Flask(__name__)
    application.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH
    application.config["SECRET_KEY"] = config.SECRET_KEY

    config.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    application.jinja_env.filters["nl2br"] = _nl2br_filter

    from .routes import main as routes_main
    from .routes import stream as routes_stream
    from .routes import inspect as routes_inspect
    from .routes import benchmark as routes_benchmark
    from .routes import auth as routes_auth
    from .routes import history as routes_history
    from .routes import spc as routes_spc
    from .routes import board as routes_board
    from .routes import admin as routes_admin
    from .routes import account as routes_account
    from .api import sequence as api_sequence
    from .api import spc as api_spc

    application.register_blueprint(routes_main.bp)
    application.register_blueprint(routes_stream.bp)
    application.register_blueprint(routes_inspect.bp)
    application.register_blueprint(routes_benchmark.bp)
    application.register_blueprint(routes_auth.bp)
    application.register_blueprint(routes_history.bp)
    application.register_blueprint(routes_spc.bp)
    application.register_blueprint(routes_board.bp)
    application.register_blueprint(routes_admin.bp)
    application.register_blueprint(routes_account.bp)
    application.register_blueprint(api_sequence.bp)
    application.register_blueprint(api_spc.bp)

    @application.context_processor
    def inject_current_user():
        from .services import auth as auth_service
        try:
            current_user = auth_service.get_current_user()
        except Exception:
            current_user = None
        return {"current_user": current_user}

    # §3-8 에러 핸들러. /api/* 는 JSON, 그 외는 HTML.
    @application.errorhandler(403)
    def error_403(err):
        if _is_api_request():
            return jsonify({"error": "forbidden"}), 403
        return render_template("errors/403.html"), 403

    @application.errorhandler(404)
    def error_404(err):
        if _is_api_request():
            return jsonify({"error": "not found"}), 404
        return render_template("errors/404.html"), 404

    @application.errorhandler(500)
    def error_500(err):
        # 스택은 로그에만. 응답에 노출 금지.
        log.exception("500 error path=%s method=%s", request.path, request.method)
        if _is_api_request():
            return jsonify({"error": "internal server error"}), 500
        return render_template("errors/500.html"), 500

    # 일반 Exception (500 이 아닌 예외로 잡히기 전) 도 500 처리
    @application.errorhandler(Exception)
    def error_exception(err):
        # HTTPException 은 각 코드별 핸들러가 있으므로 여기 안 옴 (Flask 가 알아서 라우팅)
        # 여기는 정말로 잡히지 않은 예외.
        log.exception("Unhandled exception path=%s method=%s: %s: %s",
                      request.path, request.method, type(err).__name__, err)
        if _is_api_request():
            return jsonify({"error": "internal server error"}), 500
        return render_template("errors/500.html"), 500

    return application
