"""Ready dev-stack templates for modern application frameworks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dev_stack_compose import NETWORK_EXTERNAL, _slugify
from dev_stack_routes import http_container_name
from dev_stack_templates import (
    _base_compose,
    _mysql_db,
    _postgres_db,
    _write_stack,
)

_APP = "app"
_APP_VOL = "app_code"


def _app_container(
    internal_net: str,
    stack_id: str,
    spec: dict[str, Any],
    *,
    depends: dict[str, Any] | list[str] | None = None,
) -> dict[str, Any]:
    out = dict(spec)
    out["container_name"] = http_container_name(stack_id, "app")
    out["restart"] = "unless-stopped"
    out["networks"] = [internal_net, NETWORK_EXTERNAL]
    if depends is not None:
        out["depends_on"] = depends
    return out


def _composer_init(
    internal_net: str,
    *,
    create_cmd: str,
    marker: str,
    mount: str = "/var/www/html",
    depends: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cmd = (
        "set -e; "
        f"if [ ! -f {marker} ]; then {create_cmd}; fi"
    )
    return {
        "framework-init": {
            "image": "composer:2",
            "depends_on": depends or ["db"],
            "volumes": [f"{_APP_VOL}:{mount}"],
            "working_dir": mount,
            "networks": [internal_net],
            "restart": "no",
            "entrypoint": ["/bin/sh", "-c"],
            "command": [cmd],
        }
    }


def _template_yii2(sid: str, name: str, *, sample_data: bool) -> tuple[Path, dict[str, Any]]:
    internal_net = f"leco-devstack-{sid}-internal"
    password = "localdev"
    db_name = "yii2"
    services: dict[str, Any] = {
        "db": _mysql_db(internal_net, db_name=db_name, password=password),
    }
    services.update(
        _composer_init(
            internal_net,
            create_cmd="composer create-project --prefer-dist yiisoft/yii2-app-basic .",
            marker="/app/composer.json",
            depends={"db": {"condition": "service_healthy"}},
        )
    )
    services[_APP] = _app_container(
        internal_net,
        sid,
        {
            "image": "yiisoftware/yii2-php:8.2-apache",
            "volumes": [f"{_APP_VOL}:/app"],
            "depends_on": {"framework-init": {"condition": "service_completed_successfully"}},
        },
    )
    compose = _base_compose(sid, services, {"db_data": {}, _APP_VOL: {}})
    meta = {
        "template": "yii2",
        "sample_data": sample_data,
        "framework": "Yii2",
        "components": [
            {"id": "mysql", "version": "8.0"},
            {"id": "php", "version": "8.2"},
        ],
    }
    return _write_stack(sid, name, compose, meta)


def _template_cakephp(sid: str, name: str, *, sample_data: bool) -> tuple[Path, dict[str, Any]]:
    internal_net = f"leco-devstack-{sid}-internal"
    password = "localdev"
    db_name = "cakephp"
    dsn = f"mysql://app:{password}@db:3306/{db_name}"
    services: dict[str, Any] = {
        "db": _mysql_db(internal_net, db_name=db_name, password=password),
    }
    services.update(
        _composer_init(
            internal_net,
            create_cmd="composer create-project --prefer-dist cakephp/app:~5.0 .",
            marker="/var/www/html/composer.json",
            depends={"db": {"condition": "service_healthy"}},
        )
    )
    services[_APP] = _app_container(
        internal_net,
        sid,
        {
            "image": "php:8.3-apache-bookworm",
            "volumes": [f"{_APP_VOL}:/var/www/html"],
            "environment": {
                "APACHE_DOCUMENT_ROOT": "/var/www/html/webroot",
                "DATABASE_URL": dsn,
                "SECURITY_SALT": "leco-local-dev-salt-change-me",
            },
            "depends_on": {"framework-init": {"condition": "service_completed_successfully"}},
        },
    )
    compose = _base_compose(sid, services, {"db_data": {}, _APP_VOL: {}})
    meta = {
        "template": "cakephp",
        "sample_data": sample_data,
        "framework": "CakePHP",
        "components": [
            {"id": "mysql", "version": "8.0"},
            {"id": "php", "version": "8.3"},
        ],
    }
    return _write_stack(sid, name, compose, meta)


def _template_symfony(sid: str, name: str, *, sample_data: bool) -> tuple[Path, dict[str, Any]]:
    internal_net = f"leco-devstack-{sid}-internal"
    password = "localdev"
    db_name = "symfony"
    services: dict[str, Any] = {
        "db": _mysql_db(internal_net, db_name=db_name, password=password),
    }
    services.update(
        _composer_init(
            internal_net,
            create_cmd="composer create-project --prefer-dist symfony/skeleton .",
            marker="/var/www/html/composer.json",
            depends={"db": {"condition": "service_healthy"}},
        )
    )
    services[_APP] = _app_container(
        internal_net,
        sid,
        {
            "image": "php:8.3-apache-bookworm",
            "volumes": [f"{_APP_VOL}:/var/www/html"],
            "environment": {
                "APACHE_DOCUMENT_ROOT": "/var/www/html/public",
                "DATABASE_URL": f"mysql://app:{password}@db:3306/{db_name}?serverVersion=8.0",
                "APP_ENV": "dev",
                "APP_SECRET": "leco-symfony-local-secret",
            },
            "depends_on": {"framework-init": {"condition": "service_completed_successfully"}},
        },
    )
    compose = _base_compose(sid, services, {"db_data": {}, _APP_VOL: {}})
    meta = {
        "template": "symfony",
        "sample_data": sample_data,
        "framework": "Symfony",
        "components": [
            {"id": "mysql", "version": "8.0"},
            {"id": "php", "version": "8.3"},
        ],
    }
    return _write_stack(sid, name, compose, meta)


def _template_laravel(sid: str, name: str, *, sample_data: bool) -> tuple[Path, dict[str, Any]]:
    internal_net = f"leco-devstack-{sid}-internal"
    password = "localdev"
    db_name = "laravel"
    services: dict[str, Any] = {
        "db": _mysql_db(internal_net, db_name=db_name, password=password),
    }
    services.update(
        _composer_init(
            internal_net,
            create_cmd="composer create-project --prefer-dist laravel/laravel .",
            marker="/var/www/html/composer.json",
            depends={"db": {"condition": "service_healthy"}},
        )
    )
    services[_APP] = _app_container(
        internal_net,
        sid,
        {
            "image": "php:8.3-apache-bookworm",
            "volumes": [f"{_APP_VOL}:/var/www/html"],
            "environment": {
                "APACHE_DOCUMENT_ROOT": "/var/www/html/public",
            },
            "depends_on": {"framework-init": {"condition": "service_completed_successfully"}},
        },
    )
    compose = _base_compose(sid, services, {"db_data": {}, _APP_VOL: {}})
    meta = {
        "template": "laravel",
        "sample_data": sample_data,
        "framework": "Laravel",
        "components": [
            {"id": "mysql", "version": "8.0"},
            {"id": "php", "version": "8.3"},
        ],
    }
    return _write_stack(sid, name, compose, meta)


def _template_django(sid: str, name: str, *, sample_data: bool) -> tuple[Path, dict[str, Any]]:
    internal_net = f"leco-devstack-{sid}-internal"
    password = "localdev"
    db_name = "django"
    bootstrap = (
        "set -e; "
        "pip install -q django psycopg2-binary; "
        "if [ ! -f manage.py ]; then "
        "django-admin startproject leco_site .; "
        "python - <<'PY'\n"
        "from pathlib import Path\n"
        "p = Path('leco_site/settings.py')\n"
        "text = p.read_text()\n"
        "old = \"'ENGINE': 'django.db.backends.sqlite3'\"\n"
        "new = \"'ENGINE': 'django.db.backends.postgresql',\\n        'HOST': 'db',\\n        'USER': 'postgres',\\n        'PASSWORD': 'localdev',\\n        'NAME': 'django'\"\n"
        "if old in text:\n"
        "    text = text.replace(old, new, 1)\n"
        "    p.write_text(text)\n"
        "PY\n"
        "fi; "
        "python manage.py migrate --noinput; "
        "python manage.py runserver 0.0.0.0:8000"
    )
    services: dict[str, Any] = {
        "db": _postgres_db(internal_net, db_name=db_name, password=password),
        _APP: _app_container(
            internal_net,
            sid,
            {
                "image": "python:3.12-bookworm",
                "working_dir": "/app",
                "volumes": [f"{_APP_VOL}:/app"],
                "environment": {
                    "DJANGO_SETTINGS_MODULE": "leco_site.settings",
                    "PYTHONUNBUFFERED": "1",
                },
                "entrypoint": ["/bin/bash", "-c"],
                "command": [bootstrap],
            },
            depends={"db": {"condition": "service_started"}},
        ),
    }
    compose = _base_compose(sid, services, {"db_data": {}, _APP_VOL: {}})
    meta = {
        "template": "django",
        "sample_data": sample_data,
        "framework": "Django",
        "components": [
            {"id": "postgres", "version": "16"},
            {"id": "python", "version": "3.12"},
        ],
    }
    return _write_stack(sid, name, compose, meta)


def _template_rails(sid: str, name: str, *, sample_data: bool) -> tuple[Path, dict[str, Any]]:
    internal_net = f"leco-devstack-{sid}-internal"
    password = "localdev"
    db_name = "rails"
    bootstrap = (
        "set -e; "
        "apt-get update -qq && apt-get install -y -qq build-essential libpq-dev nodejs npm >/dev/null; "
        "gem install rails bundler; "
        "if [ ! -f Gemfile ]; then "
        "rails new . --database=postgresql --skip-bundle --force; "
        "fi; "
        "bundle config set path vendor/bundle; "
        "bundle install; "
        "export DATABASE_URL=postgresql://postgres:localdev@db:5432/rails; "
        "bundle exec rails db:prepare; "
        "bundle exec rails server -b 0.0.0.0 -p 3000"
    )
    services: dict[str, Any] = {
        "db": _postgres_db(internal_net, db_name=db_name, password=password),
        _APP: _app_container(
            internal_net,
            sid,
            {
                "image": "ruby:3.3-bookworm",
                "working_dir": "/app",
                "volumes": [f"{_APP_VOL}:/app"],
                "environment": {
                    "RAILS_ENV": "development",
                    "DATABASE_URL": "postgresql://postgres:localdev@db:5432/rails",
                },
                "depends_on": {"db": {"condition": "service_started"}},
                "entrypoint": ["/bin/bash", "-c"],
                "command": [bootstrap],
            },
        ),
    }
    compose = _base_compose(sid, services, {"db_data": {}, _APP_VOL: {}})
    meta = {
        "template": "rails",
        "sample_data": sample_data,
        "framework": "Ruby on Rails",
        "components": [
            {"id": "postgres", "version": "16"},
            {"id": "ruby", "version": "3.3"},
        ],
    }
    return _write_stack(sid, name, compose, meta)


def _template_nestjs(sid: str, name: str, *, sample_data: bool) -> tuple[Path, dict[str, Any]]:
    internal_net = f"leco-devstack-{sid}-internal"
    bootstrap = (
        "set -e; "
        "if [ ! -f package.json ]; then "
        "npm init -y; "
        "npm install @nestjs/common @nestjs/core @nestjs/platform-express reflect-metadata rxjs; "
        "npm install -D @nestjs/cli typescript @types/node; "
        "npx nest new leco --skip-git --package-manager npm --strict || true; "
        "if [ -d leco ]; then cp -a leco/. . && rm -rf leco; fi; "
        "fi; "
        "npm install; "
        "npm run start"
    )
    services: dict[str, Any] = {
        _APP: _app_container(
            internal_net,
            sid,
            {
                "image": "node:20-bookworm",
                "working_dir": "/app",
                "volumes": [f"{_APP_VOL}:/app"],
                "environment": {"NODE_ENV": "development"},
                "entrypoint": ["/bin/bash", "-c"],
                "command": [bootstrap],
            },
        ),
    }
    compose = _base_compose(sid, services, {_APP_VOL: {}})
    meta = {
        "template": "nestjs",
        "sample_data": sample_data,
        "framework": "NestJS",
        "components": [{"id": "node", "version": "20"}],
    }
    return _write_stack(sid, name, compose, meta)


def _template_fastapi(sid: str, name: str, *, sample_data: bool) -> tuple[Path, dict[str, Any]]:
    internal_net = f"leco-devstack-{sid}-internal"
    bootstrap = (
        "set -e; "
        "pip install -q fastapi uvicorn[standard]; "
        "if [ ! -f main.py ]; then "
        "cat > main.py <<'PY'\n"
        "from fastapi import FastAPI\n"
        "app = FastAPI(title='LEco FastAPI')\n"
        "@app.get('/')\n"
        "def root():\n"
        "    return {'ok': True, 'service': 'fastapi'}\n"
        "@app.get('/health')\n"
        "def health():\n"
        "    return {'status': 'ok'}\n"
        "PY\n"
        "fi; "
        "uvicorn main:app --host 0.0.0.0 --port 8000"
    )
    services: dict[str, Any] = {
        _APP: _app_container(
            internal_net,
            sid,
            {
                "image": "python:3.12-bookworm",
                "working_dir": "/app",
                "volumes": [f"{_APP_VOL}:/app"],
                "entrypoint": ["/bin/bash", "-c"],
                "command": [bootstrap],
            },
        ),
    }
    compose = _base_compose(sid, services, {_APP_VOL: {}})
    meta = {
        "template": "fastapi",
        "sample_data": sample_data,
        "framework": "FastAPI",
        "components": [{"id": "python", "version": "3.12"}],
    }
    return _write_stack(sid, name, compose, meta)


def _template_express(sid: str, name: str, *, sample_data: bool) -> tuple[Path, dict[str, Any]]:
    internal_net = f"leco-devstack-{sid}-internal"
    bootstrap = (
        "set -e; "
        "if [ ! -f package.json ]; then "
        "npm init -y; "
        "npm install express; "
        "cat > index.js <<'JS'\n"
        "const express = require('express');\n"
        "const app = express();\n"
        "app.get('/', (req, res) => res.json({ ok: true, service: 'express' }));\n"
        "app.get('/health', (req, res) => res.json({ status: 'ok' }));\n"
        "app.listen(3000, () => console.log('Express on :3000'));\n"
        "JS\n"
        "fi; "
        "node index.js"
    )
    services: dict[str, Any] = {
        _APP: _app_container(
            internal_net,
            sid,
            {
                "image": "node:20-bookworm",
                "working_dir": "/app",
                "volumes": [f"{_APP_VOL}:/app"],
                "entrypoint": ["/bin/bash", "-c"],
                "command": [bootstrap],
            },
        ),
    }
    compose = _base_compose(sid, services, {_APP_VOL: {}})
    meta = {
        "template": "express",
        "sample_data": sample_data,
        "framework": "Express",
        "components": [{"id": "node", "version": "20"}],
    }
    return _write_stack(sid, name, compose, meta)


def _template_flask(sid: str, name: str, *, sample_data: bool) -> tuple[Path, dict[str, Any]]:
    internal_net = f"leco-devstack-{sid}-internal"
    bootstrap = (
        "set -e; "
        "pip install -q flask; "
        "if [ ! -f app.py ]; then "
        "cat > app.py <<'PY'\n"
        "from flask import Flask, jsonify\n"
        "app = Flask(__name__)\n"
        "@app.get('/')\n"
        "def root():\n"
        "    return jsonify(ok=True, service='flask')\n"
        "@app.get('/health')\n"
        "def health():\n"
        "    return jsonify(status='ok')\n"
        "PY\n"
        "fi; "
        "flask --app app run --host 0.0.0.0 --port 5000"
    )
    services: dict[str, Any] = {
        _APP: _app_container(
            internal_net,
            sid,
            {
                "image": "python:3.12-bookworm",
                "working_dir": "/app",
                "volumes": [f"{_APP_VOL}:/app"],
                "environment": {"FLASK_ENV": "development"},
                "entrypoint": ["/bin/bash", "-c"],
                "command": [bootstrap],
            },
        ),
    }
    compose = _base_compose(sid, services, {_APP_VOL: {}})
    meta = {
        "template": "flask",
        "sample_data": sample_data,
        "framework": "Flask",
        "components": [{"id": "python", "version": "3.12"}],
    }
    return _write_stack(sid, name, compose, meta)


FRAMEWORK_TEMPLATES: dict[str, Any] = {
    "yii2": _template_yii2,
    "cakephp": _template_cakephp,
    "symfony": _template_symfony,
    "laravel": _template_laravel,
    "django": _template_django,
    "rails": _template_rails,
    "nestjs": _template_nestjs,
    "fastapi": _template_fastapi,
    "express": _template_express,
    "flask": _template_flask,
}

FRAMEWORK_TEMPLATE_IDS: frozenset[str] = frozenset(FRAMEWORK_TEMPLATES.keys())
