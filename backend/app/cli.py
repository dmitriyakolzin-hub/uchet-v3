"""Служебные команды. Запуск на сервере:

    docker compose exec app python -m app.cli create-admin <логин>
    docker compose exec app python -m app.cli set-password <логин>
"""

import argparse
import getpass
import sys

from sqlalchemy import select

from app.core.security import MIN_PASSWORD_LENGTH, hash_password
from app.db.session import get_sessionmaker
from app.models import User


def _ask_password() -> str:
    while True:
        password = getpass.getpass("Пароль: ")
        if len(password) < MIN_PASSWORD_LENGTH:
            print(f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов.")
            continue
        if getpass.getpass("Повторите пароль: ") != password:
            print("Пароли не совпадают, попробуйте ещё раз.")
            continue
        return password


def create_admin(login: str, full_name: str) -> int:
    login = login.strip().lower()
    with get_sessionmaker()() as db:
        if db.scalar(select(User).where(User.login == login)):
            print(f"Пользователь «{login}» уже существует.")
            return 1
        password = _ask_password()
        db.add(
            User(
                login=login,
                password_hash=hash_password(password),
                full_name=full_name,
                is_admin=True,
            )
        )
        db.commit()
    print(f"Администратор «{login}» создан.")
    return 0


def set_password(login: str) -> int:
    login = login.strip().lower()
    with get_sessionmaker()() as db:
        user = db.scalar(select(User).where(User.login == login))
        if user is None:
            print(f"Пользователь «{login}» не найден.")
            return 1
        user.password_hash = hash_password(_ask_password())
        user.token_version += 1  # старые входы перестают действовать
        db.commit()
    print(f"Пароль пользователя «{login}» изменён, все его прежние входы завершены.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    p = commands.add_parser("create-admin", help="создать администратора")
    p.add_argument("login")
    p.add_argument("--name", default="Администратор", help="ФИО для отображения")

    p = commands.add_parser("set-password", help="сменить пароль пользователя")
    p.add_argument("login")

    args = parser.parse_args(argv)
    if args.command == "create-admin":
        return create_admin(args.login, args.name)
    return set_password(args.login)


if __name__ == "__main__":
    sys.exit(main())
