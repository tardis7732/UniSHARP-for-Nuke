"""Download the official UniSHARP checkpoint into this checkout."""
from pathlib import Path
import json, hashlib, requests
root = Path(__file__).resolve().parents[1]
repo = 'Insta360-Research/Unisharp'
name = 'pretained_model.pt'
session = requests.Session()
meta = session.get(f'https://huggingface.co/api/models/{repo}?blobs=true', timeout=60)
meta.raise_for_status()
metadata = meta.json()
revision = metadata['sha']
entry = next(x for x in metadata['siblings'] if x['rfilename'] == name)
expected = entry.get('lfs', {}).get('sha256')
target = root / 'checkpoints' / name
partial = target.with_suffix('.pt.partial')
target.parent.mkdir(exist_ok=True)
print(json.dumps({'revision':revision,'file':entry}), flush=True)
downloaded = not target.exists()
if downloaded:
    url = f'https://huggingface.co/{repo}/resolve/{revision}/{name}'
    with session.get(url, stream=True, timeout=(30,120)) as response:
        response.raise_for_status()
        done=0
        with partial.open('wb') as stream:
            for chunk in response.iter_content(8*1024*1024):
                stream.write(chunk)
                done+=len(chunk)
                if done % (256*1024*1024) < len(chunk): print(f'Downloaded {done/1e9:.2f} GB', flush=True)
artifact = partial if downloaded else target
with artifact.open('rb') as stream:
    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
if expected and digest != expected:
    raise RuntimeError(f'Checkpoint SHA-256 mismatch: {artifact.name}. Remove this invalid file and run the downloader again.')
if downloaded:
    partial.replace(target)
(root / 'checkpoints' / 'source.json').write_text(json.dumps({'repository':repo,'revision':revision,'filename':name,'sha256':digest,'bytes':target.stat().st_size},indent=2)+'\n',encoding='utf-8')
print('Verified '+str(target),flush=True)
