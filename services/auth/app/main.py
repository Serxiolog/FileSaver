from .common import *
from .events import validate_event
from pydantic import BaseModel, ConfigDict, Field
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

hasher = PasswordHasher()
DUMMY_HASH = hasher.hash('dummy-password-never-used-for-login')

class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer)

class Register(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)
    login: str = Field(min_length=3, max_length=64, pattern=r'^[a-zA-Z0-9_.-]+$')
    password: str = Field(min_length=10, max_length=256)
    display_name: str = Field(min_length=1, max_length=120, pattern=r'.*\S.*')

class Login(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)

class Rename(BaseModel):
    model_config = ConfigDict(extra='forbid')
    display_name: str = Field(min_length=1, max_length=120, pattern=r'.*\S.*')

def profile(user, updated=False):
    result = dict(id=user.id, login=user.login, display_name=user.display_name)
    result['updated_at' if updated else 'created_at'] = iso(user.updated_at if updated else user.created_at)
    return result

@app.post('/api/auth/register', status_code=201)
async def register(body: Register, request: Request):
    password_hash = await run_in_threadpool(hasher.hash, body.password)
    user = User(id=uid(), login=body.login.lower(), password_hash=password_hash,
                display_name=body.display_name.strip(), created_at=now(), updated_at=now(), version=1)
    try:
        async with Session.begin() as db:
            db.add(user)
            emit(db, 'user.registered', user.id, 1, dict(user_id=user.id, display_name=user.display_name), request.state.request_id)
    except IntegrityError:
        raise Problem(409, 'LOGIN_ALREADY_EXISTS', 'Login already exists')
    return profile(user)

@app.post('/api/auth/login')
async def login(body: Login):
    async with Session() as db:
        user = await db.scalar(select(User).where(User.login == body.login.lower()))
    try:
        await run_in_threadpool(hasher.verify, user.password_hash if user else DUMMY_HASH, body.password)
        if not user:
            raise VerifyMismatchError()
    except VerificationError:
        raise Problem(401, 'INVALID_CREDENTIALS', 'Invalid login or password')
    issued = int(now().timestamp())
    token = jwt.encode(dict(sub=user.id, iss=os.environ['JWT_ISSUER'], aud=os.environ['JWT_AUDIENCE'],
        iat=issued, exp=issued + 3600), Path(os.environ['JWT_PRIVATE_KEY_FILE']).read_text(), algorithm='RS256')
    return dict(access_token=token, token_type='bearer', expires_in=3600)

@app.get('/api/users/me')
async def me(request: Request):
    user_id = identity(request)
    async with Session() as db:
        user = await db.get(User, user_id)
        if not user:
            raise Problem(401, 'AUTH_REQUIRED', 'Unknown user')
        return {**profile(user), 'updated_at': iso(user.updated_at)}

@app.patch('/api/users/me')
async def rename(body: Rename, request: Request):
    user_id = identity(request)
    async with Session.begin() as db:
        user = await db.get(User, user_id, with_for_update=True)
        if not user:
            raise Problem(401, 'AUTH_REQUIRED', 'Unknown user')
        if user.display_name != body.display_name.strip():
            user.display_name = body.display_name.strip()
            user.updated_at = now()
            user.version += 1
            emit(db, 'user.renamed', user.id, user.version, dict(user_id=user.id, display_name=user.display_name), request.state.request_id)
        return profile(user, updated=True)
