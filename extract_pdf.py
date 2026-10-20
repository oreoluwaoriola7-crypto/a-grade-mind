import io,sys
import resource
resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
resource.setrlimit(resource.RLIMIT_CPU,(20,20))
from pypdf import PdfReader
raw=sys.stdin.buffer.read(10*1024*1024+1)
if len(raw)>10*1024*1024:raise ValueError('Too large')
r=PdfReader(io.BytesIO(raw))
if r.is_encrypted or not 1<=len(r.pages)<=25:raise ValueError('Invalid pages/encryption')
# Image-bearing PDFs can depend on diagrams; do not silently drop them.
for page in r.pages:
    resources=page.get('/Resources')
    if resources:
        resources=resources.get_object()
        if '/XObject' in resources:
            objects=resources['/XObject'].get_object()
            if any(obj.get_object().get('/Subtype')=='/Image' for obj in objects.values()):raise ValueError('Image-bearing PDF needs manual transcription')
text='\n'.join(page.extract_text() or '' for page in r.pages)
if not 25<=len(text)<=300000:raise ValueError('Invalid text length')
sys.stdout.write(text)
