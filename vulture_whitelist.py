"""Vulture whitelist: names reported as unused but genuinely required.

The names below either implement the openbrokerapi ServiceBroker interface
(called by its Flask blueprint, not by our own code) or are attributes we set
on driver/library objects. Vulture cannot see those callers, so they are
listed here. Run:

    uvx vulture src tests vulture_whitelist.py

This file is never imported; it is only parsed by vulture.
"""

# ServiceBroker interface parameters the framework passes but this broker ignores.
async_allowed
kwargs
operation_data

# Methods invoked by the openbrokerapi blueprint.
catalog
last_operation
last_binding_operation

# Attributes on psycopg2/sqlite connections and openbrokerapi settings.
autocommit
row_factory
DISABLE_SPACE_ORG_GUID_CHECK

# Flask route registered via the @app.get decorator; called by the web server.
healthz
