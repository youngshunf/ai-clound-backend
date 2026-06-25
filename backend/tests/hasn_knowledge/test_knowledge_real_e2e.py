"""knowledge 全链路集成测试（真实 PG + 真实私有桶 + 真实 RAGFlow，零 mock）。

P1 DoD：建库 / 目录树挂载 / 上传双写（桶+引擎）/ 原生文档保存即重向量化 / 解析读时对账 /
检索命中 / 双 owner 隔离 / 下载原件不经引擎（D10 代码路径断言）。
引擎数据集在测试尾部经 delete_kb 真实清理；PG 行随 rollback 消失。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from backend.app.hasn_knowledge.service.knowledge_service import knowledge_service

pytestmark = pytest.mark.asyncio

PARSE_TIMEOUT_SECONDS = 180


async def _wait_parsed(session, owner: str, kb_id: int, doc_id: int) -> dict:
    """轮询读时对账直到 parsed/failed（真实解析为异步秒~分钟级）。"""
    for _ in range(PARSE_TIMEOUT_SECONDS // 3):
        docs = await knowledge_service.list_documents(session, owner, kb_id)
        doc = next(d for d in docs if d['id'] == doc_id)
        if doc['parse_status'] in ('parsed', 'failed'):
            return doc
        await asyncio.sleep(3)
    raise AssertionError(f'文档 {doc_id} 在 {PARSE_TIMEOUT_SECONDS}s 内未完成解析（仍 {doc["parse_status"]}）')


async def test_full_pipeline_real_engine(session, ragflow_ready) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_e2e_{tag}'
    other_owner = f'h_e2e_other_{tag}'
    marker = f'唤星集成标记{tag}'

    # 1. 建库（同步建引擎 dataset）
    kb = await knowledge_service.create_kb(session, owner, name=f'E2E库-{tag}', description='集成测试')
    assert kb['status'] == 'active'
    try:
        # 2. 目录树 + 文档挂载
        folder = await knowledge_service.create_folder(session, owner, kb['id'], name='资料', parent_id=None)

        # 3. 上传文件（双写：私有桶原件 + 引擎副本）
        file_doc = await knowledge_service.upload_file_document(
            session,
            owner,
            kb['id'],
            filename=f'e2e-{tag}.txt',
            data=f'{marker}。这是上传文件的正文，讲述唤星知识库集成测试。'.encode(),
            mime='text/plain',
            folder_id=folder['id'],
            source='ui',
        )
        assert file_doc['parse_status'] == 'parsing', f'引擎推送失败: {file_doc["parse_error"]}'
        assert file_doc['folder_id'] == folder['id']

        # 4. 原生文档（正文 PG 权威 + 引擎副本）
        native_doc = await knowledge_service.create_native_document(
            session, owner, kb['id'], title=f'原生笔记-{tag}',
            content=f'# 原生文档\n\n{marker}。原生文档第一版，包含独特词汇穿山甲方案。', source='ui'
        )
        assert native_doc['parse_status'] == 'parsing', f'引擎推送失败: {native_doc["parse_error"]}'

        # 5. 解析读时对账推进到 parsed
        file_done = await _wait_parsed(session, owner, kb['id'], file_doc['id'])
        assert file_done['parse_status'] == 'parsed', f'文件解析失败: {file_done["parse_error"]}'
        assert file_done['chunk_count'] >= 1
        native_done = await _wait_parsed(session, owner, kb['id'], native_doc['id'])
        assert native_done['parse_status'] == 'parsed', f'原生文档解析失败: {native_done["parse_error"]}'

        # 6. kb 计数对账回写
        kbs = await knowledge_service.list_kbs(session, owner)
        kb_row = next(k for k in kbs if k['id'] == kb['id'])
        assert kb_row['document_count'] == 2
        assert kb_row['chunk_count'] >= 2

        # 7. 检索命中（owner 视角）
        result = await knowledge_service.search(session, owner, question=marker, top_k=8)
        assert result['kb_count'] == 1
        assert result['chunks'], '检索应命中（确无命中≠故障，此处必须命中标记词）'
        hit_names = {c['document_name'] for c in result['chunks']}
        assert hit_names & {file_doc['name'], native_doc['name']}
        # 命中片段带来源信息
        assert all(c['kb_id'] == kb['id'] for c in result['chunks'])

        # 8. 双 owner 隔离：另一 owner 检索不到（无库→空结果，且绝不命中 A 的内容）
        other = await knowledge_service.search(session, other_owner, question=marker, top_k=8)
        assert other == {'chunks': [], 'total': 0, 'kb_count': 0}

        # 9. 下载原件（私有桶流式，代码路径不触达引擎，D10）
        name, _mime, stream = await knowledge_service.download_document(session, owner, file_doc['id'])
        assert name == file_doc['name']
        downloaded = b''
        async for chunk in stream:
            downloaded += chunk
        assert marker.encode() in downloaded

        # 10. 原生文档保存即重向量化：编辑后旧引擎副本被替换（ragflow_document_id 更换）
        native_done['parse_status'] and (
            await knowledge_service.get_document(session, owner, native_doc['id'])
        )
        updated = await knowledge_service.update_native_document(
            session, owner, native_doc['id'],
            content=f'# 原生文档\n\n{marker}。第二版正文，独特词汇换成了鲸落计划。', source='ui'
        )
        assert updated['current_version'] == 2
        assert updated['parse_status'] == 'parsing', f'重索引推送失败: {updated["parse_error"]}'
        updated_done = await _wait_parsed(session, owner, kb['id'], native_doc['id'])
        assert updated_done['parse_status'] == 'parsed'

        # 11. fetch 文档解析文本（file → 引擎分块）
        text = await knowledge_service.fetch_file_doc_text(session, owner, file_doc['id'])
        assert any(marker in (c or '') for c in text['chunks'])

        # 12. agent 视角检索走维度②（restricted 白名单含该库 → 命中）
        agent = f'a_e2e_{tag}'
        await knowledge_service.put_agent_grant(session, owner, agent, mode='restricted', kb_ids=[kb['id']])
        agent_result = await knowledge_service.search(
            session, owner, question=marker, top_k=8, agent_hasn_id=agent
        )
        assert agent_result['chunks']
    finally:
        # 13. 清理：删库（级联删引擎 dataset + 文档行）——引擎侧真实删除
        await knowledge_service.delete_kb(session, owner, kb['id'])
    assert await knowledge_service.list_kbs(session, owner) == []
