import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile
import pytest

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('anima_build',ROOT/'scripts/build_skyrim_connector.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)

def test_package_preserves_both_targets_and_never_invents_live_verification(tmp_path,monkeypatch):
    native=tmp_path/'native';native.mkdir()
    (native/'AnimaConnector.ini').write_text('[Connection]\nPort=8768')
    (native/'runtime_support.h').write_text('runtime source')
    for name in ['docs/SKYRIM_CONNECTOR.md','docs/reviews/2026-11-02-skyrim-connector.md','configs/skyrim/native-dependencies.json','configs/skyrim/native-dependencies.legacy-1.6.318.json']:
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}')
    dll=tmp_path/'compiled.dll';dll.write_bytes(b'controlled-compiled-artifact')
    console=tmp_path/'AnimaConsole.exe';console.write_bytes(b'console-artifact');(native/'anima.ico').write_bytes(b'ico')
    monkeypatch.setattr(b,'ROOT',tmp_path);monkeypatch.setattr(b,'NATIVE',native)
    pex=tmp_path/'AnimaStartDialogue.pex';pex.write_bytes(b'pex')
    path=b.package(dll,{},console,[pex])
    with zipfile.ZipFile(path) as z:
        manifest=json.loads(z.read('AnimaConnector/build.json'))
        assert manifest['target_runtimes']==['1.6.318.0','1.7.104.0']
        assert manifest['dll_sha256']==hashlib.sha256(dll.read_bytes()).hexdigest()
        assert manifest['live_game_verified'] is False
        assert set(manifest['runtime_verification'].values())=={'not_live_verified'}
        assert z.read('SKSE/Plugins/AnimaConnector.dll')==dll.read_bytes()
        assert z.read('SKSE/Plugins/AnimaConsole.exe')==console.read_bytes()
        assert manifest['console_exe_sha256']==hashlib.sha256(console.read_bytes()).hexdigest() and manifest['plugin_version']=='0.5.3'
        assert 'AnimaConnector/source/native/anima.ico' in z.namelist()
        assert z.read('Anima.esp')==b.build_esp() and z.read('Scripts/AnimaStartDialogue.pex')==b'pex'
        assert 'AnimaConnector/source/native/runtime_support.h' in z.namelist()
        assert 'AnimaConnector/source/native-dependencies.legacy-1.6.318.json' in z.namelist()

def test_generator_configuration_keeps_arch_only_for_visual_studio():
    vs=b.configure_arguments(Path('cmake'),Path('build'),'Visual Studio 17 2022')
    ninja=b.configure_arguments(Path('cmake'),Path('build'),'Ninja')
    assert vs[vs.index('-A')+1]=='x64'
    assert '-A' not in ninja and '-DCMAKE_BUILD_TYPE=Release' in ninja

@pytest.mark.parametrize('revision,dirty',[('wrong',''),('expected',' M file')])
def test_dirty_or_mismatched_dependencies_are_rejected_before_submodule_mutation(tmp_path,monkeypatch,revision,dirty):
    config=tmp_path/'configs/skyrim';config.mkdir(parents=True)
    (config/'native-dependencies.json').write_text(json.dumps({'dependency':dict(url='https://example.test/source',commit='expected')}))
    dep=tmp_path/'.tools/dependency';dep.mkdir(parents=True);(dep/'.gitmodules').write_text('submodule')
    monkeypatch.setattr(b,'ROOT',tmp_path)
    monkeypatch.setattr(b.subprocess,'check_output',lambda args,**kw:revision if args[1]=='rev-parse' else dirty)
    commands=[];monkeypatch.setattr(b,'command',lambda args,**kw:commands.append(args))
    with pytest.raises(ValueError,match='preserve'):b.dependencies(fetch=True)
    assert commands==[]

def test_missing_dependency_does_not_download_without_fetch(tmp_path,monkeypatch):
    config=tmp_path/'configs/skyrim';config.mkdir(parents=True)
    (config/'native-dependencies.json').write_text(json.dumps({'missing':dict(url='https://example.test/source',commit='abc')}))
    monkeypatch.setattr(b,'ROOT',tmp_path)
    with pytest.raises(ValueError,match='--fetch'):b.dependencies(False)
    assert not (tmp_path/'.tools/missing').exists()
