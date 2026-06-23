import json
import os
import sys
from pathlib import Path

from flask import Flask, jsonify, request, send_file
from flask_sock import Sock
from jose import JWTError, jwt
from fastapi import HTTPException as FastAPIHTTPException

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import main as main_backend

STATIC_DIR = ROOT / 'backend' / 'static'
STATIC_DIR.mkdir(exist_ok=True)


class ConnectionManager:
    def __init__(self):
        self.active_connections = []

    def connect(self, websocket):
        self.active_connections.append(websocket)

    def disconnect(self, websocket):
        try:
            self.active_connections.remove(websocket)
        except ValueError:
            pass

    def broadcast_json(self, payload):
        for conn in list(self.active_connections):
            try:
                conn.send(json.dumps(payload))
            except Exception:
                self.disconnect(conn)


manager = ConnectionManager()


def create_app():
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path='')
    sock = Sock(app)

    @app.errorhandler(FastAPIHTTPException)
    def handle_http_exception(exc):
        return jsonify({'detail': exc.detail}), exc.status_code

    def to_jsonable(obj):
        if obj is None:
            return None
        if isinstance(obj, (dict, list, str, int, float, bool)):
            return obj
        if hasattr(obj, 'model_dump'):
            return obj.model_dump()
        if hasattr(obj, 'dict'):
            return obj.dict()
        return obj

    def json_response(payload):
        return jsonify(to_jsonable(payload))

    def require_jwt(authorization=None):
        if not authorization:
            raise FastAPIHTTPException(status_code=401, detail='missing authorization header')
        if authorization.startswith('Bearer '):
            token = authorization.split(' ', 1)[1]
        else:
            token = authorization
        try:
            payload = jwt.decode(token, main_backend.SECRET_KEY, algorithms=[main_backend.ALGORITHM])
            return payload.get('sub')
        except JWTError:
            raise FastAPIHTTPException(status_code=401, detail='invalid token')

    @app.route('/')
    def index():
        return send_file(STATIC_DIR / 'index.html')

    @app.route('/<path:path>')
    def serve_static(path):
        full = STATIC_DIR / path
        if full.exists() and full.is_file():
            return send_file(full)
        return send_file(STATIC_DIR / 'index.html')

    @app.route('/api/transactions', methods=['GET'])
    def api_list_transactions():
        username = require_jwt(request.headers.get('Authorization'))
        status = request.args.get('status')
        show_hidden = request.args.get('show_hidden', 'false').lower() in {'1', 'true', 'yes'}
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 25))
        sort_by = request.args.get('sort_by', 'created_at')
        sort_dir = request.args.get('sort_dir', 'DESC')
        q = request.args.get('q')
        txs = main_backend.fetch_transactions(
            status_filter=status,
            show_hidden=show_hidden,
            page=page,
            per_page=per_page,
            sort_by=sort_by,
            sort_dir=sort_dir,
            q=q,
        )
        return json_response([to_jsonable(tx) for tx in txs])

    @app.route('/api/login', methods=['POST'])
    def api_login():
        payload = request.get_json(silent=True) or {}
        return json_response(main_backend.api_login(payload))

    @app.route('/api/transaction/<int:tx_id>', methods=['GET'])
    def api_get_transaction(tx_id):
        username = require_jwt(request.headers.get('Authorization'))
        return json_response(main_backend.api_get_transaction(tx_id, username))

    @app.route('/api/transaction/<int:tx_id>/complete', methods=['POST'])
    def api_complete_transaction(tx_id):
        username = require_jwt(request.headers.get('Authorization'))
        result = main_backend.api_complete_transaction(tx_id, username)
        manager.broadcast_json({'type': 'update', 'tx_id': tx_id, 'status': 'COMPLETED'})
        return json_response(result)

    @app.route('/api/transaction/<int:tx_id>/reject', methods=['POST'])
    def api_reject_transaction(tx_id):
        username = require_jwt(request.headers.get('Authorization'))
        result = main_backend.api_reject_transaction(tx_id, username)
        manager.broadcast_json({'type': 'update', 'tx_id': tx_id, 'status': 'REJECTED'})
        return json_response(result)

    @app.route('/api/transaction/<int:tx_id>/send_payment_info', methods=['POST'])
    def api_send_payment_info(tx_id):
        username = require_jwt(request.headers.get('Authorization'))
        payload = request.get_json(silent=True) or {}
        result = main_backend.api_send_payment_info(tx_id, main_backend.PaymentPayload(**payload), username)
        return json_response(result)

    @app.route('/api/transaction/<int:tx_id>/hide', methods=['POST'])
    def api_hide_transaction(tx_id):
        try:
            username = require_jwt(request.headers.get('Authorization'))
            result = main_backend.api_hide_transaction(tx_id, username)
            manager.broadcast_json({'type': 'update', 'tx_id': tx_id, 'hidden': 1})
            return json_response(result)
        except Exception as exc:
            app.logger.exception('hide transaction failed for %s', tx_id)
            return jsonify({'error': str(exc)}), 500

    @app.route('/api/transaction/<int:tx_id>/send_proof', methods=['POST'])
    def api_send_proof(tx_id):
        try:
            username = require_jwt(request.headers.get('Authorization'))
            result = main_backend.api_send_proof(tx_id, username)
            manager.broadcast_json({'type': 'update', 'tx_id': tx_id, 'status': 'COMPLETED'})
            return json_response(result)
        except Exception as exc:
            app.logger.exception('send_proof failed for %s', tx_id)
            return jsonify({'error': str(exc)}), 500

    @app.route('/proofs/<path:filename>')
    def get_proof_file(filename):
        username = require_jwt(request.headers.get('Authorization'))
        path = main_backend.PROOF_DIR / filename
        if not path.exists():
            raise FastAPIHTTPException(status_code=404, detail='File not found')
        return send_file(path)

    @app.route('/api/bot/status', methods=['GET'])
    def api_bot_status():
        username = require_jwt(request.headers.get('Authorization'))
        return json_response(main_backend.api_bot_status(username))

    @app.route('/api/bot/start', methods=['POST'])
    def api_bot_start():
        username = require_jwt(request.headers.get('Authorization'))
        return json_response(main_backend.api_bot_start(username))

    @app.route('/api/settings', methods=['GET'])
    def api_get_settings():
        username = require_jwt(request.headers.get('Authorization'))
        return json_response(main_backend.api_get_settings(username))

    @app.route('/api/settings', methods=['POST'])
    def api_set_setting():
        username = require_jwt(request.headers.get('Authorization'))
        payload = request.get_json(silent=True) or {}
        result = main_backend.api_set_setting(main_backend.SettingsPayload(**payload), username)
        return json_response(result)

    @app.route('/api/admin/messages', methods=['GET'])
    def api_get_admin_messages():
        username = require_jwt(request.headers.get('Authorization'))
        limit = int(request.args.get('limit', 100))
        return json_response(main_backend.api_get_admin_messages(limit, username))

    @app.route('/api/admin/message', methods=['POST'])
    def api_post_admin_message():
        payload = request.get_json(silent=True) or {}
        result = main_backend.api_post_admin_message(main_backend.AdminIncoming(**payload))
        manager.broadcast_json({'type': 'admin_message'})
        return json_response(result)

    @app.route('/api/admin/send', methods=['POST'])
    def api_admin_send():
        username = require_jwt(request.headers.get('Authorization'))
        payload = request.get_json(silent=True) or {}
        result = main_backend.api_admin_send(main_backend.AdminReply(**payload), username)
        manager.broadcast_json({'type': 'admin_message'})
        return json_response(result)

    @app.route('/api/reports', methods=['GET'])
    def api_reports():
        username = require_jwt(request.headers.get('Authorization'))
        period = request.args.get('period', 'month')
        compare = int(request.args.get('compare', 0))
        return json_response(main_backend.api_reports(period=period, compare=compare, username=username))

    @sock.route('/ws/transactions')
    def websocket_transactions(ws):
        token = request.args.get('token')
        if not token:
            ws.close()
            return
        try:
            jwt.decode(token, main_backend.SECRET_KEY, algorithms=[main_backend.ALGORITHM])
        except JWTError:
            ws.close()
            return
        manager.connect(ws)
        try:
            while True:
                ws.receive()
        except Exception:
            manager.disconnect(ws)

    return app


app = create_app()


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8000, debug=False)
