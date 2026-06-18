import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class Category(Base):
    """
    Adjacency list: каждая запись хранит parent_id → NULL для корневых.

    ADR (хранение иерархии, US-CAT-05):
      Рассматривались:
      1. ltree (PostgreSQL) — быстрые запросы, но нет поддержки SQLite в тестах,
         требует расширения.
      2. Adjacency list + рекурсивный CTE — портабельно, хлебные крошки за O(depth)
         запросов к Python-dict (для MVP дерево помещается в память), orphan node
         обнаруживается при обходе: parent_id ссылается на несуществующий узел.
      3. Materialized path (хранить "electronics/smartphones/android") — быстрый
         поиск предков, но сложнее при переименовании узлов.

      Выбор: adjacency list.
      Критерии:
      - Скорость breadcrumbs: дерево категорий мало (< 1000 узлов), целиком
        загружается в dict за один SELECT; обход предков — O(depth) словарных
        lookup'ов без повторных SQL-запросов.
      - Обнаружение orphan node: при загрузке плоского списка проверяем, что
        parent_id каждого узла присутствует в id-множестве; несуществующий parent →
        orphan → 422. Просто и без доп. индексов.
    """

    __tablename__ = "categories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), nullable=True, unique=True)
    description = Column(Text, nullable=True)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("categories.id"), nullable=True)
    image_url = Column(String(512), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    seo = Column(JSON, nullable=True)
    meta_tags = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)