"""Validate the manuscript, PDF and Word version, then package the allowlisted editable source."""
from pathlib import Path
import hashlib,json,re
from zipfile import ZipFile,ZIP_DEFLATED
from docx import Document
from pypdf import PdfReader
HERE=Path(__file__).resolve().parent
TITLE='Persona-Aware, Knowledge-Bounded NPC Dialogue Through Model-Agnostic LLM Orchestration'
tex=(HERE/'main.tex').read_text(encoding='utf-8-sig');bib=(HERE/'references.bib').read_text(encoding='utf-8-sig')
pdf=PdfReader(HERE/'paper.pdf');text='\n'.join(p.extract_text() or '' for p in pdf.pages)
assert 6<=len(pdf.pages)<=8  # Project target for this paper.
assert re.search(r'\\title\{([^}]+)\}',tex).group(1)==TITLE
assert pdf.metadata.title==TITLE
assert (HERE/'paper.pdf').read_bytes()==(HERE/'main.pdf').read_bytes()
abstract=re.search(r'\\begin\{abstract\}(.*?)\\end\{abstract\}',tex,re.S).group(1)
words=len(abstract.split());assert 150<=words<=200,words
keys=set(re.findall(r'@\w+\{([^,]+),',bib));cited={k.strip() for group in re.findall(r'\\cite\{([^}]+)\}',tex) for k in group.split(',')}
labels=set(re.findall(r'\\label\{([^}]+)\}',tex))
for rel in re.findall(r'\\input\{([^}]+)\}',tex):labels|=set(re.findall(r'\\label\{([^}]+)\}',(HERE/rel).read_text(encoding='utf-8-sig')))
refs=set(re.findall(r'\\ref\{([^}]+)\}',tex))
assert cited<=keys and refs<=labels,(cited-keys,refs-labels)
assert not keys-cited,keys-cited
assert len(re.findall(r'\\section\{',tex))==8
for usn in ['1RVU23CSE282','1RVU23CSE443','1RVU23CSE317','1RVU23CSE316']:assert usn in text
graphics=re.findall(r'\\includegraphics(?:\[[^]]*\])?\{([^}]+)\}',tex)
for rel in graphics:assert (HERE/rel).is_file(),rel
log=(HERE/'main.log').read_text(encoding='utf-8',errors='replace')
assert not re.search(r'Overfull|LaTeX Font Warning|Citation .* undefined|Reference .* undefined|There were undefined',log)
figures=['system_architecture','knowledge_binding','rag_orchestration_pipeline','model_agnostic_architecture','cv_scene_pipeline']
for stem in figures:
    assert (HERE/'figures'/f'{stem}.pdf').is_file()
    assert '\\begin{tikzpicture}' in (HERE/'figure_sources'/f'{stem}.tex').read_text(encoding='utf-8-sig')
word=Document(HERE/'paper.docx');word_text='\n'.join(p.text for p in word.paragraphs)
table_count=len(re.findall(r'\\begin\{table',tex))+sum((HERE/r).read_text(encoding='utf-8-sig').count('\\begin{table') for r in re.findall(r'\\input\{([^}]+)\}',tex))
assert word.core_properties.title==TITLE and word_text.startswith(TITLE)
assert len(word.inline_shapes)==len(graphics),(len(word.inline_shapes),len(graphics))
assert len(word.tables)==table_count,(len(word.tables),table_count)
assert not re.search(r'\\[a-zA-Z]+|\$',word_text)
for usn in ['1RVU23CSE282','1RVU23CSE443','1RVU23CSE317','1RVU23CSE316']:assert usn in word_text
files=[HERE/x for x in ['main.tex','references.bib','README.md','REPORT_REVISION.md','validate_and_package.py','build_docx.py','paper.docx']]
files+=[HERE/'figures'/f'{s}.pdf' for s in figures]+[HERE/'figures/game_controls.jpg']
files+=sorted((HERE/'tables').glob('*.tex'))
files+=sorted((HERE/'figure_sources').glob('*.tex'))
allowed={'.md','.py','.json','.csv','.txt'}
files+=sorted(p for p in (HERE/'experiments').rglob('*') if p.is_file() and p.suffix in allowed and '__pycache__' not in p.parts)
archive=HERE/'paper_source.zip'
with ZipFile(archive,'w',ZIP_DEFLATED) as z:
    for p in files:z.write(p,p.relative_to(HERE).as_posix())
with ZipFile(archive) as z:
    assert z.testzip() is None
    members=z.namelist()
    assert all(not n.startswith('/') and '..' not in Path(n).parts for n in members)
    assert all(not any(t in Path(n).parts for t in ['.env','models','index','__pycache__']) for n in members)
audit=json.loads((HERE/'experiments/raw_results/skyrim_live_audit.json').read_text(encoding='utf-8'))
assert not {'text','session','player_text'}&{k for row in audit['rows'] for k in row}
report={'title':TITLE,'pages':len(pdf.pages),'abstract_words':words,'references':len(keys),'undefined_citations':sorted(cited-keys),
 'undefined_references':sorted(refs-labels),'native_tikz_figures':figures,'main_paper_figures':len(graphics),'in_game_screenshot_included':True,
 'pdf_sha256':hashlib.sha256((HERE/'paper.pdf').read_bytes()).hexdigest(),'docx_sha256':hashlib.sha256((HERE/'paper.docx').read_bytes()).hexdigest(),
 'source_zip_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'docx_tables':len(word.tables),'docx_images':len(word.inline_shapes),
 'zip_members':members,'overfull_boxes':0,'font_shape_warnings':0,'underfull_hboxes':log.count('Underfull \\hbox'),
 'underfull_vboxes':log.count('Underfull \\vbox'),'new_live_runs':0,
 'research_gates':['Vision accuracy unmeasured: no VLM run and no labelled frames.','Independent semantic, persona and relevance ratings pending.',
 'Reply quality under the conversation director not measured.','No multi-backend comparison, live retrieval or live speech input.']}
output=HERE.parent/'evidence/current_system_document_validation.json'
output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='zip_members'},indent=2))
