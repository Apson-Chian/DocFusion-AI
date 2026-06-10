from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Float, UniqueConstraint
from datetime import datetime
from .database import Base


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)

    # 文件基础信息
    file_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    file_type = Column(String, nullable=True)
    file_hash = Column(String, nullable=True, index=True)
    processor_version = Column(String, nullable=True, index=True)
    extract_config = Column(Text, nullable=True)

    # 总状态 + 分阶段状态
    status = Column(String, default="uploaded")
    parse_status = Column(String, default="pending")
    extract_status = Column(String, default="pending")
    match_status = Column(String, default="pending")
    progress_stage = Column(String, nullable=True)
    progress_current = Column(Integer, nullable=True)
    progress_total = Column(Integer, nullable=True)
    progress_percent = Column(Float, nullable=True)
    progress_message = Column(Text, nullable=True)

    error_message = Column(Text, nullable=True)

    # parser 输出（原样 JSON 字符串）
    result = Column(Text, nullable=True)

    # extract 模块原样输出（原样 JSON 字符串）
    extract_result = Column(Text, nullable=True)

    # matcher 模块原样输出（原样 JSON 字符串）
    match_result = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DocumentField(Base):
    """
    旧表先保留，避免其他地方引用时报错。
    现在它主要承担：
    1. 保存当前文档的解析缓存
    2. 保存一个“主结果”方便旧接口兼容
    3. 保存通用来源定位信息
    """
    __tablename__ = "document_fields"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False, index=True)

    # 文档解析缓存
    doc_id = Column(String, nullable=True)
    doc_type = Column(String, nullable=True)
    raw_text = Column(Text, nullable=True)
    paragraphs = Column(Text, nullable=True)   # JSON 字符串
    tables = Column(Text, nullable=True)       # JSON 字符串

    # 当前你们规则抽取里常见的主字段
    category = Column(String, nullable=True)
    indicator = Column(String, nullable=True)
    value = Column(String, nullable=True)
    unit = Column(String, nullable=True)
    time = Column(String, nullable=True)
    yoy = Column(String, nullable=True)

    # 通用来源定位字段（后面前端点击值时主要查这些）
    source_document = Column(String, nullable=True)
    source_paragraph = Column(Integer, nullable=True)
    source_text = Column(Text, nullable=True)
    source_span = Column(String, nullable=True)

    # 下面这些旧字段继续保留，避免原来代码报错
    project_name_source_file = Column(String, nullable=True)
    project_name_source_paragraph = Column(Integer, nullable=True)
    project_name_source_text = Column(Text, nullable=True)

    project_leader_source_file = Column(String, nullable=True)
    project_leader_source_paragraph = Column(Integer, nullable=True)
    project_leader_source_text = Column(Text, nullable=True)

    organization_name_source_file = Column(String, nullable=True)
    organization_name_source_paragraph = Column(Integer, nullable=True)
    organization_name_source_text = Column(Text, nullable=True)

    phone_source_file = Column(String, nullable=True)
    phone_source_paragraph = Column(Integer, nullable=True)
    phone_source_text = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ExtractedEntity(Base):
    """
    新增：通用抽取结果表
    用来把规则抽取/模型抽取的结果拆开存，后面做：
    1. 模板填表
    2. 字段定位
    3. 语义匹配
    4. 避免重复计算
    """
    __tablename__ = "extracted_entities"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False, index=True)

    # 文档信息
    doc_id = Column(String, nullable=True)
    source_document = Column(String, nullable=True)

    # 一条记录的分组编号
    # 例如一条记录里有 分类/指标/数值/单位/时间/同比，这些共用一个 record_id
    record_id = Column(String, nullable=True, index=True)

    # 通用字段名和值
    field_name = Column(String, nullable=False, index=True)
    field_value = Column(Text, nullable=True)
    normalized_value = Column(Text, nullable=True)

    # 来源定位
    source_paragraph = Column(Integer, nullable=True)
    source_text = Column(Text, nullable=True)
    source_span = Column(String, nullable=True)
    field_embedding = Column(Text, nullable=True)
    source_kind = Column(String, nullable=True)
    source_table_id = Column(String, nullable=True, index=True)
    source_row = Column(Integer, nullable=True)
    source_col = Column(Integer, nullable=True)
    source_header = Column(String, nullable=True)
    source_locator = Column(String, nullable=True)
    source_context = Column(Text, nullable=True)

    # 质量信息
    confidence = Column(Float, nullable=True)
    extractor_type = Column(String, nullable=True)  # rule / llm / ner / hybrid

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FieldEmbeddingCache(Base):
    """
    Cache semantic vectors for extracted field names.

    The matcher model is expensive to call repeatedly during template filling, so
    we store field-name embeddings once and reuse them across later matches.
    """
    __tablename__ = "field_embedding_cache"
    __table_args__ = (
        UniqueConstraint("model_name", "field_hash", name="uq_field_embedding_model_hash"),
    )

    id = Column(Integer, primary_key=True, index=True)
    model_name = Column(String, nullable=False, index=True)
    field_name = Column(String, nullable=False)
    field_hash = Column(String, nullable=False, index=True)
    embedding = Column(Text, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
