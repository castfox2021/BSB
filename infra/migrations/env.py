from alembic import context

connection = context.config.attributes.get('connection')
if connection is None:
    raise RuntimeError('Use python -m bsb_service.pipeline.migrate with DATABASE_URL')
context.configure(connection=connection, target_metadata=None)
with context.begin_transaction():
    context.run_migrations()
