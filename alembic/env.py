from logging.config import fileConfig

from alembic import context
from sqlalchemy import URL, engine_from_config, pool

from app.backend.models import document  # noqa: F401
from app.backend.models import product  # noqa: F401
from app.backend.models import product_embedding  # noqa: F401
from app.backend.models import user  # noqa: F401
from app.core.config import settings
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings.database_path.parent.mkdir(parents=True, exist_ok=True)
database_url = URL.create(
    "sqlite",
    database=str(settings.database_path),
)
config.set_main_option(
    "sqlalchemy.url",
    database_url.render_as_string().replace("%", "%%"),
)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
