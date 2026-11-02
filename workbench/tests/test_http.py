import base64
import json

import httpx
import pytest


@pytest.mark.asyncio
async def test_http_flow_and_original_routes(engine,monkeypatch):
    from api.main import app
    from adapters.intelligence import http
    monkeypatch.setattr(http,'engine',engine)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get('/api/health')).status_code==200
        assert (await client.get('/')).status_code==200
        assert (await client.get('/workbench/research')).status_code==200
        html=(await client.get('/workbench/')).text
        assert '/workbench/assets/' in html
        import re
        for resource in re.findall(r'(?:src|href)="(/workbench/assets/[^"]+)"',html):
            assert (await client.get(resource)).status_code==200
        assert (await client.get('/workbench/assets/not-present.js')).status_code==404
        assert (await client.get('/api/workbench/no-such-route')).status_code==404
        assert (await client.get('/workbench/no-such-route')).status_code==404
        renamed=await client.put('/api/workbench/topics/topic-a',json={'name':'新的课题名称'})
        assert renamed.json()['id']=='topic-a' and renamed.json()['name']=='新的课题名称'
        upload=await client.post('/api/workbench/topics/topic-a/import',json={'filename':'paper.md','content_base64':base64.b64encode(b'HTTP_CURRENT_TEXT').decode(),'title':'导入标题'})
        assert upload.status_code==200
        path=upload.json()['path']
        engine.store.path('topic-a',path).write_text('HTTP_EDITED_CURRENT_TEXT',encoding='utf-8')
        preview=await client.get('/api/workbench/topics/topic-a/file',params={'path':path})
        assert preview.json()['content']=='HTTP_EDITED_CURRENT_TEXT'
        prepared=await client.post('/api/workbench/topics/topic-a/prepare',json={'kind':'analysis','paths':[path],'options':{'custom_themes':['CURRENT_TEXT']}})
        assert prepared.status_code==200
        task=await client.post('/api/workbench/topics/topic-a/tasks',json={'kind':'analysis','prepared_id':prepared.json()['id']})
        assert task.status_code==202
        await engine.running[task.json()['id']]
        detail=(await client.get('/api/workbench/topics/topic-a/tasks/'+task.json()['id'])).json()
        assert detail['status']=='completed' and detail['outputs']
        assert (await client.get('/api/workbench/topics/topic-b/tasks/'+task.json()['id'])).status_code==400
        denied=await client.get('/api/workbench/topics/topic-a/file',params={'path':'../topic-b/README.md'})
        assert denied.status_code==400
        bad=await client.post('/api/workbench/topics/topic-a/import',json={'filename':'../../bad.txt','content_base64':''})
        assert bad.status_code==400
        unsupported=await client.post('/api/workbench/topics/topic-a/crawl',json={'platforms':['github'],'crawler_type':'creator','creator_ids':'x'})
        assert unsupported.status_code==400
        settings=(await client.get('/api/workbench/settings')).json()
        assert 'api_key' not in settings


@pytest.mark.asyncio
async def test_setting_save_is_explicit_and_preserves_other_fields(tmp_path,monkeypatch):
    from fastapi import FastAPI
    from adapters.intelligence import http
    root=tmp_path/'project04';root.mkdir()
    env=root/'.env';env.write_text('# user comment\nUNRELATED=keep-me\nAI_API_KEY=existing-key\n')
    monkeypatch.setattr(http,'PROJECT04',root)
    monkeypatch.setenv('AI_API_KEY','existing-key')
    app=FastAPI();app.include_router(http.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.put('/api/workbench/settings',json={'model':'new-model','base_url':'https://example.org/v1','api_key':None})
        assert response.status_code==200 and response.json()['key_configured']
        assert 'existing-key' not in response.text
    text=env.read_text()
    assert '# user comment' in text and 'UNRELATED=keep-me' in text and 'AI_API_KEY=existing-key' in text
