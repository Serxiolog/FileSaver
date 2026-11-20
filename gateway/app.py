import os
import uuid
import secrets
import hmac
from pathlib import Path
import httpx
import jwt
from flask import Flask, request, Response, jsonify, g, session, redirect, render_template, url_for
from werkzeug.exceptions import HTTPException

def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ['FLASK_SECRET_KEY']
    secure = os.environ.get('COOKIE_SECURE', 'true').lower() == 'true'
    app.config.update(MAX_CONTENT_LENGTH=1048576+65536, SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=secure, SESSION_COOKIE_SAMESITE='Lax')
    targets = {name: os.environ.get(name.upper()+'_URL', 'http://'+name+'-api:8000') for name in ['auth','upload','download','reports']}

    def fail(status, code, message):
        return jsonify(error=dict(code=code, message=message, request_id=g.request_id)), status

    @app.before_request
    def context():
        g.request_id = str(uuid.uuid4())
        session.setdefault('csrf', secrets.token_urlsafe(32))
        g.token = None
        header, cookie = request.headers.get('Authorization'), request.cookies.get('access_token')
        if header:
            if not header.lower().startswith('bearer '):
                return fail(401, 'AUTH_REQUIRED', 'Invalid authorization')
            g.token = header[7:]
            if cookie and not hmac.compare_digest(g.token, cookie):
                return fail(401, 'AUTH_REQUIRED', 'Conflicting credentials')
        else:
            g.token = cookie
        # Login and registration HTML forms are also protected against login CSRF.
        csrf_required = request.method in {'POST','PATCH','DELETE','PUT'} and (request.path.startswith('/app/') or (cookie and not header))
        if csrf_required:
            supplied = request.headers.get('X-CSRF-Token') or request.form.get('csrf', '')
            if not hmac.compare_digest(supplied, session['csrf']):
                return fail(400, 'INVALID_REQUEST', 'Invalid CSRF token')
        g.user_id = None
        if g.token:
            try:
                claims = jwt.decode(g.token, Path(os.environ['JWT_PUBLIC_KEY_FILE']).read_text(), algorithms=['RS256'],
                    issuer=os.environ['JWT_ISSUER'], audience=os.environ['JWT_AUDIENCE'],
                    options={'require':['sub','iat','exp','iss','aud']})
                g.user_id = str(uuid.UUID(claims['sub']))
            except (jwt.PyJWTError, ValueError):
                if not header and request.path in {'/app/login','/app/register','/app/logout'}:
                    g.token = None
                    g.clear_token = True
                    return None
                return fail(401, 'AUTH_REQUIRED', 'A valid access token is required')

    @app.after_request
    def headers(response):
        if getattr(g, 'clear_token', False) and not (request.path=='/app/login' and response.status_code==302):
            response.delete_cookie('access_token',path='/')
        response.headers['X-Request-ID'] = g.request_id
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        return response

    @app.errorhandler(HTTPException)
    def http_error(error):
        return fail(error.code, 'FILE_TOO_LARGE' if error.code==413 else 'INVALID_REQUEST', error.description)

    def proxy(service, path, method=None, payload=None, streaming=False):
        headers = {'X-Request-ID':g.request_id}
        if g.token:
            headers['Authorization'] = 'Bearer '+g.token
        client = httpx.Client(timeout=httpx.Timeout(30, connect=5), follow_redirects=False)
        kwargs = dict(headers=headers, params=request.args)
        if payload is not None:
            kwargs['json'] = payload
        elif request.mimetype == 'multipart/form-data':
            file = request.files.get('file')
            if not file:
                client.close()
                return fail(422, 'VALIDATION_ERROR', 'File is required')
            kwargs['files'] = {'file':(file.filename, file.stream, file.mimetype)}
        elif request.is_json:
            kwargs['json'] = request.get_json()
        try:
            upstream_request = client.build_request(method or request.method, targets[service]+path, **kwargs)
            upstream = client.send(upstream_request, stream=streaming)
        except httpx.HTTPError:
            client.close()
            return fail(503, 'SERVICE_UNAVAILABLE', 'Requested service is unavailable')
        kept = {k:v for k,v in upstream.headers.items() if k.lower() in {'content-type','content-disposition','content-length'}}
        if streaming:
            def chunks():
                try:
                    yield from upstream.iter_raw()
                finally:
                    upstream.close()
                    client.close()
            response = Response(chunks(), status=upstream.status_code, headers=kept)
            response.call_on_close(upstream.close)
            response.call_on_close(client.close)
            return response
        try:
            return Response(upstream.content, status=upstream.status_code, headers=kept)
        finally:
            upstream.close()
            client.close()

    @app.route('/api/auth/<operation>', methods=['POST'])
    def auth(operation):
        if operation not in {'login','register'}:
            return fail(404, 'INVALID_REQUEST', 'Unknown route')
        return proxy('auth', '/api/auth/'+operation)

    @app.route('/api/users/me', methods=['GET','PATCH'])
    def profile():
        if not g.user_id:
            return fail(401,'AUTH_REQUIRED','Login required')
        return proxy('auth','/api/users/me')

    @app.route('/api/files', methods=['GET','POST'])
    def files():
        if request.method=='POST' and not g.user_id:
            return fail(401,'AUTH_REQUIRED','Login required')
        return proxy('reports' if request.method=='GET' else 'upload','/api/files')

    @app.route('/api/files/<uuid:file_id>', methods=['GET','DELETE'])
    def file(file_id):
        if request.method=='DELETE' and not g.user_id:
            return fail(401,'AUTH_REQUIRED','Login required')
        return proxy('reports' if request.method=='GET' else 'upload',f'/api/files/{file_id}')

    @app.get('/api/files/<uuid:file_id>/download')
    def download(file_id):
        return proxy('download',f'/api/files/{file_id}/download',streaming=True)

    @app.get('/api/files/<uuid:file_id>/stats')
    def stats(file_id):
        if not g.user_id:
            return fail(401,'AUTH_REQUIRED','Login required')
        return proxy('reports',f'/api/files/{file_id}/stats')

    @app.get('/')
    def root():
        return redirect('/app/')

    @app.get('/health/live')
    def health():
        return {'status':'ok'}

    @app.route('/app/', methods=['GET'])
    def home():
        response = proxy('reports','/api/files',method='GET')
        if isinstance(response, tuple):
            return render_template('page.html', error='Каталог временно недоступен'), 503
        if response.status_code != 200:
            return render_template('page.html',error='Каталог временно недоступен'), response.status_code
        return render_template('page.html',catalog=response.get_json())

    @app.route('/app/<action>', methods=['GET','POST'])
    def form(action):
        if action not in {'login','register','profile','upload','logout'}:
            return fail(404,'INVALID_REQUEST','Unknown page')
        if action=='logout' and request.method=='POST':
            response = redirect('/app/')
            response.delete_cookie('access_token',path='/')
            session.clear()
            return response
        if action in {'profile','upload'} and not g.user_id:
            return redirect('/app/login')
        error = None
        status = 200
        if request.method=='POST':
            fields = {k:v for k,v in request.form.items() if k!='csrf'}
            target, path, method = {
                'login':('auth','/api/auth/login','POST'), 'register':('auth','/api/auth/register','POST'),
                'profile':('auth','/api/users/me','PATCH'), 'upload':('upload','/api/files','POST')}.get(action, ('auth','','POST'))
            result = proxy(target,path,method=method,payload=None if action=='upload' else fields)
            if isinstance(result, Response) and result.status_code < 300:
                response = redirect('/app/login' if action=='register' else '/app/')
                if action=='login':
                    response.set_cookie('access_token',result.get_json()['access_token'],max_age=3600,httponly=True,
                        secure=secure,samesite='Lax',path='/')
                return response
            failed, status = result if isinstance(result, tuple) else (result, result.status_code)
            payload = failed.get_json(silent=True)
            problem = payload.get('error', {}) if isinstance(payload, dict) else {}
            code = problem.get('code') if isinstance(problem, dict) else None
            explanations = {
                'LOGIN_ALREADY_EXISTS':'Этот логин уже занят. Выберите другой или войдите в существующий аккаунт.',
                'INVALID_CREDENTIALS':'Неверный логин или пароль.',
                'AUTH_REQUIRED':'Срок входа истёк. Войдите повторно.',
                'SERVICE_UNAVAILABLE':'Сервис временно недоступен. Попробуйте ещё раз немного позже.',
                'FILE_TOO_LARGE':'Файл слишком большой. Максимальный размер — 1 МиБ (1 048 576 байт).',
                'FILE_ACCESS_DENIED':'Это действие доступно только владельцу файла.',
            }
            if code == 'VALIDATION_ERROR':
                message = problem.get('message')
                error = message if isinstance(message, str) and message != 'Invalid request fields' else 'Проверьте заполнение полей формы.'
            else:
                error = explanations.get(code, 'Не удалось выполнить действие. Попробуйте ещё раз.')
        return render_template('page.html',action=action,error=error), status

    @app.route('/app/files/<uuid:file_id>/<action>', methods=['GET','POST'])
    def file_action(file_id, action):
        if action not in {'card','stats','delete'}:
            return fail(404,'INVALID_REQUEST','Unknown page')
        if action=='delete' and request.method!='POST':
            return fail(405,'INVALID_REQUEST','POST required')
        if action in {'stats','delete'} and not g.user_id:
            return redirect('/app/login')
        result = proxy('upload' if action=='delete' else 'reports', f'/api/files/{file_id}'+('/stats' if action=='stats' else ''),
            method='DELETE' if action=='delete' else 'GET')
        if isinstance(result, tuple):
            return render_template('page.html',error='Сервис временно недоступен'),503
        return render_template('page.html',detail=result.get_json(),action=action), result.status_code

    return app
