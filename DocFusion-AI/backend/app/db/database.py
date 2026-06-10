import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from ..core.paths import DATABASE_PATH, resolve_uploaded_file_path

DEFAULT_DATABASE_URL = f"sqlite:///{DATABASE_PATH}"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
    ensure_tasks_columns()
    ensure_document_fields_columns()
    ensure_extracted_entities_columns()
    migrate_task_file_paths()


def ensure_table_columns(table_name: str, required_columns: dict[str, str]):
    inspector = inspect(engine)

    if table_name not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}

    with engine.begin() as connection:
        for column_name, ddl in required_columns.items():
            if column_name not in existing_columns:
                connection.execute(text(ddl))


def ensure_tasks_columns():
    required_columns = {
        "file_hash": "ALTER TABLE tasks ADD COLUMN file_hash VARCHAR",
        "processor_version": "ALTER TABLE tasks ADD COLUMN processor_version VARCHAR",
        "extract_config": "ALTER TABLE tasks ADD COLUMN extract_config TEXT",
        "parse_status": "ALTER TABLE tasks ADD COLUMN parse_status VARCHAR DEFAULT 'pending'",
        "extract_status": "ALTER TABLE tasks ADD COLUMN extract_status VARCHAR DEFAULT 'pending'",
        "match_status": "ALTER TABLE tasks ADD COLUMN match_status VARCHAR DEFAULT 'pending'",
        "progress_stage": "ALTER TABLE tasks ADD COLUMN progress_stage VARCHAR",
        "progress_current": "ALTER TABLE tasks ADD COLUMN progress_current INTEGER",
        "progress_total": "ALTER TABLE tasks ADD COLUMN progress_total INTEGER",
        "progress_percent": "ALTER TABLE tasks ADD COLUMN progress_percent FLOAT",
        "progress_message": "ALTER TABLE tasks ADD COLUMN progress_message TEXT",
        "result": "ALTER TABLE tasks ADD COLUMN result TEXT",
        "extract_result": "ALTER TABLE tasks ADD COLUMN extract_result TEXT",
        "match_result": "ALTER TABLE tasks ADD COLUMN match_result TEXT",
        "error_message": "ALTER TABLE tasks ADD COLUMN error_message TEXT",
    }

    ensure_table_columns("tasks", required_columns)


def ensure_document_fields_columns():
    required_columns = {
        "category": "ALTER TABLE document_fields ADD COLUMN category VARCHAR",
        "indicator": "ALTER TABLE document_fields ADD COLUMN indicator VARCHAR",
        "value": "ALTER TABLE document_fields ADD COLUMN value VARCHAR",
        "unit": "ALTER TABLE document_fields ADD COLUMN unit VARCHAR",
        "time": "ALTER TABLE document_fields ADD COLUMN time VARCHAR",
        "yoy": "ALTER TABLE document_fields ADD COLUMN yoy VARCHAR",
        "source_document": "ALTER TABLE document_fields ADD COLUMN source_document VARCHAR",
        "source_paragraph": "ALTER TABLE document_fields ADD COLUMN source_paragraph INTEGER",
        "source_text": "ALTER TABLE document_fields ADD COLUMN source_text TEXT",
        "source_span": "ALTER TABLE document_fields ADD COLUMN source_span VARCHAR",

        "project_name_source_file": "ALTER TABLE document_fields ADD COLUMN project_name_source_file VARCHAR",
        "project_name_source_paragraph": "ALTER TABLE document_fields ADD COLUMN project_name_source_paragraph INTEGER",
        "project_name_source_text": "ALTER TABLE document_fields ADD COLUMN project_name_source_text TEXT",

        "project_leader_source_file": "ALTER TABLE document_fields ADD COLUMN project_leader_source_file VARCHAR",
        "project_leader_source_paragraph": "ALTER TABLE document_fields ADD COLUMN project_leader_source_paragraph INTEGER",
        "project_leader_source_text": "ALTER TABLE document_fields ADD COLUMN project_leader_source_text TEXT",

        "organization_name_source_file": "ALTER TABLE document_fields ADD COLUMN organization_name_source_file VARCHAR",
        "organization_name_source_paragraph": "ALTER TABLE document_fields ADD COLUMN organization_name_source_paragraph INTEGER",
        "organization_name_source_text": "ALTER TABLE document_fields ADD COLUMN organization_name_source_text TEXT",

        "phone_source_file": "ALTER TABLE document_fields ADD COLUMN phone_source_file VARCHAR",
        "phone_source_paragraph": "ALTER TABLE document_fields ADD COLUMN phone_source_paragraph INTEGER",
        "phone_source_text": "ALTER TABLE document_fields ADD COLUMN phone_source_text TEXT",
    }

    ensure_table_columns("document_fields", required_columns)


def ensure_extracted_entities_columns():
    required_columns = {
        "field_embedding": "ALTER TABLE extracted_entities ADD COLUMN field_embedding TEXT",
        "source_kind": "ALTER TABLE extracted_entities ADD COLUMN source_kind VARCHAR",
        "source_table_id": "ALTER TABLE extracted_entities ADD COLUMN source_table_id VARCHAR",
        "source_row": "ALTER TABLE extracted_entities ADD COLUMN source_row INTEGER",
        "source_col": "ALTER TABLE extracted_entities ADD COLUMN source_col INTEGER",
        "source_header": "ALTER TABLE extracted_entities ADD COLUMN source_header VARCHAR",
        "source_locator": "ALTER TABLE extracted_entities ADD COLUMN source_locator VARCHAR",
        "source_context": "ALTER TABLE extracted_entities ADD COLUMN source_context TEXT",
    }

    ensure_table_columns("extracted_entities", required_columns)


def migrate_task_file_paths():
    inspector = inspect(engine)

    if "tasks" not in inspector.get_table_names():
        return

    with engine.begin() as connection:
        rows = connection.execute(text("SELECT id, file_path FROM tasks WHERE file_path IS NOT NULL")).fetchall()
        for task_id, file_path in rows:
            try:
                normalized_path = str(resolve_uploaded_file_path(file_path))
            except ValueError:
                continue

            if file_path != normalized_path:
                connection.execute(
                    text("UPDATE tasks SET file_path = :file_path WHERE id = :task_id"),
                    {"file_path": normalized_path, "task_id": task_id},
                )
