"""hasn_im.adapters · 持久化与外设适配（SQLAlchemy CRUD、Redis、outbox relay）

CRUD/ORM 访问与 `astra_im_service` 角色的 session maker 落在这里。domain/application 依赖
adapters 的抽象，不直接触碰 ORM 细节。
"""
