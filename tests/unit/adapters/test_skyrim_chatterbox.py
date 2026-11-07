import json
import sys
import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
import httpx


@pytest.mark.parametrize("external", [False, True])
def test_connector_auto_enables_prepared_turbo_and_owns_worker_lifecycle(tmp_path, monkeypatch, external):
    from npc.adapters.skyrim import __main__ as entry
    original_bridge = entry.Bridge
    settings, workers = [], []
    class Bridge(original_bridge):
        def __init__(self, config, **kwargs):
            settings.append(config)
            super().__init__(config, **kwargs)
    class Worker:
        def __init__(self, root):
            self.process=None; self.started=0; self.closed=0; workers.append(self)
        async def start(self): self.started+=1; self.process=SimpleNamespace(returncode=None)
        async def close(self): self.closed+=1; self.process=None
    marker=tmp_path/'data/tts/.chatterbox-ready.json'; marker.parent.mkdir(parents=True); marker.write_text('{}')
    config=tmp_path/'connector.json'
    raw={'game_directory':str(tmp_path/'game'),'pipeline_mode':'inbox',
         'tts_url':'http://localhost:9999/tts' if external else None}
    config.write_text(json.dumps(raw))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys,'argv',['connector','--config',str(config)])
    monkeypatch.setattr(entry,'Bridge',Bridge)
    monkeypatch.setattr('npc.adapters.skyrim.whisper.prepare_model',lambda root: None)
    async def start(self): pass
    monkeypatch.setattr('npc.adapters.skyrim.speech.WhisperSpeech.start', start)
    monkeypatch.setattr('npc.output.chatterbox.model_path',lambda root: tmp_path)
    monkeypatch.setattr('npc.output.chatterbox.TurboWorker',Worker)
    def serve(app,**kwargs):
        with TestClient(app) as client:
            if not external:
                assert client.get('/v1/speech/health').json()['tts']=='Chatterbox Turbo'
                assert workers[0].started==1
    monkeypatch.setattr('uvicorn.run',serve); monkeypatch.setattr(entry,'port_taken',lambda port: False)  # a live bridge may hold the port
    entry.main()
    assert settings[0].tts_url==('http://localhost:9999/tts' if external else 'http://127.0.0.1:8768/v1/speech/tts')
    assert len(workers)==(0 if external else 1)
    if workers: assert workers[0].closed==1
    assert json.loads(config.read_text())==raw


@pytest.mark.parametrize("external", [False, True])
def test_bridge_adds_npc_identity_only_to_builtin_tts(tmp_path, external):
    from npc.adapters.skyrim.bridge import Bridge
    from tests.unit.adapters.test_skyrim_connector import settings, pending, result
    from tests.unit.adapters.test_skyrim_speech import audio
    requests=[]
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,content=audio(),headers={'content-type':'audio/wav'})
    async def run():
        bridge=Bridge(settings(tmp_path,audio_output='engine',tts_url='http://localhost/tts' if external else 'http://127.0.0.1:8768/v1/speech/tts'),
            root=tmp_path,client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        try:
            await pending(bridge)
            await bridge.result(result())
            assert bridge.command('session-a')['audio_file'] is not None
            assert requests[0]['text']=='Good day.'
            assert ('npc_base' in requests[0]) is not external
            if not external:
                assert requests[0]['npc_base']['local_id']=='0001347B'
                assert requests[0]['npc_base']['plugin']=='Skyrim.esm'
        finally: await bridge.close()
    asyncio.run(run())
