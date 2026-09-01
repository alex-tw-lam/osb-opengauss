"""Wiring only: builds the Flask app and starts the dev server.

This file is the only place that knows Flask.  Every request is answered by
openbrokerapi's blueprint calling GaussDbBroker; nothing else happens here.

Production: uv run --with gunicorn gunicorn -w 2 -b 0.0.0.0:5000 'osb_opengauss.app:create_app()'
Development: uv run osb-opengauss
"""

from __future__ import annotations

import logging

import openbrokerapi.settings
from flask import Flask
from openbrokerapi import api

from .broker import GaussDbBroker
from .config import DEV_BROKER_PASSWORD, Settings
from .gaussdb import GaussDBAdmin
from .state import StateStore


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings.from_env()

    # Platform behaviour: Cloud Foundry sends org/space GUIDs on OSB
    # requests, Kubernetes Service Catalog does not (see DISABLE_SPACE_ORG_GUID_CHECK).
    openbrokerapi.settings.DISABLE_SPACE_ORG_GUID_CHECK = settings.disable_space_org_guid_check

    if settings.broker_password == DEV_BROKER_PASSWORD:
        logging.getLogger(__name__).warning(
            "BROKER_PASSWORD is the built-in development default; set a real one."
        )

    app = Flask(__name__)
    app.register_blueprint(
        api.get_blueprint(
            GaussDbBroker(GaussDBAdmin(settings), StateStore(settings.state_db_path), settings),
            api.BrokerCredentials(settings.broker_username, settings.broker_password),
            app.logger,
        )
    )
    return app


def main() -> None:
    settings = Settings.from_env()
    create_app(settings).run(host=settings.host, port=settings.port, debug=False)


if __name__ == "__main__":
    main()
