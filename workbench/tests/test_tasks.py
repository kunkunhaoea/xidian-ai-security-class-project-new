import asyncio
import json

import httpx
import pytest

from adapters.shared_files import atomic_json


async def finish(engine, record):
    await engine.running[record['id']]
    return engine.get(record['id'])


@pytest.mark.asyncio
async def test_analysis_reaches_actual_http_payload_and_freezes_inputs(engine, monkeypatch):
    from pipeline import analysis_service as analysis
    monkeypatch.setenv('AI_API_KEY','offline-test-secret')
    payloads=[]
    async def transport(request):
        body=json.loads(request.content);payloads.append(body)
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'summary':'真实输入的模拟总结'})}}]})
    client=httpx.AsyncClient
    monkeypatch.setattr(analysis.httpx,'AsyncClient',lambda **kw:client(transport=httpx.MockTransport(transport),**kw))
    path=engine.store.write_new('topic-a','notes','input.md','SENTINEL 人工编辑后的当前内容')
    engine.store.write_new('topic-a','notes','unselected.md','UNSELECTED_HISTORY')
    prepared=await engine.prepare('topic-a',[path],'analysis',{'custom_themes':['SENTINEL'],'merge':True})
    engine.store.path('topic-a',path).write_text('LATER_EDIT',encoding='utf-8')
    task=engine.create('topic-a','analysis',prepared_id=prepared['id'])
    result=await finish(engine,task)
    assert result['status']=='completed'
    actual=json.dumps(payloads,ensure_ascii=False)
    assert 'SENTINEL' in actual and 'LATER_EDIT' not in actual and 'UNSELECTED_HISTORY' not in actual
    report=engine.store.path('topic-a',result['outputs'][0]).read_text()
    assert '真实输入的模拟总结' in report and prepared['documents'][0]['sha256'] in report
    following=await engine.prepare('topic-a',[path],'analysis',{'custom_themes':['LATER_EDIT']})
    assert following['documents'][0]['text']=='LATER_EDIT'


@pytest.mark.asyncio
async def test_ideas_use_only_selected_files_and_real_reflection_loop(engine, monkeypatch):
    from api.routers import scientist
    payloads=[]
    async def call(payload):
        payloads.append(payload)
        return json.dumps({'ideas':[{'Title':'可检验的研究想法','Short Hypothesis':'假设','Experiments':'指标和基线'}]},ensure_ascii=False)
    monkeypatch.setattr(scientist,'_call_llm',call)
    def prohibited(*args,**kwargs):
        raise AssertionError('must never read global history')
    monkeypatch.setattr(scientist,'_filter_idea_context',prohibited)
    monkeypatch.setattr(scientist,'_v2_knowledge_base_dir',prohibited)
    path=engine.store.write_new('topic-a','notes','input.md','SELECTED_CURRENT_TEXT')
    prepared=await engine.prepare('topic-a',[path],'ideas',{'count':1,'reflections':2})
    task=engine.create('topic-a','ideas',prepared_id=prepared['id'])
    result=await finish(engine,task)
    assert result['status']=='completed' and len(payloads)==3
    assert all('SELECTED_CURRENT_TEXT' in json.dumps(p) for p in payloads)
    output=result['outputs'][0]
    engine.store.path('topic-a',output).write_text('# USER_EDITED_IDEA\nmodified',encoding='utf-8')
    engine.retry(task['id'],archive_only=True)
    assert engine.store.path('topic-a',output).read_text().startswith('# USER_EDITED_IDEA')
    assert len(payloads)==3
    assert 'USER_EDITED_IDEA' in engine.store.read_inputs('topic-a',[output])['documents'][0]['text']


@pytest.mark.asyncio
async def test_save_failure_can_recover_without_model_call(engine,monkeypatch):
    from api.routers import scientist
    calls=[]
    async def call(payload):
        calls.append(payload)
        return json.dumps({'ideas':[{'Title':'保存恢复'}]})
    monkeypatch.setattr(scientist,'_call_llm',call)
    path=engine.store.write_new('topic-a','notes','input.md','material')
    prepared=await engine.prepare('topic-a',[path],'ideas',{'count':1,'reflections':1})
    write=engine.store.write_new
    def failing(*args,**kwargs):raise OSError('磁盘写入失败')
    monkeypatch.setattr(engine.store,'write_new',failing)
    task=engine.create('topic-a','ideas',prepared_id=prepared['id'])
    result=await finish(engine,task)
    assert result['status']=='failed' and (engine.location(task['id'])/'result.json').is_file()
    count=len(calls)
    monkeypatch.setattr(engine.store,'write_new',write)
    recovered=engine.retry(task['id'],archive_only=True)
    assert recovered['outputs'] and recovered['status']=='completed' and len(calls)==count


@pytest.mark.asyncio
async def test_cancel_before_first_coroutine_step(engine):
    task=engine.create('topic-a','crawl',{'platforms':['arxiv']})
    future=engine.running[task['id']]
    engine.cancel(task['id'])
    await asyncio.gather(future,return_exceptions=True)
    await asyncio.sleep(0)
    assert engine.get(task['id'])['status']=='cancelled'


def test_recovery_of_interrupted_task(engine):
    record={'id':'a'*32,'topic_id':'topic-a','kind':'analysis','status':'running'}
    engine.save(record)
    engine.recover()
    assert engine.get(record['id'])['status']=='interrupted'


@pytest.mark.asyncio
async def test_only_unique_crawl_directory_is_archived(engine,tmp_path,monkeypatch):
    from adapters.intelligence import engine as module
    from api.services.crawler_manager import crawler_manager
    monkeypatch.setattr(crawler_manager,'status','idle')
    fake=tmp_path/'crawler';fake.mkdir()
    (fake/'main.py').write_text('''import sys,json,pathlib
root=pathlib.Path(sys.argv[sys.argv.index('--save_data_path')+1]);platform=sys.argv[sys.argv.index('--platform')+1]
p=root/platform/'jsonl';p.mkdir(parents=True,exist_ok=True)
(p/'contents.jsonl').write_text(json.dumps({'title':'CURRENT_RUN','url':'https://example.org/new','content':'body','access_token':'must-not-leak'})+'\\n')
print('采集完成')
''')
    monkeypatch.setattr(module,'PROJECT04',fake)
    stale=fake/'data/arxiv/jsonl';stale.mkdir(parents=True)
    (stale/'old.jsonl').write_text(json.dumps({'title':'OLD_BATCH','url':'https://example.org/old'})+'\n')
    task=engine.create('topic-a','crawl',{'platforms':['arxiv'],'keywords':'current'})
    result=await finish(engine,task)
    assert result['status']=='completed'
    assert [m['title'] for m in engine.store.materials('topic-a')]==['CURRENT_RUN']
    assert 'must-not-leak' not in '\n'.join(p.read_text() for p in engine.location(task['id']).rglob('*.jsonl'))


@pytest.mark.asyncio
async def test_novelty_fetch_is_frozen_and_does_not_requery(engine,monkeypatch):
    from api.routers import scientist
    calls=[]
    async def transport(request):
        calls.append(str(request.url));return httpx.Response(200,json={'results':[{'display_name':'补充论文','publication_year':2025,'doi':'https://doi.org/example'}]})
    client=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:client(transport=httpx.MockTransport(transport),**kw))
    async def call(payload):
        assert payload['openalex_novelty_check'][0]['title']=='补充论文'
        return json.dumps({'ideas':[{'Title':'novelty candidate'}]})
    monkeypatch.setattr(scientist,'_call_llm',call)
    path=engine.store.write_new('topic-a','notes','x.md','selected')
    snapshot=await engine.prepare('topic-a',[path],'ideas',{'strategy':'v2_novelty','count':1})
    task=engine.create('topic-a','ideas',prepared_id=snapshot['id'])
    assert (await finish(engine,task))['status']=='completed'
    assert len(calls)==1


@pytest.mark.asyncio
async def test_model_failure_not_reported_as_success(engine,monkeypatch):
    from pipeline import analysis_service as analysis
    monkeypatch.setenv('AI_API_KEY','offline-test-secret')
    async def transport(request):return httpx.Response(503)
    client=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:client(transport=httpx.MockTransport(transport),**kw))
    path=engine.store.write_new('topic-a','notes','x.md','SENTINEL')
    prepared=await engine.prepare('topic-a',[path],'analysis',{'custom_themes':['SENTINEL']})
    task=engine.create('topic-a','analysis',prepared_id=prepared['id'])
    result=await finish(engine,task)
    assert result['status']=='failed' and 'AI总结失败' in result['error']
    assert not result['outputs']


@pytest.mark.asyncio
async def test_prepared_input_cannot_cross_topics(engine):
    path=engine.store.write_new('topic-a','notes','x.md','selected')
    snapshot=await engine.prepare('topic-a',[path],'ideas',{})
    with pytest.raises(ValueError):engine.create('topic-b','ideas',prepared_id=snapshot['id'])


@pytest.mark.asyncio
async def test_separate_analysis_and_partial_unsupported_input(engine):
    a=engine.store.write_new('topic-a','notes','a.md','MATCH_A content')
    b=engine.store.write_new('topic-a','notes','b.md','MATCH_A second')
    unsupported=engine.store.write_new('topic-a','notes','bad.bin',b'bad')
    prepared=await engine.prepare('topic-a',[a,b,unsupported],'analysis',{'custom_themes':['MATCH_A'],'merge':False})
    task=engine.create('topic-a','analysis',prepared_id=prepared['id'])
    result=await finish(engine,task)
    assert len(result['outputs'])==2 and result['status']=='partial'
    assert engine.retry(task['id'],archive_only=True)['status']=='partial'


@pytest.mark.asyncio
async def test_model_config_frozen_without_credentials_on_disk(engine,monkeypatch):
    from pipeline import analysis_service as analysis
    monkeypatch.setenv('AI_API_KEY','TEST_PRIVATE_KEY_NEVER_PERSIST')
    monkeypatch.setenv('AI_MODEL','original-model')
    sent=[]
    async def transport(request):
        sent.append(json.loads(request.content));return httpx.Response(200,json={'choices':[{'message':{'content':'{"summary":"test"}'}}]})
    client=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:client(transport=httpx.MockTransport(transport),**kw))
    path=engine.store.write_new('topic-a','notes','x.md','MATCH')
    prepared=await engine.prepare('topic-a',[path],'analysis',{'custom_themes':['MATCH']})
    task=engine.create('topic-a','analysis',prepared_id=prepared['id'])
    monkeypatch.setenv('AI_MODEL','changed-after-submission')
    assert (await finish(engine,task))['status']=='completed'
    assert sent[0]['model']=='original-model'
    assert 'TEST_PRIVATE_KEY_NEVER_PERSIST' not in ''.join(p.read_text() for p in engine.state.rglob('*') if p.is_file())


def test_comments_and_already_downloaded_files_are_preserved(engine):
    record={'id':'b'*32,'topic_id':'topic-a','kind':'crawl','status':'failed','outputs':[]}
    engine.save(record)
    root=engine.location(record['id'])/'raw'/'xhs'
    (root/'jsonl').mkdir(parents=True)
    (root/'images'/'post123').mkdir(parents=True)
    (root/'jsonl'/'contents.jsonl').write_text(json.dumps({'note_id':'post123','title':'原帖','url':'https://example.com/post','desc':'正文'})+'\n')
    (root/'jsonl'/'comments.jsonl').write_text(json.dumps({'note_id':'post123','comment_id':'c1','content':'原始讨论'})+'\n')
    (root/'images'/'post123'/'image.png').write_bytes(b'png-content')
    result=engine.archive_crawl(record['id'])
    folder=result['outputs'][0]
    assert engine.store.path('topic-a',folder+'/image.png').read_bytes()==b'png-content'
    assert '原始讨论' in engine.store.path('topic-a',folder+'/comment-c1.json').read_text()
    engine.archive_crawl(record['id'])
    assert len(list(engine.store.path('topic-a',folder).glob('image*')))==1


@pytest.mark.asyncio
async def test_service_shutdown_marks_active_task_interrupted(engine,monkeypatch):
    import api.main as main
    from api.routers import scientist
    monkeypatch.setattr(main,'workbench_engine',engine)
    async def wait_for_cancel(payload):
        await asyncio.Event().wait()
    monkeypatch.setattr(scientist,'_call_llm',wait_for_cancel)
    async with main.app.router.lifespan_context(main.app):
        path=engine.store.write_new('topic-a','notes','x.md','selected')
        prepared=await engine.prepare('topic-a',[path],'ideas',{})
        task=engine.create('topic-a','ideas',prepared_id=prepared['id'])
        await asyncio.sleep(0)
        assert engine.get(task['id'])['status']=='running'
    assert engine.get(task['id'])['status']=='interrupted'
